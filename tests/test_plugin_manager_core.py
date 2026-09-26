"""插件系统（`plugin_manager/`）的纯逻辑单元测试 —— manifest / dependency。

为什么要补：39 功能域走查表里「插件系统」那一行的"相关测试"只有兜底的
import 冒烟（`tests/test_entry_imports.py`），而 `plugin_manager/` 下 10 个文件、
约 90KB 的代码一个专门测试都没有。前两轮补测试（存档备份 D-112 / 预下载
D-115、D-116）都是当场抓到真缺陷，这一轮同样按"先写'应该怎样'、看它红，
再决定修不修"来做。

本文件覆盖**零文件系统、零网络**的部分：

* `manifest.py` —— 解析 / 校验 / 描述降级 / 序列化；
* `dependency.py` —— SemVer 比较、约束解析、依赖与冲突检查、拓扑排序；
* `permissions.py` —— 权限分级、授权状态、持久化格式、i18n 显示键；
* `hook_bus.py` —— 注册/注销、优先级、四种返回策略、异常隔离、线程安全；
* `base.py` —— 抽象基类与 `notify()` 的权限闸门。

需要 `tmp_path` 的运行时行为见 ``tests/test_plugin_manager_io.py``
与 ``tests/test_plugin_manager_manager.py``。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from plugin_manager.base import HookPoint, PluginBase
from plugin_manager.dependency import (
    DependencyResolver,
    PluginDependency,
    _check_constraint,
    _compare_semver,
    _parse_constraint,
    _parse_version,
    parse_dependencies,
)
from plugin_manager.hook_bus import (
    HookBus,
    HookHandler,
    HookStrategy,
    _HOOK_DEFAULT_STRATEGY,
)
from plugin_manager.manifest import PLUGIN_MANIFEST_SCHEMA, PluginManifest
from plugin_manager.permissions import (
    PermissionGrant,
    PermissionRiskLevel,
    PluginPermission,
    PluginPermissionState,
    _PERMISSION_DISPLAY_KEYS,
    _PERMISSION_RISK_MAP,
    classify_permissions,
    get_all_permissions,
    get_permission_display_key,
    get_permission_risk,
)


def _raw(**kw) -> dict:
    """一份合法的 plugin.json 字典，可用关键字覆盖任意字段。"""
    data = {
        "id": "com.example.demo",
        "name": "Demo",
        "version": "1.2.3",
        "author": "tester",
        "min_fmcl_version": "1.0.0",
    }
    data.update(kw)
    return data


class _Recorder:
    """回调替身（形状核对过：`HookBus` 只调 ``callback(**kwargs)``）。"""

    def __init__(self, name: str, result=None, boom: BaseException | None = None) -> None:
        self.name = name
        self.result = result
        self.boom = boom
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom is not None:
            raise self.boom
        return self.result


# ═══════════════════════════════════════════════════════════════════
# manifest.py —— 解析与默认值
# ═══════════════════════════════════════════════════════════════════


def test_manifest_from_dict_fills_required_and_optional_defaults():
    m = PluginManifest.from_dict(_raw())
    assert (m.id, m.name, m.version, m.author, m.min_fmcl_version) == (
        "com.example.demo",
        "Demo",
        "1.2.3",
        "tester",
        "1.0.0",
    )
    assert m.description == {} and m.max_fmcl_version is None
    assert m.permissions == [] and m.dependencies == {} and m.conflicts == {}
    assert m.tags == [] and m.exports == [] and m.imports == []
    assert m.entry == "__init__" and m.install_path is None


def test_manifest_from_dict_missing_required_fields_becomes_empty_string():
    """缺必填字段时**不抛异常**，只填空串；`validate()` 只抓得住其中一部分。

    `name` / `author` 在 schema 里是 ``minLength: 1``，但 `validate()` 从不检查
    这两个字段 —— 这里把现状钉住（缺口已记进报告）。
    """
    m = PluginManifest.from_dict({"id": "com.example.demo"})
    assert (m.name, m.version, m.author, m.min_fmcl_version) == ("", "", "", "")
    errors = m.validate()
    assert any("version" in e for e in errors)
    assert any("min_fmcl_version" in e for e in errors)
    assert not [e for e in errors if "name" in e or "author" in e]


def test_manifest_from_dict_ignores_unknown_and_runtime_keys():
    """未知键不进 dataclass；`install_path` 只能由参数给，不能被 JSON 伪造。"""
    m = PluginManifest.from_dict(_raw(evil="x", install_path="/etc/passwd"), install_path=None)
    assert not hasattr(m, "evil")
    assert m.install_path is None


def test_manifest_validate_accepts_a_wellformed_manifest():
    assert PluginManifest.from_dict(_raw()).validate() == []


@pytest.mark.parametrize("bad_id", ["1abc", "com example", "com/example", "", "-lead"])
def test_manifest_validate_rejects_bad_ids(bad_id):
    errors = PluginManifest.from_dict(_raw(id=bad_id)).validate()
    assert errors, f"非法 ID 应当被拒: {bad_id!r}"
    assert any("插件 ID" in e for e in errors)


@pytest.mark.parametrize("bad_version", ["1.0", "1", "1.0.0.0", "abc", "v1.0.0", ""])
def test_manifest_validate_rejects_bad_versions(bad_version):
    errors = PluginManifest.from_dict(_raw(version=bad_version)).validate()
    assert any("SemVer" in e for e in errors), f"{bad_version!r} 应当被拒绝"


def test_manifest_validate_accepts_build_metadata_version():
    """``1.2.3+build.5`` 是合法 SemVer，schema 的 pattern 也明确允许 ``\\+[\\w.]+``。

    D-118：`validate()` 只按 ``-`` 切分就判断核心段，于是把构建元数据判成
    "第三个数字不是数字"，与**同一个文件里的 schema** 自相矛盾。
    """
    assert PluginManifest.from_dict(_raw(version="1.2.3+build.5")).validate() == []
    # 带 pre-release 的写法一直是好的，作为对照
    assert PluginManifest.from_dict(_raw(version="1.2.3-beta.1+build.5")).validate() == []


def test_manifest_validate_checks_fmcl_version_range_and_entry():
    assert any("min_fmcl_version" in e for e in PluginManifest.from_dict(_raw(min_fmcl_version="1.0")).validate())
    assert any("max_fmcl_version" in e for e in PluginManifest.from_dict(_raw(max_fmcl_version="x")).validate())
    assert PluginManifest.from_dict(_raw(max_fmcl_version=None)).validate() == []
    assert any("入口模块" in e for e in PluginManifest.from_dict(_raw(entry="1bad")).validate())
    assert any("入口模块" in e for e in PluginManifest.from_dict(_raw(entry="")).validate())


def test_manifest_from_file_reads_json_and_keeps_install_path(tmp_path):
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(_raw(version="1.0")), encoding="utf-8")
    m = PluginManifest.from_file(path, install_path=tmp_path)
    assert m.id == "com.example.demo"
    assert m.version == "1.0"  # 非法版本不让 from_file 抛异常，只记 warning
    assert m.install_path == tmp_path


def test_manifest_from_file_lets_bad_json_raise(tmp_path):
    """坏 JSON 由调用方兜底（`PluginLoader.scan_installed` 吞掉并跳过该插件）。"""
    path = tmp_path / "plugin.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        PluginManifest.from_file(path)


def test_manifest_roundtrip_to_dict_keeps_all_persisted_fields(tmp_path):
    m = PluginManifest.from_dict(
        _raw(
            description={"zh_CN": "演示"},
            permissions=["network.http"],
            dependencies={"com.other": ">=1.0"},
            conflicts={"com.bad": "<2.0"},
            tags=["ui"],
            entry="main",
        ),
        install_path=tmp_path,
    )
    again = PluginManifest.from_dict(m.to_dict())
    assert again.to_dict() == m.to_dict()
    assert again == m
    assert "install_path" not in m.to_dict()  # 运行时字段不进持久化格式
    assert again.install_path is None


def test_manifest_equality_and_hash_are_id_only():
    """相等性只看 id —— 同一插件的两个版本互相 ``==``（记录现状）。"""
    a = PluginManifest.from_dict(_raw(version="1.0.0"))
    b = PluginManifest.from_dict(_raw(version="2.0.0", name="别的名字"))
    assert a == b and hash(a) == hash(b)
    assert a != "com.example.demo"
    assert len({a, b}) == 1


def test_manifest_get_description_prefers_language_then_falls_back():
    m = PluginManifest.from_dict(_raw(description={"en_US": "Hello", "ja_JP": "こんにちは"}))
    assert m.get_description("en_US") == "Hello"
    assert m.get_description("ja_JP") == "こんにちは"
    assert m.get_description("fr_FR") == "Hello"  # 未命中 → 降级 en_US
    assert PluginManifest.from_dict(_raw(description={"zh_CN": "你好"})).get_description("fr_FR") == "你好"
    assert PluginManifest.from_dict(_raw(description={})).get_description() == ""


def test_manifest_get_description_tolerates_plain_string():
    """作者把 ``description`` 写成纯字符串（不是 ``{lang: text}``）时不能炸。

    D-119：schema 的 ``properties`` 里**根本没有** ``description`` 这一项
    （`_OPTIONAL_DEFAULTS` 有），作者没有任何提示必须写成字典；一旦写成字符串，
    ``get_description()`` 走到 ``self.description.values()`` 直接 AttributeError ——
    而它是在界面线程渲染插件列表/详情时调用的。
    """
    m = PluginManifest.from_dict(_raw(description="一个纯字符串描述"))
    assert m.get_description() == "一个纯字符串描述"
    assert m.get_description("en_US") == "一个纯字符串描述"


def test_manifest_schema_required_matches_documented_required_fields():
    assert sorted(PLUGIN_MANIFEST_SCHEMA["required"]) == [
        "author",
        "id",
        "min_fmcl_version",
        "name",
        "version",
    ]


# ═══════════════════════════════════════════════════════════════════
# dependency.py —— SemVer 比较
# ═══════════════════════════════════════════════════════════════════


def test_parse_version_splits_core_and_prerelease():
    assert _parse_version("1.2.3") == (1, 2, 3, ())
    assert _parse_version("1.2.3-beta.1") == (1, 2, 3, ("beta", "1"))
    assert _parse_version("1.2") == (1, 2, 0, ())
    assert _parse_version("1") == (1, 0, 0, ())


@pytest.mark.parametrize(
    "left, right, expected",
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0.1", "1.0.0", 1),
        ("1.0.0", "1.1.0", -1),
        ("2.0.0", "1.9.9", 1),
        ("1.0.0", "1.0.0-beta", 1),  # 正式版 > 预发布版
        ("1.0.0-beta", "1.0.0", -1),
        ("1.0.0-alpha", "1.0.0-beta", -1),
        ("1.0.0-beta.2", "1.0.0-beta.10", -1),  # 数字段按数值比，不是字典序
        ("1.0.0-beta.1", "1.0.0-beta.1.1", -1),  # 段数少的小
    ],
)
def test_compare_versions_orders_semver(left, right, expected):
    """含 D-130 的两条：``1.0.0-beta.2`` < ``1.0.0-beta.10``（数字段按数值比）
    与 ``1.0.0-beta.1`` < ``1.0.0-beta.1.1``（SemVer §11.4.4：字段少的小）。

    原来"字段数不同"是按"缺失段 = 空串"处理的，而空串不是数字 →
    走到"数字优先"那条分支 → 结论正好反过来（短的反而大），
    于是市场会把**更旧的预发布版**当成"可更新"推给用户。
    """
    assert _compare_semver(left, right) == expected
    assert _compare_semver(right, left) == -expected
    assert DependencyResolver.compare_versions(left, right) == expected


def test_compare_versions_ignores_build_metadata():
    """SemVer 规范 §10：构建元数据**不参与**优先级比较。

    D-117：`_parse_version` 只在 ``-`` 后面剥 ``+``，于是 ``1.2.3+build.5``
    的 ``+build.5`` 留在核心段里，``int("3+build")`` 直接 ValueError。
    受影响的是每次「检查更新」都会走的 `PluginMarket.check_updates`、
    每次加载都会走的 `PluginManager._check_fmcl_version`，
    以及 `check_version_compatibility`；而清单 schema 的 version pattern
    明确允许 ``+build``，所以这不是理论问题。
    """
    assert _parse_version("1.2.3+build.5") == (1, 2, 3, ())
    assert _compare_semver("1.2.3+build.5", "1.2.3") == 0
    assert _compare_semver("1.2.3+build.5", "1.2.4") == -1
    assert _compare_semver("1.2.3-beta.1+build.5", "1.2.3-beta.1") == 0


# ═══════════════════════════════════════════════════════════════════
# dependency.py —— 约束解析与检查
# ═══════════════════════════════════════════════════════════════════


def test_parse_constraint_handles_operators_and_bare_version():
    cs = _parse_constraint(">=1.0.0,<2.0.0")
    assert [(c.op, c.version) for c in cs] == [(">=", "1.0.0"), ("<", "2.0.0")]
    assert [(c.op, c.version) for c in _parse_constraint("==1.5.0")] == [("==", "1.5.0")]
    assert [(c.op, c.version) for c in _parse_constraint("!=1.5.0")] == [("!=", "1.5.0")]
    assert [(c.op, c.version) for c in _parse_constraint(" 1.5.0 ")] == [("==", "1.5.0")]
    assert _parse_constraint("") == []
    assert _parse_constraint("   ") == []


def test_check_constraint_combines_multiple_constraints():
    cs = _parse_constraint(">=1.0.0,<2.0.0")
    assert _check_constraint(cs, "1.5.0") is True
    assert _check_constraint(cs, "0.9.9") is False
    assert _check_constraint(cs, "2.0.0") is False
    assert _check_constraint(_parse_constraint("!=1.5.0"), "1.5.0") is False


def test_parse_dependencies_keeps_empty_constraint_as_any_version():
    """``{"com.a": ""}`` 的意思是"任意版本"，不是"没有这个依赖"。

    D-120：`parse_dependencies` 原来会把约束列表为空的条目**整条丢掉**，
    于是 `check_version_compatibility` 不再报"缺少依赖"、`check_conflicts`
    不再报"与已装插件冲突" —— 两个漏报都是静默的。
    """
    deps = parse_dependencies({"com.a": "", "com.b": ">=1.0"})
    assert [d.plugin_id for d in deps] == ["com.a", "com.b"]
    assert deps[0].constraints == []
    assert deps[1].constraints != []
    assert _check_constraint([], "0.0.1") is True  # 空约束 = 任何版本都满足


def test_check_version_compatibility_reports_missing_and_unsatisfied():
    r = DependencyResolver()
    assert r.check_version_compatibility({"com.a": ">=1.0,<2.0"}, {"com.a": "1.5.0"}) == (True, [])

    ok, errors = r.check_version_compatibility({"com.a": ">=2.0"}, {"com.a": "1.5.0"})
    assert ok is False and len(errors) == 1 and "不满足约束" in errors[0]

    assert r.check_version_compatibility({"com.a": ">=1.0"}, {}) == (False, ["缺少依赖: com.a"])
    # 空约束：仍然要求"依赖存在"
    assert r.check_version_compatibility({"com.a": ""}, {}) == (False, ["缺少依赖: com.a"])
    assert r.check_version_compatibility({"com.a": ""}, {"com.a": "9.9.9"}) == (True, [])


def test_check_conflicts_detects_installed_conflicts():
    r = DependencyResolver()
    ok, errors = r.check_conflicts({"com.bad": "<2.0"}, {"com.bad": "1.5.0"})
    assert ok is False and "冲突" in errors[0]
    assert r.check_conflicts({"com.bad": "<2.0"}, {}) == (True, [])  # 没装就不算冲突
    assert r.check_conflicts({"com.bad": "<2.0"}, {"com.bad": "2.5.0"}) == (True, [])
    ok, errors = r.check_conflicts({"com.bad": ""}, {"com.bad": "3.0.0"})  # 空约束 = 任何版本都冲突
    assert ok is False and "冲突" in errors[0]


# ═══════════════════════════════════════════════════════════════════
# dependency.py —— 拓扑排序与循环检测
# ═══════════════════════════════════════════════════════════════════


def test_resolve_load_order_puts_dependencies_first():
    order, errors = DependencyResolver().resolve_load_order(
        {
            "com.app": ("1.0.0", {"com.lib": ">=1.0"}),
            "com.lib": ("1.2.0", {}),
            "com.extra": ("0.1.0", {"com.lib": ">=1.0"}),
        }
    )
    assert errors == []
    assert order.index("com.lib") < order.index("com.app")
    assert order.index("com.lib") < order.index("com.extra")


def test_resolve_load_order_is_deterministic_and_skips_external_deps():
    r = DependencyResolver()
    order, errors = r.resolve_load_order({"com.z": ("1.0.0", {}), "com.a": ("1.0.0", {}), "com.m": ("1.0.0", {})})
    assert (order, errors) == (["com.a", "com.m", "com.z"], [])
    # 依赖不在本次集合里（外部模块）→ 跳过，不算错
    assert r.resolve_load_order({"com.a": ("1.0.0", {"com.thirdparty": ">=1.0"})}) == (["com.a"], [])


def test_resolve_load_order_reports_cycles_and_drops_them_from_order():
    r = DependencyResolver()
    order, errors = r.resolve_load_order(
        {"com.a": ("1.0.0", {"com.b": ">=1.0"}), "com.b": ("1.0.0", {"com.a": ">=1.0"})}
    )
    assert order == []
    assert len(errors) == 1 and "循环依赖" in errors[0]
    # 自依赖同样算环
    order, errors = r.resolve_load_order({"com.self": ("1.0.0", {"com.self": ">=1.0"})})
    assert order == [] and "循环依赖" in errors[0]


def test_plugin_dependency_dataclass_shape():
    """替身形状核对：`PluginDependency` 就是 (plugin_id, constraints)。"""
    dep = PluginDependency(plugin_id="com.a", constraints=_parse_constraint(">=1.0"))
    assert dep.plugin_id == "com.a" and len(dep.constraints) == 1


# ═══════════════════════════════════════════════════════════════════
# permissions.py —— 权限分级、授权状态与持久化
# ═══════════════════════════════════════════════════════════════════


def test_every_permission_has_a_risk_level_and_a_display_key():
    """每个权限都必须有风险等级 + i18n 键，否则权限弹窗会显示裸键名。"""
    for perm in get_all_permissions():
        assert perm in _PERMISSION_RISK_MAP, f"{perm.value} 没有风险等级"
        assert isinstance(get_permission_risk(perm), PermissionRiskLevel)
        assert perm in _PERMISSION_DISPLAY_KEYS, f"{perm.value} 没有显示键"
        assert get_permission_display_key(perm).startswith("plugin_permission_")


def test_permission_display_keys_exist_in_all_locales():
    """权限显示键是**数据驱动**的（代码里没有字面量），`scripts/check_i18n.py`
    扫不到它们 —— 这条补上那段覆盖：加权限却忘了加文案，弹窗会显示键名。
    """
    locales_dir = pathlib.Path(__file__).resolve().parent.parent / "ui" / "locales"
    files = sorted(locales_dir.glob("*.json"))
    assert files, "没找到语言文件"
    for path in files:
        keys = set(json.loads(path.read_text(encoding="utf-8")))
        missing = sorted(k for k in _PERMISSION_DISPLAY_KEYS.values() if k not in keys)
        assert not missing, f"{path.name} 缺权限显示键: {missing}"


def test_classify_permissions_buckets_by_risk_and_ignores_unknown():
    buckets = classify_permissions(["network.http", "filesystem.write", "core.process", "不存在的权限"])
    assert buckets[PermissionRiskLevel.LOW] == [PluginPermission.NETWORK_HTTP]
    assert buckets[PermissionRiskLevel.MEDIUM] == [PluginPermission.FILESYSTEM_WRITE]
    assert buckets[PermissionRiskLevel.HIGH] == [PluginPermission.CORE_PROCESS]
    assert sum(len(v) for v in buckets.values()) == 3  # 未知权限被静默丢弃（记录现状）


def test_permission_state_defaults_grant_revoke_and_status():
    st = PluginPermissionState(plugin_id="com.demo")
    assert len(st.grants) == len(get_all_permissions())
    assert st.get_ungranted_permissions() and not st.is_granted(PluginPermission.NETWORK_HTTP)
    assert st.check_or_request(PluginPermission.NETWORK_HTTP) == "need_confirm"

    st.grant(PluginPermission.NETWORK_HTTP)
    assert st.is_granted(PluginPermission.NETWORK_HTTP)
    assert st.check_or_request(PluginPermission.NETWORK_HTTP) == "granted"

    st.grant(PluginPermission.CORE_PROCESS, always=True)
    assert st.grants[PluginPermission.CORE_PROCESS].always_allowed is True
    assert st.grants[PluginPermission.CORE_PROCESS].risk_level == PermissionRiskLevel.HIGH

    st.revoke(PluginPermission.CORE_PROCESS)
    assert st.is_granted(PluginPermission.CORE_PROCESS) is False
    assert st.grants[PluginPermission.CORE_PROCESS].always_allowed is False
    # `PluginPermission` 是 str 混入枚举，`__hash__` 来自 str → 字符串键同样命中成员
    assert st.check_or_request("network.http") == "granted"
    assert st.check_or_request("不存在的权限") == "denied"


def test_permission_state_serialization_roundtrip_and_dirty_input():
    """持久化格式往返 + 脏数据容错。

    D-129：`from_dict` 原来对每个值直接 ``val.get(...)``，权限文件里只要有一个
    值不是字典（被手改过、写到一半），这里就抛 AttributeError；而调用方
    `PluginManager._load_perm_state` 整段 try/except 一吞 —— 这个插件的**全部**
    授权状态都恢复不了（静默回到"全未授权"，用户以为授权还在）。
    """
    st = PluginPermissionState(plugin_id="com.demo")
    st.grant(PluginPermission.NETWORK_HTTP)
    st.grant(PluginPermission.CORE_LAUNCH_HOOK, always=True)
    data = st.to_dict()
    assert data["network.http"] == {"granted": True, "always_allowed": False}
    assert data["core.launch_hook"]["always_allowed"] is True

    back = PluginPermissionState.from_dict("com.demo", data)
    assert back.is_granted(PluginPermission.NETWORK_HTTP)
    assert back.grants[PluginPermission.CORE_LAUNCH_HOOK].always_allowed is True

    # 脏数据：未知键忽略、缺字段按未授权、值不是 dict 也不炸
    dirty = PluginPermissionState.from_dict(
        "com.demo", {"不认识": {"granted": True}, "network.http": {}, "core.process": "yes"}
    )
    assert dirty.is_granted(PluginPermission.NETWORK_HTTP) is False
    assert dirty.is_granted(PluginPermission.CORE_PROCESS) is False


def test_permission_state_preserves_explicitly_given_grants_dict():
    only = {PluginPermission.NETWORK_HTTP: PermissionGrant(PluginPermission.NETWORK_HTTP, PermissionRiskLevel.LOW)}
    st = PluginPermissionState(plugin_id="com.demo", grants=only)
    assert len(st.grants) == 1
    assert st.check_or_request(PluginPermission.CORE_PROCESS) == "denied"


# ═══════════════════════════════════════════════════════════════════
# hook_bus.py —— 注册 / 触发 / 顺序 / 异常隔离
# ═══════════════════════════════════════════════════════════════════


def test_hook_bus_register_dedupes_same_callback_and_unregisters():
    bus = HookBus()
    cb = _Recorder("cb")
    assert bus.register(HookPoint.APP_STARTUP, cb, plugin_id="com.a") is True
    assert bus.register(HookPoint.APP_STARTUP, cb, plugin_id="com.a") is False  # 同插件同回调 → 忽略
    assert bus.register(HookPoint.APP_STARTUP, _Recorder("cb2"), plugin_id="com.a") is True
    assert bus.get_listener_count("com.a") == 2

    assert bus.unregister(HookPoint.APP_STARTUP, "com.a") == 2
    assert bus.unregister(HookPoint.APP_STARTUP, "com.a") == 0
    assert bus.has_listeners(HookPoint.APP_STARTUP) is False


def test_hook_bus_unregister_all_spans_every_hook_point():
    bus = HookBus()
    bus.register(HookPoint.APP_STARTUP, _Recorder("a"), plugin_id="com.a")
    bus.register(HookPoint.GAME_CRASHED, _Recorder("b"), plugin_id="com.a")
    bus.register(HookPoint.APP_STARTUP, _Recorder("c"), plugin_id="com.b")
    assert bus.get_listener_count() == 3
    assert bus.unregister_all("com.a") == 2
    assert bus.get_listener_count() == 1 and bus.get_listener_count("com.b") == 1


def test_hook_bus_emit_all_runs_in_priority_order_and_passes_kwargs():
    bus = HookBus()
    order: list[str] = []
    bus.register(HookPoint.APP_STARTUP, lambda **kw: order.append("晚"), plugin_id="p2", priority=200)
    bus.register(HookPoint.APP_STARTUP, lambda **kw: order.append("早"), plugin_id="p1", priority=10)
    assert bus.emit(HookPoint.APP_STARTUP) is None  # ALL 策略返回 None
    assert order == ["早", "晚"]

    seen: list[dict] = []
    bus2 = HookBus()
    bus2.register(HookPoint.APP_STARTUP, lambda **kw: seen.append(kw), plugin_id="p")
    bus2.emit(HookPoint.APP_STARTUP, version_id="1.20.1", flag=True)
    assert seen == [{"version_id": "1.20.1", "flag": True}]


def test_hook_bus_same_priority_keeps_registration_order():
    bus = HookBus()
    order: list[str] = []
    for name in ("甲", "乙", "丙"):
        bus.register(HookPoint.APP_STARTUP, (lambda n: (lambda **kw: order.append(n)))(name), plugin_id=name)
    bus.emit(HookPoint.APP_STARTUP)
    assert order == ["甲", "乙", "丙"]


def test_hook_bus_collect_returns_pairs_and_skips_none():
    bus = HookBus()  # GAME_PRE_LAUNCH 的默认策略是 COLLECT
    bus.register(HookPoint.GAME_PRE_LAUNCH, _Recorder("a", result=["--x"]), plugin_id="com.a", priority=1)
    bus.register(HookPoint.GAME_PRE_LAUNCH, _Recorder("b", result=None), plugin_id="com.b")
    bus.register(HookPoint.GAME_PRE_LAUNCH, _Recorder("c", result=["--y"]), plugin_id="com.c")
    assert bus.emit(HookPoint.GAME_PRE_LAUNCH) == [("com.a", ["--x"]), ("com.c", ["--y"])]


def test_hook_bus_first_returns_first_non_none():
    bus = HookBus()  # VERSION_PRE_INSTALL 的默认策略是 FIRST
    bus.register(HookPoint.VERSION_PRE_INSTALL, _Recorder("a", result=None), plugin_id="com.a", priority=1)
    bus.register(HookPoint.VERSION_PRE_INSTALL, _Recorder("b", result="阻止"), plugin_id="com.b", priority=2)
    bus.register(HookPoint.VERSION_PRE_INSTALL, _Recorder("c", result="不该到这里"), plugin_id="com.c", priority=3)
    assert bus.emit(HookPoint.VERSION_PRE_INSTALL, version_id="1.20.1") == "阻止"


def test_hook_bus_empty_hook_point_returns_strategy_neutral_value():
    bus = HookBus()
    assert bus.emit(HookPoint.APP_STARTUP) is None
    assert bus.emit(HookPoint.GAME_PRE_LAUNCH) == []
    assert bus.emit(HookPoint.VERSION_PRE_INSTALL) is None
    assert bus.emit(HookPoint.APP_STARTUP, 任意="参数") is None


def test_hook_bus_exception_in_one_handler_does_not_stop_others():
    """崩溃隔离：一个插件抛异常不能影响别的插件，也不能冒到调用方。"""
    bus = HookBus()
    bad = _Recorder("bad", boom=RuntimeError("插件炸了"))
    good = _Recorder("good", result="ok")
    for hook in (HookPoint.APP_STARTUP, HookPoint.GAME_PRE_LAUNCH, HookPoint.VERSION_PRE_INSTALL):
        bus.register(hook, bad, plugin_id="com.bad", priority=1)
        bus.register(hook, good, plugin_id="com.good", priority=2)

    assert bus.emit(HookPoint.APP_STARTUP) is None
    assert good.calls, "坏插件抛异常后，后面的插件仍然要被调用"
    assert bus.emit(HookPoint.GAME_PRE_LAUNCH) == [("com.good", "ok")]
    assert bus.emit(HookPoint.VERSION_PRE_INSTALL) == "ok"


def test_hook_bus_short_circuit_strategy(monkeypatch):
    """SHORT_CIRCUIT 是枚举里有、默认表里没人用的策略 —— 直接测执行器 + 换表。"""
    bus = HookBus()
    handlers = [
        HookHandler(1, "com.a", _Recorder("a", result=False), HookPoint.APP_STARTUP),
        HookHandler(2, "com.b", _Recorder("b", result=True), HookPoint.APP_STARTUP),
        HookHandler(3, "com.c", _Recorder("c", result=True), HookPoint.APP_STARTUP),
    ]
    assert bus._execute_short_circuit(handlers, {}) is True
    assert handlers[2].callback.calls == [], "短路之后不应继续调用"

    monkeypatch.setitem(_HOOK_DEFAULT_STRATEGY, HookPoint.APP_STARTUP, HookStrategy.SHORT_CIRCUIT)
    bus2 = HookBus()
    assert bus2.emit(HookPoint.APP_STARTUP) is False  # 没有处理器
    bus2.register(HookPoint.APP_STARTUP, _Recorder("x", result=True), plugin_id="com.x")
    assert bus2.emit(HookPoint.APP_STARTUP) is True


def test_hook_bus_every_hook_point_has_a_default_strategy():
    missing = [hp.value for hp in HookPoint if hp not in _HOOK_DEFAULT_STRATEGY]
    assert not missing, f"这些钩子点没有默认策略（emit 会退化成 ALL）: {missing}"


def test_hook_bus_is_thread_safe_for_register_and_emit():
    import threading

    bus = HookBus()
    errors: list[BaseException] = []

    def worker(tag: int):
        try:
            for i in range(25):
                bus.register(HookPoint.APP_STARTUP, (lambda **kw: None), plugin_id=f"p{tag}-{i}")
                bus.emit(HookPoint.APP_STARTUP, x=i)
        except BaseException as e:  # noqa: BLE001 - 把线程里的异常带回主线程断言
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert bus.get_listener_count() == 8 * 25


# ═══════════════════════════════════════════════════════════════════
# base.py —— 抽象基类、log / notify 的权限闸门
# ═══════════════════════════════════════════════════════════════════


class DemoPlugin(PluginBase):
    """最小可用插件：只实现两个抽象方法。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    def on_enable(self) -> None:
        self.events.append("enable")

    def on_disable(self) -> None:
        self.events.append("disable")


def test_plugin_base_is_abstract_and_optional_hooks_have_defaults():
    with pytest.raises(TypeError):
        PluginBase()  # type: ignore[abstract]
    p = DemoPlugin()
    assert p.on_load() is None
    assert p.on_uninstall() is None
    assert p.get_default_config() == {}
    assert p.get_settings_ui(None) is None
    assert p.get_tab_ui(None) is None
    assert p.get_sidebar_item() is None
    p.on_enable()
    p.on_disable()
    assert p.events == ["enable", "disable"]


def test_plugin_notify_is_blocked_without_permission():
    """没有 ui.notification 权限时，`notify()` 只能写日志，不能弹到用户脸上。"""

    class _Manager:
        def __init__(self) -> None:
            self.notified: list[tuple] = []

        def _notify_user(self, plugin_id, title, message, level="info"):
            self.notified.append((plugin_id, title, message, level))

    p = DemoPlugin()
    p.manifest = PluginManifest.from_dict(_raw())
    p._manager = _Manager()
    p._perm_state = PluginPermissionState(plugin_id=p.manifest.id)

    assert p.notify("标题", "内容") is None
    assert p._manager.notified == []

    p._perm_state.grant(PluginPermission.UI_NOTIFICATION)
    p.notify("标题", "内容", "warning")
    assert p._manager.notified == [(p.manifest.id, "标题", "内容", "warning")]


def test_plugin_log_uses_manifest_id_and_never_raises():
    p = DemoPlugin()
    p.manifest = PluginManifest.from_dict(_raw())
    p.log("一条消息")
    p.log("一条消息", "warning")
    p.log("未知级别", "不存在的级别")  # getattr 回退到 info，不应抛异常
