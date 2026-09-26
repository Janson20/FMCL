"""整合包安装窗口 - 支持 Modrinth / MultiMC / CurseForge / HMCL / MCBBS / 通用压缩包

业务逻辑已搬到 ``services/modpack_service.py``（阶段 1 任务 1.8-B）：六种格式的
探测与元数据解析（``_load_mrpack_info`` 及其六个 ``_load_info_*`` 分支）、
"统一安装入口 → 旧格式分发"的调用编排、``current/max`` 的进度百分比口径。
本文件只剩纯界面部分（控件构建、``filedialog`` / ``messagebox`` / 通知、
``_mp_progress`` 轮询与 ``after`` 调度、``CTkBooleanVar``、i18n 文案、
``_trigger_ach``）；下面每个受影响的方法都退化成对服务的**薄委托**，
**方法名与签名保持不变**，界面可见行为不变。

D-95 的既有两个守卫原样保留：没有给 ``launcher.on_progress`` 赋值、
没有 ``_orig_on_progress`` 字段；``_poll_progress`` 里的
``hasattr(launcher_inst, "_mp_progress")`` 一字未改
（``tests/test_ui_cross_object_writes.py`` 钉着这三条）。
"""

import os
import threading
import tkinter.messagebox as messagebox
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from app.context import current_context
from services.modpack_service import ModpackService, progress_percent
from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification
from ui.i18n import _


def _get_modpack_service(owner: Any = None) -> ModpackService:
    """惰性取得服务实例（实现见 ``services/modpack_service.py``）。

    查找顺序："``owner.context.try_get`` → ``owner`` 上自造并缓存"。
    服务不需要 ``AppContext``，构造期也只保存参数，因此界面在
    ``__init__`` 之前（例如后台线程里）调用也安全。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(ModpackService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_modpack_service_fallback", None)
    if service is None:
        service = ModpackService()
        try:
            owner._modpack_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


def _trigger_ach(achievement_id: str, value: int = 1, trigger_type: str = "increment"):
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
    except Exception:
        pass


class ModpackInstallWindow(ctk.CTkToplevel):
    """整合包安装窗口 - 支持 .mrpack（Modrinth）和 .zip（MultiMC）格式"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.callbacks = callbacks
        self._mrpack_path: Optional[str] = None
        self._mrpack_info: Optional[Dict[str, Any]] = None
        self._optional_var_map: Dict[str, ctk.BooleanVar] = {}

        # 窗口配置
        self.title(_("mp_install_title"))
        self.geometry("580x480")
        self.minsize(520, 420)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 580, 480
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 阶段 1.22（D-95）：这里原本保存了一份 `launcher.on_progress` 并在安装结束后
        # "还原"它 —— 但**整个安装流程从未替换过它**，所以那段保存/还原没有任何作用；
        # 反而有副作用：若安装期间别人改了 `on_progress`，finally 会把它写回旧值
        # （`self._orig_on_progress` 为 None 时直接写成 None，等于把回调抹掉）。
        # 因此删掉这段跨对象写入；进度改由下面的轮询 `launcher._mp_progress` 呈现。
        # 注意：`modpack_server.py:58` 也有一个同样从未被使用的 `_orig_on_progress` 字段。
        self._launcher_instance: Optional[Any] = None

        self._build_ui()

    def _build_ui(self):
        """构建界面"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # ── 标题 ──
        ctk.CTkLabel(
            main_frame,
            text=_("mp_install_header"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text=_("mp_install_subtitle"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 15))

        # ── 文件选择区域 ──
        file_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        file_frame.pack(fill=ctk.X, pady=(0, 12))

        self._file_label = ctk.CTkLabel(
            file_frame,
            text=_("mp_no_file_selected"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            wraplength=440,
        )
        self._file_label.pack(padx=15, pady=(12, 5), fill=ctk.X)

        btn_row = ctk.CTkFrame(file_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        ctk.CTkButton(
            btn_row,
            text=_("mp_select_file_btn"),
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._select_file,
        ).pack(side=ctk.LEFT, padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text=_("mp_download_modrinth"),
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._open_modrinth_browser,
        ).pack(side=ctk.LEFT)

        # ── 整合包信息区域（初始隐藏）──
        self._info_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._info_name_label = ctk.CTkLabel(
            self._info_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        )
        self._info_name_label.pack(padx=15, pady=(12, 2), fill=ctk.X)

        self._info_summary_label = ctk.CTkLabel(
            self._info_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            wraplength=480,
        )
        self._info_summary_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._info_version_label = ctk.CTkLabel(
            self._info_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["success"],
            anchor=ctk.W,
        )
        self._info_version_label.pack(padx=15, pady=(0, 5), fill=ctk.X)

        # 可选文件
        self._optional_frame = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        self._optional_frame.pack(padx=15, pady=(0, 12), fill=ctk.X)

        # ── 进度区域（初始隐藏）──
        self._progress_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        ctk.CTkLabel(
            self._progress_frame,
            text=_("mp_install_progress_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(12, 8), anchor=ctk.W)

        self._mp_progress_label = ctk.CTkLabel(
            self._progress_frame,
            text=_("mp_prog_mrpack_init"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._mp_progress_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._mc_progress_label = ctk.CTkLabel(
            self._progress_frame,
            text=_("mp_prog_vanilla_init"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._mc_progress_label.pack(padx=15, pady=(0, 8), fill=ctk.X)

        self._progress_status = ctk.CTkLabel(
            self._progress_frame,
            text=_("mp_installing"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        self._progress_status.pack(padx=15, pady=(0, 5), fill=ctk.X)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, height=12, fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"]
        )
        self._progress_bar.pack(fill=ctk.X, padx=15, pady=(0, 12))
        self._progress_bar.set(0)

        # ── 底部按钮 ──
        self._bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self._bottom_frame.pack(fill=ctk.X, pady=(12, 0))

        self._install_btn = ctk.CTkButton(
            self._bottom_frame,
            text=_("modpack_start_install"),
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_install,
        )
        self._install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            self._bottom_frame,
            text=_("close"),
            height=40,
            width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

    # ─── 文件选择 ─────────────────────────────────────────────

    def _select_file(self):
        """选择整合包文件"""
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self,
            title=_("mp_select_dialog_title"),
            filetypes=[
                (_("mp_filetype_all_supported"), "*.mrpack;*.zip"),
                ("Modrinth (.mrpack)", "*.mrpack"),
                (_("mp_filetype_zip"), "*.zip"),
                (_("mp_filetype_all"), "*.*"),
            ],
        )
        if not path:
            return
        self._mrpack_path = path
        self._file_label.configure(text=os.path.basename(path), text_color=COLORS["text_primary"])
        # 后台加载整合包信息
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_reading_info"))
        self._run_in_thread(self._load_mrpack_info)

    def _open_modrinth_browser(self):
        """打开 Modrinth 整合包浏览窗口"""
        from ui.windows.modpack_browser import ModpackBrowserWindow

        ModpackBrowserWindow(self, on_modpack_selected=self._on_modrinth_downloaded)

    def _on_modrinth_downloaded(self, mrpack_path: str):
        """Modrinth 整合包下载完成后的回调"""
        self._mrpack_path = mrpack_path
        self._file_label.configure(text=os.path.basename(mrpack_path), text_color=COLORS["text_primary"])
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_reading_info"))
        self._run_in_thread(self._load_mrpack_info)

    def _load_mrpack_info(self):
        """读取整合包信息（后台线程）- 自动检测所有支持的格式（薄委托：services/modpack_service.py）

        "探测格式 → 选六个解析分支之一 → 打 ``format`` 标记"整体在服务层；
        本方法只做"读自己的字段、写自己的字段、把结果或错误丢回主线程"。
        检测日志 ``[Modpack Install] 检测格式: ...`` 跟着搬进服务（logzero 不是 GUI）。
        """
        try:
            path = self._mrpack_path
            if not path:
                return

            info = _get_modpack_service(self).load_pack_info(self.callbacks, path)
            self._mrpack_info = info
            self.after(0, lambda info=info: self._show_mrpack_info(info))

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._show_error(msg))

    def _load_info_mrpack(self, path: str) -> Dict[str, Any]:
        """Modrinth ``.mrpack`` 元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_mrpack(self.callbacks, path)

    def _load_info_multimc(self, path: str) -> Dict[str, Any]:
        """MultiMC 包元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_multimc(self.callbacks, path)

    def _load_info_curseforge(self, path: str) -> Dict[str, Any]:
        """CurseForge 包元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_curseforge(self.callbacks, path)

    def _load_info_hmcl(self, path: str) -> Dict[str, Any]:
        """HMCL 包元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_hmcl(self.callbacks, path)

    def _load_info_mcbbs(self, path: str) -> Dict[str, Any]:
        """MCBBS 包元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_mcbbs(self.callbacks, path)

    def _load_info_compress(self, path: str) -> Dict[str, Any]:
        """通用压缩包元数据（薄委托：services/modpack_service.py）"""
        return _get_modpack_service(self).load_info_compress(self.callbacks, path)

    def _clear_optional_frame(self):
        """清空可选文件区域的所有子控件"""
        for widget in self._optional_frame.winfo_children():
            widget.destroy()
        self._optional_var_map.clear()

    def _show_mrpack_info(self, info: Dict[str, Any]):
        """在 UI 中显示整合包信息（多格式支持）"""
        if not self.winfo_exists():
            return
        pack_format = info.get("format", "mrpack")

        self._info_name_label.configure(text=info.get("name", _("mp_unknown_modpack")))
        summary = info.get("summary", "")
        if summary:
            self._info_summary_label.configure(text=summary)
        else:
            self._info_summary_label.pack_forget()

        # 版本信息
        mc_version = info.get("mc_version", info.get("minecraftVersion", _("mp_unknown_version")))
        version_text = _("mp_mc_version", version=mc_version)

        if pack_format in ("multimc", "curseforge", "mcbbs"):
            lt = info.get("loader_type")
            if lt:
                lv = info.get("loader_version", "")
                version_text += f" | {lt} {lv}".strip()

        # 格式标签
        format_labels = {
            "mrpack": "Modrinth",
            "multimc": "MultiMC",
            "curseforge": "CurseForge",
            "hmcl": "HMCL",
            "mcbbs": "MCBBS",
            "compress": _("mp_format_compress"),
            "launcher_pack": _("mp_format_launcher_pack"),
        }
        format_display = format_labels.get(pack_format, pack_format)
        version_text += f" | [{format_display}]"

        self._info_version_label.configure(text=version_text)

        # 可选文件 / 组件列表
        self._clear_optional_frame()

        if pack_format == "multimc":
            self._show_mmc_components(info)
        elif pack_format in ("mrpack",):
            self._show_mrpack_optional_files(info)
        elif pack_format in ("curseforge",):
            self._show_cf_files(info)
        elif pack_format in ("mcbbs", "hmcl", "compress", "launcher_pack"):
            self._show_generic_components(info)

        self._info_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.NORMAL, text=_("modpack_start_install"))

    def _show_mmc_components(self, info: Dict):
        components = info.get("components", [])
        if components:
            ctk.CTkLabel(
                self._optional_frame,
                text=_("mp_optional_components"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))
            for comp in components:
                if isinstance(comp, dict):
                    comp_text = comp.get("name", comp.get("uid", "?"))
                else:
                    comp_text = str(comp)
                ctk.CTkLabel(
                    self._optional_frame,
                    text=f"  - {comp_text}",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    text_color=COLORS["text_primary"],
                    anchor=ctk.W,
                ).pack(anchor=ctk.W, pady=1)

    def _show_mrpack_optional_files(self, info: Dict):
        optional_files = info.get("optionalFiles", [])
        if optional_files:
            ctk.CTkLabel(
                self._optional_frame,
                text=_("mp_optional_components"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))
            for opt_name in optional_files:
                var = ctk.BooleanVar(value=False)
                self._optional_var_map[opt_name] = var
                ctk.CTkCheckBox(
                    self._optional_frame,
                    text=opt_name,
                    variable=var,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["accent"],
                    hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_primary"],
                ).pack(anchor=ctk.W, pady=1)

    def _show_cf_files(self, info: Dict):
        """显示 CurseForge 可选文件"""
        required_count = info.get("required_files", 0)
        optional_count = info.get("optional_files", 0)
        if required_count or optional_count:
            ctk.CTkLabel(
                self._optional_frame,
                text=_("mp_cf_files_info", required=required_count, optional=optional_count),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))

    def _show_generic_components(self, info: Dict):
        """显示通用组件信息（HMCL / MCBBS / 压缩包等）"""
        components = info.get("components", [])
        description = info.get("description", "")
        if description:
            ctk.CTkLabel(
                self._optional_frame,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
                wraplength=480,
            ).pack(fill=ctk.X, pady=(0, 3))
        if components:
            ctk.CTkLabel(
                self._optional_frame,
                text=_("mp_optional_components"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(3, 3))
            for comp in components:
                if isinstance(comp, dict):
                    comp_text = comp.get("name", comp.get("uid", "?"))
                else:
                    comp_text = str(comp)
                ctk.CTkLabel(
                    self._optional_frame,
                    text=f"  - {comp_text}",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    text_color=COLORS["text_primary"],
                    anchor=ctk.W,
                ).pack(anchor=ctk.W, pady=1)

    def _show_error(self, msg: str):
        """显示错误"""
        if not self.winfo_exists():
            return
        self._file_label.configure(text=_("mp_invalid_file"), text_color=COLORS["error"])
        self._install_btn.configure(state=ctk.DISABLED, text=_("modpack_start_install"))
        messagebox.showerror(_("error"), _("mp_read_error", error=msg), parent=self)

    # ─── 安装 ─────────────────────────────────────────────────

    def _on_install(self):
        """开始安装"""
        if not self._mrpack_path or not self._mrpack_info:
            return

        # 收集可选文件
        optional_files = [name for name, var in self._optional_var_map.items() if var.get()]

        # 切换到进度视图
        self._info_frame.pack_forget()
        self._progress_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_installing"))
        self._close_btn.pack_forget()

        self._run_in_thread(self._do_install, optional_files)

    def _do_install(self, optional_files: list):
        """执行安装（后台线程）- 使用统一 install_modpack 入口"""
        launcher = getattr(self.callbacks.get("install_modpack"), "__self__", None)
        if launcher is None:
            launcher = getattr(self.callbacks.get("install_mrpack"), "__self__", None)
        if launcher:
            self._launcher_instance = launcher

        self._polling = True

        def _poll_progress():
            if not self._polling or not self.winfo_exists():
                return
            try:
                launcher_inst = self._launcher_instance
                if launcher_inst and hasattr(launcher_inst, "_mp_progress"):
                    mp = launcher_inst._mp_progress
                    phase = mp.get("phase", "")
                    overall = mp.get("overall", 0)

                    if phase == "detected":
                        self._progress_status.configure(text=_("mp_format_detected", name=mp.get("format_name", "?")))
                    elif phase == "parallel":
                        mp_data = mp.get("mrpack", {})
                        mc_data = mp.get("vanilla", {})
                        # current/max 的百分比口径与服务端窗口共用同一份实现
                        mp_pct = progress_percent(mp_data)
                        mc_pct = progress_percent(mc_data)
                        self._mp_progress_label.configure(
                            text=_("mp_prog_mrpack_label", pct=f"{mp_pct:.0f}", label=mp_data.get("label", ""))
                        )
                        self._mc_progress_label.configure(
                            text=_("mp_prog_vanilla_label", pct=f"{mc_pct:.0f}", label=mc_data.get("label", ""))
                        )
                    else:
                        self._progress_status.configure(
                            text=mp.get("status_text", mp.get("format_name", _("mp_installing")))
                        )

                    self._progress_bar.set(overall)

                    if phase == "loader" or phase == "done":
                        self._polling = False
                        return
            except Exception:
                pass

            if self._polling:
                self.after(200, _poll_progress)

        self.after(0, _poll_progress)

        # 使用统一安装入口（"统一入口 → 旧格式分发"的判定与两次调用都在服务层）
        try:
            # ``pack_format`` 在原文里只在"没有统一入口"的分支内取值；这里提前算好
            # 传进去 —— 它是一次纯读取（读自己的 ``_mrpack_info``），无副作用也不会抛，
            # 因此对可达输入完全等价。
            pack_format = self._mrpack_info.get("format", "mrpack") if self._mrpack_info else "mrpack"
            success, result = _get_modpack_service(self).call_install_entry(
                self.callbacks, self._mrpack_path, optional_files, pack_format
            )
            self.after(0, lambda s=success, r=result: self._on_install_done(s, r))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._on_install_done(False, msg))
        finally:
            self._polling = False
            # 阶段 1.22（D-95）：这里原本"还原 launcher.on_progress"。那段代码是
            # 死逻辑 + 有害副作用（从未替换过它，却会在别人的回调变更后把它写回旧值
            # 甚至 None）。进度呈现靠轮询 `launcher._mp_progress`，不需要碰这个回调。
            #
            # 仍未解决的部分（已登记在 07-known-defects.md D-95）：客户端安装窗口与
            # 服务端安装窗口**都会**轮询同一个 `launcher._mp_progress` 字典，
            # 两者同时开着就会互相显示对方的进度。彻底修法需要在 `launcher/mrpack.py`
            # 里给进度字典加一个"本次安装"的会话标识、由调用方带上再比对；
            # 那属于 1.17 改接线（或阶段 2 用 TaskRunner 替掉这套临时进度字典）的范围。

    def _on_install_done(self, success: bool, result: str):
        """安装完成"""
        if not self.winfo_exists():
            return
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        if success:
            self._progress_status.configure(text=_("mp_install_done"), text_color=COLORS["success"])
            self._install_btn.configure(
                text=_("mp_install_done_btn", result=result), fg_color=COLORS["success"], state=ctk.DISABLED
            )
            show_notification("📦", _("notify_modpack_installed"), result, notify_type="success")
            _trigger_ach("modder_lazy")
            # 刷新主窗口版本列表
            self.after(0, self.master._refresh_versions)
            messagebox.showinfo(_("mp_install_done_title"), _("mp_install_done_msg", result=result), parent=self)
            self.destroy()
        else:
            self._progress_status.configure(text=_("mp_install_failed_status"), text_color=COLORS["error"])
            self._install_btn.configure(text=_("mp_install_retry"), state=ctk.NORMAL)
            show_notification("📦", _("notify_modpack_failed"), str(result)[:50], notify_type="error")
            messagebox.showerror(_("mp_install_failed_status"), _("mp_install_error", error=result), parent=self)

    # ─── 工具 ─────────────────────────────────────────────────

    def _run_in_thread(self, func, *args):
        """在后台线程运行函数"""
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()
