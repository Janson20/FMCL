"""阶段 2 任务 2.9（i18n 桥）的回归守卫。

## 这一组测试真正想钉住的三件事

1. **`Tr.map` 是绑定的正确写法，`Tr.t()` 不是。**
   QML 跟踪属性读取、不跟踪函数返回值，所以 `text: Tr.t("k")` 在语言切换时**不会重算**
   且**不报错**。这一条无法在 Python 侧直接断言 QML 的绑定行为，所以用两个可断言的代理：
   `map` 返回的是**新字典**（切语言后 `is` 关系改变 → QML 的 `QVariantMap` 属性必然发通知），
   以及一条**真 QML** 的端到端断言（见 `test_qml_binding_reacts_to_language_switch`）。

2. **动态拼接的键能查到真值（缺陷 D-77）。**
   `ui/` 里有 11 处 `_(i18n + "_n")` 这类运行时拼键，QML 的 `qsTr` 静态扫描发现不了。
   实测共有 52 个 `X + "_n"` 形状的真实键，这里抽真实的那个当样本 —— 不是构造的假数据。

3. **热切换是真的热切换。** 旧实现只换字典与配置、界面要重启才生效（`ui/windows/launcher_settings.py`）；
   QML 侧靠整表替换即时生效，这是**行为增强**，必须钉住不能被改回去。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

LOCALES = REPO_ROOT / "ui" / "locales"


def _app():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


class FakeConfig(QObject):
    """假配置：**避免测试去改真实的 `config.json`**。"""

    def __init__(self, language: str = "zh_CN"):
        super().__init__()
        self.language = language
        self.saves = 0

    def save_config(self):
        self.saves += 1
        return True


@pytest.fixture
def bridge():
    from app.bridges.tr_bridge import TrBridge

    _app()
    cfg = FakeConfig("zh_CN")
    b = TrBridge(config=cfg, locales=LOCALES)
    b._fake_config = cfg  # type: ignore[attr-defined]
    # 每个用例从 zh_CN 开始，避免用例之间互相污染（阶段 1 踩过这类坑）
    b.setLanguage("zh_CN")
    yield b


# ─── 1. 整表 map ───────────────────────────────────────────────


def test_map_carries_every_key_of_the_locale_file(bridge):
    raw = json.loads((LOCALES / "zh_CN.json").read_text(encoding="utf-8"))
    assert bridge.keyCount == len(raw) > 1000, f"键数异常: {bridge.keyCount}"
    assert bridge.map["app_title"] == raw["app_title"]


def test_map_snapshot_is_replaced_not_mutated_in_place(bridge):
    """切语言后 `map` 是一份**新**字典。

    ## 这条断言**不是**"QML 能自动刷新"的机制（我原先这么写，是错的）

    `poc/_probe_tr_map_mutation.py` 做了两次变异：
    把它改成原地 `clear()/update()`，QML 绑定测试**照样通过**；而把
    `languageChanged.emit()` 去掉，绑定测试**立刻变红**。
    所以承重的是**通知**，不是字典身份。

    保留这条断言，是为了钉住一条**防御性约定**：外部若持有过 `bridge.map` 的引用，
    不该看到内容被就地改掉。它不承担"热切换能不能生效"的举证责任。
    """
    before = bridge.map
    assert bridge.setLanguage("en_US") is True
    after = bridge.map
    assert after is not before, "map 被就地改了 —— 持有旧引用的一方会看到内容变化"
    assert before is not after


def test_language_property_tracks_the_switch(bridge):
    assert bridge.language == "zh_CN"
    bridge.setLanguage("ja_JP")
    assert bridge.language == "ja_JP"


def test_switch_emits_language_changed(bridge):
    hits = []
    bridge.languageChanged.connect(lambda: hits.append(1))
    bridge.setLanguage("en_US")
    assert len(hits) == 1, f"languageChanged 发了 {len(hits)} 次"
    # 切到同一个语言不应重复发（绑定会被无谓重算）
    bridge.setLanguage("en_US")
    assert len(hits) == 1


def test_unknown_language_is_rejected_and_signals(bridge):
    before = bridge.map
    failed = []
    bridge.languageFailed.connect(failed.append)
    assert bridge.setLanguage("xx_YY") is False
    assert failed and "xx_YY" in failed[0]
    assert bridge.map is before, "失败的切换不该动键表"


def test_available_languages_are_exposed_as_a_property(bridge):
    langs = bridge.availableLanguages
    codes = {item["code"] for item in langs}
    assert codes == {"zh_CN", "en_US", "zh_TW", "ja_JP"}, codes
    for item in langs:
        assert item["name"], f"{item['code']} 没有显示名"


# ─── 2. 动态拼接的键（D-77） ────────────────────────────────────


def test_dynamic_key_composed_at_runtime_resolves_through_map(bridge):
    """**D-77 的实测**：`_(i18n + "_n")` 这种拼出来的键，`map` 里查得到真值。

    样本取自真实语言文件（`scfg_level_name` → `scfg_level_name_n`），不是构造的假键。
    """
    base = "scfg_level_name"
    composed = base + "_n"  # 与 ui/windows/server_config_editor.py:637 同构
    assert composed in bridge.map, f"{composed} 不在键表里"
    value = bridge.map[composed]
    assert value != composed, "查到的还是键名本身 —— 等于没查到"
    assert value == "世界存档名"

    desc = base + "_d"  # 同文件的 :662 用的是 _d 后缀
    assert desc in bridge.map and bridge.map[desc] != desc


def test_all_real_dynamic_keys_of_this_shape_resolve(bridge):
    """把所有 52 个同形状的键都过一遍 —— 单个样本不足以支撑"整表方案成立"。"""
    raw = json.loads((LOCALES / "zh_CN.json").read_text(encoding="utf-8"))
    pairs = [(k[: -len("_n")], k) for k in raw if k.endswith("_n")]
    assert len(pairs) >= 20, f"样本太少（{len(pairs)}），断言强度不够"
    missing = [base + "_n" for base, _ in pairs if bridge.map.get(base + "_n") in (None, base + "_n")]
    assert missing == [], f"这些拼出来的键在 map 里查不到真值: {missing}"


def test_dynamic_key_also_works_after_language_switch(bridge):
    """切语言之后拼键依然可用（新字典里同样有这些键）。"""
    bridge.setLanguage("en_US")
    composed = "scfg_level_name_n"
    assert composed in bridge.map
    assert bridge.map[composed] != composed


# ─── 3. 真 QML 端到端：绑定会跟着语言重算 ───────────────────────


def test_qml_binding_reacts_to_language_switch(tmp_path):
    """**端到端**：真引擎 + 真 QML，`Tr.map[...]` 的绑定在语言切换后必须变。

    这条比前面所有 Python 侧断言都强：它证明"整表替换"真的被 QML 当成了属性变化。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.bridges.tr_bridge import TrBridge

    app = _app()
    engine = QQmlApplicationEngine()
    b = TrBridge(config=FakeConfig("zh_CN"), locales=LOCALES)
    engine.rootContext().setContextProperty("Tr", b)

    # **必须挑一个两种语言译文真的不同的键**：第一版用了 `app_title`，而它在 zh_CN 与
    # en_US 里是同一个字符串（`FMCL - Fusion Minecraft Launcher`），于是断言比较的是
    # 两个相同的值、无论绑定有没有重算都会"看不出差别"。
    zh_raw = json.loads((LOCALES / "zh_CN.json").read_text(encoding="utf-8"))
    en_raw = json.loads((LOCALES / "en_US.json").read_text(encoding="utf-8"))
    probe_key = next(
        (k for k in zh_raw if k in en_raw and zh_raw[k] != en_raw[k] and len(str(zh_raw[k])) > 1),
        None,
    )
    assert probe_key, "找不到两种语言译文不同的键，这条测试失去意义"

    qml_file = tmp_path / "binding_probe.qml"
    qml_file.write_text(
        "import QtQuick\n"
        "QtObject {\n"
        "    objectName: 'root'\n"
        "    property string viaMap: Tr.map['%s'] ?? 'missing'\n"
        "}\n" % probe_key,
        encoding="utf-8",
        newline="",
    )
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    roots = engine.rootObjects()
    assert roots, "QML 没建出来"
    root = roots[0]

    zh = root.property("viaMap")
    assert zh and zh != "missing", f"首次取值就失败: {zh!r}"
    assert zh == zh_raw[probe_key]

    b.setLanguage("en_US")
    for _ in range(20):
        app.processEvents()
    en = root.property("viaMap")
    assert en != zh, f"语言切了但 QML 绑定没变（{zh!r} -> {en!r}）—— map 的整表替换没被当成属性变化"
    assert en == en_raw[probe_key], f"绑定变了但值不对: {en!r} != {en_raw[probe_key]!r}"


# ─── 4. 命令式 API ─────────────────────────────────────────────


def test_t_falls_back_to_the_key_name(bridge):
    """缺键时与旧 `_()` 行为一致：返回键名本身。"""
    assert bridge.t("__no_such_key__") == "__no_such_key__"
    assert bridge.t("app_title") == bridge.map["app_title"]


def test_tf_formats_placeholders(bridge):
    raw = json.loads((LOCALES / "zh_CN.json").read_text(encoding="utf-8"))
    key = next((k for k, v in raw.items() if "{" in str(v)), None)
    assert key, "语言文件里找不到带占位符的键，这条测试失去意义"
    assert bridge.tf(key, {}) == bridge.t(key)  # 缺参时服务层静默保留原文
    assert bridge.has(key) is True
    assert bridge.has("__no_such_key__") is False


def test_describe_is_json_friendly(bridge):
    info = bridge.describe()
    assert info["language"] == "zh_CN"
    assert info["keys"] > 1000
    assert "zh_CN" in info["available"]
    assert info["locales_dir"].endswith("locales")


# ─── 5. 配置持久化与作用域 ─────────────────────────────────────


def test_language_is_persisted_to_config(bridge):
    cfg = bridge._fake_config  # type: ignore[attr-defined]
    bridge.setLanguage("ja_JP")
    assert cfg.language == "ja_JP"
    assert cfg.saves >= 1, "切换语言没有落盘（旧实现会写 config.json）"


def test_persist_failure_does_not_break_the_switch(bridge):
    cfg = bridge._fake_config  # type: ignore[attr-defined]

    def boom():
        raise OSError("磁盘满了")

    cfg.save_config = boom  # type: ignore[assignment]
    assert bridge.setLanguage("en_US") is True, "落盘失败不该让语言切换失败"
    assert bridge.language == "en_US"


def test_bridge_does_not_import_the_tk_ui_package():
    """契约决策 12：QML 运行时不许 import `ui/`（会拉起 customtkinter）。

    `ui/locales/` 只是**路径**，不是 import —— 这条用子进程真跑一次来证明。
    """
    code = (
        "import os, sys;"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "sys.path.insert(0, r'{root}');"
        "from PySide6.QtGui import QGuiApplication;"
        "app = QGuiApplication([]);"
        "from app.bridges.tr_bridge import TrBridge;"
        "b = TrBridge();"
        "bad = [m for m in sys.modules if m == 'ui' or m.startswith('ui.')];"
        "print('UI_MODULES=' + ','.join(sorted(bad)));"
        "print('KEYS=' + str(b.keyCount))"
    ).format(root=REPO_ROOT)
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert out.returncode == 0, f"子进程失败: {out.stderr}"
    assert "UI_MODULES=" in out.stdout and "UI_MODULES=\n" in out.stdout + "\n", f"拖进了 ui 包: {out.stdout}"
    keys = int([ln for ln in out.stdout.splitlines() if ln.startswith("KEYS=")][0].split("=")[1])
    assert keys > 1000, f"无参构造应该能自己找到语言文件，实际键数 {keys}"
