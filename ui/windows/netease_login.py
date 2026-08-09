"""网易云音乐扫码登录窗口

登录流程与 YesPlayMusic 一致（官方扫码接口，走 eapi 通道）:
    1. 请求 unikey，生成二维码内容 https://music.163.com/login?codekey=<unikey>
    2. 轮询扫码状态: 800 已过期（自动刷新）/ 801 等待扫码 / 802 已扫码待确认
    3. 803 授权成功，解析响应携带的登录 Cookie 应用到网易云音源会话
       （支持 VIP 歌曲播放与 VIP 歌词），并通过回调持久化保存

线程模型:
    - 网络请求（unikey 获取、状态轮询）全部在后台线程执行
    - 后台线程绝不直接操作 Tk 组件，只向队列投递事件
    - 主线程调度器（after 轮询）统一处理事件并更新 UI，
      避免 tkinter 跨线程调用在存在多个 Tk 根时被静默丢弃/报错
      （"image pyimageN doesn't exist" 的根源之一）
    - 二维码使用显式绑定窗口自身 Tk 解释器的 tk.PhotoImage（PNG data），
      不依赖 ImageTk / tkinter 全局默认根，杜绝解释器错绑
"""

import queue
import threading
import time

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.music_source import (
    wy_apply_cookie,
    wy_get_cookie_str,
    wy_is_logged_in,
    wy_login_qr_check,
    wy_login_qr_key,
)

# 扫码状态码
_QR_EXPIRED = 800  # 二维码已过期
_QR_WAIT_SCAN = 801  # 等待扫码
_QR_SCANNED = 802  # 已扫码，待确认
_QR_SUCCESS = 803  # 授权成功

# 轮询间隔（秒）
_QR_POLL_INTERVAL = 1.5
# 二维码尺寸
_QR_SIZE = 200
# 主线程调度器轮询间隔（毫秒）
_UI_DISPATCH_INTERVAL_MS = 150

_QR_URL_PREFIX = "https://music.163.com/login?codekey="


class NeteaseLoginWindow(ctk.CTkToplevel):
    """网易云音乐扫码登录弹窗"""

    def __init__(self, parent, on_success=None):
        super().__init__(fg_color=COLORS["bg_dark"])
        self._parent = parent
        self._on_success = on_success
        self._running = True
        self._poll_running = False
        self._qr_key = ""
        self._flow_seq = 0
        self._ui_queue: "queue.Queue" = queue.Queue()

        self.title(_("wy_login_window_title"))
        self.geometry("340x480")
        self.resizable(False, False)
        try:
            self.transient(parent)
            self.grab_set()
        except Exception:
            pass

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda e: self._on_close())
        # 主线程调度器：仅在主线程用 after 注册，后台线程绝不触碰 Tk
        try:
            self.after(_UI_DISPATCH_INTERVAL_MS, self._ui_dispatcher)
        except Exception:
            pass
        self._start_qr_flow()

    def _r(self, widget, **mapping):
        """注册组件到父设置窗口的主题刷新列表（主题切换时同步刷新）"""
        try:
            self._parent._r(widget, **mapping)
        except Exception:
            pass

    def _build_ui(self):
        hint = ctk.CTkLabel(
            self,
            text=_("wy_login_qr_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        hint.pack(pady=(20, 4))
        self._r(hint, text_color="text_primary")

        self._qr_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_secondary"],
            width=_QR_SIZE,
            height=_QR_SIZE,
        )
        self._qr_label.pack(pady=(4, 2))
        self._r(self._qr_label, text_color="text_secondary")

        self._status_label = ctk.CTkLabel(
            self,
            text=_("wy_login_qr_generating"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=300,
            justify="center",
        )
        self._status_label.pack(pady=(2, 6))
        self._r(self._status_label, text_color="text_secondary")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(2, 16))

        self._refresh_btn = ctk.CTkButton(
            btn_row,
            text=_("wy_login_refresh_btn"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._start_qr_flow,
        )
        self._refresh_btn.pack(side=ctk.LEFT, padx=(0, 8))
        self._r(self._refresh_btn, fg_color="bg_light", hover_color="card_border")

        self._cancel_btn = ctk.CTkButton(
            btn_row,
            text=_("wy_login_cancel_btn"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_close,
        )
        self._cancel_btn.pack(side=ctk.LEFT)
        self._r(self._cancel_btn, fg_color="bg_light", hover_color="card_border")

    # ── 二维码流程（启动） ──────────────────────────

    def _start_qr_flow(self):
        """重新获取 unikey 并启动扫码轮询（主线程调用，可重复执行）"""
        self._poll_running = False
        self._flow_seq += 1
        self._qr_key = ""
        try:
            self._status_label.configure(text=_("wy_login_qr_generating"))
            self._qr_label.configure(text="", image=None)
            self._refresh_btn.configure(state="disabled")
        except Exception:
            pass
        threading.Thread(target=self._unikey_worker, daemon=True).start()

    def _unikey_worker(self):
        """后台线程：获取 unikey，结果经队列投递（绝不触碰 Tk）"""
        seq = self._flow_seq
        try:
            key = wy_login_qr_key()
        except Exception as e:
            logger.warning(f"网易云获取 unikey 异常: {e}")
            key = None
        if self._running:
            self._ui_queue.put(("unikey", seq, key))

    def _start_poll_worker(self, key: str):
        """启动扫码状态轮询线程（主线程调用）"""
        self._poll_running = True
        threading.Thread(target=self._poll_worker, args=(key,), daemon=True).start()

    def _poll_worker(self, key: str):
        """后台线程：轮询扫码状态，结果经队列投递（绝不触碰 Tk）"""
        while self._poll_running and self._running:
            time.sleep(_QR_POLL_INTERVAL)
            try:
                result = wy_login_qr_check(key)
            except Exception as e:
                logger.debug(f"网易云扫码状态查询异常: {e}")
                if self._running:
                    self._ui_queue.put(("network_error", str(e)))
                continue
            code = result.get("code")
            if code == _QR_SUCCESS or (code == 200 and result.get("cookie")):
                self._poll_running = False
                logger.info(f"网易云扫码授权成功，响应字段: {sorted(result.keys())}")
                self._ui_queue.put(("poll", key, _QR_SUCCESS, result.get("cookie", "")))
            elif code == _QR_EXPIRED:
                self._poll_running = False
                self._ui_queue.put(("poll", key, _QR_EXPIRED, None))
            elif code == _QR_SCANNED:
                self._ui_queue.put(("poll", key, _QR_SCANNED, None))
            elif code == _QR_WAIT_SCAN:
                self._ui_queue.put(("poll", key, _QR_WAIT_SCAN, None))

    # ── 主线程调度器 ────────────────────────────────

    def _ui_dispatcher(self):
        """主线程事件调度器：统一处理后台线程投递的事件"""
        if not self._running:
            return
        try:
            while True:
                try:
                    event = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)
        except Exception as e:
            logger.debug(f"网易云登录 UI 事件处理异常: {e}")
        try:
            self.after(_UI_DISPATCH_INTERVAL_MS, self._ui_dispatcher)
        except Exception:
            pass

    def _handle_event(self, event):
        kind = event[0]
        try:
            if kind == "unikey":
                # 过期事件或刷新后旧的 unikey 请求：忽略
                if event[1] != self._flow_seq:
                    return
                key = event[2]
                if key:
                    self._qr_key = key
                    self._render_qr(key)
                    self._start_poll_worker(key)
                else:
                    self._on_qr_fetch_failed()
            elif kind == "poll":
                # 仅处理当前二维码对应 key 的状态事件
                if event[1] != self._qr_key:
                    return
                code = event[2]
                if code == _QR_SUCCESS:
                    self._on_login_success(event[3] or "")
                elif code == _QR_EXPIRED:
                    self._on_qr_expired()
                elif code == _QR_SCANNED:
                    self._set_status_text(_("wy_login_qr_scanned"))
                elif code == _QR_WAIT_SCAN:
                    self._set_status_text(_("wy_login_qr_waiting"))
            elif kind == "network_error":
                self._on_poll_network_error(event[1])
        except Exception as e:
            logger.debug(f"网易云登录 UI 事件执行异常: {e}")

    # ── UI 渲染（仅主线程） ─────────────────────────

    def _render_qr(self, key: str):
        """用 unikey 生成二维码并显示（主线程）

        使用显式绑定窗口自身 Tk 解释器的 tk.PhotoImage（PNG data），
        不依赖 ImageTk / tkinter 全局默认根，避免插件或其它临时 Tk
        根污染默认根后出现 "image pyimageN doesn't exist" 崩溃。
        """
        try:
            import io
            import tkinter as tk

            import qrcode

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=6,
                border=2,
            )
            qr.add_data(f"{_QR_URL_PREFIX}{key}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            # 按窗口缩放比例预缩放，与 CTk 控件逻辑尺寸保持一致
            try:
                scaling = self._qr_label._get_widget_scaling()
            except Exception:
                scaling = 1.0
            target = max(1, round(_QR_SIZE * (scaling or 1.0)))
            if img.size != (target, target):
                img = img.resize((target, target))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            photo = tk.PhotoImage(master=self._qr_label, data=buf.getvalue())
            self._qr_label.configure(image=photo, text="")
            self._qr_label._image = photo
            self._refresh_btn.configure(state="normal")
            self._status_label.configure(text=_("wy_login_qr_waiting"))
        except Exception as e:
            logger.warning(f"生成网易云二维码失败: {e}")
            self._status_label.configure(text=_("wy_login_qr_fetch_failed"))
            self._refresh_btn.configure(state="normal")

    def _set_status_text(self, text: str):
        if self._running:
            self._status_label.configure(text=text)

    def _on_qr_fetch_failed(self):
        if not self._running:
            return
        self._status_label.configure(text=_("wy_login_qr_fetch_failed"))
        self._refresh_btn.configure(state="normal")

    def _on_poll_network_error(self, error: str):
        if self._running:
            self._status_label.configure(text=_("wy_login_network_error", error=error[:60]))

    def _on_qr_expired(self):
        """二维码过期：提示后自动重新生成（与 YesPlayMusic 行为一致）"""
        if not self._running:
            return
        self._status_label.configure(text=_("wy_login_qr_expired"))
        self.after(800, self._start_qr_flow)

    def _on_login_success(self, cookie: str):
        """扫码授权成功：应用并持久化登录 Cookie（主线程）

        Cookie 可能来自响应体 cookie 字段，也可能仅随 Set-Cookie 响应头
        （requests 已自动写入会话）。此处多来源提取：
            1. 响应体 cookie 字符串 → 应用到会话
            2. 从会话导出全部登录 Cookie 用于持久化（覆盖 1 的更完整）
            3. 最终以 MUSIC_U 是否就位判定登录是否真正成功
        """
        if not self._running:
            return
        # 去除 ' HTTPOnly' 标记后解析应用（与 YesPlayMusic 一致）
        cookie = (cookie or "").replace(" HTTPOnly", "")
        if cookie:
            wy_apply_cookie(cookie)
        # 响应体无 cookie 字段时，Set-Cookie 响应头已被 requests 自动
        # 写入会话，直接从会话导出完整登录 Cookie
        saved_cookie = wy_get_cookie_str()
        if not saved_cookie:
            saved_cookie = cookie
        if not wy_is_logged_in():
            # 授权成功但会话中无登录态（MUSIC_U 缺失）：提示并允许重试
            logger.warning(f"网易云扫码授权成功但未获取到登录 Cookie，响应 cookie 为空: {not cookie}")
            self._status_label.configure(text=_("wy_login_cookie_failed"))
            self._refresh_btn.configure(state="normal")
            return
        try:
            self._parent.callbacks.get("set_wy_cookie", lambda c: None)(saved_cookie)
        except Exception as e:
            logger.warning(f"保存网易云登录 Cookie 失败: {e}")
        self._status_label.configure(text=_("wy_login_success"), text_color=COLORS["success"])
        self._refresh_btn.configure(state="disabled")
        self._cancel_btn.configure(state="disabled")
        if self._on_success is not None:
            try:
                self._on_success()
            except Exception as e:
                logger.warning(f"网易云登录成功回调异常: {e}")
        # 短暂展示成功状态后自动关闭
        self.after(900, self._on_close)

    def _on_close(self):
        self._running = False
        self._poll_running = False
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
