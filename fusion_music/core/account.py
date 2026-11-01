"""网易云账号：登录 / 登出 / 凭据加密持久化。

登录方式
--------
* **扫码登录**（推荐，也是 FMCL 唯一实现的方式）：取 ``unikey`` → 本地渲染二维码
  → 轮询 ``800/801/802/803``。
* **手机号 + 短信验证码**：``/api/sms/captcha/sent`` → ``/api/login/cellphone``。
* **手机号 + 密码**：同一路由。注意网易已逐步下线密码登录，服务端可能返回
  ``code=502 请切换登录方式或升级版本``，此时应引导用户改用扫码或短信。
* **手动粘贴 Cookie**：把外部获取的 ``MUSIC_U`` 等 Cookie 直接交给音源会话。

凭据以 AES-256-GCM 认证加密后写入 ``<程序目录>/data/credentials.enc``，
密钥见 :mod:`app.security.vault`。**绝不在日志里打印 Cookie。**
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Property, Signal, Slot

from .. import paths
from ..paths import APP_VERSION
from ..security import vault
from ..security.vault import (
    MASTER_PASSWORD_ENV,
    VaultError,
    dpapi_available,
    set_portable_preference,
)
from ..sources import (
    wy_apply_cookie,
    wy_clear_cookie,
    wy_fetch_profile,
    wy_get_cookie_str,
    wy_get_user_playlists,
    wy_is_logged_in,
    wy_login_qr_check,
    wy_login_qr_key,
    wy_source,
)
from ..sources import netease

logger = logging.getLogger(__name__)

QR_URL_PREFIX = "https://music.163.com/login?codekey="
QR_POLL_INTERVAL = 1.5
QR_POLL_MAX_SECONDS = 300

# 网易云扫码状态码
QR_EXPIRED = 800
QR_WAIT_SCAN = 801
QR_SCANNED = 802
QR_SUCCESS = 803

PROVIDER = "netease"
MUSIC_U_TTL_SECONDS = netease.COOKIE_TTL_SECONDS  # 拿不到 Set-Cookie 时的兜底估算

# 网易云会员标识（vipCode / vipType 位掩码都归一到这几个名字）。
# 名字本身由音源层给出（app/sources/netease.py 的 VIP_NAME_*），两处共用一份文案。
VIP_LABELS = {
    0: "普通用户",
    1: netease.VIP_NAME_PACKAGE,
    10: netease.VIP_NAME_BLACK,
    11: netease.VIP_NAME_BLACK,
    20: netease.VIP_NAME_SVIP,
    100: netease.VIP_NAME_BLACK,
    220: netease.VIP_NAME_PACKAGE,
    300: netease.VIP_NAME_SVIP,
}

#: 会员等级用的中文数字。用的是「壹贰叁肆」这套大写写法 ——
#: 网易云客户端显示的就是「黑胶SVIP·肆」。
_CN_DIGITS = ("零", "壹", "贰", "叁", "肆", "伍", "陆", "柒", "捌", "玖")


def vip_level_suffix(level: Any) -> str:
    """会员等级后缀：``4`` → ``·肆``（拼在「黑胶SVIP」后面）。

    网易云客户端写的是「黑胶SVIP·肆」而不是「黑胶SVIP Lv4」，这里跟着它。
    等级是服务端给的：超出 1..99 时**不猜**，原样退回阿拉伯数字；
    0 / 拿不到时返回空串，界面上就不显示等级。
    """
    try:
        n = int(level)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n > 99:
        return f"·{n}"
    if n < 10:
        return "·" + _CN_DIGITS[n]
    tens, ones = divmod(n, 10)
    head = "拾" if tens == 1 else _CN_DIGITS[tens] + "拾"
    return "·" + head + (_CN_DIGITS[ones] if ones else "")


class _Emitter(QObject):
    qrReady = Signal(object)
    qrPoll = Signal(object)
    loginDone = Signal(object)
    profileDone = Signal(object)
    playlistsDone = Signal(object)
    checkDone = Signal(object)
    renewDone = Signal(str)

class AccountManager(QObject):
    """网易云账号状态、登录流程与凭据续期。"""

    qrChanged = Signal()
    statusChanged = Signal()
    loggedInChanged = Signal()
    profileChanged = Signal()
    playlistsChanged = Signal()
    busyChanged = Signal()
    credentialsChanged = Signal()
    message = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._emitter = _Emitter(self)
        self._emitter.qrReady.connect(self._on_qr_ready)
        self._emitter.qrPoll.connect(self._on_qr_poll)
        self._emitter.loginDone.connect(self._on_login_done)
        self._emitter.profileDone.connect(self._on_profile_done)
        self._emitter.playlistsDone.connect(self._on_playlists_done)
        self._emitter.checkDone.connect(self._on_check_done)
        self._emitter.renewDone.connect(self._on_renew_done)

        self._logged_in = False
        self._busy = False
        self._status = "未登录"
        self._qr_image = ""
        self._qr_key = ""
        self._qr_seq = 0
        self._qr_running = False

        self._nickname = ""
        self._avatar = ""
        self._user_id = 0
        self._vip_type = 0
        self._has_music_package = False
        self._has_black_vip = False
        self._is_svip = False
        self._red_vip_level = 0
        self._red_vip_annual_count = 0
        self._remote_playlists: list = []
        # 凭据有效期（unix 秒）：登录 / 续期时从 Set-Cookie 读，读不到就按 180 天估
        self._obtained_at = 0
        self._expires_at = 0

    # ── 属性 ────────────────────────────────────────────────

    @Property(bool, notify=loggedInChanged)
    def loggedIn(self) -> bool:  # noqa: N802
        return self._logged_in

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=qrChanged)
    def qrImage(self) -> str:  # noqa: N802
        return self._qr_image

    @Property(str, notify=profileChanged)
    def nickname(self) -> str:
        return self._nickname

    @Property(str, notify=profileChanged)
    def avatar(self) -> str:
        return self._avatar

    @Property(int, notify=profileChanged)
    def userId(self) -> int:  # noqa: N802
        return self._user_id

    @Property(str, notify=profileChanged)
    def vipLabel(self) -> str:  # noqa: N802
        if not self._logged_in:
            return ""
        if self._is_svip:
            # 「黑胶SVIP·肆」，不是「黑胶SVIP Lv4」（见 vip_level_suffix）
            return VIP_LABELS[300] + vip_level_suffix(self._red_vip_level)
        if self._has_black_vip or (self._vip_type & 10) == 10 or self._vip_type in (10, 11):
            return VIP_LABELS[100]
        if self._has_music_package or (self._vip_type & 1) == 1 or self._vip_type == 1:
            return VIP_LABELS[220]
        return VIP_LABELS.get(self._vip_type, "普通用户")

    @Property(bool, notify=profileChanged)
    def isVip(self) -> bool:  # noqa: N802
        return self._is_svip or self._has_black_vip or self._has_music_package

    @Property(bool, notify=profileChanged)
    def isSvip(self) -> bool:  # noqa: N802
        return self._is_svip

    @Property(int, notify=profileChanged)
    def vipType(self) -> int:  # noqa: N802
        return self._vip_type

    @Property(str, notify=profileChanged)
    def vipDetail(self) -> str:  # noqa: N802
        """给设置页用的权益明细，方便排查识别问题。"""
        if not self._logged_in:
            return ""
        parts = []
        if self._is_svip:
            parts.append("黑胶SVIP")
        elif self._has_black_vip:
            parts.append("黑胶VIP")
        if self._has_music_package:
            parts.append("音乐包")
        if self._red_vip_level > 0:
            # 这一行是排查用的，等级特意保留服务端原始的「LvN」写法：
            # 界面上显示的是「·肆」，报问题时得能和服务端返回的数字对上
            parts.append(f"等级 Lv{self._red_vip_level}")
        if self._red_vip_annual_count > 0:
            parts.append("年费")
        detail = " · ".join(parts) if parts else "无会员权益"
        return f"{detail}（vipType={self._vip_type}）"

    @Property("QVariantList", notify=playlistsChanged)
    def remotePlaylists(self):  # noqa: N802
        return self._remote_playlists

    @Property(bool, constant=True)
    def hasSavedCredentials(self) -> bool:  # noqa: N802
        return vault.exists()

    @Property(str, constant=True)
    def credentialPath(self) -> str:  # noqa: N802
        return str(paths.credentials_file())

    @Property(str, constant=True)
    def keySource(self) -> str:  # noqa: N802
        return vault.key_source()

    # ── 便携模式（凭据能不能换台电脑解开）──────────────────

    @Property(bool, notify=credentialsChanged)
    def portableMode(self) -> bool:  # noqa: N802
        """凭据是否与机器无关：换台电脑 / 换个 Windows 用户照样保持登录。"""
        try:
            return vault.portable()
        except Exception:
            return False

    @Property(bool, constant=True)
    def dpapiAvailable(self) -> bool:  # noqa: N802
        """当前平台有没有 DPAPI。没有的话密钥只能裸存，便携模式其实是常开的。"""
        # 注意：``vault`` 是包导出的**单例**（见 security/__init__.py），
        # 模块级函数得单独 import，不能写成 vault.dpapi_available()
        return dpapi_available()

    @Property(bool, notify=credentialsChanged)
    def keyUsable(self) -> bool:  # noqa: N802
        """主密钥在本机解得开吗。``False`` = 数据目录是从别的机器拷过来的。"""
        return vault.key_usable()

    @Property(str, notify=credentialsChanged)
    def portableHint(self) -> str:  # noqa: N802
        """设置页里那行随状态变的小字。"""
        if os.environ.get(MASTER_PASSWORD_ENV):
            return "当前由环境变量 FUSION_MUSIC_MASTER_PASSWORD 派生密钥，本来就与机器无关。"
        if not self.dpapiAvailable:
            return "当前平台没有 DPAPI，密钥只能以原始字节保存 —— 便携模式是常开的。"
        if not self.keyUsable:
            return "主密钥由另一台机器 / 另一个 Windows 用户保护，本机解不开，已保存的登录状态无法恢复。"
        if self.portableMode:
            return "主密钥不以本机绑定：整个程序目录拷到别的电脑也能保持登录。"
        return "主密钥由本机 DPAPI 保护：拷到别的电脑就解不开，需要重新登录。"

    @Slot(bool)
    def setPortableMode(self, enabled: bool) -> None:  # noqa: N802
        """切换便携模式（只换密钥的存法，不动密钥本身，不需要重新登录）。"""
        enabled = bool(enabled)
        self._config.set("account.portable", enabled)
        try:
            source = vault.set_portable(enabled)
        except VaultError as e:
            # 密钥解不开：开关状态回到事实，并把出路告诉用户
            self._config.set("account.portable", vault.portable())
            self.credentialsChanged.emit()
            self.errorOccurred.emit(str(e))
            return

        self.credentialsChanged.emit()
        if enabled and source == "portable":
            self.message.emit("便携模式已开启：整个程序目录拷到别的电脑也能保持登录")
        elif enabled:
            self.message.emit("已记下便携模式：本平台的密钥本来就与机器无关")
        elif source == "dpapi":
            self.message.emit("便携模式已关闭：主密钥改由本机 DPAPI 保护，换电脑需要重新登录")
        else:
            self.errorOccurred.emit(
                "当前平台没有 DPAPI，密钥只能以原始字节保存，关不掉便携模式"
            )

    @Slot()
    def rebuildKey(self) -> None:  # noqa: N802
        """密钥解不开时的出路：重建密钥（旧凭据作废，需要重新登录一次）。

        旧密钥文件会改名为 ``master.key.unreadable`` 留着，不是静默删除。
        """
        try:
            vault.rebuild_key()
        except VaultError as e:
            self.errorOccurred.emit(str(e))
            return
        wy_clear_cookie()
        self._reset_login_state()
        self._set_status("未登录")
        self.credentialsChanged.emit()
        self.message.emit(
            "已重建主密钥（旧密钥备份为 master.key.unreadable），请重新登录网易云"
        )

    def _sync_portable(self) -> None:
        """把配置里的便携模式落到密钥文件上（启动时调用一次）。

        配置是「意图」，密钥文件是「事实」：换机之后配置写着便携、密钥却还是
        本机绑定的，一样解不开 —— 所以每次启动都对齐一次。
        """
        want = bool(self._config.get("account.portable", False))
        try:
            set_portable_preference(want)
            if vault.portable() != want and vault.key_usable():
                vault.set_portable(want)
        except VaultError as e:
            # 密钥本来就解不开：留给 restore() / 设置页去提示，这里不重复喊
            logger.debug("应用便携模式失败: %s", e)

    @Property(str, constant=True)
    def dataDir(self) -> str:  # noqa: N802
        return str(paths.data_dir())

    # ── 凭据有效期 ──────────────────────────────────────────

    @Property(int, notify=credentialsChanged)
    def credentialDaysLeft(self) -> int:  # noqa: N802
        """凭据还剩几天（向上取整；-1 = 未知 / 没保存凭据）。

        向上取整是为了不把「还剩 20 小时」显示成「已过期」。
        """
        if not self._expires_at:
            return -1
        remaining = self._expires_at - time.time()
        if remaining <= 0:
            return 0
        return int(math.ceil(remaining / 86400))

    @Property(str, notify=credentialsChanged)
    def credentialExpiry(self) -> str:  # noqa: N802
        """给设置页显示的一行有效期文案。"""
        if not vault.exists():
            return "未保存凭据"
        if not self._expires_at:
            return "未知（续期一次即可读到）"
        when = time.strftime("%Y-%m-%d", time.localtime(self._expires_at))
        if self._expires_at <= time.time():
            return f"{when}（已过期，请重新登录）"
        return f"{when}（还剩 {self.credentialDaysLeft} 天）"

    @Property(bool, notify=credentialsChanged)
    def credentialExpiring(self) -> bool:  # noqa: N802
        """是否已进入自动续期的窗口（设置里那个 cookie_refresh_days）。"""
        return self._logged_in and netease.should_renew(
            self._expires_at, int(time.time()), self._renew_days()
        )

    def _renew_days(self) -> int:
        try:
            return int(self._config.get("account.cookie_refresh_days", 7) or 0)
        except (TypeError, ValueError):
            return 7

    # ── 启动恢复 ────────────────────────────────────────────

    @Slot()
    def restore(self) -> None:
        """启动时用已保存的加密凭据恢复登录态，并做一次服务端校验。"""
        # 先把「便携模式」这个意图落到密钥文件上，再谈读凭据
        self._sync_portable()
        if not bool(self._config.get("account.auto_login", True)):
            self._set_status("未登录")
            return
        payload = None
        try:
            payload = vault.load()
        except VaultError as e:
            # 设置页据此显示「密钥在本机解不开」那张卡片，所以这里要刷新一次 ——
            # 这条路径上不会走到下面的 credentialsChanged
            self.credentialsChanged.emit()
            self.errorOccurred.emit(str(e))
            self._set_status("凭据不可用")
            return
        except Exception as e:
            logger.warning("读取凭据失败: %s", e)

        if not payload or payload.get("provider") != PROVIDER:
            self._set_status("未登录")
            return

        # 有效期先读出来：设置页要显示它，而且后面判断「该不该续期」全靠它
        self._load_expiry(payload)
        self.credentialsChanged.emit()

        cookies = payload.get("cookies") or ""
        if not cookies:
            self._set_status("未登录")
            return

        if not wy_apply_cookie(cookies):
            self._set_status("未登录")
            return

        if not wy_is_logged_in():
            self._set_status("凭据已失效，请重新登录")
            return

        self._logged_in = True
        self._nickname = str(payload.get("nickname") or "")
        self._avatar = str(payload.get("avatar_url") or "")
        self._user_id = int(payload.get("user_id") or 0)
        self._apply_vip_fields(payload)
        self._set_status("已登录")
        self.loggedInChanged.emit()
        self.profileChanged.emit()
        logger.info("已从加密凭据恢复网易云登录态")

        # 服务端校验 + 必要时续期 + 拉取歌单（异步，不阻塞启动）
        threading.Thread(target=self._check_worker, daemon=True, name="wy-check").start()

    def _load_expiry(self, payload: Dict[str, Any]) -> None:
        """读出凭据有效期；老版本凭据没有这个字段时按「拿到时刻 + 180 天」补。"""
        self._obtained_at = int(payload.get("obtained_at") or 0)
        self._expires_at = int(payload.get("expires_at") or 0)
        if not self._expires_at and self._obtained_at:
            self._expires_at = self._obtained_at + MUSIC_U_TTL_SECONDS

    def _apply_vip_fields(self, profile: dict) -> None:
        """从（凭据或接口返回的）profile 里取出会员字段。

        ``is_svip`` / ``has_black_vip`` 由音源层依据权威接口
        ``/api/music-vip-membership/client/vip/info`` 的 vipCode 判定，
        这里只负责落库与派生。
        """
        self._vip_type = int(profile.get("vip_type") or 0)
        self._has_music_package = bool(profile.get("has_music_package"))
        self._has_black_vip = bool(profile.get("has_black_vip"))
        self._is_svip = bool(profile.get("is_svip"))
        self._red_vip_level = int(profile.get("red_vip_level") or 0)
        self._red_vip_annual_count = int(profile.get("red_vip_annual_count") or 0)

    # ── 扫码登录 ────────────────────────────────────────────

    @Slot()
    def startQrLogin(self) -> None:  # noqa: N802
        self._qr_seq += 1
        self._qr_running = True
        self._set_busy(True)
        self._set_status("正在获取二维码…")
        self._qr_image = ""
        self.qrChanged.emit()
        seq = self._qr_seq
        threading.Thread(target=self._qr_key_worker, args=(seq,), daemon=True, name="wy-qr-key").start()

    @Slot()
    def cancelQrLogin(self) -> None:  # noqa: N802
        self._qr_running = False
        self._qr_seq += 1
        self._set_busy(False)
        self._set_status("已取消")

    def _qr_key_worker(self, seq: int) -> None:
        try:
            key = wy_login_qr_key()
        except Exception as e:
            logger.warning("获取网易云二维码 unikey 失败: %s", e)
            key = None
        self._emitter.qrReady.emit((seq, key))

    @Slot(object)
    def _on_qr_ready(self, payload) -> None:
        seq, key = payload
        if seq != self._qr_seq or not self._qr_running:
            return
        if not key:
            self._set_busy(False)
            self._set_status("二维码获取失败")
            self.errorOccurred.emit("无法获取二维码，请检查网络后重试")
            return
        self._qr_key = key
        png = self._render_qr(key)
        if not png:
            self._set_busy(False)
            self._set_status("二维码生成失败")
            self.errorOccurred.emit("本地生成二维码失败（需要 qrcode 与 Pillow 依赖）")
            return
        from PySide6.QtCore import QUrl

        self._qr_image = QUrl.fromLocalFile(png).toString()
        self.qrChanged.emit()
        self._set_status("请使用网易云音乐 App 扫描二维码")
        threading.Thread(
            target=self._qr_poll_worker, args=(seq, key), daemon=True, name="wy-qr-poll"
        ).start()

    def _render_qr(self, key: str) -> Optional[str]:
        """本地把 ``https://music.163.com/login?codekey=<key>`` 渲染成 PNG。

        FMCL 同样使用本地渲染（不请求任何二维码图片接口）。
        """
        try:
            import qrcode

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(f"{QR_URL_PREFIX}{key}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="#14121F", back_color="white").convert("RGB")
            img = img.resize((260, 260))
            dest = paths.cache_dir() / "login" / "qr.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, format="PNG")
            return str(dest)
        except Exception as e:
            logger.warning("生成二维码失败: %s", e)
            return None

    def _qr_poll_worker(self, seq: int, key: str) -> None:
        deadline = time.monotonic() + QR_POLL_MAX_SECONDS
        while self._qr_running and seq == self._qr_seq and time.monotonic() < deadline:
            time.sleep(QR_POLL_INTERVAL)
            if not (self._qr_running and seq == self._qr_seq):
                return
            try:
                result = wy_login_qr_check(key)
            except Exception as e:
                logger.debug("扫码状态查询异常: %s", e)
                continue
            if not isinstance(result, dict):
                continue
            code = result.get("code")
            cookie = result.get("cookie") or ""
            self._emitter.qrPoll.emit((seq, key, code, cookie))
            if code in (QR_SUCCESS, QR_EXPIRED) or (code == 200 and cookie):
                return
        self._emitter.qrPoll.emit((seq, key, -1, ""))

    @Slot(object)
    def _on_qr_poll(self, payload) -> None:
        seq, key, code, cookie = payload
        if seq != self._qr_seq or not self._qr_running:
            return
        if code == QR_SUCCESS or (code == 200 and cookie):
            self._qr_running = False
            self._apply_login_cookie(cookie)
        elif code == QR_EXPIRED:
            self._set_status("二维码已失效，正在刷新…")
            self._qr_running = False
            self._qr_image = ""
            self.qrChanged.emit()
            threading.Timer(0.8, self.startQrLogin).start()
        elif code == QR_SCANNED:
            self._set_status("扫描成功，请在手机上确认登录")
        elif code == QR_WAIT_SCAN:
            self._set_status("等待扫码…")
        elif code == -1:
            self._qr_running = False
            self._set_busy(False)
            self._set_status("二维码已超时，请重新获取")

    # ── 手机号 / 验证码 / Cookie ────────────────────────────

    @Slot(str, str, result=bool)
    def sendCaptcha(self, phone: str, countryCode: str = "86") -> bool:  # noqa: N802
        phone = (phone or "").strip()
        if not phone:
            self.errorOccurred.emit("请输入手机号")
            return False
        self._set_busy(True)
        self._set_status("正在发送验证码…")
        threading.Thread(
            target=self._captcha_worker, args=(phone, countryCode or "86"), daemon=True,
            name="wy-sms",
        ).start()
        return True

    def _captcha_worker(self, phone: str, ctcode: str) -> None:
        src = wy_source()
        resp: Dict[str, Any] = {}
        try:
            if src is not None:
                resp = src._eapi_post(  # noqa: SLF001
                    "/api/sms/captcha/sent", {"cellphone": phone, "ctcode": ctcode}
                ) or {}
        except Exception as e:
            logger.warning("发送验证码失败: %s", e)
        self._emitter.loginDone.emit(("captcha", resp))

    @Slot(str, str, str, bool)
    def loginWithPhone(
        self, phone: str, secret: str, countryCode: str = "86", usePassword: bool = False
    ) -> None:  # noqa: N802
        phone = (phone or "").strip()
        if not phone or not secret:
            self.errorOccurred.emit("请填写手机号与验证码/密码")
            return
        self._set_busy(True)
        self._set_status("正在登录…")
        threading.Thread(
            target=self._phone_login_worker,
            args=(phone, secret, countryCode or "86", bool(usePassword)),
            daemon=True,
            name="wy-phone-login",
        ).start()

    def _phone_login_worker(self, phone: str, secret: str, ctcode: str, use_password: bool) -> None:
        src = wy_source()
        resp: Dict[str, Any] = {}
        try:
            if src is not None:
                payload: Dict[str, Any] = {
                    "phone": phone,
                    "countrycode": ctcode,
                    "rememberLogin": True,
                }
                if use_password:
                    payload["password"] = secret
                else:
                    payload["captcha"] = secret
                resp = src._eapi_post("/api/login/cellphone", payload) or {}  # noqa: SLF001
        except Exception as e:
            logger.warning("手机号登录异常: %s", e)
            resp = {"code": -1, "message": str(e)}
        self._emitter.loginDone.emit(("phone", resp))

    @Slot(str)
    def loginWithCookie(self, cookie: str) -> None:  # noqa: N802
        cookie = (cookie or "").strip()
        if not cookie:
            self.errorOccurred.emit("请粘贴 Cookie")
            return
        self._set_busy(True)
        self._set_status("正在校验 Cookie…")
        self._apply_login_cookie(cookie)

    @Slot(object)
    def _on_login_done(self, payload) -> None:
        kind, resp = payload
        if kind == "captcha":
            code = (resp or {}).get("code")
            if code == 200:
                self._set_status("验证码已发送")
                self.message.emit("验证码已发送，请查看手机短信")
            else:
                self._set_busy(False)
                self._set_status("验证码发送失败")
                self.errorOccurred.emit(_describe_error(resp, "验证码发送失败"))
            return

        if kind == "phone":
            code = (resp or {}).get("code")
            if code == 200:
                cookie = wy_get_cookie_str()
                if wy_is_logged_in():
                    self._apply_login_cookie(cookie)
                else:
                    self._set_busy(False)
                    self._set_status("登录失败")
                    self.errorOccurred.emit("登录响应成功但未获取到登录凭据")
            else:
                self._set_busy(False)
                self._set_status("登录失败")
                self.errorOccurred.emit(_describe_error(resp, "登录失败"))

    # ── 登录成功后的统一处理 ────────────────────────────────

    def _apply_login_cookie(self, cookie: str) -> None:
        cookie = (cookie or "").replace(" HTTPOnly", "")
        if cookie:
            wy_apply_cookie(cookie)
        saved = wy_get_cookie_str() or cookie
        if not wy_is_logged_in():
            self._set_busy(False)
            self._set_status("登录失败")
            self.errorOccurred.emit("授权成功但未取到登录凭据，请重试")
            return

        self._logged_in = True
        self._set_status("已登录")
        self.loggedInChanged.emit()

        # 先保存凭据，再异步校验并补充昵称/头像
        self._persist_credentials(saved, {})
        threading.Thread(target=self._refresh_worker, daemon=True, name="wy-refresh").start()
        self.message.emit("登录成功")

    def _persist_credentials(self, cookies: str, profile: Dict[str, Any]) -> None:
        if not bool(self._config.get("account.save_credentials", True)):
            return
        if not cookies:
            return
        payload = {
            "provider": PROVIDER,
            "cookies": cookies,
            "user_id": int(profile.get("user_id") or self._user_id or 0),
            "nickname": profile.get("nickname") or self._nickname or "",
            "avatar_url": profile.get("avatar_url") or self._avatar or "",
            "vip_type": int(profile.get("vip_type") or self._vip_type or 0),
            "has_music_package": bool(
                profile.get("has_music_package", self._has_music_package)
            ),
            "has_black_vip": bool(profile.get("has_black_vip", self._has_black_vip)),
            "is_svip": bool(profile.get("is_svip", self._is_svip)),
            "red_vip_level": int(profile.get("red_vip_level") or self._red_vip_level or 0),
            "red_vip_annual_count": int(
                profile.get("red_vip_annual_count") or self._red_vip_annual_count or 0
            ),
            "obtained_at": int(time.time()),
            "expires_at": self._resolve_expiry(),
            "app_version": APP_VERSION,
        }
        try:
            ok = vault.save(payload)
            if ok:
                self._obtained_at = int(payload["obtained_at"])
                self._expires_at = int(payload["expires_at"])
                self.credentialsChanged.emit()
            else:
                self.errorOccurred.emit("凭据加密保存失败，本次登录状态不会被记住")
        except VaultError as e:
            self.errorOccurred.emit(str(e))
        except Exception as e:
            logger.warning("保存凭据失败: %s", e)
            self.errorOccurred.emit("凭据保存失败，本次登录状态不会被记住")

    def _resolve_expiry(self) -> int:
        """凭据到期时间。

        优先级：本次会话里服务端刚给的 ``Set-Cookie`` Max-Age（续期响应就有）
        > 已经记着的有效期 > 拿到的时刻 + 180 天估算。

        「已经记着的」这一档不能省：把 cookie 塞回会话时只带 name=value
        （``apply_cookie_str`` 会丢掉 Max-Age），拿不到 jar 里的过期时间就只
        剩估算，而估算会把真实有效期一路往后推 —— 那样自动续期永远不会触发。
        """
        jar_expiry = netease.cookie_expiry()
        if jar_expiry:
            return jar_expiry
        if self._expires_at:
            return self._expires_at
        return int(time.time()) + MUSIC_U_TTL_SECONDS

    def _refresh_worker(self) -> None:
        profile = None
        try:
            profile = wy_fetch_profile()
        except Exception as e:
            logger.debug("拉取账号信息失败: %s", e)
        self._emitter.profileDone.emit(profile)

    # ── 服务端校验 + 续期 ───────────────────────────────────

    def _check_worker(self) -> None:
        """启动后的第一次校验：查登录态、必要时续期、顺带刷新账号信息。

        三件事都在同一个后台线程里做，避免开三个线程：

        1. :func:`netease.login_state` 判断「有效 / 已失效 / 网络不可用」——
           只有确认失效才把界面置为未登录；
        2. 凭据剩余有效期不足 ``account.cookie_refresh_days`` 天时调续期接口；
        3. 拉一次 profile 刷新昵称与会员信息。
        """
        state = "offline"
        profile = None
        renewed = ""
        try:
            state = netease.login_state()
        except Exception as e:
            logger.debug("登录态校验失败: %s", e)
        if state == "ok":
            if self._should_renew():
                renewed = netease.refresh_login()
            try:
                profile = wy_fetch_profile()
            except Exception as e:
                logger.debug("拉取账号信息失败: %s", e)
        self._emitter.checkDone.emit((state, profile, renewed))

    def _should_renew(self) -> bool:
        return netease.should_renew(self._expires_at, int(time.time()), self._renew_days())

    @Slot(object)
    def _on_check_done(self, payload) -> None:
        state, profile, renewed = payload
        self._set_busy(False)

        if state == "expired":
            # 服务端明确说凭据不认了：置为未登录并引导重新登录。
            # 凭据文件**不删** —— 万一是风控误判，删了就真回不来了。
            logger.info("网易云登录已失效，已清除内存中的登录态")
            self._reset_login_state()
            self._set_status("登录已失效，请重新登录")
            self.errorOccurred.emit("网易云登录已失效，请重新登录")
            return

        if state == "offline":
            # 网络不通而已，保持登录态，不要打扰用户
            logger.debug("登录态校验因网络问题跳过")
            return

        if renewed:
            self._persist_credentials(renewed, {})
            self.message.emit("登录凭据已自动续期")
            self.credentialsChanged.emit()

        if profile:
            self._nickname = str(profile.get("nickname") or "")
            self._avatar = str(profile.get("avatar_url") or "")
            self._user_id = int(profile.get("user_id") or 0)
            self._apply_vip_fields(profile)
            self._set_status("已登录")
            self.profileChanged.emit()
            # 用服务端返回的完整信息覆盖一次凭据（含 cookie 与新的有效期）
            self._persist_credentials(
                wy_get_cookie_str(),
                {
                    "user_id": self._user_id,
                    "nickname": self._nickname,
                    "avatar_url": self._avatar,
                    "vip_type": self._vip_type,
                    "has_music_package": self._has_music_package,
                    "has_black_vip": self._has_black_vip,
                    "is_svip": self._is_svip,
                    "red_vip_level": self._red_vip_level,
                    "red_vip_annual_count": self._red_vip_annual_count,
                },
            )
        # 走到这里说明服务端认可了当前会话（state == "ok"），歌单可以同步了
        threading.Thread(target=self._playlists_worker, daemon=True, name="wy-playlists").start()

    @Slot(str)
    def _on_renew_done(self, cookies: str) -> None:
        self._set_busy(False)
        if not cookies:
            self.errorOccurred.emit("续期失败，请检查网络后重试（或重新登录）")
            return
        self._persist_credentials(cookies, {})
        self.message.emit("登录凭据已续期")
        self.credentialsChanged.emit()

    @Slot()
    def renewCredentials(self) -> None:  # noqa: N802
        """手动续期（设置页按钮）。"""
        if not self._logged_in:
            self.errorOccurred.emit("请先登录网易云账号")
            return
        self._set_busy(True)
        threading.Thread(target=self._renew_worker, daemon=True, name="wy-renew").start()

    def _renew_worker(self) -> None:
        try:
            cookies = netease.refresh_login()
        except Exception as e:
            logger.debug("续期失败: %s", e)
            cookies = ""
        self._emitter.renewDone.emit(cookies or "")

    @Slot(object)
    def _on_profile_done(self, profile) -> None:
        """手动 ``refresh()`` 的结果（启动那次校验走 :meth:`_on_check_done`）。"""
        self._set_busy(False)
        if not profile:
            # 不在这里判断凭据是否失效：网络抖动也会拿不到 profile，
            # 真要判定失效得靠 netease.login_state()（见 _on_check_done）。
            self.errorOccurred.emit("拉取账号信息失败，请检查网络后重试")
            return
        self._nickname = str(profile.get("nickname") or "")
        self._avatar = str(profile.get("avatar_url") or "")
        self._user_id = int(profile.get("user_id") or 0)
        self._apply_vip_fields(profile)
        self._set_status("已登录")
        self.profileChanged.emit()
        # 用服务端返回的完整信息覆盖一次凭据
        self._persist_credentials(
            wy_get_cookie_str(),
            {
                "user_id": self._user_id,
                "nickname": self._nickname,
                "avatar_url": self._avatar,
                "vip_type": self._vip_type,
                "has_music_package": self._has_music_package,
                "has_black_vip": self._has_black_vip,
                "is_svip": self._is_svip,
                "red_vip_level": self._red_vip_level,
                "red_vip_annual_count": self._red_vip_annual_count,
            },
        )
        threading.Thread(target=self._playlists_worker, daemon=True, name="wy-playlists").start()

    def _playlists_worker(self) -> None:
        try:
            items = wy_get_user_playlists()
        except Exception as e:
            logger.warning("拉取网易云歌单失败: %s", e)
            items = None
        self._emitter.playlistsDone.emit(items if items is not None else [])

    @Slot()
    def syncPlaylists(self) -> None:  # noqa: N802
        """手动重新同步网易云歌单。"""
        if not self._logged_in:
            self.errorOccurred.emit("请先登录网易云账号")
            return
        self._set_busy(True)
        self._set_status("正在同步歌单…")
        threading.Thread(target=self._playlists_worker, daemon=True, name="wy-playlists").start()

    @Slot(object)
    def _on_playlists_done(self, items) -> None:
        self._remote_playlists = list(items or [])
        self.playlistsChanged.emit()

    # ── 登出 ────────────────────────────────────────────────

    @Slot()
    def logout(self) -> None:
        wy_clear_cookie()
        try:
            vault.delete()
        except Exception as e:
            logger.warning("删除凭据失败: %s", e)
        self._reset_login_state()
        self._qr_image = ""
        self._set_status("已退出登录")
        self._set_busy(False)
        self.qrChanged.emit()
        self.credentialsChanged.emit()
        self.message.emit("已退出登录，本地凭据已删除")

    def _reset_login_state(self) -> None:
        """清掉内存里的登录态（不含凭据文件）。"""
        self._logged_in = False
        self._nickname = ""
        self._avatar = ""
        self._user_id = 0
        self._vip_type = 0
        self._has_music_package = False
        self._has_black_vip = False
        self._is_svip = False
        self._red_vip_level = 0
        self._red_vip_annual_count = 0
        self._remote_playlists = []
        self.loggedInChanged.emit()
        self.profileChanged.emit()
        self.playlistsChanged.emit()

    @Slot()
    def refresh(self) -> None:
        if not self._logged_in:
            return
        self._set_busy(True)
        threading.Thread(target=self._refresh_worker, daemon=True, name="wy-refresh").start()

    # ── 内部工具 ────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        if text != self._status:
            self._status = text
            self.statusChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

def _describe_error(resp: Any, fallback: str) -> str:
    if not isinstance(resp, dict):
        return fallback
    code = resp.get("code")
    msg = str(resp.get("message") or resp.get("msg") or "").strip()
    if code == 502:
        return "网易已下线该登录方式，请改用扫码登录或短信验证码登录"
    if code == 503:
        return "验证码错误或已过期"
    if code == 501:
        return "账号或密码错误"
    if code == 400:
        return "请求被拒绝，请稍后重试"
    if msg:
        return f"{fallback}：{msg}"
    return f"{fallback}（错误码 {code}）" if code is not None else fallback
