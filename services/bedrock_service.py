"""基岩版服务 —— `ui/app_bedrock.py`（`BedrockMixin`，726 行 / 28 方法）里那些
**本质是逻辑、只是恰好写在界面类里**的代码（阶段 1 任务 1.11）。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。界面效果一律通过调用方传进来的 sink（``status_cb`` /
``code_cb`` / ``done_cb``）或返回值完成。服务自身：

- 不弹窗、不起线程、不调 ``after``、不碰控件；
- 不 ``import ui.*``、不 ``import ui.i18n``（**文案一律留界面**）；
- 对 GUI 的依赖为 0，由 ``scripts/check_services_purity.py`` 持续守住。

## 切缝落在哪（四段）

1. **纯逻辑**：可用版本的过滤（关键字 + UWP/GDK 筛选）、分页（每页 20 条、
   页码夹紧、切片）、release 计数、已安装版本里按 name 查找。
   这些从 `_on_bedrock_filter_change` / `_get_bedrock_total_pages` /
   `_render_bedrock_available` / `_handle_bedrock_task` / `_on_bedrock_launch`
   里逐字搬出，零改写。
2. **前置检查的判定**：MCAPPX 条款是否已同意、GDK 版是否需要先装 .NET 10 SDK
   （并给出官方下载直链）、GDK 版启动前闭源认证组件是否就绪。
   "要不要问用户"由界面决定，服务只回答"缺什么"。
3. **编排**：安装 / 启动 / 删除各自那一次回调解调 + 结果归一化；MSA 设备码
   登录的完整流程（缓存快速路径 → 互斥锁 → 设备码 → 轮询 → 落盘）；
   在资源管理器里打开版本根目录。界面只负责把结果映射成 ``_task_queue`` 任务。
4. **锁**：原来的两个**模块级**锁（启动防重入 `_BEDROCK_LAUNCH_LOCK`、
   设备码登录互斥 `_MSA_LOGIN_LOCK`）搬到这里，仍是模块级（进程范围内唯一），
   语义不变；`ui/app_bedrock.py` 保留同名别名以便旧引用继续解析。

## 留在界面（不搬）的部分

控件创建与布局、主题色登记（`_theme_refs`）、列表渲染与空态、
`winfo_children()` 清理、`_run_in_thread` 调度、**全部 `_("...")` 文案**、
以及四个"问用户"的对话框：MCAPPX 条款、.NET 10 缺失、闭源组件同意、
删除确认、MSA 设备码提示框。

## 离线可测性

所有外部动作都可注入替身（默认值就是真实实现，行为不变）：

======================  ==========================================
注入参数                 对应的真实实现
======================  ==========================================
``components``          ``launcher.bedrock.components``（is_ready / download）
``dotnet``              ``launcher.bedrock.dotnet``（has_sdk10 / sdk_download_url）
``xbox_signed_in``      ``launcher.bedrock.env.is_xbox_signed_in``
``msauth``              ``launcher.bedrock.msauth``（设备码登录四件套）
``config_provider``     ``config.config``（条款 / 组件同意标记）
``startfile``           ``os.startfile``（打开版本根目录）
======================  ==========================================

`launcher/bedrock/` 整包实测**零控件调用**，所以本模块只做编排，不重复实现
下载 / 解压 / 注册 / 启动 —— 那些已经在 `launcher.bedrock` 里了。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from logzero import logger

from services.base import Service

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

#: 可用版本列表的每页条数（原 `self._bedrock_page_size = 20`，对齐游戏标签页）。
DEFAULT_PAGE_SIZE: int = 20

#: UWP / GDK 两种安装形态。GDK 版额外依赖 .NET 10 SDK 与闭源认证组件。
BUILD_TYPE_UWP: str = "UWP"
BUILD_TYPE_GDK: str = "GDK"

#: 版本条目里"正式版"的类型值（`_handle_bedrock_task` 用它统计 release 数量）。
TYPE_RELEASE: str = "release"

# ═══════════════════════════════════════════════════════════════════
# 进程级互斥锁（原来在 `ui/app_bedrock.py` 的模块级）
# ═══════════════════════════════════════════════════════════════════

#: 启动流程互斥锁：同一时刻只允许一个基岩版启动流程（防重复点击/并发触发）。
LAUNCH_LOCK = threading.Lock()

#: 登录流程互斥锁：多线程并发点击时只允许一个设备码登录流程。
MSA_LOGIN_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════
# 纯逻辑（逐字搬出，零改写）
# ═══════════════════════════════════════════════════════════════════


def total_pages(count: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """原 `_get_bedrock_total_pages`（`:312-315`）逐字搬出。

    ``count`` 为 0 时返回 1（空列表也显示成 "1/1"），否则至少 1 页。
    """
    if not count:
        return 1
    return max(1, (count + page_size - 1) // page_size)


def clamp_page(page: int, pages: int) -> int:
    """原 `_render_bedrock_available` 里那两行夹紧逻辑（`:323-326`）逐字搬出。"""
    if page > pages:
        return pages
    if page < 1:
        return 1
    return page


@dataclass(frozen=True)
class PageSlice:
    """一页的切片结果（页码已按总页数夹紧）。

    ``page`` 是**夹紧后**的页码 —— 界面必须把它写回 ``self._bedrock_page``，
    这正是原文"先把页码改掉、再渲染"的顺序（列表变短后停在越界页会自愈）。
    """

    items: List[Dict[str, Any]]
    page: int
    total_pages: int


def paginate(
    filtered: List[Dict[str, Any]], page: int, page_size: int = DEFAULT_PAGE_SIZE
) -> PageSlice:
    """原 `_render_bedrock_available` 的"算总页数 → 夹紧页码 → 切片"三步（`:322-336`）。

    原文把这三步夹在两次控件操作之间（先清空列表、再配置页码标签、最后切片
    渲染）；服务只做纯计算，调用方按原顺序使用 ``PageSlice`` 即可。
    """
    pages = total_pages(len(filtered), page_size)
    current = clamp_page(page, pages)
    start = (current - 1) * page_size
    return PageSlice(filtered[start : start + page_size], current, pages)


def filter_versions(
    available: List[Dict[str, Any]],
    keyword: str,
    build_filter: str,
    all_label: str,
) -> List[Dict[str, Any]]:
    """原 `_on_bedrock_filter_change`（`:300-308`）的列表推导逐字搬出。

    Args:
        available: ``self.bedrock_available``
        keyword: ``self.bedrock_search_var.get()`` 的**原始**取值
            （``.strip().lower()`` 属于逻辑，在服务里做）
        build_filter: ``self.bedrock_filter_var.get()``
        all_label: ``_("bedrock_filter_all")`` —— 服务不 import ``ui.i18n``，
            "全部"这个档位的**显示文案**由界面注入；这样
            `scripts/check_i18n.py` 仍能在界面文件里看到那个字面量键，
            键的"存在性 / 占位符 / 调用点缺参"三项检查不会静默失效。
    """
    kw = keyword.strip().lower()
    return [
        v
        for v in available
        if (not kw or kw in v.get("version", "").lower())
        and (build_filter == all_label or v.get("build_type") == build_filter)
    ]


def count_release(available: List[Dict[str, Any]]) -> int:
    """原 `_handle_bedrock_task` 里 ``release_count`` 的表达式（`:658`）逐字搬出。"""
    return len([v for v in available if v.get("type") == TYPE_RELEASE])


def find_installed(
    installed: List[Dict[str, Any]], name: str
) -> Optional[Dict[str, Any]]:
    """原 `_on_bedrock_launch` / `_launch_bedrock_worker` 里的 ``next(...)``（`:504`、`:548`）。

    找不到返回 None（原文用 ``next(gen, None)``）。
    """
    return next((v for v in installed if v.get("name") == name), None)


# ═══════════════════════════════════════════════════════════════════
# 结果类型（界面据此选文案，服务不碰 i18n）
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class InstallOutcome:
    """一次安装调用的归一化结果。

    对应原文 `_install_bedrock_worker` 里的三分支：

    ==========  =====================================================
    分支         原文
    ==========  =====================================================
    成功         ``success and isinstance(info, dict)`` →
                 ``info.get("name", version)`` 作为显示名
    失败         ``str(info)`` 作为错误文本
    ==========  =====================================================

    ``display`` 同时承担"成功时的版本名"与"失败时的错误文本"两种含义，
    与原文那个 ``(version, success, info)`` 三元组逐位对应。
    """

    version: str
    success: bool
    display: str


@dataclass(frozen=True)
class RemoveOutcome:
    """一次删除调用的归一化结果（原文 ``(name, success, msg)``）。"""

    name: str
    success: bool
    message: str


@dataclass(frozen=True)
class LaunchOutcome:
    """一次启动调用的归一化结果（原文 ``(name, success, msg)``）。

    `run_launch` 返回 ``None`` 表示**这次触发被防重入锁挡掉了**（原文
    "已有一个启动流程在跑"时直接 return，不投递任何任务）—— 界面必须
    据此**不刷新状态栏**，与原文一致。
    """

    name: str
    success: bool
    message: str


@dataclass(frozen=True)
class DotnetCheck:
    """.NET 10 SDK 前置检查结果。

    ``has_sdk10`` 为 True 时 ``download_url`` 是空串（原文此时直接返回，
    根本不会去取直链）。为 False 时 ``download_url`` 已经是**退路之后**的
    地址：取直链失败就退回 ``dotnet.SDK_DOWNLOAD_PAGE`` 下载页。
    """

    has_sdk10: bool
    download_url: str


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class BedrockService(Service):
    """基岩版业务服务：列表/过滤/分页、安装/启动/删除编排、前置检查。

    约定（与 `services/server_service.py`、`services/resource_service.py` 一致）：

    - 构造期只做赋值，不读盘、不连网、不建线程；可脱离 `AppContext` 单独
      实例化（``BedrockService()`` 在 pytest 里就能用）。
    - 需要用户交互时，由调用方传 sink（``status_cb`` / ``code_cb`` /
      ``done_cb``）或读返回值；服务自身不 import 任何 GUI。
    - **异常不翻译**：服务把底层异常原样抛出，日志与提示文案留在界面 ——
      这样界面方法里那些 ``try/except`` 的边界与原文逐字相同
      （`_on_bedrock_launch` 与 `_ensure_dotnet10_for_gdk` 的"异常即放行"
      就是靠这个边界成立的）。

    **线程契约**：`run_launch` / `msa_login_flow` 必须在 worker 线程里调用
    （它们会阻塞在 ``LAUNCH_LOCK`` / ``MSA_LOGIN_LOCK`` 以及设备码轮询上）；
    起线程仍然是界面的事，服务不自己起线程。
    """

    name = "bedrock"
    label = "基岩版"

    def __init__(
        self,
        context: Any = None,
        *,
        components: Any = None,
        dotnet: Any = None,
        xbox_signed_in: Optional[Callable[[], bool]] = None,
        msauth: Any = None,
        config_provider: Optional[Callable[[], Any]] = None,
        startfile: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """构造期只保存注入项；``None`` 一律表示"用真实实现"（惰性 import）。

        惰性 import 是**刻意**的：原文的这些 import 全都写在函数体里
        （`from launcher.bedrock import dotnet` 等），把它们提到模块顶部会改变
        启动期的导入顺序与代价。
        """
        super().__init__(context)
        self._components_module = components
        self._dotnet_module = dotnet
        self._xbox_signed_in = xbox_signed_in
        self._msauth_module = msauth
        self._config_provider = config_provider
        self._startfile = startfile

    # ─── 惰性依赖解析（私有：这是注入管道，不是业务 API）────

    def _resolve_components(self):
        """``launcher.bedrock.components``（`is_ready` / `download`）。"""
        if self._components_module is not None:
            return self._components_module
        from launcher.bedrock import components

        return components

    def _resolve_dotnet(self):
        """``launcher.bedrock.dotnet``（`has_sdk10` / `sdk_download_url` / `SDK_DOWNLOAD_PAGE`）。"""
        if self._dotnet_module is not None:
            return self._dotnet_module
        from launcher.bedrock import dotnet

        return dotnet

    def _resolve_msauth(self):
        """``launcher.bedrock.msauth``（设备码登录四件套）。"""
        if self._msauth_module is not None:
            return self._msauth_module
        from launcher.bedrock import msauth

        return msauth

    def _resolve_signed_in(self) -> Callable[[], bool]:
        """``launcher.bedrock.env.is_xbox_signed_in``（返回**函数本身**，由调用方再调）。

        **必须**用 ``from launcher.bedrock.env import is_xbox_signed_in`` 这种
        "从子模块里取名字"的形式：原文那条 ``except ImportError``（`:563`）
        同时兜住了"子模块不存在"与"名字不存在"两种情况。换成
        ``from launcher.bedrock import env; env.is_xbox_signed_in`` 会把后者
        变成 ``AttributeError``，**从 ``except ImportError`` 里漏出去** ——
        本该"放行启动"的分支会变成启动失败。
        """
        if self._xbox_signed_in is not None:
            return self._xbox_signed_in
        from launcher.bedrock.env import is_xbox_signed_in

        return is_xbox_signed_in

    def _resolve_config(self):
        """全局配置对象（``config.config``，与旧代码同一份实例）。"""
        if self._config_provider is not None:
            return self._config_provider()
        from config import config

        return config

    # ─── 版本列表 / 过滤 / 分页（纯逻辑，无副作用）──────────

    def total_pages(self, count: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
        """总页数（原 `_get_bedrock_total_pages`）。"""
        return total_pages(count, page_size)

    def paginate(
        self,
        filtered: List[Dict[str, Any]],
        page: int,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> PageSlice:
        """当前页的切片（页码已夹紧）。"""
        return paginate(filtered, page, page_size)

    def filter_versions(
        self,
        available: List[Dict[str, Any]],
        keyword: str,
        build_filter: str,
        all_label: str,
    ) -> List[Dict[str, Any]]:
        """关键字 + UWP/GDK 过滤（原 `_on_bedrock_filter_change`）。"""
        return filter_versions(available, keyword, build_filter, all_label)

    def count_release(self, available: List[Dict[str, Any]]) -> int:
        """正式版（``type == "release"``）数量（原 `_handle_bedrock_task`）。"""
        return count_release(available)

    # 记录：模块级 `find_installed()` 是本文件内部（`run_launch` /
    # `component_setup_needed`）使用的纯函数，**不**在服务上再包一层方法 ——
    # 那层包装没有任何生产调用方，属于死 API（验证脚本会守这一点）。

    # ─── 数据加载（回调取值，失败原样抛出）──────────────────

    def load_installed(self, callbacks: Dict[str, Callable]) -> List[Dict[str, Any]]:
        """原 `_load_bedrock_installed` 的取值部分（`:286`）。

        原文在取值外面套了 ``try/except``：失败时 ``logger.error`` + 投递
        ``bedrock_load_error`` 任务。那段**留界面**（日志文案与队列协议都是
        界面的事），本方法只负责"调回调、把结果给出去"，异常不翻译。
        """
        return callbacks.get("bedrock_get_installed_versions", lambda: [])()

    def load_available(self, callbacks: Dict[str, Callable]) -> List[Dict[str, Any]]:
        """原 `_load_bedrock_available` 的取值部分（`:294`）。同上，异常不翻译。"""
        return callbacks.get("bedrock_get_available_versions", lambda: [])()

    def root_dir(self, callbacks: Dict[str, Callable]) -> str:
        """原 `_open_bedrock_folder` 的第一行（`:633`）。

        记录现状、疑为缺陷：原文这一行**在 try 之外**，回调抛异常时会直接
        冒泡出 `_open_bedrock_folder`（没有任何地方接住）。本方法保持同样的
        边界：异常照抛，界面的 ``try`` 只包住"打开目录"那一步。
        """
        return callbacks.get("bedrock_get_root", lambda: "")()

    # ─── 前置检查 ───────────────────────────────────────────

    def terms_accepted(self) -> bool:
        """MCAPPX 版本库条款是否已同意（原 `_on_bedrock_install` 开头）。"""
        return bool(self._resolve_config().bedrock_terms_accepted)

    def mark_terms_accepted(self) -> None:
        """记录"已同意条款"（原文是"改标记 + `save_config()`"两步）。"""
        config = self._resolve_config()
        config.bedrock_terms_accepted = True
        config.save_config()

    def component_download_consented(self) -> bool:
        """闭源认证组件下载是否已同意（原 `_confirm_bedrock_components` 开头）。"""
        return bool(self._resolve_config().bedrock_components_consented)

    def mark_components_consented(self) -> None:
        """记录"已同意组件下载"。"""
        config = self._resolve_config()
        config.bedrock_components_consented = True
        config.save_config()

    def requires_dotnet10(self, version_info: Dict[str, Any]) -> bool:
        """该版本是否需要 .NET 10 SDK 前置（原 `_on_bedrock_install` 的半个条件，`:452`）。"""
        return version_info.get("build_type") == BUILD_TYPE_GDK

    def check_dotnet10(self) -> DotnetCheck:
        """.NET 10 SDK 检测 + （缺失时的）官方下载直链（原 `_ensure_dotnet10_for_gdk`）。

        原文的 "取直链失败就退回下载页" 两步搬到这里；弹窗与
        ``webbrowser.open`` **留界面**。检测本身抛异常时照抛，由界面的
        ``except`` 走"异常即放行下载"那条老路。
        """
        dotnet = self._resolve_dotnet()
        if dotnet.has_sdk10():
            return DotnetCheck(True, "")
        url = dotnet.SDK_DOWNLOAD_PAGE
        try:
            url = dotnet.sdk_download_url()
        except Exception as e:
            logger.warning(f"获取 .NET SDK 下载直链失败（改用下载页）: {e}")
        return DotnetCheck(False, url)

    def component_setup_needed(
        self, callbacks: Dict[str, Callable], name: str
    ) -> bool:
        """启动前是否需要先征得"下载闭源认证组件"的同意（原 `_on_bedrock_launch`，`:502-509`）。

        仅当该版本存在、且是 GDK 版、且组件未就绪时返回 True。
        原文这段包在 ``try/except`` 里、失败即"放行启动"，那个边界留界面。
        """
        installed = self.load_installed(callbacks)
        info = find_installed(installed, name)
        if info and info.get("build_type") == BUILD_TYPE_GDK:
            return not self._resolve_components().is_ready()
        return False

    # ─── 安装 / 删除编排 ────────────────────────────────────

    def install_version(
        self, callbacks: Dict[str, Callable], version: str, name: str = ""
    ) -> InstallOutcome:
        """调一次 ``bedrock_install_version`` 并归一化结果（原 `_install_bedrock_worker`）。

        原文的硬下标访问 ``callbacks["bedrock_install_version"]`` 一字不改
        （`scripts/check_callback_keys.py` 仍按 hard 读取统计它）。第二个位置
        参数原文传的是空串（``name=""``），保持默认值一致。

        异常不翻译：原文这里的 ``except Exception`` 会 ``logger.error`` 并投递
        "安装失败"任务，那段留界面。
        """
        success, info = callbacks["bedrock_install_version"](version, name)
        if success and isinstance(info, dict):
            return InstallOutcome(version, True, info.get("name", version))
        return InstallOutcome(version, False, str(info))

    def remove_version(
        self, callbacks: Dict[str, Callable], name: str
    ) -> RemoveOutcome:
        """调一次 ``bedrock_remove_version`` 并归一化结果（原 `_remove_bedrock_worker`）。"""
        success, msg = callbacks["bedrock_remove_version"](name)
        return RemoveOutcome(name, success, msg)

    def open_in_explorer(self, path: str) -> None:
        """用系统默认方式打开目录（原 `os.startfile(root)`，`:639`）。

        ``os.startfile`` 只有 Windows 有；做成可注入之后测试可以在任何平台上
        跑，且默认值就是真实实现（行为不变）。异常照抛 —— 原文的
        ``except Exception`` + ``logger.warning`` 留在界面。
        """
        opener = self._startfile
        if opener is None:
            import os

            opener = os.startfile
        opener(path)

    # ─── 启动编排 ───────────────────────────────────────────

    def run_launch(
        self,
        callbacks: Dict[str, Callable],
        name: str,
        *,
        status_cb: Optional[Callable[[str], None]] = None,
        download_notice: str = "",
        login_status_cb: Optional[Callable[[str], None]] = None,
        login_code_cb: Optional[Callable[[str, str], None]] = None,
        login_done_cb: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[LaunchOutcome]:
        """完整的启动流程（原 `_launch_bedrock_worker`，`:539-571`）。

        步骤与原文逐条对应：抢防重入锁 → 查该版本是否 GDK → 组件缺失则通知
        并下载 → 系统无 Xbox 身份则走 MSA 设备码登录 → 调
        ``bedrock_launch_version(name, "", access_token)``。

        Args:
            status_cb: 组件下载阶段的状态文本 sink（原文直接投
                ``bedrock_component_status`` 任务）。
            download_notice: **开始下载组件前**要播报的那句文案。服务不碰
                i18n，所以文案由界面注入（``_("bedrock_component_downloading")``），
                服务只决定**时机**。空串表示不播报。
            login_status_cb / login_code_cb / login_done_cb: 转发给
                :meth:`msa_login_flow` 的三个 sink。

        Returns:
            归一化的启动结果；``None`` 表示这次触发被防重入锁挡掉
            （原文此时只 ``logger.warning`` 然后 return，**不投递任何任务**，
            界面据此不刷新状态栏）。

        Raises:
            底层异常原样抛出（原文的 ``except Exception`` +
            ``logger.error`` + 投递失败任务留界面）。``finally`` 里一定会释放
            防重入锁，所以异常路径不会把锁漏掉。
        """
        if not LAUNCH_LOCK.acquire(blocking=False):
            logger.warning(f"基岩版启动已在执行中，忽略重复触发: {name}")
            return None
        try:
            # GDK 版且系统无 Xbox 身份时，先走微软账户设备码登录（认证注入）
            access_token = ""
            installed = self.load_installed(callbacks)
            info = find_installed(installed, name)
            if info and info.get("build_type") == BUILD_TYPE_GDK:
                # 闭源认证组件按需下载（已由主线程征得用户同意）
                components = self._resolve_components()
                if not components.is_ready():
                    if status_cb is not None and download_notice:
                        status_cb(download_notice)
                    components.download(status_cb=status_cb)
                try:
                    signed_in = self._resolve_signed_in()
                    if not signed_in():
                        access_token = self.msa_login_flow(
                            status_cb=login_status_cb,
                            code_cb=login_code_cb,
                            done_cb=login_done_cb,
                        )
                except ImportError:
                    pass
            success, msg = callbacks["bedrock_launch_version"](name, "", access_token)
            return LaunchOutcome(name, success, msg)
        finally:
            LAUNCH_LOCK.release()

    def msa_login_flow(
        self,
        *,
        status_cb: Optional[Callable[[str], None]] = None,
        code_cb: Optional[Callable[[str, str], None]] = None,
        done_cb: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """获取微软账户 access_token（原 `_msa_login_flow`，`:573-608`）。

        优先用缓存的凭证自动刷新，无缓存才走设备码登录。多线程并发点击时
        互斥：只允许一个线程发起设备码登录，其余线程持锁后重试缓存并复用，
        避免弹出多个登录窗口。

        原文的三处界面动作改成三个 sink：
        ``bedrock_msa_status`` → ``status_cb``、
        ``bedrock_msa_code`` → ``code_cb``、
        ``bedrock_msa_done`` → ``done_cb``。

        **线程契约**：会阻塞在 ``MSA_LOGIN_LOCK`` 与设备码轮询上，必须在
        worker 线程里调用。原文对 ``status_cb`` 传的是"总是可调用的 lambda"，
        本方法对 ``None`` 做了兜底（等价：不传就是没人听）。
        """
        msauth = self._resolve_msauth()

        def _status(text: str) -> None:
            if status_cb is not None:
                status_cb(text)

        # 快速路径：已有缓存凭证时直接使用，无需等待锁
        cached = msauth.get_cached_access_token(status_cb=_status)
        if cached:
            return cached

        with MSA_LOGIN_LOCK:
            # 持锁后再次尝试缓存（可能前面的线程刚完成登录并写入了缓存）
            cached = msauth.get_cached_access_token(status_cb=_status)
            if cached:
                return cached

            device = msauth.request_device_code()
            code = device.get("user_code", "")
            uri = device.get("verification_uri") or "https://www.microsoft.com/link"
            # 主线程弹窗提示 + 打开浏览器
            if code_cb is not None:
                code_cb(code, uri)
            token = msauth.poll_device_code(
                device["device_code"],
                interval=int(device.get("interval", 5)),
                status_cb=_status,
            )
            msauth.save_credentials(
                token.get("access_token", ""), token.get("refresh_token", "")
            )
            if done_cb is not None:
                done_cb(token.get("access_token", ""), "")
            return token["access_token"]


__all__ = [
    "BedrockService",
    "DotnetCheck",
    "InstallOutcome",
    "LAUNCH_LOCK",
    "LaunchOutcome",
    "MSA_LOGIN_LOCK",
    "PageSlice",
    "RemoveOutcome",
    "clamp_page",
    "count_release",
    "filter_versions",
    "find_installed",
    "paginate",
    "total_pages",
]
