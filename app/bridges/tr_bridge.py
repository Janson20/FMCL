"""i18n 桥 —— QML 侧的 `Tr` 单例（阶段 2 任务 2.9）。

## 为什么是"整表 `map`"而不是 `t(key)` + `revision`

契约 §5.2 与决策 1 定下的方案：**绑定里必须读 `Tr.map["key"]`，不许调 `Tr.t("key")`**。

原因是 QML 的绑定跟踪的是**属性读取**，不是函数返回值：

* `text: Tr.map["app_title"]` —— 表达式里读了一个带 `NOTIFY` 的属性，语言切换时整表替换 →
  所有读过它的绑定**自动重算**；
* `text: Tr.t("app_title")` —— QML 记下的是"这个绑定依赖 `t` 这个函数"，而函数不会被通知，
  于是**语言切了但界面不动**，而且**不报错**（静默失效，最难查的一类）。

阶段 0 的记录里给的写法是 `revision >= 0 ? Tr.t(k) : ""`（靠手写一个假依赖）。本实现不用它：
整表属性本身就是真依赖，不需要每个使用处记得多写一句。**这条由闸门 R4 静态检查兜底**
（绑定里出现 `Tr.t(` 即违规，命令式 JS 里允许）。

## 动态拼接的键（缺陷 D-77）

`ui/windows/server_config_editor.py` 等处有 `_(i18n + "_n")` 这类**运行时拼出来的键**，
QML 的 `qsTr` 静态扫描发现不了它们。整表方案天然解决：`map` 是"键 → 译文"的完整字典，
**任何**拼出来的键都能直接查。这条不是设计推断，`tests/test_tr_bridge.py` 里有实测断言。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

logger = logging.getLogger("app.bridges.tr_bridge")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 语言文件目录的候选位置（开发态 → 打包态）。取第一个含 `zh_CN.json` 的。
_LOCALES_CANDIDATES = ("ui/locales", "app_locales", "locales")


def locales_dir() -> Optional[Path]:
    """找到语言文件目录。

    * 开发态：`<仓库>/ui/locales`；
    * 打包态：PyInstaller 的 `<_MEIPASS>/<候选名>`（阶段 4 会按 `build.spec` 实际收集名调整）。

    只拼路径、**不 import `ui/`** —— 契约决策 12：QML 运行时导入 `ui` 包会拉起 customtkinter。
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(PROJECT_ROOT)
    for root in roots:
        for name in _LOCALES_CANDIDATES:
            candidate = root / name
            if (candidate / "zh_CN.json").is_file():
                return candidate
    return None


def _root_config() -> Any:
    """根模块的 `config` 单例；**取不到就返回 None**（测试环境可能没有 config.json）。

    为什么单独一个函数：`TrBridge` 是本仓库唯一在**没有 AppContext** 的情况下被构造的
    桥（`main_qml` 里 `qmlRegisterSingletonType`/`setContextProperty` 都是无参造它），
    所以它得自己去找那份"进程级唯一配置"，而不是指望装配方注入。
    """
    try:
        from config import config as root_config

        return root_config
    except Exception as e:  # noqa: BLE001 - 配置缺席时退回"系统语言"（旧行为）
        logger.warning("取根模块 config 失败（语言将按系统语言决定）: %s", e)
        return None


class TrBridge(QObject):
    """上下文属性 `Tr` —— QML 里唯一的取文案入口。"""

    #: **整表替换**时发出。所有读 `map` 的绑定都会重算。
    languageChanged = Signal()
    #: 切换失败（未知语言码 / 语言文件缺失）时发出，带人话原因。
    languageFailed = Signal(str)

    def __init__(self, config: Any = None, locales: Optional[Path] = None, parent: Optional[QObject] = None) -> None:
        """
        Args:
            config: 注入的配置对象（测试用；注入后只读写它，不碰真实的 `config.json`）。
                **不注入时**用根模块的 `config` 单例 —— 生产路径就是这条：
                `main_qml._instantiate` 是 `TrBridge()` 无参构造，而"配置里选的语言"
                必须生效（阶段 3.1 人工验收报的缺陷 D-160：`config.json` 改成 en_US
                之后界面还是中文，因为这里拿到的是 None）。
            locales: 覆盖语言文件目录（测试用）。
        """
        super().__init__(parent)
        self._config = config if config is not None else _root_config()
        self._map: Dict[str, str] = {}
        self._language = ""
        self._available: List[Dict[str, str]] = []
        self._locales = locales or locales_dir()

        from services import i18n_service

        self._i18n = i18n_service
        self._boot()

    # ─── 启动 ───────────────────────────────────────────────

    def _boot(self) -> None:
        """载入语言：目录 → 配置里的语言 → 系统语言兜底。"""
        if self._locales is not None:
            self._i18n.set_locales_dir(self._locales)
        else:
            logger.warning("找不到语言文件目录（候选 %s）—— 界面将回退到显示键名", list(_LOCALES_CANDIDATES))

        preferred = ""
        if self._config is not None:
            preferred = str(getattr(self._config, "language", "") or "")

        try:
            self._language = self._i18n.init_i18n(preferred or None)
        except Exception as e:  # noqa: BLE001 - 语言载入失败不该挡住界面
            logger.error("i18n 初始化失败: %s", e)
            self._language = ""

        self._refresh_snapshot()
        logger.info("i18n 就绪：语言=%s，键=%d，目录=%s", self._language, len(self._map), self._locales)

    def _refresh_snapshot(self) -> None:
        """把当前语言的**全部键值**抓成一份字典。

        ## 承重的是"通知"，不是"新字典"（变异测试实测更正）

        最初这里写的是"必须是新字典，原地 clear/update 收不到通知"—— **这是错的**。
        `poc/_probe_tr_map_mutation.py` 对两个假设各做了一次变异：

        * M1 把本方法改成原地 `clear()/update()` → QML 绑定测试**依然通过**
          （`QVariantMap` 属性取值本来就会拷一份，字典对象身份不承重）；
        * M2 去掉 `setLanguage` 里的 `languageChanged.emit()` → 绑定测试**立刻变红**。

        所以 `Tr` 热切换生效的**唯一承重条件**是"改完内容必须发 `languageChanged`"。
        这里仍返回新字典，只是为了别把外部持有过的引用就地改掉 —— 一条防御性约定。
        """
        self._map = dict(self._i18n._translations)
        langs = self._i18n.get_available_languages()
        self._available = [{"code": code, "name": name} for code, name in sorted(langs.items())]

    # ─── QML 只读属性 ───────────────────────────────────────

    @Property("QVariantMap", notify=languageChanged)
    def map(self) -> Dict[str, str]:  # noqa: A003 - QML 属性名，契约冻结
        """键 → 译文。**绑定请读它**：`text: Tr.map["app_title"] ?? "app_title"`。"""
        return self._map

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._language

    @Property("QVariantList", notify=languageChanged)
    def availableLanguages(self) -> List[Dict[str, str]]:  # noqa: N802
        """`[{code, name}, …]`。

        做成属性而不是只给 `availableLanguages()` 槽：QML 不跟踪函数返回值，
        语言列表变了界面不会变（与 ThemeBridge 的 `themes` 同一条理由）。
        """
        return self._available

    @Property(int, notify=languageChanged)
    def keyCount(self) -> int:  # noqa: N802
        return len(self._map)

    # ─── QML 可调用 ─────────────────────────────────────────

    @Slot(str, result=str)
    def t(self, key: str) -> str:
        """取文案。**命令式**场景用（JS 逻辑、菜单标题、`console.log`）。

        绑定里请改用 `Tr.map[...]` —— 见模块文档。缺键时与旧 `_()` 行为一致：返回键名本身。
        """
        return self._i18n._translate(key)

    @Slot(str, "QVariantMap", result=str)
    def tf(self, key: str, kwargs: Dict[str, Any]) -> str:
        """带占位符参数的翻译：`Tr.tf("version_count", {"count": 7})`。

        QML 侧做不了 `str.format(**kwargs)`，而服务层的 `_translate` 支持它，
        所以这里把这条路也开出来（缺参时服务层会静默保留原文，与旧行为一致）。
        """
        try:
            return self._i18n._translate(key, **(kwargs or {}))
        except Exception as e:  # noqa: BLE001 - 参数不合法不该让界面崩
            logger.warning("翻译 %s 的占位符参数有误: %s", key, e)
            return self._i18n._translate(key)

    @Slot(str, result=bool)
    def has(self, key: str) -> bool:
        return key in self._map

    @Slot(str, result=bool)
    def setLanguage(self, code: str) -> bool:  # noqa: N802
        """切换语言（**热切换**，无需重启）。

        旧实现只改 i18n 字典与配置、**不刷新任何控件**（`ui/windows/launcher_settings.py`
        的说明就是"需重启"）；QML 侧靠 `map` 的整表替换真正做到即时生效 ——
        这是对用户可见的**行为增强**，已在 `04-parity-matrix.md` 的 A-15 备注里标注。
        """
        if code == self._language and self._map:
            return True
        if not self._i18n.set_language(code):
            reason = f"未知语言: {code}"
            logger.error(reason)
            self.languageFailed.emit(reason)
            return False

        self._language = code
        self._refresh_snapshot()
        self._persist(code)
        self.languageChanged.emit()
        logger.info("语言已切换为 %s（%d 个键）", code, len(self._map))
        return True

    @Slot()
    def refresh(self) -> None:
        """重新抓一次当前语言的键值表（语言文件在运行期被改过时用）。"""
        self._refresh_snapshot()
        self.languageChanged.emit()

    @Slot(result="QVariantMap")
    def describe(self) -> Dict[str, Any]:
        return {
            "language": self._language,
            "keys": len(self._map),
            "locales_dir": str(self._locales) if self._locales else "",
            "available": [item["code"] for item in self._available],
        }

    # ─── 内部 ───────────────────────────────────────────────

    def _persist(self, code: str) -> None:
        """把语言写回配置（沿用旧 `set_language` 的语义）。

        旧实现在 `ui/windows/launcher_settings.py` 里做这件事；QML 侧没有那个界面层，
        所以由桥代做。**只写语言这一个字段**，失败只记 warning。
        """
        if self._config is None:
            return
        try:
            self._config.language = code
            save = getattr(self._config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # noqa: BLE001
            logger.warning("语言偏好写回配置失败（不影响本次切换）: %s", e)


__all__ = ["TrBridge", "locales_dir"]
