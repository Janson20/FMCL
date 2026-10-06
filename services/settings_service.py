"""设置页服务（阶段 3 任务 3.4）。

## 范围（`03-phases.md` 的 3.4 + 2026-10-06 用户五条裁决）

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| M-01 | 启动游戏后最小化开关 | `ui/windows/launcher_settings.py:118-147` `989-998` |
| M-02 | 国内镜像源开关（+ 成就 `advanced_mirror`） | `150-182` `1000-1011` |
| M-03 | Java 模式下拉（自动 / 扫描 / 自定义） | `185-239` `1127-1146` |
| M-04 | Java 自定义路径 + 浏览可执行文件 | `243-267` `1148-1159` |
| M-05 | Java 扫描结果列表 + 刷新 + 单选应用 | `274-304` `1161-1215` |
| M-06 | 界面语言下拉（旧版"需重启"，本版热切换） | `325-361` `1013-1029` |
| M-07 | 主题下拉（5 预设 + 用户主题标记） | `364-432` `1043-1053` |
| M-08 | 导入主题 .json（+ 成就 `personalize_import_theme`） | `435-444` `1055-1076` |
| M-09 | 自定义强调色（`#RRGGBB` 校验 / 空值清除 / 非法提示） | `447-482` `1078-1102` |
| M-10 | 随机强调色 | `485-495` `1104-1117` |
| M-11 | 版本动态主题开关 + 提示 | `498-530` `1119-1125` |
| M-12 | 下载线程数（1..255） | `533-567` `1031-1041` |
| M-24 | 底部「应用」= **重启启动器进程** / 关闭 | `54-73` `910-930` |
| M-25 | 控件写盘语义（**本次改为草稿**，见下） | `989-1125` |

## 与旧实现的**唯一**语义差别：M-Q1 选项 B（草稿范式）

用户 2026-10-06 裁决（`docs/refactor/16-phase3-execution-log.md` §11.1/§11.2）：

* **B1 整窗一份草稿** —— `begin_draft()` 拿一份基线快照，之后所有控件改动只落 `_draft`；
  「确定」批量写盘、「取消」丢弃。切页面/切分区都不丢（草稿活在服务里，不活在页面里）。
* **B2 可预览、不落盘** —— 主题、强调色、语言三项在改动的瞬间**只改内存**
  （`preview_*`：直接写 `services.palette.COLORS` 与 i18n 字典，**不碰 `config.json`**），
  这样用户在点「确定」之前就看得见效果；`discard()` 把预览还原成已保存值。
* **B3 成就改到「确定」时触发**，且**同类成就一次会话只触发一次**（取消不计数）。
  旧实现是改动瞬间就 `_trigger_ach`。
* **B4 动作型条目立即执行** —— 导入主题文件不进草稿（它没有"待保存"语义），
  成就也在导入成功时当场触发（与旧实现一致）。
* **B5 「应用」改名「保存并重启」** —— 先落盘草稿再拉起新进程。
* **B6 未保存改动离开时提示一次** —— 由壳层的"离开守卫"承担（`app/bridges/nav_bridge.py`），
  服务只提供 `dirty()`；确认离开时调 `discard()` 还原预览。

## 三条纪律

1. **业务规则在这里**：写盘顺序、成就映射、线程数夹取、强调色校验、主题显示名拼法、
   Java 行文本拼法、语言/主题的预览与还原，全在本模块；桥与 QML 不做判断（红线 2）。
2. **写盘一律走核心层已有方法**（`launcher/core.py` 的 `set_*`），不在服务里另写一套 ——
   那些方法里还有镜像补丁、主题应用、日期落盘等副作用，绕过它们等于丢掉行为。
   launcher 缺席（单元测试）时才退回"直接写 config + 一次 save_config"。
3. **零 UI 依赖**：不 import tkinter / PySide6 / ui.*；预览只改内存里的颜色与语言字典。
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - 重启启动器需要拉起子进程（旧实现同款）
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.base import Service
from services.theme_service import ThemeEngine, get_theme_engine

# ─── 常量：与旧界面逐条对齐 ──────────────────────────────────────

#: 下载线程数的合法区间（旧滑块 `from_=1, to=255`；核心层 `set_download_threads`
#: 也是 `max(1, min(255, threads))`，两处必须一致）
THREADS_MIN = 1
THREADS_MAX = 255
THREADS_DEFAULT = 4

#: Java 选择模式（旧下拉三个选项的稳定 id；旧代码里存的就是这三个字符串）
JAVA_MODE_AUTO = "auto"
JAVA_MODE_SCAN = "scan"
JAVA_MODE_CUSTOM = "custom"
JAVA_MODES: Tuple[str, ...] = (JAVA_MODE_AUTO, JAVA_MODE_SCAN, JAVA_MODE_CUSTOM)

#: 稳定 id → i18n 键（旧下拉的显示文案）
JAVA_MODE_LABEL_KEYS: Dict[str, str] = {
    JAVA_MODE_AUTO: "settings_java_mode_auto",
    JAVA_MODE_SCAN: "settings_java_mode_scan",
    JAVA_MODE_CUSTOM: "settings_java_mode_custom",
}

#: 草稿里的 9 个键。顺序 = 界面上的出现顺序（也用于 `changes()` 的稳定输出）。
DRAFT_KEYS: Tuple[str, ...] = (
    "minimize_on_game_launch",
    "mirror_enabled",
    "download_threads",
    "language",
    "theme_name",
    "accent_color",
    "dynamic_version_theme",
    "java_mode",
    "java_custom_path",
)

#: **需要预览**的三个键（B2）：改动瞬间只改内存，确定才落盘。
PREVIEW_KEYS: Tuple[str, ...] = ("language", "theme_name", "accent_color")

#: 键 → 提交时触发的成就（B3）。旧实现（`launcher_settings.py`）的对应关系：
#: `language`→`advanced_polyglot`、`theme_name`→`personalize_theme_master`、
#: `mirror_enabled`→`advanced_mirror`、`accent_color`→`personalize_my_color`。
COMMIT_ACHIEVEMENTS: Dict[str, str] = {
    "language": "advanced_polyglot",
    "theme_name": "personalize_theme_master",
    "mirror_enabled": "advanced_mirror",
    "accent_color": "personalize_my_color",
}

#: 下载线程数的成就是**条件式**的（旧实现 `_check_ach("advanced_multithread", threads > 1)`）
THREADS_ACHIEVEMENT = "advanced_multithread"

#: 导入主题的成就是**动作型**的（B4：导入成功当场触发，不进草稿）
IMPORT_ACHIEVEMENT = "personalize_import_theme"

#: 主题引擎里的"默认"主题名（`ThemeEngine._load_preset_themes` 的第一个）
DEFAULT_THEME = "default"

#: 自定义强调色的前缀（旧校验：`color.startswith("#") and len(color) == 7` + 十六进制解析）
ACCENT_PREFIX = "#"
ACCENT_LENGTH = 7

#: 强调色校验失败的 i18n 键（旧实现 `_("settings_accent_invalid")`）
ACCENT_INVALID_KEY = "settings_accent_invalid"

#: 重启用：开发态回退的入口脚本（打包态直接用 `sys.executable`）
ENTRY_SCRIPT_NAME = "main_qml.py"


# ─── 纯函数（可单独测，不含状态） ────────────────────────────────


def clamp_threads(value: Any) -> int:
    """把任意输入夹到 ``[1, 255]``。非数字退回默认值（旧滑块的取值路径）。

    旧实现是 `int(round(value))`（滑块给的是浮点），夹取在核心层
    `set_download_threads` 里做。这里两件事都做：界面上显示的数字与写进配置的
    数字必须**同一个**，否则会出现"滑块显示 300、配置里是 255"。
    """
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return THREADS_DEFAULT
    return max(THREADS_MIN, min(THREADS_MAX, number))


def normalize_accent(text: Any) -> Optional[str]:
    """归一化强调色输入。

    * 空串 / None / 全空白 → ``None``（**清除**，回到主题自带强调色）；
    * `#RRGGBB` → 原样返回（大小写保持用户输入，旧实现也不改写大小写）；
    * 其它 → 抛 :class:`ValueError`（调用方翻成 `settings_accent_invalid`）。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if len(raw) != ACCENT_LENGTH or not raw.startswith(ACCENT_PREFIX):
        raise ValueError(ACCENT_INVALID_KEY)
    try:
        int(raw[1:], 16)
    except ValueError as exc:
        raise ValueError(ACCENT_INVALID_KEY) from exc
    return raw


def threads_is_multithreaded(threads: Any) -> bool:
    """`advanced_multithread` 的判据（旧实现：`threads > 1`）。"""
    return clamp_threads(threads) > 1


def java_kind(row: Dict[str, Any]) -> str:
    """`JRE` / `JDK`（旧实现 `"JRE" if rt.get("is_jre") else "JDK"`）。"""
    return "JRE" if row.get("is_jre") else "JDK"


def java_row_title(row: Dict[str, Any]) -> str:
    """扫描结果一行的主文本（旧实现 `:1188` 的 f-string，逐字保留拼法）。

    旧代码：``f"Java {major} ({kind}) - {version_str} [{arch}]"``。
    """
    return "Java {major} ({kind}) - {version} [{arch}]".format(
        major=row.get("major_version", "?"),
        kind=java_kind(row),
        version=row.get("version_str", "?"),
        arch=row.get("arch", "?"),
    )


def java_row_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """扫描结果 → 界面用的行字典（`value` 是"选中后写进 java_custom_path"的值）。"""
    path = str(row.get("path", "") or "")
    return {
        "path": path,
        "value": path,
        "title": java_row_title(row),
        "subtitle": str(row.get("home", "") or ""),
        "major_version": row.get("major_version"),
        "arch": str(row.get("arch", "") or ""),
        "is_jre": bool(row.get("is_jre")),
    }


def theme_view(theme: Dict[str, Any], user_tag: str = "") -> Dict[str, Any]:
    """主题字典 → 下拉项（旧实现 `_refresh_theme_list` 的显示名拼法）。

    用户导入的主题带一个后缀标记（旧实现是 `f"{name} {_('settings_theme_user_tag')}"`，
    那个键在语言文件里是一个证件符号）。**后缀由调用方给**：服务层不持有译文，
    桥把 `Tr` 的译文传进来（与 `settings_theme_user_tag` 同一条老路）。
    """
    name = str(theme.get("name", "") or "")
    source = str(theme.get("source", "") or "")
    label = name
    if source == "user" and user_tag:
        label = f"{name} {user_tag}"
    return {
        "name": name,
        "label": label,
        "source": source,
        "author": str(theme.get("author", "") or ""),
        "description": str(theme.get("description", "") or ""),
        "is_user": source == "user",
    }


def describe_changes(saved: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    """返回 `draft` 相对 `saved` **真正变了**的键（值相等的不算）。

    比较用 `==` 但把 `None` 与 `""` 归一：`java_custom_path` 从 `None` 变 `""`
    在实际语义上是"没变"（两者都表示"没设自定义路径"），不算改动 —— 否则
    用户点一下输入框再点确定就会平白触发一次写盘。
    """
    out: Dict[str, Any] = {}
    for key in DRAFT_KEYS:
        old = saved.get(key)
        new = draft.get(key)
        if isinstance(old, str) or isinstance(new, str):
            old_cmp = "" if old is None else old
            new_cmp = "" if new is None else new
        else:
            old_cmp, new_cmp = old, new
        if old_cmp != new_cmp:
            out[key] = new
    return out


def java_mode_shows_custom(mode: Any) -> bool:
    """该模式是否显示"自定义路径"区域（旧 `_on_java_mode_change` 的分支）。"""
    return str(mode or "") == JAVA_MODE_CUSTOM


def java_mode_shows_scan(mode: Any) -> bool:
    """该模式是否显示"扫描结果"区域（旧 `_on_java_mode_change` 的分支）。"""
    return str(mode or "") == JAVA_MODE_SCAN


# ─── 服务 ────────────────────────────────────────────────────────


class SettingsService(Service):
    """设置页的业务出口。状态只有三样：基线快照、草稿、本次会话已触发的成就。"""

    name = "settings"
    label = "设置"

    def __init__(
        self,
        context: Any = None,
        *,
        launcher: Any = None,
        theme_engine: Optional[ThemeEngine] = None,
        i18n: Any = None,
        config: Any = None,
        spawn: Optional[Callable[..., Any]] = None,
        request_quit: Optional[Callable[[], Any]] = None,
        opener: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """
        Args:
            launcher: 显式注入的 ``MinecraftLauncher``。**测试用**；生产路径为 None，
                届时从 ``AppContext`` 取 ``"launcher"``（与 `VersionService` 同款）。
            theme_engine: 主题引擎（默认取 `services.theme_service` 的全局单例）。
            i18n: 语言模块（默认 `services.i18n_service`，与 `Tr` 桥同一个单例）。
            config: 配置对象（默认从上下文取；两者都没有时回落到根模块单例）。
            spawn: 拉起新进程的实现（重启用）。测试注入。
            request_quit: "让当前进程退出"的实现（重启用）。**由桥接过去** ——
                服务层不知道 QML 怎么退出（QML 侧是 `Qt.quit()`）。
            opener: 打开目录的实现（日志页"打开日志目录"）。测试注入。
        """
        super().__init__(context)
        self._launcher = launcher
        self._engine = theme_engine
        self._i18n = i18n
        self._config = config
        self._spawn = spawn
        self._request_quit = request_quit
        self._opener = opener

        #: 基线（上一次"已保存"的 9 个键）
        self._saved: Dict[str, Any] = {}
        #: 草稿（B1：整窗一份，活在服务里，切页面不丢）
        self._draft: Dict[str, Any] = {}
        #: 本次会话已触发过的成就（B3：同类一次会话只触发一次）
        self._fired: set = set()

    # ─── 依赖解析 ────────────────────────────────────────────

    def use_launcher(self, launcher: Any) -> None:
        """注入核心层实例（生产路径由桥在装配后调用一次）。"""
        self._launcher = launcher

    def _resolve_launcher(self) -> Any:
        if self._launcher is not None:
            return self._launcher
        try:
            self._launcher = self.context.try_get("launcher")
        except Exception as e:  # noqa: BLE001 - 没有上下文（单测）时安静退回配置对象
            self.log.debug("取 launcher 失败: %s", e)
            self._launcher = None
        return self._launcher

    def config_obj(self) -> Any:
        """配置对象：注入的 → 上下文里的 → 根模块单例（三者是同一个实例）。"""
        if self._config is not None:
            return self._config
        try:
            self._config = self.context.config
        except Exception:  # noqa: BLE001 - 没绑定上下文时退回根模块
            try:
                from config import config as root_config

                self._config = root_config
            except Exception:  # noqa: BLE001
                self._config = None
        return self._config

    def engine(self) -> Optional[ThemeEngine]:
        """主题引擎单例。**没初始化过时不自己 init**（那是启动期的活）。"""
        if self._engine is not None:
            return self._engine
        try:
            self._engine = get_theme_engine()
        except Exception as e:  # noqa: BLE001 - 引擎缺席时主题区退化为只读
            self.log.warning("主题引擎不可用: %s", e)
            self._engine = None
        return self._engine

    def i18n(self) -> Any:
        if self._i18n is None:
            from services import i18n_service

            self._i18n = i18n_service
        return self._i18n

    def _read(self, getter: str, attr: str, default: Any) -> Any:
        """先问核心层的 getter（行为权威），没有就退回配置对象。"""
        launcher = self._resolve_launcher()
        fn = getattr(launcher, getter, None) if launcher is not None else None
        if callable(fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - 单个 getter 坏掉不该让整页读不出值
                self.log.warning("%s() 读取失败，回退配置项 %s: %s", getter, attr, e)
        cfg = self.config_obj()
        return getattr(cfg, attr, default) if cfg is not None else default

    # ─── 快照与草稿 ──────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """当前**已保存**的 9 个键。"""
        return {
            "minimize_on_game_launch": bool(
                self._read("get_minimize_on_game_launch", "minimize_on_game_launch", False)
            ),
            "mirror_enabled": bool(self._read("get_mirror_enabled", "mirror_enabled", True)),
            "download_threads": clamp_threads(self._read("get_download_threads", "download_threads", THREADS_DEFAULT)),
            "language": str(self._read("get_language", "language", "") or ""),
            "theme_name": str(self._read("get_theme_name", "theme_name", DEFAULT_THEME) or DEFAULT_THEME),
            "accent_color": self._read("get_accent_color", "accent_color", None) or None,
            "dynamic_version_theme": bool(
                self._read("get_dynamic_version_theme", "dynamic_version_theme", False)
            ),
            "java_mode": str(self._read("get_java_mode", "java_mode", JAVA_MODE_AUTO) or JAVA_MODE_AUTO),
            "java_custom_path": self._read("get_java_custom_path", "java_custom_path", None) or None,
        }

    def begin_draft(self, *, force: bool = False) -> Dict[str, Any]:
        """开一份草稿并返回它。

        **已有草稿时不覆盖用户改过的东西**（除非 `force=True`）—— B1 的"整窗一份草稿"
        就靠这一条：界面每次进入设置域都会调它，而用户之前改了一半的东西必须还在。

        ## 但基线要**重定**（阶段 3 任务 3.4 人工验收反馈的修复）

        草稿活的时间比页面长，而"已保存的值"可能在草稿之外被改掉 —— 最典型的是
        **顶栏地球切语言**（`Tr.setLanguage` 直接写 `config.language`，改的就是基线里
        那一项）。原来的写法把旧基线一直用到天荒地老，于是：

        1. 装配期建的草稿里 `language` = 当时的语言（例如 en_US）；
        2. 用户用地球切到中文 → 已保存值 = zh_CN；
        3. 用户进设置页、随手改个**主题** → 预览会把整份草稿应用一遍 →
           **把过期的 en_US 又按回内存里**（界面当场变英文，而 `config.json` 还是 zh_CN）。

        现在的语义是"**把草稿重定到新基线上**"：
        * 相对旧基线**真正改过**的键 → 保留用户的改动；
        * 没动过的键 → 跟随最新的已保存值；
        * 基线本身换成最新快照。
        用户 2026-10-06 报的"语言还是会自动变英文"就是这个过期基线。
        """
        fresh = self.snapshot()
        if force or not self._draft:
            self._saved = fresh
            self._draft = dict(fresh)
            return dict(self._draft)

        edited = describe_changes(self._saved, self._draft)
        rebased = dict(fresh)
        rebased.update(edited)
        self._saved = fresh
        self._draft = rebased
        return dict(self._draft)

    def current_draft(self) -> Dict[str, Any]:
        """当前草稿（没有草稿时返回一份快照，**不建立**草稿）。"""
        return dict(self._draft) if self._draft else self.snapshot()

    def has_draft(self) -> bool:
        return bool(self._draft)

    def changes(self) -> Dict[str, Any]:
        """草稿相对基线真正变了的键。"""
        return describe_changes(self._saved or self.snapshot(), self.current_draft())

    def dirty(self) -> bool:
        return bool(self.changes())

    def set_draft(self, key: str, value: Any) -> Dict[str, Any]:
        """改草稿里的一项。返回一份**给桥用**的结果：

        ``{"ok", "key", "value", "error", "dirty", "changed", "preview"}``

        * ``error`` 非空时是 i18n 键（目前只有强调色非法一种），值**不会**写进草稿；
        * ``changed`` = 这一项与基线不同（用于成就与"是否要重算预览"）；
        * ``preview`` = 这一项属于 B2 的预览三项（主题/强调色/语言），桥据此刷新
          `Theme` / `Tr` 的只读快照 —— 刷新动作在桥里，不在服务里（服务不碰 QML）。
        """
        name = str(key or "")
        result = {
            "ok": False,
            "key": name,
            "value": value,
            "error": "",
            "dirty": False,
            "changed": False,
            "preview": name in PREVIEW_KEYS,
        }
        if name not in DRAFT_KEYS:
            result["error"] = f"unknown_key:{name}"
            return result

        self.begin_draft()
        try:
            coerced = self._coerce(name, value)
        except ValueError as exc:
            result["error"] = str(exc)
            return result

        self._draft[name] = coerced
        result["ok"] = True
        result["value"] = coerced
        result["dirty"] = self.dirty()
        result["changed"] = name in self.changes()

        if result["preview"]:
            # B2：改动的瞬间只改内存，看得见效果，但不写盘
            self.apply_preview(self._draft)
        return result

    def _coerce(self, key: str, value: Any) -> Any:
        """把界面来的值归一成配置里的形态；不合法时抛 `ValueError`（值是 i18n 键）。"""
        if key in ("minimize_on_game_launch", "mirror_enabled", "dynamic_version_theme"):
            return bool(value)
        if key == "download_threads":
            return clamp_threads(value)
        if key == "accent_color":
            return normalize_accent(value)
        if key == "java_mode":
            mode = str(value or "")
            if mode not in JAVA_MODES:
                raise ValueError(f"unknown_java_mode:{mode}")
            return mode
        if key == "java_custom_path":
            text = str(value or "").strip()
            return text or None
        if key == "language":
            code = str(value or "")
            if code not in self.available_languages():
                raise ValueError(f"unknown_language:{code}")
            return code
        if key == "theme_name":
            name = str(value or "")
            engine = self.engine()
            if engine is None or engine.load_theme(name) is None:
                raise ValueError(f"unknown_theme:{name}")
            return name
        return value

    def discard(self) -> None:
        """丢弃草稿并把预览还原成**已保存**的值（B6 的"确认丢弃"与「取消」都调它）。"""
        baseline = self._saved or self.snapshot()
        if self._draft:
            self.restore_preview(baseline)
        self._draft = {}
        self._saved = {}
        self.log.info("设置草稿已丢弃，预览还原为已保存值")

    # ─── 预览（B2：内存生效、不落盘） ────────────────────────

    def apply_preview(self, values: Dict[str, Any]) -> None:
        """把 `values` 里的主题 / 强调色 / 语言应用到**内存**（不写 config）。

        主题走 `ThemeEngine.apply_theme`（原地改 `services.palette.COLORS`，全进程
        共享同一个 dict，QML 侧 `Theme` 桥一刷新就看得见）；语言走
        `i18n_service.set_language`（**只换字典，不碰配置** —— 旧界面那个
        "改了语言必须重启"就是这么来的：它把持久化写在另一条回调里）。
        """
        language = values.get("language")
        if language:
            try:
                self.i18n().set_language(str(language))
            except Exception as e:  # noqa: BLE001 - 语言文件坏掉不该让预览整条断掉
                self.log.warning("预览语言 %s 失败: %s", language, e)
        # **只有传了主题相关键才动主题**（2026-10-06 加固）：以前无条件调，
        # 于是"只传 language 的那次预览"会把主题按回 default（`theme_name=None` →
        # 走 `DEFAULT_THEME`），把用户选的主题悄悄抹掉。
        if "theme_name" in values or "accent_color" in values:
            self._apply_theme_preview(values.get("theme_name"), values.get("accent_color"))

    def _apply_theme_preview(self, theme_name: Any, accent: Any) -> bool:
        engine = self.engine()
        if engine is None:
            return False
        name = str(theme_name or DEFAULT_THEME)
        theme = engine.load_theme(name)
        if theme is None:
            self.log.warning("预览主题失败：未知主题 %s", name)
            return False
        engine.apply_theme(theme, accent or None)
        return True

    def preview_theme(self, theme_name: str, accent: Any = "__keep__") -> bool:
        """只切主题（强调色保持草稿里的值）。返回是否成功。"""
        draft = self.current_draft()
        value = draft.get("accent_color") if accent == "__keep__" else accent
        return self._apply_theme_preview(theme_name, value)

    def preview_accent(self, text: Any) -> Dict[str, Any]:
        """只改强调色（同样只改内存）。非法输入返回错误键，不改任何东西。"""
        try:
            accent = normalize_accent(text)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "value": None}
        draft = self.current_draft()
        if self._apply_theme_preview(draft.get("theme_name"), accent):
            return {"ok": True, "error": "", "value": accent}
        return {"ok": False, "error": "theme_engine_unavailable", "value": None}

    def preview_language(self, code: str) -> bool:
        """只切语言（内存）。"""
        try:
            return bool(self.i18n().set_language(str(code or "")))
        except Exception as e:  # noqa: BLE001
            self.log.warning("预览语言 %s 失败: %s", code, e)
            return False

    def restore_preview(self, values: Optional[Dict[str, Any]] = None) -> None:
        """把预览还原成 `values`（默认=已保存的基线）。"""
        self.apply_preview(values if values is not None else (self._saved or self.snapshot()))

    # ─── 提交（B3：写盘 + 统一触发成就） ─────────────────────

    def commit(self) -> Dict[str, Any]:
        """把草稿写盘。返回 `{"ok", "changed", "applied", "errors", "achievements"}`。

        * 只写**真正变了**的键（`describe_changes`）—— 没变的键不去碰核心层，
          避免"点一次确定就重打一次镜像补丁 / 重存一次配置"；
        * 逐个键调用核心层已有的 `set_*`（写盘顺序 = `DRAFT_KEYS` 顺序，稳定可测）；
        * 全部写完后**统一触发成就**（B3）：改了的键才计数，同类一次会话只触发一次；
        * 写完重新取基线并清空草稿 —— 之后 `dirty()` 自然为假。
        """
        self.begin_draft()
        changes = self.changes()
        applied: List[str] = []
        errors: List[str] = []
        for key in DRAFT_KEYS:
            if key not in changes:
                continue
            try:
                self._write(key, changes[key])
            except Exception as e:  # noqa: BLE001 - 一个键写失败不该丢掉其它键
                self.log.error("写入设置项 %s 失败: %s", key, e)
                errors.append(key)
                continue
            applied.append(key)

        fired = self._fire_achievements(changes, applied)
        self._saved = self.snapshot()
        self._draft = {}
        ok = not errors
        self.log.info("设置已保存：%s（失败 %s，成就 %s）", applied, errors, fired)
        return {
            "ok": ok,
            "changed": bool(changes),
            "applied": applied,
            "errors": errors,
            "achievements": fired,
        }

    def _write(self, key: str, value: Any) -> None:
        """写一个键：优先核心层方法，缺席时直接写配置（各键的对应关系见模块头）。"""
        launcher = self._resolve_launcher()
        method_name = _WRITE_METHODS[key]
        method = getattr(launcher, method_name, None) if launcher is not None else None
        if callable(method):
            method(value)
            return
        cfg = self.config_obj()
        if cfg is None:
            raise RuntimeError("没有 launcher 也没有 config，无法写盘")
        setattr(cfg, _WRITE_ATTRS[key], value)
        save = getattr(cfg, "save_config", None)
        if callable(save):
            save()

    # ─── 成就（B3 / B4） ────────────────────────────────────

    def _fire_achievements(self, changes: Dict[str, Any], applied: Sequence[str]) -> List[str]:
        """按"改了的键"触发成就；同类一次会话只触发一次。返回本次新触发的 id。"""
        fired: List[str] = []
        for key in DRAFT_KEYS:
            if key not in changes or key not in applied:
                continue
            achievement = COMMIT_ACHIEVEMENTS.get(key)
            if achievement and self.trigger_achievement(achievement):
                fired.append(achievement)
            if key == "download_threads":
                # 条件式：旧实现 `_check_ach("advanced_multithread", threads > 1)`
                if self.check_achievement(THREADS_ACHIEVEMENT, threads_is_multithreaded(changes[key])):
                    fired.append(THREADS_ACHIEVEMENT)
        return fired

    def trigger_achievement(self, achievement_id: str, value: int = 1) -> bool:
        """触发一次成就。**同一 id 一次会话只真触发一次**（B3）。"""
        if achievement_id in self._fired:
            return False
        self._fired.add(achievement_id)
        try:
            engine = self._achievement_engine()
            if engine is not None:
                engine.update_progress(achievement_id, value=value)
                return True
        except Exception as e:  # noqa: BLE001 - 成就失败不该影响设置保存
            self.log.debug("触发成就 %s 失败: %s", achievement_id, e)
        return False

    def check_achievement(self, achievement_id: str, condition: bool) -> bool:
        """条件式成就（`check_and_unlock`）。条件不成立时**不消耗**会话额度。"""
        if not condition:
            return False
        if achievement_id in self._fired:
            return False
        self._fired.add(achievement_id)
        try:
            engine = self._achievement_engine()
            if engine is not None:
                engine.check_and_unlock(achievement_id, True)
                return True
        except Exception as e:  # noqa: BLE001
            self.log.debug("条件成就 %s 失败: %s", achievement_id, e)
        return False

    @staticmethod
    def _achievement_engine() -> Any:
        """成就引擎单例（惰性 import，与 `resource_service._trigger_ach` 同款）。"""
        try:
            from services.achievement_engine import get_achievement_engine

            return get_achievement_engine()
        except Exception:  # noqa: BLE001
            return None

    def fired_achievements(self) -> List[str]:
        """本次会话已触发的成就 id（测试与自检用）。"""
        return sorted(self._fired)

    # ─── 主题（M-07 / M-08 / M-10） ─────────────────────────

    def themes(self) -> List[Dict[str, Any]]:
        """可用主题（预设 + 用户导入），已拼好显示名。`user_tag` 由调用方给译文。"""
        engine = self.engine()
        if engine is None:
            return []
        try:
            raw = engine.get_available_themes()
        except Exception as e:  # noqa: BLE001 - 主题目录读不动不该让设置页崩掉
            self.log.warning("读取主题列表失败: %s", e)
            return []
        return [theme_view(item) for item in raw]

    def import_theme(self, path: str) -> Tuple[bool, str]:
        """导入主题 .json（B4：**立即执行**，不进草稿）。返回 `(成功, 消息)`。

        消息是**引擎给的中文句子**（旧实现把 `import_theme_from_file` 的返回值
        直接丢进状态栏），保持原样以免与旧提示不一致。
        """
        engine = self.engine()
        if engine is None:
            return False, "主题引擎不可用"
        ok, message = engine.import_theme_from_file(str(path or ""))
        if ok:
            # B4：动作型条目的成就当场触发
            self.trigger_achievement(IMPORT_ACHIEVEMENT)
            self.log.info("主题导入成功: %s", message)
        return bool(ok), str(message)

    def random_accent(self) -> str:
        """随机强调色（旧 `_on_random_accent` 调的就是这个静态方法）。"""
        return ThemeEngine.generate_random_accent()

    def version_accent(self, version_id: str) -> Dict[str, Any]:
        """某版本对应的动态强调色（M-11 的提示文案要用它来举例）。"""
        engine = self.engine()
        if engine is None:
            return {}
        info = engine.get_version_accent(str(version_id or "").strip())
        return dict(info) if info else {}

    # ─── 语言（M-06） ───────────────────────────────────────

    def available_languages(self) -> Dict[str, str]:
        """`{code: 名称}`（旧 `get_available_languages()`，四个语言）。"""
        try:
            return dict(self.i18n().get_available_languages())
        except Exception as e:  # noqa: BLE001
            self.log.warning("读取语言列表失败: %s", e)
            return {}

    def language_options(self) -> List[Dict[str, str]]:
        """语言下拉的数据源（按 code 排序，与 `Tr.availableLanguages` 同序）。"""
        return [{"code": code, "name": name} for code, name in sorted(self.available_languages().items())]

    def current_language(self) -> str:
        try:
            return str(self.i18n().get_current_language())
        except Exception:  # noqa: BLE001
            return str(self.snapshot().get("language", ""))

    # ─── Java（M-03 / M-04 / M-05） ─────────────────────────

    def java_modes(self) -> List[Dict[str, str]]:
        """Java 模式下拉的数据源（稳定 id + i18n 键；译文在 QML 侧取）。"""
        return [{"value": mode, "label_key": JAVA_MODE_LABEL_KEYS[mode]} for mode in JAVA_MODES]

    def java_scan_task(self, ctx: Any = None) -> Dict[str, Any]:
        """扫描系统 Java（走任务：冷缓存时 `scan_all()` 会翻目录，不能压在主线程上）。

        `ctx` 是 `Tasks` 桥的任务上下文（本任务不用进度，也不支持取消 ——
        扫描是一次性的只读动作，旧实现同样不可取消）。
        """
        launcher = self._resolve_launcher()
        if launcher is None:
            return {"rows": [], "count": 0, "error": "launcher_unavailable"}
        try:
            raw = launcher.scan_system_java() or []
        except Exception as e:  # noqa: BLE001 - 扫描失败要给界面一个能显示的原因
            self.log.error("扫描系统 Java 失败: %s", e)
            return {"rows": [], "count": 0, "error": str(e)}
        rows = [java_row_view(item) for item in raw]
        self.log.info("扫描到 %d 个 Java 运行时", len(rows))
        return {"rows": rows, "count": len(rows), "error": ""}

    # ─── 重启（M-24 / B5） ──────────────────────────────────

    def restart_argv(self) -> List[str]:
        """拉起新进程的命令行。

        * 打包态：`[sys.executable]`（旧实现 `subprocess.Popen([script])`）；
        * 开发态：`sys.orig_argv` 原样重放（这样 `main.py --ui qml` 与
          `main_qml.py` 两种入口都能正确重启）；拿不到时退回
          `[sys.executable, <仓库根>/main_qml.py]`。
        """
        if getattr(sys, "frozen", False):
            return [sys.executable]
        orig = list(getattr(sys, "orig_argv", None) or [])
        if len(orig) >= 2:
            return orig
        root = Path(__file__).resolve().parent.parent
        return [sys.executable, str(root / ENTRY_SCRIPT_NAME)]

    def restart_launcher(self) -> Dict[str, Any]:
        """拉起新进程并请求退出。返回 `{"ok", "argv", "error"}`。

        **与旧实现的一处有意偏离**：旧代码 spawn 失败时弹一个错误框、然后**照样**
        `parent.quit()`（用户看到的是一句话 + 启动器关门，游戏都没得启动）。
        这里改成"失败就**不退出**"，把错误交给界面显示 —— 退出清理链照常由用户
        自己关窗触发。这条偏离已登记进 `07-known-defects.md`。
        """
        argv = self.restart_argv()
        spawn = self._spawn or subprocess.Popen
        try:
            kwargs: Dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            spawn(argv, **kwargs)
        except Exception as e:  # noqa: BLE001 - 拉不起来就留在原地，别把用户关在门外
            self.log.error("重启启动器失败: %s", e)
            return {"ok": False, "argv": argv, "error": str(e)}
        self.log.info("已拉起新进程：%s", argv)
        return {"ok": True, "argv": argv, "error": ""}

    def request_quit(self) -> bool:
        """请求当前进程退出（由桥注入的实现负责真正的退出，如 QML 的 `Qt.quit()`）。"""
        if self._request_quit is None:
            return False
        try:
            self._request_quit()
            return True
        except Exception as e:  # noqa: BLE001
            self.log.warning("请求退出失败: %s", e)
            return False

    def set_quit_handler(self, handler: Optional[Callable[[], Any]]) -> None:
        """装配期注入"让界面退出"的出口（桥在 `bind()` 里调一次）。

        做成显式 setter 而不是让桥去写 `_request_quit`：那个属性是构造参数，
        桥上直接改私有属性会在改名时静默失效（而且读代码的人看不出这是契约）。
        """
        self._request_quit = handler

    def save_and_restart(self) -> Dict[str, Any]:
        """「保存并重启」（B5）：**先落盘草稿，再**拉新进程，最后请求退出。

        写盘失败时**不重启也不退出** —— 否则用户的改动既没保存、窗口也关了。
        """
        result = self.commit()
        if not result["ok"]:
            return {"ok": False, "stage": "commit", "errors": result["errors"], "argv": [], "error": ""}
        restart = self.restart_launcher()
        if not restart["ok"]:
            return {"ok": False, "stage": "spawn", "errors": [], "argv": restart["argv"], "error": restart["error"]}
        if not self.request_quit():
            return {
                "ok": False,
                "stage": "quit",
                "errors": [],
                "argv": restart["argv"],
                "error": "quit_unavailable",
            }
        return {"ok": True, "stage": "quit", "errors": [], "argv": restart["argv"], "error": ""}

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update(
            {
                "dirty": self.dirty(),
                "draft": self.current_draft(),
                "changes": sorted(self.changes()),
                "achievements": self.fired_achievements(),
            }
        )
        return info


#: 键 → 核心层写盘方法（`launcher/core.py` 里**已有**的 setter，一个都不新造）
_WRITE_METHODS: Dict[str, str] = {
    "minimize_on_game_launch": "set_minimize_on_game_launch",
    "mirror_enabled": "set_mirror_enabled",
    "download_threads": "set_download_threads",
    "language": "set_language",
    "theme_name": "set_theme_name",
    "accent_color": "set_accent_color",
    "dynamic_version_theme": "set_dynamic_version_theme",
    "java_mode": "set_java_mode",
    "java_custom_path": "set_java_custom_path",
}

#: 键 → 配置对象上的属性名（launcher 缺席时的回退路径；两者必须指向同一个键）
_WRITE_ATTRS: Dict[str, str] = {key: key for key in DRAFT_KEYS}


__all__ = [
    "ACCENT_INVALID_KEY",
    "COMMIT_ACHIEVEMENTS",
    "DEFAULT_THEME",
    "DRAFT_KEYS",
    "IMPORT_ACHIEVEMENT",
    "JAVA_MODES",
    "JAVA_MODE_LABEL_KEYS",
    "PREVIEW_KEYS",
    "SettingsService",
    "THREADS_ACHIEVEMENT",
    "THREADS_MAX",
    "THREADS_MIN",
    "clamp_threads",
    "describe_changes",
    "java_mode_shows_custom",
    "java_mode_shows_scan",
    "java_row_title",
    "java_row_view",
    "normalize_accent",
    "theme_view",
    "threads_is_multithreaded",
]
