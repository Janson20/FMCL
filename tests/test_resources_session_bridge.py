"""`services/resource_session.py` 与 `app/bridges/resources_bridge.py` 的回归守卫（阶段 3 任务 3.6）。

两层的分工是这一轮的核心设计，所以测试也按它分：

* **会话**（`ResourceSession`）负责"我的资源页要记住什么"：当前版本 / 标签页 / 搜索词 /
  页码 / 扫描缓存（按目录 mtime）/ 模组元数据 / 更新结果。它是纯 Python 对象，
  本文件直接单测，不碰 Qt。
* **桥**（`ResourcesBridge`）只负责把会话念给 QML、把 QML 的动作转成会话调用、
  把后台任务的回调变成信号。跨线程那部分（worker → `posted` → `_deliver`）用
  `processEvents()` 驱动，**不 sleep 猜时序**。

替身策略：真服务（`ResourceService`）不碰网络与磁盘的部分照用，**目录扫描用真文件系统**
（`tmp_path` 里造几个 jar/zip），**双源更新检查与批量更新换成假函数**（它们要联网）。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.bridges.resources_bridge import TASK_META, ResourcesBridge  # noqa: E402
from app.tasks import TaskRunner  # noqa: E402
from services.resource_service import InstallResult, ResourceService, allowed_types  # noqa: E402
from services.resource_session import (  # noqa: E402
    STATE_EMPTY,
    STATE_ERROR,
    STATE_LOADING,
    STATE_READY,
    TAB_KEYS,
    ResourceSession,
    installed_version_ids,
    resolve_version_id,
    resource_label_key,
)

_APP = QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture(autouse=True, scope="module")
def _chinese_locale() -> Any:
    """整个模块用中文语言文件：否则 `_()` 返回键名，"翻好了没有"就没法断言。"""
    from services import i18n_service

    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang


def pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """驱动主线程事件循环直到条件成立（跨线程投递没有它就永远落不了地）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not condition():
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


# ═══════════════════════════════════════════════════════════════════
# 纯函数
# ═══════════════════════════════════════════════════════════════════


class TestResolveVersion:
    """`resolve_version_id` —— "这一屏看哪个版本"的唯一判定点。"""

    def test_route_version_wins_when_installed(self) -> None:
        assert resolve_version_id("b", ["a", "b", "c"], "c") == "b"

    def test_falls_back_to_last_launched(self) -> None:
        assert resolve_version_id("", ["a", "b", "c"], "c") == "c"

    def test_falls_back_to_first_installed(self) -> None:
        assert resolve_version_id("", ["a", "b"], "") == "a"

    def test_unknown_route_version_falls_back_instead_of_using_it(self) -> None:
        """路由给的版本已经被删了：退回一个**真实存在**的版本，而不是拼一个空目录。"""
        assert resolve_version_id("gone", ["a", "b"], "b") == "b"
        assert resolve_version_id("gone", ["a", "b"], "") == "a"

    def test_no_versions_at_all_returns_empty(self) -> None:
        """一个版本都没装 → 空串（页面据此走"没有版本"的空态）。

        刻意**不**把请求值留住：那会让页头显示一个不存在的版本号、`hasVersion`
        也变成假绿（阶段 3.6 单测抓到过）。
        """
        assert resolve_version_id("x", [], "") == ""
        assert resolve_version_id("", [], "zz") == ""
        assert resolve_version_id("", [], "") == ""


class TestLabelKeys:
    def test_known_tabs_have_keys(self) -> None:
        for key in TAB_KEYS:
            assert resource_label_key(key) != key

    def test_unknown_tab_returns_itself(self) -> None:
        """旧实现是 `labels.get(rtype, rtype)` —— 未知类型原样返回（不抛）。"""
        assert resource_label_key("datapacks") == "datapacks"


class TestInstalledVersionIds:
    def test_prefers_launcher(self) -> None:
        launcher = SimpleNamespace(get_installed_version_ids=lambda: ["b", "a"])
        assert installed_version_ids(launcher, None) == ["a", "b"]

    def test_falls_back_to_versions_dir(self, tmp_path: Path) -> None:
        versions = tmp_path / "versions"
        (versions / "1.21").mkdir(parents=True)
        (versions / "1.20.4").mkdir()
        (versions / "not-a-dir.txt").write_text("x", encoding="utf-8")
        config = SimpleNamespace(get_versions_dir=lambda: versions)
        assert installed_version_ids(None, config) == ["1.20.4", "1.21"]

    def test_broken_launcher_falls_back_and_broken_config_returns_empty(self, tmp_path: Path) -> None:
        class Boom:
            def get_installed_version_ids(self) -> List[str]:
                raise RuntimeError("核心炸了")

        assert installed_version_ids(Boom(), SimpleNamespace(get_versions_dir=lambda: tmp_path / "nope")) == []


class TestAllowedTypes:
    """`resource_service.allowed_types` —— 页签可见性的**唯一**判定点（用户 2026-10-07 裁决）。"""

    def test_vanilla_version_only_gets_resourcepacks_and_saves(self, tmp_path: Path) -> None:
        mc = vanilla_mc(tmp_path)
        assert allowed_types("1.20.4", mc) == ["resourcepacks", "saves"]

    def test_loader_version_gets_all_four(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        assert allowed_types("1.20.4-forge", mc) == list(TAB_KEYS)

    def test_global_directory_gets_all_four(self, tmp_path: Path) -> None:
        """空版本 = 全局 `.minecraft`：那里本来就可能有旧版本留下的 `mods`。"""
        mc = vanilla_mc(tmp_path)
        assert allowed_types("", mc) == list(TAB_KEYS)

    def test_undecidable_version_degrades_to_the_vanilla_set(self, tmp_path: Path) -> None:
        """判不出来时按**原版**那一档给：保守，但绝不会出现"一个页签都没有"的空屏。"""
        mc = tmp_path / ".minecraft"
        assert allowed_types("no-such-version", mc) == ["resourcepacks", "saves"]


# ═══════════════════════════════════════════════════════════════════
# 会话
# ═══════════════════════════════════════════════════════════════════


def write_version_json(mc: Path, version: str, *, loader: str = "") -> Path:
    """造一个最小的版本 JSON（`has_mod_loader_from_json` 的输入）。

    ⚠ **必须造它**，否则会踩到 `version_utils.has_mod_loader_from_json` 的兜底分支：
    版本目录存在但**一个 JSON 都没有**时，它退回"按版本 ID 里的加载器关键词猜测"，
    于是 `1.20.4-forge` 会被判成"装了加载器"→ 资源目录变成**版本隔离目录**
    （`versions/<id>/mods`）。那本身是正确行为，只是本文件的用例想测"全局目录"的话，
    得先让这个判定有确定的答案。

    `loader=""` 时 JSON 里没有任何加载器字段 → `has_loader=False` → 全局目录。
    """
    version_dir = mc / "versions" / version
    version_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": version, "type": "release"}
    if loader:
        payload["libraries"] = [
            {"name": f"net.minecraftforge:forge:{version}", "url": "https://example.invalid/"}
        ]
    path = version_dir / f"{version}.json"
    import json as _json

    path.write_text(_json.dumps(payload), encoding="utf-8")
    return path


def make_mc(tmp_path: Path, *, version: str = "1.20.4-forge", loader: str = "forge") -> Path:
    """造一个最小的 `.minecraft`：四个资源目录 + 一个版本 JSON（**默认带 forge**）。

    两处踩过的坑，写在这里免得再犯：

    * `loader=""` 并不等于"原版"：版本目录里**一个 JSON 都没有**时，
      `version_utils.has_mod_loader_from_json` 会**退化成按版本 ID 猜** ——
      `1.20.4-forge` 照样被判成"装了加载器"。要测原版得用一个名字里没有加载器关键词的
      版本（见 `vanilla_mc`）。
    * 阶段 3.6 用户裁决之后**原版版本只给「资源包 / 地图」两个页签**
      （`resource_service.allowed_types`），所以模组相关的用例必须用带加载器的版本，
      否则页签会被过滤掉、看起来像坏了。
    """
    mc = tmp_path / ".minecraft"
    for folder in ("mods", "resourcepacks", "saves", "shaderpacks"):
        (mc / folder).mkdir(parents=True)
    write_version_json(mc, version, loader=loader)
    return mc


def vanilla_mc(tmp_path: Path) -> Path:
    """造一个**原版**版本的 `.minecraft`（版本 ID 里没有加载器关键词）。

    ⚠ 注意资源目录的位置：原版版本走**全局** `.minecraft/<folder>`，而带加载器的版本走
    `versions/<id>/<folder>`（版本隔离）。用哪个 fixture 决定 `res_base(...)` 的参数。
    """
    mc = tmp_path / ".minecraft"
    for folder in ("mods", "resourcepacks", "saves", "shaderpacks"):
        (mc / folder).mkdir(parents=True)
    write_version_json(mc, "1.20.4", loader="")
    return mc


def touch(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def res_base(mc: Path, version: str, *, isolated: bool = False) -> Path:
    """资源目录的根：隔离版本在 `versions/<id>/`，否则就是 `.minecraft/`。

    与 `resource_service.resource_dir` 的两条分支一一对应（见 `write_version_json`
    的说明：是否隔离由版本 JSON 里有没有加载器决定）。
    """
    return (mc / "versions" / version) if isolated else mc


def empty_resource_dir(
    mc: Path, folder: str, *, version: str = "1.20.4-forge", isolated: bool = True
) -> Path:
    """建出（但**不放文件**）一个资源目录 —— 测"目录存在但里面是空的"那条空态。

    为什么需要它：隔离版本的资源目录**不会**被 `make_mc` 建出来（它只建全局那四个），
    所以"空目录"用例如果只 `make_mc` 就会命中"目录不存在"（`rm_folder_not_exist`），
    文案与状态键都不是用例想钉的那条。
    """
    path = res_base(mc, version, isolated=isolated) / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


class TestSessionScan:
    def test_empty_directory_is_the_empty_state_with_the_right_key(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        #: ⚠ 目录要**存在**（隔离版本的资源目录不是 `make_mc` 建的那四个全局目录）：
        #: 不存在时命中的是 `rm_folder_not_exist`，不是本用例要钉的 `rm_folder_empty`。
        empty_resource_dir(mc, "resourcepacks")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab("resourcepacks")
        result = session.refresh()
        assert result["state"] == STATE_EMPTY
        assert result["status_key"] == "rm_folder_empty"
        assert result["status_params"]["label_key"] == "resource_packs"

    def test_missing_directory_is_created_and_reads_as_empty(self, tmp_path: Path) -> None:
        """目录不存在 → **建出来**，然后按"空目录"报（用户 2026-10-07 裁决）。

        旧实现只在「打开文件夹」与「安装文件」时 `mkdir`，单纯打开资源管理器不建，
        于是第一次进一个还没有 `resourcepacks` 的版本会看到「文件夹不存在: <路径>」。
        现在建完继续按空目录走，所以文案是「{label} 文件夹为空」，而 `rm_folder_not_exist`
        在新界面上不会再出现。
        """
        mc = tmp_path / ".minecraft"  # 一个资源目录都不建
        #: 只造版本 JSON（带加载器）：这样**至少**能确定模组页签是合法的，
        #: 用例要测的是"目录不存在"那条分支，不该被页签过滤搅进来。
        write_version_json(mc, "1.20.4-forge", loader="forge")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        result = session.refresh()
        assert result["state"] == STATE_EMPTY
        assert result["status_key"] == "mod_folder_empty"
        #: 目录真的被建出来了（不是只在内存里当成空）
        assert (mc / "versions" / "1.20.4-forge" / "mods").is_dir()

        #: 非模组页签同样：`resourcepacks` 也会被建出来
        session.set_tab("resourcepacks")
        result = session.refresh()
        assert result["status_key"] == "rm_folder_empty"
        assert result["status_params"]["label_key"] == "resource_packs"
        assert (mc / "versions" / "1.20.4-forge" / "resourcepacks").is_dir()

    def test_relative_minecraft_callback_still_creates_in_the_right_place(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """基准目录只有一份：相对回调 + 绝对 config 也必须建到同一个 ``.minecraft``。

        2026-10-07 第三次验收反馈的钉子（现象："自动新建文件夹没做"）。根因不在
        `_ensure_resource_dir`，而在**基准目录被解析了两次**：

        * 核心未就绪时 `get_minecraft_dir()` 给的是**相对**的 ``.minecraft``；
        * `ResourceSession.minecraft_dir` 走 ``config.minecraft_dir`` 那条路是绝对的；
        * 而 `resource_dir()` 以前调 `service.resource_dir_for(callbacks, …)`，那条路会
          **自己再解析一次** → 拿到相对的 → 版本隔离目录被拼成
          ``.minecraft\\versions\\<版本>\\mods``（相对**进程工作目录**）。

        后果有两层，缺一不可：目录被建到**仓库根/安装目录**下的 ``.minecraft``；
        而那个相对路径又恰好存在（同一仓库根），于是 `_ensure_resource_dir` 判断
        "已经有了"、什么都不做 —— 用户真正那个 ``.minecraft`` 里始终没有目录。

        所以本用例必须**真的切一次工作目录**（`monkeypatch.chdir`）+ 用相对回调，
        否则 `mkdtemp` 给的绝对路径会让这条缺陷完全隐身。
        """
        absolute_mc = tmp_path / ".minecraft"
        write_version_json(absolute_mc, "1.20.4-forge", loader="forge")
        #: 进程工作目录换到一个**干净**的地方：任何相对路径都会落到这里，好断言"没落这"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        session = ResourceSession(
            ResourceService(),
            #: config 那条路是绝对的（核心起来了就是它）
            config=SimpleNamespace(minecraft_dir=str(absolute_mc)),
            #: 回调那条路故意给相对的：核心没起来时就是这个形状
            callbacks={"get_minecraft_dir": lambda: ".minecraft"},
        )
        session.set_version("1.20.4-forge")
        assert session.minecraft_dir.is_absolute(), "会话的基准目录必须被归一成绝对路径"

        for folder, key in (("mods", "mod_folder_empty"), ("resourcepacks", "rm_folder_empty")):
            session.set_tab(folder)
            result = session.refresh()
            assert result["status_key"] == key
            #: ① 建在**真正**那个 .minecraft 里
            assert (absolute_mc / "versions" / "1.20.4-forge" / folder).is_dir(), (
                f"{folder} 没建到 {absolute_mc}（基准目录被解析成了相对路径）"
            )
            #: ② 工作目录下**不能**出现影子 .minecraft
            assert not (cwd / ".minecraft").exists(), (
                f"{folder} 建到了进程工作目录下的影子 .minecraft"
            )

    def test_resource_dir_in_matches_module_resource_dir(self, tmp_path: Path) -> None:
        """新入口 `resource_dir_in` 与原 `resource_dir` 的分支必须逐字一致。"""
        from services.resource_service import ResourceService, resource_dir

        mc = make_mc(tmp_path)
        service = ResourceService()
        session = ResourceSession(service, config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        for folder in ("mods", "resourcepacks", "saves", "shaderpacks"):
            want = resource_dir("1.20.4-forge", mc, folder)
            got = session.resource_dir(folder)
            assert got == want, f"{folder}: {got} != {want}"
        #: 原版版本走全局目录那条分支
        write_version_json(mc, "1.20.4")
        session.set_version("1.20.4")
        for folder in ("resourcepacks", "saves"):
            want = resource_dir("1.20.4", mc, folder)
            got = session.resource_dir(folder)
            assert got == want, f"{folder}: {got} != {want}"

    def test_mods_tab_always_rescans_and_asks_for_metadata(self, tmp_path: Path) -> None:
        """模组页签**永不**用扫描缓存（旧 `_refresh_current_list` 一进 mods 就 pop 缓存）。"""
        mc = make_mc(tmp_path)
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "mods" / "a.jar")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        #: 版本必须是**带加载器**那个：原版版本会把「模组」页签过滤掉，`set_tab("mods")`
        #: 之后页签也落不到模组上，`_scan_mods` 根本不会被走到（阶段 3.6 fixture 教训）。
        session.set_version("1.20.4-forge")
        first = session.refresh()
        #: 模组页签的"扫描"只到"目录里有东西"为止：状态是 loading（等元数据），
        #: **列表仍是空的** —— 元数据到了才 `apply_mods_metadata`（旧实现也是先挂
        #: "正在读取模组信息…"那一行，再渲染卡片）
        assert first["state"] == STATE_LOADING
        assert first["items"] == []
        assert session.load_state == STATE_LOADING
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "mods" / "b.jar")
        session.refresh()
        assert len(session.items) == 0
        assert session.load_state == STATE_LOADING
        #: 元数据到手之后条目才出现
        session.apply_mods_metadata([
            {"name": "a.jar", "path": str(session.mods_dir / "a.jar")},
            {"name": "b.jar", "path": str(session.mods_dir / "b.jar")},
        ])
        assert len(session.items) == 2
        assert session.load_state == STATE_READY

    def test_other_tabs_reuse_the_scan_cache_until_the_directory_changes(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        packs = res_base(mc, "1.20.4-forge", isolated=True) / "resourcepacks"
        touch(packs / "a.zip")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        calls: List[str] = []
        real = ResourceService.scan_resources

        def counting(resource_dir: Path, resource_type: str) -> List[Dict[str, Any]]:
            calls.append(str(resource_dir))
            return real(resource_dir, resource_type)

        service = ResourceService()
        service.scan_resources = staticmethod(counting)  # type: ignore[method-assign]
        session = ResourceSession(service, config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab("resourcepacks")

        first = session.refresh()
        assert first["state"] == STATE_READY and first["total"] == 1
        assert len(calls) == 1
        assert calls[0] == str(packs), "扫的不是隔离版本的资源目录"
        #: 第二次：目录 mtime 没变 → 吃缓存，**不再扫**。
        #: 判据用**调用计数**（把 `scan_resources` 换成记账替身）而不是比对返回值 ——
        #: 比对返回值要先把 `_items` 改成哨兵，而那会同时骗过"到底扫没扫"这件事。
        second = session.refresh()
        assert second["total"] == 1
        assert len(calls) == 1, "第二次又扫了一遍（缓存没生效）"
        #: `force=True` 必须绕开缓存（刷新按钮走这条）
        session.refresh(force=True)
        assert len(calls) == 2
        #: 目录 mtime 变了 → 缓存作废（改的是**被扫的那个目录**的时间戳）
        touch(packs / "b.zip")
        os.utime(packs, (time.time() + 5, time.time() + 5))
        third = session.refresh()
        assert third["total"] == 2
        assert len(calls) == 3

    def test_force_bypasses_the_cache(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "resourcepacks" / "a.zip")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab("resourcepacks")
        session.refresh()
        session._items = []
        third = session.refresh(force=True)
        assert third["total"] == 1

    def test_switching_version_clears_every_caching_layer(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "resourcepacks" / "a.zip")
        #: 第二个版本也**如实声明加载器**：不然 1.21 会被判成原版，页签被过滤到
        #: 资源包上 —— 那时"缓存清没清"就测不准了（页签已经被换掉）。
        write_version_json(mc, "1.21", loader="forge")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab("resourcepacks")
        session.refresh()
        assert session.total == 1
        session.set_version("1.21")
        assert session.total == 0
        assert session.search == ""
        assert session.page == 1
        assert session.load_state == STATE_EMPTY

    def test_scan_failure_becomes_the_error_state(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        #: ⚠ 目录**必须存在**：否则会先命中"目录不存在"那条分支（那是空态，不是错误态）。
        #: 用例要测的是"扫描本身抛了"。
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "resourcepacks" / "a.zip")

        class Boom(ResourceService):
            @staticmethod
            def scan_resources(resource_dir: Path, resource_type: str) -> List[Dict[str, Any]]:
                raise OSError("盘符没了")

        session = ResourceSession(Boom(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab("resourcepacks")
        result = session.refresh()
        assert result["state"] == STATE_ERROR
        assert "盘符没了" in session.error
        assert result["status_key"] == "rm_operation_failed"


class TestSessionAllowedTabs:
    """会话层的页签过滤与"换版本后页签仍然合法"（用户 2026-10-07 裁决）。"""

    def test_allowed_tabs_follow_the_loader_and_the_global_scope(self, tmp_path: Path) -> None:
        mc = vanilla_mc(tmp_path)
        write_version_json(mc, "1.20.4-forge", loader="forge")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})

        session.set_version("1.20.4")  # 原版：默认页签（mods）不合法 → 落到第一个合法页签
        assert session.allowed_tabs() == ["resourcepacks", "saves"]
        assert session.tab == "resourcepacks"

        session.set_version("1.20.4-forge")  # 带加载器：四个都给
        assert session.allowed_tabs() == list(TAB_KEYS)

        session.set_tab("mods")
        assert session.tab == "mods"
        session.set_version("1.20.4")  # 再切回原版：当前页签不合法 → 自动落回
        assert session.tab == "resourcepacks", "换版本后停在了这个版本没有的页签上"

        session.set_version("")  # 全局目录：四个都给
        assert session.allowed_tabs() == list(TAB_KEYS)
        assert session.has_version is False

    def test_has_mod_loader_treats_the_global_directory_as_capable(self, tmp_path: Path) -> None:
        mc = vanilla_mc(tmp_path)
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("")
        assert session.has_mod_loader() is True
        session.set_version("1.20.4")
        assert session.has_mod_loader() is False

    def test_resource_dir_of_a_vanilla_version_is_the_global_one(self, tmp_path: Path) -> None:
        """原版版本**没有**版本隔离目录（旧窗口从原版版本进来看到的就是全局那一份）。"""
        mc = vanilla_mc(tmp_path)
        write_version_json(mc, "1.20.4-forge", loader="forge")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})

        session.set_version("1.20.4-forge")
        assert session.resource_dir("mods") == mc / "versions" / "1.20.4-forge" / "mods"
        session.set_version("1.20.4")
        assert session.resource_dir("resourcepacks") == mc / "resourcepacks"
        session.set_version("")
        for folder in TAB_KEYS:
            assert session.resource_dir(folder) == mc / folder


class TestSessionSearchAndPaging:
    def build(self, tmp_path: Path, count: int, tab: str = "mods") -> ResourceSession:
        """造一个"带加载器的版本 + count 个资源"的会话。

        ⚠ 文件必须落在**版本隔离目录**里（`versions/1.20.4-forge/<folder>`）：这个
        fixture 用的是带 forge 的版本，写进全局 `.minecraft/<folder>` 的话页面一个
        条目都看不到（阶段 3.6 修 fixture 时踩到的第一类错）。
        """
        mc = make_mc(tmp_path)
        base = res_base(mc, "1.20.4-forge", isolated=True)
        for i in range(count):
            if tab == "mods":
                touch(base / tab / f"mod-{i:02d}.jar")
            else:
                touch(base / tab / f"pack-{i:02d}.zip")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.set_tab(tab)
        session.refresh()
        return session

    def test_page_size_is_ten_and_pages_add_up(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 23)
        session.apply_mods_metadata([{"name": f"m{i}", "path": f"/m{i}", "modid": ""} for i in range(23)])
        assert session.page_size == 10
        assert session.page_count == 3
        assert session.total == 23
        assert len(session.items) == 10
        session.goto(3)
        assert len(session.items) == 3
        assert session.page == 3

    def test_page_out_of_range_is_clamped(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 5)
        session.apply_mods_metadata([{"name": f"m{i}", "path": f"/m{i}", "modid": ""} for i in range(5)])
        session.goto(99)
        assert session.page == 1  # 只有一页 → 钳到 1

    def test_search_resets_the_page_and_filters_mods_on_five_fields(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 3)
        session.apply_mods_metadata([
            {"name": "Sodium", "modid": "sodium", "author": "jelly", "description": "render", "path": "/a",
             "filename": "sodium.jar"},
            {"name": "Lithium", "modid": "lithium", "author": "jelly", "description": "tick", "path": "/b",
             "filename": "lithium.jar"},
        ])
        session.set_search("jelly")  # 先把页数撑到 1 页以上
        result = session.set_search("LITH")
        assert result is not None
        assert session.search == "lith"  # `strip().lower()`
        assert session.total == 1
        assert session.page == 1
        # 命中 `author` 字段（模组分支匹配 5 个字段）
        assert session.set_search("jelly") is not None
        assert session.total == 2
        # 搜不到 → 那条专门的文案
        result = session.set_search("zzz")
        assert result["status_key"] == "mod_search_no_results"

    def test_search_on_non_mod_tabs_only_matches_the_name(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 2, tab="resourcepacks")
        session.set_search("pack-01")
        assert session.total == 1
        session.set_search("")
        assert session.total == 2

    def test_unchanged_search_does_not_recompute(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 1)
        session.apply_mods_metadata([{"name": "a", "path": "/a"}])
        assert session.set_search("a") is not None
        assert session.set_search("a ") is None  # 规范化之后相同

    def test_switching_tab_clears_search_and_page(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 2)
        session.apply_mods_metadata([{"name": "a", "path": "/a"}])
        session.set_search("a")
        session.set_tab("resourcepacks")
        assert session.search == ""
        assert session.page == 1

    def test_unknown_tab_is_ignored(self, tmp_path: Path) -> None:
        session = self.build(tmp_path, 1)
        session.set_tab("datapacks")
        assert session.tab == "mods"


class TestSessionModsMetadata:
    def test_apply_metadata_recomputes_and_reports_the_list_count(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        mods = res_base(mc, "1.20.4-forge", isolated=True) / "mods"
        touch(mods / "a.jar")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.refresh()
        result = session.apply_mods_metadata([{"name": "A", "path": str(mods / "a.jar"), "modid": "a"}])
        assert result["state"] == STATE_READY
        assert session.total == 1
        assert result["status_key"] == "rm_list_count"
        assert session.mod_count == 1

    def test_metadata_that_comes_back_empty_is_the_mod_folder_empty_state(self, tmp_path: Path) -> None:
        """目录里有非 jar 的东西（扫描到了），但元数据一条都没有 → `mod_folder_empty`。"""
        mc = make_mc(tmp_path)
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "mods" / "a.jar")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.refresh()
        result = session.apply_mods_metadata([])
        assert result["state"] == STATE_EMPTY
        assert result["status_key"] == "mod_folder_empty"

    def test_trigger_achievements_runs_the_three_calls(self, tmp_path: Path, monkeypatch: Any) -> None:
        from services import resource_service as rs

        calls: List[Any] = []
        monkeypatch.setattr(rs, "_trigger_ach", lambda *a, **k: calls.append(("trigger", a, k)))
        monkeypatch.setattr(rs, "_check_ach", lambda *a, **k: calls.append(("check", a, k)))
        rs.ResourceService.trigger_achievements(3, tmp_path)
        assert [c[1][0] for c in calls[:2]] == ["modder_first_mod", "modder_mod_expert"]
        assert calls[1][2]["trigger_type"] == "set"
        assert calls[2][1][0] == "modder_full_house"

    def test_trigger_achievements_does_nothing_without_mods(self, tmp_path: Path, monkeypatch: Any) -> None:
        from services import resource_service as rs

        calls: List[Any] = []
        monkeypatch.setattr(rs, "_trigger_ach", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(rs, "_check_ach", lambda *a, **k: calls.append(a))
        rs.ResourceService.trigger_achievements(0, tmp_path)
        assert calls == []


class TestSessionUpdates:
    def prepare(self, tmp_path: Path) -> ResourceSession:
        mc = make_mc(tmp_path)
        mods = res_base(mc, "1.20.4-forge", isolated=True) / "mods"
        touch(mods / "a.jar")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        session.refresh()
        session.apply_mods_metadata([
            {"name": "A", "path": str(mods / "a.jar"), "modid": "a", "version": "1.0", "disabled": False},
            {"name": "B", "path": str(mods / "b.jar.disabled"), "modid": "b", "version": "1.0",
             "disabled": True},
            {"name": "C", "path": str(mods / "c.jar"), "modid": "", "version": "1.0", "disabled": False},
        ])
        return session

    def test_updatable_mods_need_a_modid_and_must_be_enabled(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        payload, failure = session.prepare_update_check()
        assert failure == ""
        mods, game_version, mod_loader = payload
        assert [m["name"] for m in mods] == ["A"]
        assert game_version == "1.20.4"

    def test_unknown_game_version_stops_before_any_request(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        session.set_version("totally-not-a-version")
        payload, failure = session.prepare_update_check()
        assert payload is None
        assert failure == "mod_update_unknown_version"

    def test_no_modid_reports_its_own_key(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        session.apply_mods_metadata([{"name": "C", "path": "/c", "modid": ""}])
        payload, failure = session.prepare_update_check()
        assert payload is None
        assert failure == "mod_update_no_modid"

    def test_check_result_messages(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        session.begin_update_check()
        assert session.checking is True
        assert session.update_check_result(2, 10) == ("mod_updates_available", {"count": 2})
        assert session.checking is False
        session.begin_update_check()
        assert session.update_check_result(0, 10) == ("mod_up_to_date", {"total": 10})
        session.begin_update_check()
        assert session.update_check_failed("网络不通") == ("mod_update_check_failed", {"error": "网络不通"})

    def test_batch_update_needs_both_game_version_and_loader(self, tmp_path: Path) -> None:
        """旧实现在这里多判了一个 `mod_loader`（检查更新那条没判）—— **照抄这个不一致**。"""
        session = self.prepare(tmp_path)
        payload, failure = session.prepare_batch_update(["a"])
        assert payload is not None and failure == ""
        # 原版版本（无加载器）：批量更新拒绝，检查更新却放行
        session.set_version("1.20.4")
        payload, failure = session.prepare_batch_update(["a"])
        assert payload is None and failure == "mod_browser_unknown_loader"

    def test_empty_selection_is_a_no_op(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        payload, failure = session.prepare_batch_update([])
        assert payload is None and failure == ""

    def test_finish_batch_clears_the_result_dict(self, tmp_path: Path) -> None:
        session = self.prepare(tmp_path)
        session.begin_update_check()
        session._collect_update("a", {"mod_name": "A"})
        assert session.updates
        session.finish_batch()
        assert session.updates == {}


class TestSessionThumbnails:
    def test_only_resourcepacks_and_shaderpacks_want_thumbnails(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        base = res_base(mc, "1.20.4-forge", isolated=True)
        touch(base / "resourcepacks" / "a.zip")
        touch(base / "shaderpacks" / "b.zip")
        #: 模组这一份也放进**隔离目录**：放在全局 `.minecraft/mods` 的话，本用例
        #: 用带加载器的版本，模组页签扫的是隔离目录 —— 那条"模组不要缩略图"的
        #: 断言就会在**空列表**上成立（假绿）。
        touch(base / "mods" / "c.jar")
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")

        session.set_tab("resourcepacks")
        session.refresh()
        assert len(session.thumbnails_wanted()) == 1

        session.set_tab("mods")
        session.refresh()
        assert session.thumbnails_wanted() == []

    def test_generation_advances_on_each_reload(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        session = ResourceSession(ResourceService(), config=None, callbacks={"get_minecraft_dir": lambda: str(mc)})
        session.set_version("1.20.4-forge")
        first = session.bump_thumbnail_generation()
        assert session.bump_thumbnail_generation() == first + 1


# ═══════════════════════════════════════════════════════════════════
# 桥
# ═══════════════════════════════════════════════════════════════════


class FakeLauncher:
    def __init__(self, mc_dir: Path, versions: Optional[List[str]] = None) -> None:
        self.mc_dir = mc_dir
        #: ⚠ 不能写 `versions or [...]`：**空列表是假值**，`Harness(mc, versions=[])`
        #: 会被换成默认那两个版本，"一个版本都没装"那条用例于是永远测不到它想测的
        #: 分支（阶段 3.6 单测抓到过：`hasVersion` 断言假绿）。按 `is None` 判。
        self.versions = ["1.20.4-forge", "1.21"] if versions is None else list(versions)

    def get_minecraft_dir(self) -> str:
        return str(self.mc_dir)

    def get_installed_versions(self) -> List[Dict[str, Any]]:
        return [{"id": v} for v in self.versions]

    def get_installed_version_ids(self) -> List[str]:
        return list(self.versions)


class FakeContext:
    """真的 `AppContext` 的**最小替身**：只带 `try_get` / `config` / `tasks`。

    ⚠ 用真 `AppContext` 不行：它没有 `config` 属性，而桥要读
    `last_launched_version` 与 `minecraft_dir`。所以这里给一个 SimpleNamespace ——
    **但 `tasks` 必须是真 `TaskRunner`**，否则会话的异步方法会静默失效
    （`ResourceService.new_session` 从 `context.tasks` 取调度器）。
    """

    def __init__(self, service: Any, launcher: Any = None, last_launched: str = "", tasks: Any = None) -> None:
        self._services = {"resource": service}
        if launcher is not None:
            self._services["launcher"] = launcher
        self.config = SimpleNamespace(last_launched_version=last_launched, minecraft_dir="")
        self.tasks = tasks or TaskRunner(max_workers=2, name="test-resources")
        self._owns_tasks = tasks is None

    def try_get(self, name: str) -> Any:
        return self._services.get(name)

    def require(self, name: str) -> Any:
        value = self._services.get(name)
        if value is None:
            raise KeyError(name)
        return value

    def shutdown(self) -> None:
        if self._owns_tasks:
            self.tasks.shutdown(wait=True)


class Harness:
    """桥 + 假上下文 + 可注入的"网络函数"。"""

    def __init__(self, mc_dir: Path, *, versions: Optional[List[str]] = None, last_launched: str = "") -> None:
        self.mc_dir = mc_dir
        self.launcher = FakeLauncher(mc_dir, versions)
        self.service = ResourceService()
        self.context = FakeContext(self.service, self.launcher, last_launched)
        #: 网络函数记账（不联网）
        self.check_calls: List[Any] = []
        self.batch_calls: List[Any] = []
        self._install_fakes()
        self.bridge = ResourcesBridge()
        self.bridge.bind(self.context)
        self.status: List[Any] = []
        self.notices: List[Any] = []
        self.bridge.statusMessage.connect(lambda text, level: self.status.append((text, level)))
        self.bridge.noticeRequested.connect(lambda message, level: self.notices.append((message, level)))
        self.deletes: List[Any] = []
        self.bridge.deleteRequested.connect(lambda path, name: self.deletes.append((path, name)))

    def _install_fakes(self) -> None:
        service = self.service

        def fake_check_updates(mods, game_version, mod_loader, workers=8, on_progress=None, on_update=None):
            self.check_calls.append((tuple(m.get("modid") for m in mods), game_version, mod_loader))
            for i, mod in enumerate(mods, start=1):
                if on_progress:
                    on_progress(i, len(mods))
                if on_update:
                    on_update(mod["modid"], {
                        "project_id": "pid-" + mod["modid"],
                        "latest_version": "9.9",
                        "current_version": mod.get("version", ""),
                        "mod_name": mod.get("name", mod["modid"]),
                        "mod_path": mod.get("path", ""),
                    })
            return len(mods)

        def fake_batch(modids, update_info, game_version, mod_loader, workers=8, on_progress=None):
            self.batch_calls.append((tuple(modids), game_version, mod_loader))
            for i, _ in enumerate(modids, start=1):
                if on_progress:
                    on_progress(i, len(modids))
            return len(modids), 0

        service.check_updates = staticmethod(fake_check_updates)
        service.batch_update_mods = staticmethod(fake_batch)

    #: `Harness` 默认用哪个版本（`FakeLauncher` 的默认列表第一个）
    VERSION = "1.20.4-forge"

    def mods_dir(self, version: str = "") -> Path:
        """某个版本的**模组目录**（带加载器的版本 = `versions/<id>/mods`）。

        用例不要自己拼 `.minecraft/mods` —— 那是**原版**版本的目录；`Harness` 默认用
        带加载器的版本，拼错了会得到"文件明明写进去了却说没找到"。
        """
        return res_base(self.mc_dir, version or self.VERSION, isolated=True) / "mods"

    def close(self) -> None:
        self.context.shutdown()


def write_mods(
    mc: Path, count: int = 2, *, version: str = "1.20.4-forge", isolated: bool = True
) -> None:
    """往"这个版本的模组目录"里放几个 jar。

    带加载器的版本走**版本隔离目录**（`versions/<id>/mods`），原版走全局
    `.minecraft/mods` —— 与 `resource_service.resource_dir` 的两条分支一一对应。
    """
    base = res_base(mc, version, isolated=isolated)
    for i in range(count):
        touch(base / "mods" / f"mod-{i}.jar", 32)


def write_mod_jar(
    mc: Path,
    filename: str,
    *,
    modid: str,
    name: str = "",
    version: str = "1.0",
    version_id: str = "1.20.4-forge",
    isolated: bool = True,
) -> Path:
    """造一个**真 jar**（zip + `fabric.mod.json`）：`extract_mod_metadata` 能读出 modid。

    为什么更新检查的用例必须用它：`updatable_mods()` 只认**有 modid 且未禁用**的条目，
    而读不出元数据的 jar 走 `extract_all_mods_metadata` 的兜底（`modid=""`）—— 于是
    `prepare_update_check` 直接以 `mod_update_no_modid` 收场，一次请求都不会发出去
    （阶段 3.6 单测抓到过：检查更新的用例里 `check_calls` 一直是空的，几条断言全部
    在空列表上空转成假绿）。

    ⚠ 落点默认是**版本隔离目录**（`versions/1.20.4-forge/mods`），与 `make_mc` 默认
    那个带 forge 的版本一致；要往全局 `.minecraft/mods` 里放就传 `isolated=False`
    （或换 `version_id`），否则会出现"jar 造好了、页面却看不到"。
    """
    import json as _json
    import zipfile

    path = res_base(mc, version_id, isolated=isolated) / "mods" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": 1, "id": modid, "name": name or modid, "version": version}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", _json.dumps(payload))
    return path


#: 更新检查要的两条模组：名字序与 modid 序**故意相反**（旧实现按 `mod_name` 排序，
#: 两者同序就测不出排序键到底是哪个）。
UPDATE_MODS = (
    {"filename": "mod-a.jar", "modid": "zeta-mod", "name": "Alpha Mod"},
    {"filename": "mod-b.jar", "modid": "alpha-mod", "name": "Beta Mod"},
)


def write_update_mods(mc: Path, *, version_id: str = "1.20.4-forge", isolated: bool = True) -> None:
    for spec in UPDATE_MODS:
        write_mod_jar(
            mc,
            spec["filename"],
            modid=spec["modid"],
            name=spec["name"],
            version_id=version_id,
            isolated=isolated,
        )


class TestBridgeSnapshot:
    def test_properties_are_empty_but_safe_without_a_service(self) -> None:
        bridge = ResourcesBridge()
        bridge.bind(None)
        assert bridge.available is False
        assert bridge.items == []
        assert bridge.versionId == ""
        assert bridge.hasVersion is False
        assert bridge.loadState == "empty"
        # 所有槽都不该抛
        bridge.activate("1.20.4")
        bridge.setTab("resourcepacks")
        bridge.setSearch("x")
        bridge.goto(2)
        bridge.refresh()
        bridge.checkUpdates()
        bridge.exportList()
        bridge.openFolder()
        bridge.cancel()
        bridge.detach()

    def test_activate_picks_the_route_version_and_scans(self, tmp_path: Path) -> None:
        """**用户报的那条 bug 的钉子**：从 1.21 进来就必须停在 1.21，而不是列表第一个。"""
        mc = make_mc(tmp_path)
        #: 1.21 也要*如实*声明加载器 + jar 落在它的隔离目录里：不然它被判成原版、
        #: 「模组」页签被过滤，扫到的就是空目录（版本号对了、状态却不对）。
        write_version_json(mc, "1.21", loader="forge")
        write_mods(mc, 1, version="1.21")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.21")
            assert pump(lambda: harness.bridge.versionId == "1.21")
            assert harness.bridge.hasVersion is True
            assert harness.bridge.loadState == "loading"  # 目录里有东西，等元数据
            #: 选择器的第一项永远是「全局 .minecraft」（产品行为），其后才是已安装版本；
            #: 已安装版本的第一个是 1.20.4-forge —— 而最终生效的是路由给的 1.21。
            choices = [choice["value"] for choice in harness.bridge.versionChoices]
            assert choices == ["", "1.20.4-forge", "1.21"], choices
            assert harness.bridge.versionId == "1.21", "路由版本被列表第一个盖掉了"
        finally:
            harness.close()

    def test_activate_without_a_route_version_uses_last_launched(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.21", loader="forge")
        harness = Harness(mc, last_launched="1.21")
        try:
            harness.bridge.activate("")
            assert pump(lambda: harness.bridge.versionId == "1.21")
        finally:
            harness.close()

    def test_unknown_route_version_is_ignored(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.21", loader="forge")
        harness = Harness(mc, last_launched="1.21")
        try:
            harness.bridge.activate("deleted-version")
            assert pump(lambda: harness.bridge.versionId == "1.21")
        finally:
            harness.close()

    def test_no_installed_versions_is_reported_as_absent(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        harness = Harness(mc, versions=[], last_launched="")
        try:
            harness.bridge.activate("1.20.4-forge")  # 连路由给的版本也不算数
            assert harness.bridge.hasVersion is False
            #: 文案键 2026-10-07 改了：`mod_browser_need_version_install` 是给**安装流程**
            #: 用的话，摆在"我的资源"里既不准确也吓人（用户截图里的就是它）。
            assert harness.bridge.noVersionKey == "resource_no_version_hint"
        finally:
            harness.close()

    def test_metadata_lands_and_decorates_the_rows(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_mods(mc, 1, version="1.20.4-forge")
        (res_base(mc, "1.20.4-forge", isolated=True) / "mods" / "old.jar.disabled").write_bytes(b"x")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            rows = harness.bridge.items
            names = sorted(str(r["display_name"]) for r in rows)
            #: 禁用后缀由桥按 i18n 拼（旧实现在界面里拼）
            assert any("已禁用" in n for n in names), names
            assert all(r["isMod"] is True for r in rows)
            assert all("path_key" in r for r in rows)
            assert harness.bridge.loadState == "ready"
        finally:
            harness.close()

    def test_empty_mod_folder_is_the_mod_specific_empty_state(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        #: 隔离版本的模组目录要**存在但为空**：不存在时是 `rm_folder_not_exist`
        #: （"目录不存在"），而本用例钉的是 `mod_folder_empty`（"模组文件夹为空"）。
        empty_resource_dir(mc, "mods")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert harness.bridge.loadState == "empty"
            assert harness.bridge.modCount == 0
            assert any("模组文件夹为空" in text for text, _ in harness.status), harness.status
        finally:
            harness.close()


class TestVersionSelection:
    """2026-10-07 验收反馈的钉子：**"从哪个版本进来都看第一个"**。

    根因在 `bind()`：它跑在 `PageStack.pushFrame()` 之前，那时 `routeParams` 还没赋值，
    早先的实现在这里就"猜"了一个默认版本，于是 `activate(路由版本)` 里那句
    "和当前一样就跳过"永远命中。现在 `bind()` 只准备候选、当前值停在「全局 .minecraft」，
    版本一律由 `activate()` 按 **路由参数 → 最近使用 → 第一个已安装 → 全局目录** 定。
    """

    def test_bind_only_prepares_the_choices_and_does_not_scan(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_mods(mc, 1)
        harness = Harness(mc)
        try:
            assert harness.bridge._scanned_once is False, "bind() 不该扫描"
            assert harness.bridge.items == []
            assert harness.bridge.versionId == ""
            assert harness.bridge.globalScope is True
            #: 选择器的第一项永远是「全局 .minecraft」（`value` 为空串）
            assert harness.bridge.versionChoices[0]["value"] == ""
            assert harness.bridge.versionChoices[0]["text"] == "全局 .minecraft"
            assert [c["value"] for c in harness.bridge.versionChoices] == ["", "1.20.4-forge", "1.21"]
        finally:
            harness.close()

    def test_activate_keeps_the_route_version_instead_of_the_first_installed(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.21", loader="forge")
        write_mods(mc, 1, version="1.21")
        #: 已安装列表的第一个是 1.20.4-forge（默认那两个版本里排序在前的那个）
        harness = Harness(mc)
        try:
            assert harness.bridge.versionChoices[1]["value"] == "1.20.4-forge"
            harness.bridge.activate("1.21")
            assert pump(lambda: harness.bridge.versionId == "1.21")
            assert harness.bridge.versionId == "1.21", "路由版本被'已安装列表第一个'盖掉了"
            assert harness.bridge.hasVersion is True
            assert harness.bridge.globalScope is False
            assert harness.bridge.loadState == "loading"
        finally:
            harness.close()

    def test_activate_without_a_route_version_falls_back_to_the_first_installed(self, tmp_path: Path) -> None:
        """一级导航进来（无路由版本）+ **没有**最近使用版本 → 第一个已安装版本。

        ⚠ 这条与"没装任何版本 → 全局目录"是两件事：空串对 `activate` 的含义是
        "路由没给版本"，只有候选列表**真的为空**时才会落到全局目录。
        """
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.21", loader="forge")
        harness = Harness(mc, last_launched="")
        try:
            assert harness.bridge.versionId == ""
            harness.bridge.activate("")
            assert pump(lambda: harness.bridge.versionId == "1.20.4-forge")
            assert harness.bridge.globalScope is False
            assert harness.bridge.hasVersion is True
        finally:
            harness.close()

    def test_last_launched_still_wins_over_the_first_installed(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.21", loader="forge")
        harness = Harness(mc, last_launched="1.21")
        try:
            harness.bridge.activate("")
            assert pump(lambda: harness.bridge.versionId == "1.21")
        finally:
            harness.close()

    def test_set_version_with_an_empty_string_switches_to_the_global_directory(self, tmp_path: Path) -> None:
        """选择器里选「全局 .minecraft」= `setVersion("")`，**合法**且真的去扫全局目录。"""
        mc = make_mc(tmp_path)
        #: 只往**全局** `.minecraft/mods` 里放一个模组（隔离目录里没有）：
        #: 切过去之后能扫到它，才算真的换了目录，而不是"版本号变了、目录没变"。
        write_mod_jar(mc, "global-mod.jar", modid="global-mod", isolated=False)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.versionId == "1.20.4-forge")
            assert harness.bridge.globalScope is False
            #: 隔离目录里一个模组都没有（`global-mod.jar` 只在全局那一份里）——
            #: 这样"切过去之后扫到了东西"才说明真的换了目录。
            assert harness.bridge.modCount == 0
            assert harness.bridge.items == []
            harness.bridge.setVersion("")
            assert harness.bridge.versionId == ""
            assert harness.bridge.globalScope is True
            assert harness.bridge.allowedTabs == list(TAB_KEYS)
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0), "全局目录没被扫"
            paths = [str(row["path"]) for row in harness.bridge.items]
            assert paths and all(str(mc / "mods") in p for p in paths), paths
        finally:
            harness.close()


class TestNoInstalledVersions:
    """一个版本都没装：**不报错**，退到全局 `.minecraft` 目录（用户 2026-10-07 裁决）。"""

    def test_falls_back_to_the_global_directory_and_scans_it(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_mod_jar(mc, "global-mod.jar", modid="global-mod", isolated=False)
        harness = Harness(mc, versions=[], last_launched="")
        try:
            harness.bridge.activate("1.20.4-forge")  # 路由给的版本也不在已安装列表里
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0), "全局目录没被扫"
            assert harness.bridge.versionId == ""
            assert harness.bridge.globalScope is True
            assert harness.bridge.hasVersion is False
            assert harness.bridge.loadState == "ready"
            assert harness.bridge.total == 1
            #: 一个候选都没有时选择器只剩「全局 .minecraft」这一项
            assert [c["value"] for c in harness.bridge.versionChoices] == [""]
            assert harness.bridge.allowedTabs == list(TAB_KEYS)
            #: **不是报错**：全程不该出现 error 级状态（用户截图里那句"未指定游戏版本"）
            assert [s for s in harness.status if s[1] == "error"] == [], harness.status
            assert not any("未指定游戏版本" in text for text, _ in harness.status), harness.status
        finally:
            harness.close()


class TestAllowedTabsFiltering:
    """原版版本不该看到「模组 / 光影」（用户 2026-10-07 裁决，行为变更）。"""

    def test_vanilla_version_hides_mods_and_shaderpacks(self, tmp_path: Path) -> None:
        mc = vanilla_mc(tmp_path)
        harness = Harness(mc, versions=["1.20.4"], last_launched="1.20.4")
        try:
            harness.bridge.activate("1.20.4")
            assert harness.bridge.allowedTabs == ["resourcepacks", "saves"]
            keys = [tab["key"] for tab in harness.bridge.tabs]
            assert keys == ["resourcepacks", "saves"], keys
            assert "mods" not in keys and "shaderpacks" not in keys
            #: 默认页签是 `mods`，在这个版本里不合法 → 会话已经落到第一个合法页签
            assert harness.bridge.tab == "resourcepacks"
            assert harness.bridge.isMods is False
            assert harness.bridge.tabLabelKey == "resource_packs"
            assert [t["key"] for t in harness.bridge.tabs if t["active"]] == ["resourcepacks"]
        finally:
            harness.close()

    def test_loader_version_gets_all_four_tabs(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert harness.bridge.allowedTabs == list(TAB_KEYS)
            assert [tab["key"] for tab in harness.bridge.tabs] == list(TAB_KEYS)
            assert harness.bridge.tab == "mods"
            assert harness.bridge.isMods is True
        finally:
            harness.close()

    def test_global_scope_gets_all_four_tabs(self, tmp_path: Path) -> None:
        mc = vanilla_mc(tmp_path)
        harness = Harness(mc, versions=["1.20.4"], last_launched="1.20.4")
        try:
            harness.bridge.activate("1.20.4")
            assert harness.bridge.allowedTabs == ["resourcepacks", "saves"]
            harness.bridge.setVersion("")
            assert harness.bridge.globalScope is True
            assert harness.bridge.allowedTabs == list(TAB_KEYS)
            assert [tab["key"] for tab in harness.bridge.tabs] == list(TAB_KEYS)
        finally:
            harness.close()

    def test_switching_to_a_vanilla_version_moves_off_the_mods_tab(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_version_json(mc, "1.20.4", loader="")  # 如实声明：这个版本是原版
        harness = Harness(mc, versions=["1.20.4-forge", "1.20.4"])
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.versionId == "1.20.4-forge")
            assert harness.bridge.tab == "mods"
            harness.bridge.setVersion("1.20.4")
            assert harness.bridge.versionId == "1.20.4"
            assert harness.bridge.tab == "resourcepacks", "换版本后停在了这个版本没有的页签上"
            assert harness.bridge.allowedTabs == ["resourcepacks", "saves"]
            assert "mods" not in [tab["key"] for tab in harness.bridge.tabs]
            assert harness.bridge.tabLabelKey == "resource_packs"
            assert harness.bridge.isMods is False
        finally:
            harness.close()


class TestBridgeActions:
    def test_set_tab_resets_search_and_rescans(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        touch(res_base(mc, "1.20.4-forge", isolated=True) / "resourcepacks" / "a.zip")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.loadState == "empty", timeout_s=15.0)
            harness.bridge.setSearch("abc")
            assert harness.bridge.search == "abc"
            harness.bridge.setTab("resourcepacks")
            assert harness.bridge.tab == "resourcepacks"
            assert harness.bridge.search == ""
            assert harness.bridge.total == 1
            assert harness.bridge.isMods is False
            assert harness.bridge.tabLabelKey == "resource_packs"
        finally:
            harness.close()

    def test_delete_requests_then_confirms_only_that_path(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_mods(mc, 2, version="1.20.4-forge")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            target = str(harness.bridge.items[0]["path"])
            harness.bridge.requestDelete(target, "mod-0.jar")
            assert harness.deletes == [(target, "mod-0.jar")]
            assert Path(target).exists()
            harness.bridge.confirmDelete(target)
            assert not Path(target).exists()
        finally:
            harness.close()

    def test_delete_failure_becomes_a_status_and_a_toast(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            harness.bridge.confirmDelete(str(harness.mods_dir() / "not-there.jar"))
            assert harness.status and harness.status[-1][1] == "error"
            assert harness.notices and harness.notices[-1][1] == "error"
        finally:
            harness.close()

    def test_toggle_mod_renames_and_reports_the_new_name(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        #: 文件、断言里的两条路径都必须是**隔离版本的模组目录**（带加载器的版本不读
        #: 全局 `.minecraft/mods`）—— 写/查两处不一致就是"文件明明写进去了却说没找到"。
        target = touch(res_base(mc, "1.20.4-forge", isolated=True) / "mods" / "a.jar")
        disabled = target.with_name("a.jar.disabled")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            harness.bridge.toggleMod(str(target), False)
            assert disabled.exists()
            assert "已禁用" in harness.status[-1][0]
            harness.bridge.toggleMod(str(disabled), True)
            assert target.exists()
            assert "已启用" in harness.status[-1][0]
        finally:
            harness.close()

    def test_submit_files_counts_installs_and_reports_the_result(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        src = tmp_path / "incoming.jar"
        touch(src, 8)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            harness.status.clear()  # 丢掉 activate 期间那条（本用例只看 submitFiles 的结果）
            harness.bridge.submitFiles([])
            assert harness.status == []  # 空列表什么都不做
            harness.bridge.submitFiles([str(src)])
            assert (harness.mods_dir() / "incoming.jar").exists()
            assert "成功安装" in harness.status[-1][0]
            assert harness.notices and harness.notices[-1][1] == "success"
        finally:
            harness.close()

    def test_submit_files_reports_a_duplicate_by_its_own_key(self, tmp_path: Path) -> None:
        """重名文件报 `rm_file_exists`（不是笼统的"没装上"）。

        ⚠ 判据是"**这一串**状态里出现过它"，不是"最后一条是它"：旧实现
        `ui/windows/resource_manager.py::_select_file_install` 在循环里逐条报结果，
        循环之后**无条件**用 `rm_no_resources_installed` 收尾（`install_resource`
        的 docstring 把这条"紧接着被覆盖"记为既有行为）。所以这一轮会发两条：
        逐条结果在前、批量收尾在后 —— 两条都要钉住。
        """
        mc = make_mc(tmp_path)
        src = tmp_path / "dup.jar"
        touch(src, 8)
        harness = Harness(mc)
        try:
            #: ⚠ `harness.mods_dir()` 只能**在 harness 建好之后**调（原先这行写在
            #: `harness = Harness(mc)` 前面，直接 `UnboundLocalError` 把用例判红，
            #: 而报错跟被测行为毫无关系）。
            touch(harness.mods_dir() / "dup.jar")
            harness.bridge.activate("1.20.4-forge")
            harness.status.clear()
            harness.bridge.submitFiles([str(src)])
            assert len(harness.status) == 2, harness.status
            assert "文件已存在" in harness.status[0][0], harness.status
            assert "未安装任何资源" in harness.status[1][0], harness.status
        finally:
            harness.close()

    def test_drop_files_filters_by_extension_and_uses_the_drop_specific_message(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        good = tmp_path / "good.jar"
        bad = tmp_path / "notes.txt"
        touch(good)
        touch(bad)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            harness.status.clear()
            harness.bridge.dropFiles([str(bad)])
            assert "没有可安装的文件" in harness.status[-1][0]
            harness.bridge.dropFiles([str(good)])
            assert (harness.mods_dir() / "good.jar").exists()
            assert "成功安装" in harness.status[-1][0]
        finally:
            harness.close()

    def test_export_without_mods_is_the_empty_message(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            harness.bridge.exportList()
            assert "没有可导出的模组" in harness.status[-1][0]
        finally:
            harness.close()

    def test_export_writes_the_clipboard_through_the_dialogs_bridge(self, tmp_path: Path) -> None:
        """**只写剪贴板、不落盘**（旧行为；对照表 L-Q7 的待裁决项）。"""
        mc = make_mc(tmp_path)
        write_mods(mc, 1, version="1.20.4-forge")
        harness = Harness(mc)
        written: List[str] = []
        harness.bridge._engine = SimpleNamespace(
            _fmcl_bridges={"Dialogs": SimpleNamespace(set_clipboard=lambda text: written.append(text) or True)}
        )
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            harness.bridge.exportList()
            assert len(written) == 1
            assert "mod-0.jar" in written[0]
            assert "已复制" in harness.status[-1][0]
            assert not list(tmp_path.glob("*.txt")), "不该落盘（只写剪贴板）"
        finally:
            harness.close()

    def test_export_reports_failure_when_the_clipboard_refuses(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        write_mods(mc, 1, version="1.20.4-forge")
        harness = Harness(mc)
        harness.bridge._engine = SimpleNamespace(
            _fmcl_bridges={"Dialogs": SimpleNamespace(set_clipboard=lambda text: False)}
        )
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            harness.bridge.exportList()
            assert harness.status[-1][1] == "error"
        finally:
            harness.close()


class TestBridgeUpdates:
    def prepare(self, tmp_path: Path) -> Harness:
        mc = make_mc(tmp_path)
        write_update_mods(mc)
        harness = Harness(mc)
        harness.bridge.activate("1.20.4-forge")
        assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
        return harness

    def test_check_updates_flips_busy_then_publishes_sorted_rows(self, tmp_path: Path) -> None:
        harness = self.prepare(tmp_path)
        try:
            harness.bridge.checkUpdates()
            #: 提交换请求后到主线程处理回调之前一定是忙的（`_deliver` 要事件循环才跑）
            assert harness.bridge.busy is True
            assert pump(lambda: not harness.bridge.busy, timeout_s=10.0)
            #: 发出去的请求 = 两条有 modid 的模组（顺序 = 目录扫描序），
            #: 游戏版本 / 加载器由 `update_targets("1.20.4-forge")` 解出。
            #: ⚠ 原断言写成 `assert ... or True`，恒真 —— 等于没测，这里换成真判据。
            assert harness.check_calls == [(("zeta-mod", "alpha-mod"), "1.20.4", "forge")], harness.check_calls
            rows = harness.bridge.updates
            assert [r["modid"] for r in rows] == ["zeta-mod", "alpha-mod"], rows
            #: 排序键是 `mod_name`（旧实现 `sorted(..., key=mod_name)`，见 `_collect_updates`）。
            #: 两个模组的名字序与 modid 序**相反**，所以这条断言真的在测排序键。
            assert [r["mod_name"] for r in rows] == ["Alpha Mod", "Beta Mod"], rows
            assert all(r["latest_version"] == "9.9" for r in rows)
            assert any("可更新" in text for text, _ in harness.status)
        finally:
            harness.close()

    def test_check_updates_reports_unknown_version_without_calling_out(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        #: snapshot 必须**如实声明加载器**：原版版本没有「模组」页签，模组列表永远
        #: 是空的（`modCount > 0` 那条前置条件会直接超时，用例根本走不到要测的分支）。
        write_version_json(mc, "snapshot", loader="forge")
        write_update_mods(mc, version_id="snapshot")
        #: ⚠ 不能靠 `setVersion("snapshot")` 造这个场景：`resolve_version_id` 的设计就是
        #: "请求的版本不在已安装列表里 → 退回一个**真实存在**的版本"（本文件
        #: `TestResolveVersion` 正是在钉这条），所以那样调版本根本不会变，用例只会走到
        #: "没有 modid"那条分支（阶段 3.6 单测实测：状态栏是 `mod_update_no_modid`）。
        #: 要测"解不出游戏版本"，就得让会话真的停在这样一个已安装版本上。
        harness = Harness(mc, versions=["snapshot"], last_launched="")
        try:
            harness.bridge.activate("snapshot")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            assert harness.bridge.versionId == "snapshot", "版本没停在做测试的那个上"
            harness.bridge.checkUpdates()
            assert harness.check_calls == []
            assert "无法确定游戏版本" in harness.status[-1][0]
        finally:
            harness.close()

    def test_apply_updates_runs_the_batch_and_clears_the_rows(self, tmp_path: Path) -> None:
        harness = self.prepare(tmp_path)
        try:
            harness.bridge.checkUpdates()
            assert pump(lambda: not harness.bridge.busy and harness.bridge.updatesCount > 0, timeout_s=10.0)
            ids = [r["modid"] for r in harness.bridge.updates]
            assert ids == ["zeta-mod", "alpha-mod"], ids
            harness.bridge.applyUpdates(ids)
            assert harness.bridge.busy is True
            assert pump(lambda: not harness.bridge.busy, timeout_s=10.0)
            assert harness.batch_calls and list(harness.batch_calls[0][0]) == ids
            assert harness.bridge.updatesCount == 0
            assert any("更新完成" in text for text, _ in harness.status)
        finally:
            harness.close()

    def test_apply_updates_with_an_empty_list_does_nothing(self, tmp_path: Path) -> None:
        harness = self.prepare(tmp_path)
        try:
            harness.bridge.applyUpdates([])
            assert harness.batch_calls == []
        finally:
            harness.close()

    def test_apply_updates_without_a_loader_reports_the_failure(self, tmp_path: Path) -> None:
        """原版版本（无加载器）：批量更新被拒（旧实现那句 `not mod_loader` 照抄）。

        ⚠ 这条**不能**再要求 `modCount > 0`：原版版本只给「资源包 / 地图」两个页签
        （`allowed_types`），模组页签根本不会出现，那个前置条件必然超时。要钉的是
        "批量更新在没有加载器时被拦住"，与列表里有没有模组无关。
        """
        mc = vanilla_mc(tmp_path)
        harness = Harness(mc, versions=["1.20.4"], last_launched="1.20.4")
        try:
            harness.bridge.activate("1.20.4")
            assert harness.bridge.versionId == "1.20.4"
            assert harness.bridge.allowedTabs == ["resourcepacks", "saves"]
            harness.bridge.applyUpdates(["x"])
            assert harness.batch_calls == []
            assert harness.status and harness.status[-1][1] == "error"
            assert "无法确定游戏版本或加载器类型" in harness.status[-1][0], harness.status
        finally:
            harness.close()


class TestBridgeThreading:
    def test_metadata_progress_arrives_on_the_main_thread(self, tmp_path: Path) -> None:
        """worker 只排队 + emit；落属性的是主线程的 `_deliver`。"""
        mc = make_mc(tmp_path)
        write_mods(mc, 3, version="1.20.4-forge")
        harness = Harness(mc)
        seen: List[int] = []
        harness.bridge.progressChanged.connect(lambda: seen.append(threading.get_ident()))
        main_id = threading.get_ident()
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            assert seen, "进度/完成信号一次都没发"
            assert all(tid == main_id for tid in seen), "有信号是在非主线程上发的"
        finally:
            harness.close()

    def test_unknown_event_kind_is_ignored_not_fatal(self, tmp_path: Path) -> None:
        mc = make_mc(tmp_path)
        harness = Harness(mc)
        try:
            harness.bridge._pending.append({"kind": "no-such-event"})
            harness.bridge.posted.emit()
            _APP.processEvents()
            assert harness.bridge.items == []
        finally:
            harness.close()


class TestBridgeCancel:
    def test_cancel_stops_the_metadata_task_without_publishing_half_a_list(self, tmp_path: Path) -> None:
        """取消**真的**停住那一轮：不铺半份结果，也不立刻重跑。

        为什么用可控的慢替身，而不是真 `extract_mods_metadata`：真实现解 4 个坏 jar
        只要几百毫秒，而 `cancel()` 与任务开跑之间有竞态 —— 取消可能落在"已提交、
        还没开跑"那条缝里（那时 `TaskRunner._execute` 把整条任务跳过，连 `on_done`
        都不发），断言就变成看运气。替身把"任务正在跑"变成可控的：

        1. 先置位 `started` —— 测试据此**确认任务真的在跑**再取消（这个前置条件本身
           也是判据的一部分：任务没跑起来时 `cancel()` 是个空操作）；
        2. 再等取消事件，置位后返回**一份非空的部分结果** —— 那正是"半份列表"，
           桥必须把它丢掉；
        3. 等不到取消就抛（`cancel()` 没生效时用例会明确报出来，而不是超时后假装通过）。
        """
        mc = make_mc(tmp_path)
        write_mods(mc, 4, version="1.20.4-forge")
        started = threading.Event()
        saw_cancel = threading.Event()
        half_a_list = [{"name": "half.jar", "path": str(mc / "mods" / "half.jar"), "modid": "half"}]

        def slow_extract(mods_dir: Path, status_callback: Any = None, cancel_event: Any = None) -> List[Dict[str, Any]]:
            started.set()
            if status_callback:
                status_callback(1, 4)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    saw_cancel.set()
                    return half_a_list
                time.sleep(0.005)
            raise AssertionError("取消事件一直没置位：cancel() 没传到任务里")

        harness = Harness(mc)
        #: 替身装在**实例**上（不是类上）：改类属性会污染同进程里后面所有用例
        harness.service.extract_mods_metadata = staticmethod(slow_extract)  # type: ignore[method-assign]
        try:
            harness.bridge.activate("1.20.4-forge")
            assert pump(lambda: TASK_META in harness.bridge._handles, timeout_s=15.0)
            assert pump(started.is_set, timeout_s=15.0), "元数据任务根本没跑起来"
            harness.bridge.cancel()
            assert pump(lambda: not harness.bridge.busy, timeout_s=15.0)
            assert saw_cancel.is_set(), "取消事件没传到任务里（cancel() 没生效）"
            assert harness.bridge.modCount == 0, "被取消的那一轮不该把半份结果铺到界面上"
            names = sorted(str(r["display_name"]) for r in harness.bridge.items)
            assert names == ["mod-0.jar", "mod-1.jar", "mod-2.jar", "mod-3.jar"], names
            assert all(r["modid"] == "" for r in harness.bridge.items), "半份元数据漏进了列表"
            #: 取消之后退回落一次**真实重扫**：只扫目录（文件名都在），不重提元数据
            #: —— 重提等于把"取消"变成"重跑"，用户按下的那颗按钮就永远停不下来。
            assert harness.bridge.loadState == "ready"
            assert harness.bridge.total == 4
        finally:
            harness.close()


class TestThumbnailWiring:
    def test_thumbnail_source_is_none_without_a_provider(self, tmp_path: Path) -> None:
        """没装图片通道时返回 None（界面退化成兜底图标），**不抛**。"""
        harness = Harness(tmp_path)
        try:
            assert harness.bridge._thumbnail_provider is None
            assert harness.bridge._thumbnail_source("D:/x.zip", "AAAA") is None
        finally:
            harness.close()

    def test_thumbnail_source_uses_the_provider_when_present(self, tmp_path: Path) -> None:
        harness = Harness(tmp_path)
        harness.bridge._thumbnail_provider = SimpleNamespace(register=lambda path, data: "image://fmcl-thumb/7")
        try:
            assert harness.bridge._thumbnail_source("D:/x.zip", "AAAA") == "image://fmcl-thumb/7"
        finally:
            harness.close()

    def test_a_broken_provider_degrades_instead_of_raising(self, tmp_path: Path) -> None:
        harness = Harness(tmp_path)

        def boom(path: str, data: str) -> str:
            raise RuntimeError("provider 坏了")

        harness.bridge._thumbnail_provider = SimpleNamespace(register=boom)
        try:
            assert harness.bridge._thumbnail_source("D:/x.zip", "AAAA") is None
        finally:
            harness.close()


class TestInstallResultMapping:
    """`_report_install` 的四条文案（旧 `_report_install_result` 逐字）。"""

    @pytest.mark.parametrize(
        "result, needle",
        [
            (InstallResult(False, "exists", name="a.jar"), "文件已存在"),
            (InstallResult(False, "map_exists", name="world"), "地图已存在"),
            (InstallResult(False, "unsupported", ext=".rar"), "不支持的地图格式"),
            (InstallResult(False, "failed", error="磁盘满"), "安装失败"),
        ],
    )
    def test_each_outcome_maps_to_its_message(self, tmp_path: Path, result: Any, needle: str) -> None:
        harness = Harness(tmp_path)
        try:
            assert harness.bridge._report_install(result) is False
            assert needle in harness.status[-1][0]
        finally:
            harness.close()

    def test_installed_is_true_without_a_message(self, tmp_path: Path) -> None:
        harness = Harness(tmp_path)
        try:
            assert harness.bridge._report_install(InstallResult(True, "installed")) is True
            assert harness.status == []
        finally:
            harness.close()


class TestThreadSanity:
    def test_no_task_threads_are_left_behind(self, tmp_path: Path) -> None:
        """退出前 `detach()` 要能收干净（不然退出时任务还在往已销毁的对象投递）。"""
        mc = make_mc(tmp_path)
        write_mods(mc, 2, version="1.20.4-forge")
        harness = Harness(mc)
        try:
            harness.bridge.activate("1.20.4-forge")
            pump(lambda: harness.bridge.modCount > 0, timeout_s=15.0)
            harness.bridge.detach()
        finally:
            harness.context.shutdown()
        _APP.processEvents()
        #: `TaskRunner` 的 worker 线程是**常驻**的（`_ensure_workers` 一次建满
        #: `max_workers` 个），所以 `shutdown()` 之后要让它们真的退出再数。
        deadline = time.monotonic() + 5.0
        alive = [t for t in threading.enumerate() if t.name.startswith("test-resources")]
        while alive and time.monotonic() < deadline:
            time.sleep(0.01)
            alive = [t for t in threading.enumerate() if t.name.startswith("test-resources")]
        assert alive == [], f"还有活着的任务线程: {alive}"
