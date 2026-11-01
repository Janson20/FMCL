"""网易云补充接口（发现页 / 歌词翻译）与账号信息修正。

FMCL 只用到搜索、播放地址、歌词、歌单与登录；这里在不改动 vendored ``wy.py``
的前提下，复用它的 eapi 加密通道补齐「发现页」需要的接口。

**通道选择**：全部走 eapi。实测本机环境下 ``music.163.com/weapi/*`` 会被风控
拦截返回空响应（与 ``wy.py:1345-1346`` 的注释一致），而
``interface3.music.163.com/eapi/api/*`` 正常返回。

所有接口都是**尽力而为**：失败时返回空结果，由 UI 优雅降级。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .base import MusicInfo, duration_matches
from .wy import NetEaseMusicSource
from ..units import to_int

logger = logging.getLogger(__name__)

#: 会员名。vipCode 表（本模块）与账号页的 vipType 表（app/core/account.py）
#: 引用同一份文案 —— 两处各写一遍时，「黑胶SVIP」在一处、另一处是「SVIP」，
#: 用户在设置页与账号卡片里看到的等级名称就对不上了。
VIP_NAME_BLACK = "黑胶VIP"
VIP_NAME_SVIP = "黑胶SVIP"
VIP_NAME_PACKAGE = "音乐包"


class NetEaseSource(NetEaseMusicSource):
    """网易云音源：在 vendored 实现上修正账号会员信息的读取。

    用子类覆盖而不是直接改 ``wy.py``，是为了让那 8 个文件与上游 FMCL 保持
    逐字一致（见 NOTICE.md），将来重新同步上游不会冲突。

    上游的两个问题（实测 2026-09，SVIP 账号）::

        account.vipType = 11      # 标量
        profile.vipType = 110     # 位掩码！100|10 = SVIP + 黑胶VIP
        profile.vipRights = {}    # 该接口下是空对象，associator 等一概没有

    上游只读 ``profile.get("vipType")``，拿到 110；而标签表只有
    0/1/10/11/20，于是落到默认值「普通用户」。

    这里的做法是先问权威接口 ``/api/music-vip-membership/client/vip/info``，
    它返回每种会员的 ``vipCode`` 与到期时间，不存在歧义::

        associator   vipCode=100  黑胶VIP
        musicPackage vipCode=220  音乐包
        redplus      vipCode=300  黑胶SVIP
        albumVip     vipCode=400  专辑VIP
        voiceBookVip vipCode=500  有声书VIP

    拿不到时再退回 ``vipType``，并按位掩码解释（含 100 位即 SVIP）。

    另外 ``get_user_playlists()`` 走的是 ``self.fetch_login_profile()``，
    覆盖之后它拿到的 user_id 也更稳（``profile.userId`` 缺失时回退 ``account.id``）。
    """

    # vipCode -> 会员名
    VIP_CODE_LABELS = {
        100: VIP_NAME_BLACK,
        220: VIP_NAME_PACKAGE,
        300: VIP_NAME_SVIP,
        400: "专辑VIP",
        500: "有声书VIP",
    }

    def fetch_login_profile(self) -> Optional[dict]:  # noqa: D102
        try:
            resp = self._eapi_post("/api/nuser/account/get", {})  # noqa: SLF001
        except Exception as e:
            logger.warning("获取网易云账号信息异常: %s", e)
            return None

        if not isinstance(resp, dict) or resp.get("code") != 200:
            logger.debug(
                "网易获取登录用户信息失败: code=%s",
                resp.get("code") if isinstance(resp, dict) else resp,
            )
            return None

        account = resp.get("account") or {}
        profile = resp.get("profile") or {}
        if not account and not profile:
            # 未登录时这两个字段都是 null
            return None

        user_id = to_int(profile.get("userId")) or to_int(account.get("id"))

        # account.vipType 是标量，profile.vipType 是位掩码，两者含义不同，
        # 标量优先（与官方客户端一致）
        vip_type = to_int(account.get("vipType")) or to_int(profile.get("vipType"))

        vip = self._fetch_vip_info()
        if vip:
            is_svip = vip["is_svip"]
            has_black_vip = vip["has_black_vip"]
            has_music_package = vip["has_music_package"]
            source = "vip_info"
            extra = {
                "red_vip_level": vip["red_vip_level"],
                "red_vip_annual_count": vip["red_vip_annual_count"],
                "vip_codes": vip["active_codes"],
            }
        else:
            # 权威接口不可用时退回 vipType
            is_svip, has_black_vip, has_music_package = vip_flags_from_type(vip_type)
            source = "vip_type"
            extra = {"red_vip_level": 0, "red_vip_annual_count": 0, "vip_codes": []}

        logger.debug(
            "网易账号信息: vipType(account=%s / profile=%s) source=%s svip=%s 黑胶=%s 音乐包=%s %s",
            account.get("vipType"), profile.get("vipType"), source,
            is_svip, has_black_vip, has_music_package, extra.get("vip_codes"),
        )

        result = {
            "nickname": profile.get("nickname", "") or account.get("userName", ""),
            "avatar_url": profile.get("avatarUrl", ""),
            "user_id": user_id,
            "vip_type": vip_type,
            "has_music_package": has_music_package,
            "has_black_vip": has_black_vip,
            "is_svip": is_svip,
            "vip_source": source,
        }
        result.update(extra)
        return result

    def _fetch_vip_info(self) -> Optional[dict]:
        """查询会员权益（权威来源）。失败返回 None，由调用方退回 vipType。"""
        try:
            resp = self._eapi_post(  # noqa: SLF001
                "/api/music-vip-membership/client/vip/info", {}
            )
        except Exception as e:
            logger.debug("获取网易云会员信息失败: %s", e)
            return None
        if not isinstance(resp, dict) or resp.get("code") != 200:
            return None
        data = resp.get("data")
        if not isinstance(data, dict):
            return None

        def _active(key: str) -> Optional[dict]:
            node = data.get(key)
            if not isinstance(node, dict) or not node.get("vipCode"):
                return None
            expire = to_int(node.get("expireTime"))
            # expireTime 为 0/缺失时不做判定，避免把永久权益误杀
            if expire and expire < int(time.time() * 1000):
                return None
            return node

        redplus = _active("redplus")
        associator = _active("associator")
        music_package = _active("musicPackage")

        # 未开通的会员类型也会带 vipCode 和未来的 expireTime，只有 vipLevel 为 0
        # 能区分出来（实测 albumVip / voiceBookVip 就是这种形态），
        # 因此附加信息里再要求 vipLevel > 0。
        active_codes = {}
        for key in ("redplus", "associator", "musicPackage", "albumVip", "voiceBookVip"):
            node = _active(key)
            if node and to_int(node.get("vipLevel")) > 0:
                active_codes[key] = to_int(node.get("vipCode"))

        return {
            "is_svip": redplus is not None,
            "has_black_vip": associator is not None or redplus is not None,
            "has_music_package": music_package is not None,
            "red_vip_level": to_int(data.get("redVipLevel")),
            "red_vip_annual_count": to_int(data.get("redVipAnnualCount")),
            "active_codes": active_codes,
        }


def vip_flags_from_type(vip_type: int) -> Tuple[bool, bool, bool]:
    """把 ``vipType`` 解析成 ``(is_svip, has_black_vip, has_music_package)``。

    ``vipType`` 有两种编码，实测同一个 SVIP 账号会同时出现::

        account.vipType = 11     # 标量：10 黑胶VIP + 1 音乐包
        profile.vipType = 110    # 位掩码：100 SVIP + 10 黑胶VIP

    位掩码的位::

        1    音乐包
        10   黑胶VIP
        100  黑胶SVIP

    另有文档记载的标量值 20 也表示 SVIP。因为两种编码混在一起，
    这里按位判断（``0`` 与 ``1`` 单独处理，避免把 0 当成「音乐包」）。

    仅在权威接口 ``/api/music-vip-membership/client/vip/info`` 不可用时才用。
    """
    try:
        v = int(vip_type or 0)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        return False, False, False

    is_svip = bool(v & 100) or v == 20
    has_black_vip = is_svip or bool(v & 10) or v in (10, 11)
    has_music_package = bool(v & 1) or v == 1
    return is_svip, has_black_vip, has_music_package


def _wy():
    from . import wy_source

    return wy_source()

def _safe(fn, default):
    try:
        return fn()
    except Exception as e:
        logger.debug("网易云补充接口失败: %s", e)
        return default

# ──────────────────────────────────────────────────────────────
# 登录态校验与续期
# ──────────────────────────────────────────────────────────────

# 服务端给 MUSIC_U 的 Max-Age 是 180 天，续期后会重新计时
COOKIE_TTL_SECONDS = 180 * 24 * 3600

def should_renew(expires_at: int, now: int, days: int) -> bool:
    """凭据是否该续期了（剩余有效期不足 ``days`` 天）。

    ``days <= 0`` 表示关闭自动续期；``expires_at`` 缺失（老版本凭据）时续一次
    把有效期补上。
    """
    if days <= 0:
        return False
    if expires_at <= 0:
        return True
    return expires_at - now <= days * 86400

def cookie_expiry() -> int:
    """当前会话里 ``MUSIC_U`` 的过期时间（unix 秒；拿不到返回 0）。

    登录 / 续期响应带 ``Set-Cookie: MUSIC_U=...; Max-Age=15552000``，
    requests 会把它记进 cookie jar，这里读出来当作凭据的真实有效期，
    比「拿到的时刻 + 180 天」准。
    """
    src = _wy()
    if src is None:
        return 0
    try:
        for cookie in src._session.cookies:  # noqa: SLF001
            if cookie.name == "MUSIC_U" and cookie.expires:
                return int(cookie.expires)
    except Exception as e:
        logger.debug("读取网易云 cookie 有效期失败: %s", e)
    return 0

def login_state() -> str:
    """服务端校验登录态，返回 ``"ok"`` / ``"expired"`` / ``"offline"``。

    区分「凭据真的失效」和「网络不通」很重要：前者应当把界面置为未登录并引导
    重新登录，后者绝不能因此清掉本地的登录态。
    """
    src = _wy()
    if src is None:
        return "offline"
    if not _safe(lambda: src.is_logged_in(), False):
        return "expired"
    try:
        resp = src._eapi_post("/api/nuser/account/get", {})  # noqa: SLF001
    except Exception as e:
        logger.debug("网易云登录态校验请求失败: %s", e)
        return "offline"
    if not isinstance(resp, dict):
        return "offline"
    code = resp.get("code")
    if code == 200:
        return "ok" if (resp.get("account") or resp.get("profile")) else "expired"
    # 未登录 / 需要登录：301 未登录、250 需要验证
    if code in (250, 301, 302, 401, 403):
        return "expired"
    # 其它错误码当作暂时性问题，不敢据此把用户登出
    return "offline"

def refresh_login() -> str:
    """续期登录凭据，成功返回新的 cookie 串（失败返回空串）。

    接口是 ``/api/login/token/refresh``。实测（2026-09，真实账号）::

        eapi                      code=200
        music.163.com/api（weapi 加密体）  code=200
        music.163.com/weapi       空响应体（与其它接口一致，被风控拦）

    **服务端不在响应体里回 cookie，而是用 ``Set-Cookie`` 换发一个新的
    MUSIC_U**（值会变、Max-Age 重新计时 180 天），所以要重新导出会话 cookie
    再落盘。换发后旧的 MUSIC_U 仍然有效（不会把已登录的会话踢下线），因此
    即使落盘失败也不会导致掉登录。
    """
    src = _wy()
    if src is None or not _safe(lambda: src.is_logged_in(), False):
        return ""
    before = _safe(lambda: src.get_cookie_str(), "") or ""
    resp = _safe(lambda: src._eapi_post("/api/login/token/refresh", {}), {}) or {}  # noqa: SLF001
    if not isinstance(resp, dict) or resp.get("code") != 200:
        logger.debug(
            "网易云登录续期失败: %s",
            resp.get("code") if isinstance(resp, dict) else resp,
        )
        return ""
    after = _safe(lambda: src.get_cookie_str(), "") or ""
    if after == before:
        # 值没变也算成功（服务端可能只延长了 Max-Age），照样把有效期带回去
        logger.debug("网易云登录续期成功，但 cookie 值未变化")
    return after or before

# ──────────────────────────────────────────────────────────────
# 歌词（逐行 + 翻译 + 罗马音 + 逐字）
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LyricBundle:
    """一次 ``/api/song/lyric`` 取回来的全部歌词数据。

    ``word`` / ``word_translation`` / ``word_roma`` 是**逐字**版本（yrc / ytlyric /
    yromalrc），只有部分歌曲有；界面在没有的时候退化成按行时间摊出来的伪动态。
    """

    lrc: str = ""
    translation: str = ""
    roma: str = ""
    word: str = ""
    word_translation: str = ""
    word_roma: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.lrc or self.word)


def fetch_lyric_bundle(
    song_id: str,
    *,
    want_translation: bool = True,
    want_roma: bool = False,
    want_word: bool = True,
) -> Optional[LyricBundle]:
    """一次请求取回逐行歌词 + 翻译 + 罗马音 + 逐字歌词。

    为什么要合成一次：老代码是「``get_lyric`` 取逐行」+「``fetch_translation``
    取翻译」两次请求，而**同一个接口**在带上 ``yv=-1, ytv=-1, yrv=-1`` 之后
    就会把逐字歌词一并下发（实测 2026-10，登录与否都一样）。于是三次需求
    （逐行 / 翻译 / 逐字）合并成一次网络往返。

    失败（网络异常、返回不是 JSON、``code`` 非 200）返回 ``None``，
    由调用方决定是否退回逐音源的 ``get_lyric``。
    """
    song_id = str(song_id or "").strip()
    if not song_id:
        return None
    src = _wy()
    if src is None:
        return None

    data: Dict[str, object] = {
        "id": song_id,
        "lv": -1,
        "tv": -1 if want_translation else 0,
        "rv": -1 if want_roma else 0,
        "kv": -1,
    }
    if want_word:
        data.update({"yv": -1, "ytv": -1 if want_translation else 0,
                     "yrv": -1 if want_roma else 0})
    try:
        resp = src._eapi_post("/api/song/lyric", data)  # noqa: SLF001
    except Exception as e:
        logger.debug("获取歌词失败 [%s]: %s", song_id, e)
        return None
    if not isinstance(resp, dict) or resp.get("code") != 200:
        return None

    def _pick(key: str) -> str:
        node = resp.get(key)
        if isinstance(node, dict):
            return str(node.get("lyric") or "")
        return ""

    bundle = LyricBundle(
        lrc=_pick("lrc"),
        translation=_pick("tlyric"),
        roma=_pick("romalrc"),
        word=_pick("yrc"),
        word_translation=_pick("ytlyric"),
        word_roma=_pick("yromalrc"),
    )
    if bundle.is_empty:
        return None
    return bundle


def fetch_translation(
    song_id: str, *, want_translation: bool = True, want_roma: bool = False
) -> Tuple[str, str]:
    """获取翻译歌词与罗马音歌词（逐音源兜底路径用）。

    FMCL 的 ``get_lyric`` 其实请求了 ``tv``/``rv`` 却只读 ``lrc.lyric``，
    翻译与罗马音形同虚设；这里真正接上。
    """
    if not song_id or not (want_translation or want_roma):
        return "", ""
    bundle = fetch_lyric_bundle(
        song_id, want_translation=want_translation, want_roma=want_roma, want_word=False
    )
    if bundle is None:
        return "", ""
    return bundle.translation, bundle.roma

# ──────────────────────────────────────────────────────────────
# 发现页
# ──────────────────────────────────────────────────────────────

def recommend_playlists(limit: int = 30) -> List[Dict]:
    """推荐歌单。"""
    src = _wy()
    if src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post(  # noqa: SLF001
            "/api/personalized/playlist", {"limit": int(limit), "total": True, "n": 1000}
        ),
        {},
    ) or {}
    out: List[Dict] = []
    for item in resp.get("result") or []:
        try:
            out.append(
                {
                    "id": str(item.get("id")),
                    "name": str(item.get("name") or ""),
                    "cover": str(item.get("picUrl") or ""),
                    "play_count": int(item.get("playCount") or 0),
                    "track_count": int(item.get("trackCount") or 0),
                    "description": str(item.get("copywriter") or ""),
                    "source": "wy",
                }
            )
        except Exception:
            continue
    return out

def new_songs(limit: int = 12) -> List[MusicInfo]:
    """最新音乐。"""
    src = _wy()
    if src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post("/api/personalized/newsong", {"limit": int(limit)}),  # noqa: SLF001
        {},
    ) or {}
    songs = []
    for item in resp.get("result") or []:
        song = item.get("song") if isinstance(item, dict) else None
        if isinstance(song, dict):
            songs.append(song)
        elif isinstance(item, dict) and item.get("id"):
            songs.append(item)
    return _parse_songs(src, songs)

def toplists() -> List[Dict]:
    """官方排行榜（飙升榜 / 新歌榜 / 热歌榜 …）。"""
    src = _wy()
    if src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post("/api/toplist/detail", {}),  # noqa: SLF001
        {},
    ) or {}
    out: List[Dict] = []
    for item in resp.get("list") or []:
        try:
            cover = str(item.get("coverImgUrl") or "")
            if not cover:
                cover = str((item.get("playlist") or {}).get("coverImgUrl") or "")
            out.append(
                {
                    "id": str(item.get("id") or (item.get("playlist") or {}).get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "cover": cover,
                    "update_frequency": str(item.get("updateFrequency") or ""),
                    "track_count": int(item.get("trackCount") or 0),
                    "source": "wy",
                }
            )
        except Exception:
            continue
    return [t for t in out if t["id"]]

def hot_searches() -> List[Dict]:
    """热搜榜（接口不可用时返回空）。"""
    src = _wy()
    if src is None:
        return []
    for path in ("/api/search/hot/detail", "/api/search/hot"):
        resp = _safe(lambda p=path: src._eapi_post(p, {}), None)  # noqa: SLF001
        if isinstance(resp, dict) and resp.get("code") == 200:
            data = resp.get("data") or resp.get("result") or {}
            items = data.get("hots") if isinstance(data, dict) else data
            out = []
            for item in items or []:
                try:
                    out.append(
                        {
                            "keyword": str(item.get("searchWord") or item.get("first") or ""),
                            "score": int(item.get("score") or item.get("second") or 0),
                        }
                    )
                except Exception:
                    continue
            if out:
                return out[:20]
    return []

def daily_recommend() -> List[MusicInfo]:
    """每日推荐歌曲（需登录）。"""
    src = _wy()
    if src is None or not _safe(lambda: src.is_logged_in(), False):
        return []
    resp = _safe(
        lambda: src._eapi_post("/api/v1/discovery/recommend/songs", {"limit": 100}),  # noqa: SLF001
        {},
    ) or {}
    songs = ((resp.get("data") or {}).get("dailySongs")) or (resp.get("recommend") or [])
    return _parse_songs(src, songs)

def playlist_tracks(playlist_id: str) -> List[MusicInfo]:
    """歌单歌曲（复用音源已有的多路由实现）。"""
    src = _wy()
    if src is None:
        return []
    return _safe(lambda: src.get_playlist_tracks(str(playlist_id)), []) or []

def playlist_meta(playlist_id: str) -> Dict:
    """歌单基本信息（名称 / 封面 / 简介 / 作者）。"""
    src = _wy()
    if src is None:
        return {}
    resp = _safe(
        lambda: src._eapi_post(  # noqa: SLF001
            "/api/v6/playlist/detail", {"id": str(playlist_id), "n": 1, "s": 0}
        ),
        {},
    ) or {}
    pl = resp.get("playlist") or {}
    if not pl:
        return {}
    return {
        "id": str(pl.get("id") or playlist_id),
        "name": str(pl.get("name") or ""),
        "cover": str(pl.get("coverImgUrl") or ""),
        "description": str(pl.get("description") or ""),
        "track_count": int(pl.get("trackCount") or 0),
        "play_count": int(pl.get("playCount") or 0),
        "creator": str((pl.get("creator") or {}).get("nickname") or ""),
    }

# ──────────────────────────────────────────────────────────────
# 漫游（个性化推荐流）
# ──────────────────────────────────────────────────────────────

# 私人 FM 每次只给 3 首，且不认 limit，要凑一批就得连打几次
ROAM_FM_BATCH = 3

def roam_batch(size: int = 15) -> List[Tuple[MusicInfo, str]]:
    """取一批个性化推荐，返回 ``[(MusicInfo, 推荐理由), …]``。

    三级来源，越靠前越「懂你」：

    1. **私人 FM** ``/api/v1/radio/get``（需登录）：每次 3 首、连打就是一条
       取之不尽的流，服务端还会给出推荐理由（「你关注的音乐人新歌」
       「NO.43 飙升榜」「小众推荐」…），实测每次返回的都不重复；
    2. **每日推荐** ``/api/v1/discovery/recommend/songs``（需登录）：
       FM 拿不到时的兜底，随机抽一批，免得每次漫游都是同一批歌；
    3. **推荐新音乐** ``/api/personalized/newsong``（免登录）：没登录时至少
       还能漫游，只是推荐理由变成「新歌推荐」。
    """
    src = _wy()
    if src is None or size <= 0:
        return []

    if _safe(lambda: src.is_logged_in(), False):
        batch = _roam_from_fm(src, size)
        if batch:
            return batch
        batch = _roam_from_daily(src, size)
        if batch:
            return batch
    return _roam_from_new_songs(src, size)

def _roam_from_fm(src, size: int) -> List[Tuple[MusicInfo, str]]:
    out: List[Tuple[MusicInfo, str]] = []
    seen = set()
    rounds = max(1, (int(size) + ROAM_FM_BATCH - 1) // ROAM_FM_BATCH)
    for _ in range(rounds):
        resp = _safe(lambda: src._eapi_post("/api/v1/radio/get", {}), {}) or {}  # noqa: SLF001
        items = resp.get("data") if isinstance(resp, dict) else None
        if not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            songmid = str(item.get("id") or "")
            if not songmid or songmid in seen:
                continue
            seen.add(songmid)
            parsed = _parse_songs(src, [item])
            if parsed:
                out.append((parsed[0], str(item.get("reason") or "")))
            if len(out) >= size:
                return out
    return out

def _roam_from_daily(src, size: int) -> List[Tuple[MusicInfo, str]]:
    songs = _safe(lambda: daily_recommend(), []) or []
    if not songs:
        return []
    picked = random.sample(songs, min(size, len(songs))) if len(songs) > 1 else list(songs)
    return [(mi, "每日推荐") for mi in picked]

def _roam_from_new_songs(src, size: int) -> List[Tuple[MusicInfo, str]]:
    songs = _safe(lambda: new_songs(max(size, 10)), []) or []
    return [(mi, "新歌推荐") for mi in songs[:size]]

# ──────────────────────────────────────────────────────────────
# 搜索联想
# ──────────────────────────────────────────────────────────────

#: 联想词最多返回几条（接口自己给 6 条左右，这里只是兜住极端情况）
SUGGEST_LIMIT = 8

def parse_suggestions(payload: Any, limit: int = SUGGEST_LIMIT) -> List[str]:
    """从联想接口的响应里取出关键词列表（纯函数，方便离线回归）。

    结构：``{"code": 200, "result": {"allMatch": [{"keyword": "周杰伦", ...}, …]}}``。
    去重、去空白、按接口给的顺序截断 —— **顺序就是网易云的排序**，不要重排。
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    out: List[str] = []
    seen = set()
    for item in result.get("allMatch") or []:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword") or "").strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        out.append(keyword)
        if len(out) >= max(1, int(limit)):
            break
    return out

def search_suggest(keyword: str, limit: int = SUGGEST_LIMIT) -> List[str]:
    """搜索联想词（网易云的「猜你想搜」）。

    实测（2026-10）：``/api/search/suggest/keyword`` 走 eapi 通道，
    ``周杰`` → 周杰伦 / 周杰伦歌单 / 周杰伦告白气球 … 六条，而且是**纯关键词**，
    正好对上搜索框的联想需求（另一个 ``/api/search/suggest/web`` 给的是
    歌曲 / 歌手 / 专辑 / 歌单实体，界面上要另做排版，这里不用）。

    失败一律返回空列表：联想接口不通不该弹错误，输入框照样能用。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    src = _wy()
    if src is None:
        return []
    try:
        resp = src._eapi_post(  # noqa: SLF001 - 复用已实现的加密通道
            "/api/search/suggest/keyword", {"s": keyword}
        )
    except Exception as e:
        logger.debug("搜索联想失败 [%s]: %s", keyword, e)
        return []
    if not isinstance(resp, dict) or resp.get("code") != 200:
        return []
    return parse_suggestions(resp, limit=limit)

# ──────────────────────────────────────────────────────────────
# 歌手页 / 搜索结果置顶
# ──────────────────────────────────────────────────────────────

# 歌手信息是公开数据，一个会话内基本不变：来回点同一个歌手时不再重复请求
_ARTIST_CACHE: Dict[str, Dict] = {}
_ARTIST_CACHE_LOCK = threading.Lock()
_ARTIST_CACHE_LIMIT = 40

def search_artists(keyword: str, limit: int = 10) -> List[Dict]:
    """按名字搜索歌手（``/api/search/get`` type=100）。

    这个接口同时给出粉丝数（``fansSize``），是「粉丝」一栏的唯一来源 ——
    歌手详情接口本身不带粉丝数。
    """
    keyword = (keyword or "").strip()
    src = _wy()
    if not keyword or src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post(  # noqa: SLF001
            "/api/search/get",
            {"s": keyword, "type": 100, "limit": int(limit), "offset": 0},
        ),
        {},
    ) or {}
    out: List[Dict] = []
    for item in ((resp.get("result") or {}).get("artists") or []):
        try:
            out.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "cover": str(item.get("picUrl") or ""),
                    "alias": [str(a) for a in (item.get("alias") or [])],
                    "music_size": to_int(item.get("musicSize")),
                    "album_size": to_int(item.get("albumSize")),
                    "mv_size": to_int(item.get("mvSize")),
                    "fans_size": to_int(item.get("fansSize")),
                }
            )
        except Exception:
            continue
    return [a for a in out if a["id"]]

def search_playlists(keyword: str, limit: int = 6) -> List[Dict]:
    """按关键词搜索歌单（搜索结果置顶卡片用，type=1000）。"""
    keyword = (keyword or "").strip()
    src = _wy()
    if not keyword or src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post(  # noqa: SLF001
            "/api/search/get",
            {"s": keyword, "type": 1000, "limit": int(limit), "offset": 0},
        ),
        {},
    ) or {}
    out: List[Dict] = []
    for item in ((resp.get("result") or {}).get("playlists") or []):
        try:
            out.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "cover": str(item.get("coverImgUrl") or ""),
                    "track_count": to_int(item.get("trackCount")),
                    "play_count": to_int(item.get("playCount")),
                    "creator": str((item.get("creator") or {}).get("nickname") or ""),
                }
            )
        except Exception:
            continue
    return [p for p in out if p["id"]]

def pick_artist(artists: List[Dict], name: str) -> Dict:
    """在搜索结果里挑最像的那个歌手。

    优先级：完全同名 > 名字互相包含 > 第一个。同名/同前缀有多个时取粉丝多的，
    避免选到「周杰伦.」这种小号。
    """
    if not artists:
        return {}
    name = (name or "").strip().lower()
    if not name:
        return dict(artists[0])

    def _fans(a: Dict) -> int:
        return to_int(a.get("fans_size"))

    exact = [a for a in artists if str(a.get("name", "")).lower() == name]
    if exact:
        return dict(max(exact, key=_fans))
    loose = [
        a
        for a in artists
        if name in str(a.get("name", "")).lower() or str(a.get("name", "")).lower() in name
    ]
    if loose:
        return dict(max(loose, key=_fans))
    return dict(artists[0])

def artist_page(artist_id: str = "", name: str = "") -> Dict:
    """歌手页所需的全部数据：歌手信息 + 热门歌曲。

    只给名字时先搜 id（点歌曲里的歌手名走这条路），给了 id 就直接取详情。
    返回 ``{"id","name","cover","alias","brief_desc","music_size","album_size",
    "mv_size","fans_size","hot_songs": [MusicInfo]}``；找不到返回 ``{}``。
    """
    artist_id = str(artist_id or "").strip()
    name = str(name or "").strip()
    if not artist_id and not name:
        return {}
    if artist_id:
        with _ARTIST_CACHE_LOCK:
            cached = _ARTIST_CACHE.get(artist_id)
        if cached is not None:
            return dict(cached)

    page = _safe(lambda: _artist_page_uncached(artist_id, name), {}) or {}
    if page and page.get("id"):
        with _ARTIST_CACHE_LOCK:
            if len(_ARTIST_CACHE) >= _ARTIST_CACHE_LIMIT:
                _ARTIST_CACHE.clear()
            _ARTIST_CACHE[str(page["id"])] = dict(page)
    return page

def _artist_page_uncached(artist_id: str, name: str) -> Dict:
    src = _wy()
    if src is None:
        return {}

    # 搜索接口能给出粉丝数，详情接口给不出，所以在按名字进来时先搜一次
    match = pick_artist(search_artists(name, 5), name) if name else {}
    aid = artist_id or str(match.get("id") or "")
    if not aid:
        return {}
    # 只有搜到的确实是同一个歌手时，才敢用它（和它的粉丝数）补字段
    matched = match if str(match.get("id") or "") == aid else {}

    resp = _safe(lambda: src._eapi_post(f"/api/v1/artist/{aid}", {}), {}) or {}  # noqa: SLF001
    artist = resp.get("artist") or {}
    if not artist and not matched:
        return {}

    page = {
        "id": str(artist.get("id") or aid),
        "name": str(artist.get("name") or matched.get("name") or ""),
        "cover": str(artist.get("picUrl") or matched.get("cover") or ""),
        "alias": [str(a) for a in (artist.get("alias") or matched.get("alias") or [])],
        "brief_desc": str(artist.get("briefDesc") or ""),
        "music_size": to_int(artist.get("musicSize")) or to_int(matched.get("music_size")),
        "album_size": to_int(artist.get("albumSize")) or to_int(matched.get("album_size")),
        "mv_size": to_int(artist.get("mvSize")) or to_int(matched.get("mv_size")),
        "fans_size": to_int(matched.get("fans_size")),
        "hot_songs": _parse_songs(src, resp.get("hotSongs") or []),
    }
    return page

# ──────────────────────────────────────────────────────────────
# 专辑页
# ──────────────────────────────────────────────────────────────

_ALBUM_CACHE: Dict[str, Dict] = {}
_ALBUM_CACHE_LOCK = threading.Lock()

# 专辑类型 -> 中文标签
ALBUM_TYPE_LABELS = {
    "album": "专辑",
    "single": "单曲",
    "ep": "EP",
    "compilation": "精选集",
    "live": "现场",
}

def same_title(left: str, right: str) -> bool:
    """两个名字是不是同一个（忽略空白与大小写，允许包含关系）。

    用来核对「别的音源的专辑 id 撞到网易云某张专辑」这种情况：id 查出来的
    专辑名如果跟曲目里的专辑名对不上，就不能用这个 id。
    """
    a = "".join(str(left or "").split()).lower()
    b = "".join(str(right or "").split()).lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a

def search_albums(keyword: str, limit: int = 10) -> List[Dict]:
    """按关键词搜索专辑（``/api/search/get`` type=10）。"""
    keyword = (keyword or "").strip()
    src = _wy()
    if not keyword or src is None:
        return []
    resp = _safe(
        lambda: src._eapi_post(  # noqa: SLF001
            "/api/search/get",
            {"s": keyword, "type": 10, "limit": int(limit), "offset": 0},
        ),
        {},
    ) or {}
    out: List[Dict] = []
    for item in ((resp.get("result") or {}).get("albums") or []):
        try:
            artist = item.get("artist") or {}
            out.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "cover": str(item.get("picUrl") or ""),
                    "artist_id": str(artist.get("id") or ""),
                    "artist_name": str(artist.get("name") or ""),
                    "size": to_int(item.get("size")),
                    "publish_time": to_int(item.get("publishTime")),
                    "company": str(item.get("company") or ""),
                }
            )
        except Exception:
            continue
    return [a for a in out if a["id"]]

def pick_album(albums: List[Dict], name: str) -> Dict:
    """在搜索结果里挑最像的那张专辑：同名 > 歌手也同名 > 名字互相包含 > 第一个。"""
    if not albums:
        return {}
    wanted = (name or "").strip().lower()
    if not wanted:
        return dict(albums[0])
    exact = [a for a in albums if str(a.get("name", "")).lower() == wanted]
    if exact:
        # 同名的多张里挑曲目多的（通常才是「原版专辑」）
        return dict(max(exact, key=lambda a: to_int(a.get("size"))))
    loose = [
        a
        for a in albums
        if wanted in str(a.get("name", "")).lower() or str(a.get("name", "")).lower() in wanted
    ]
    if loose:
        return dict(max(loose, key=lambda a: to_int(a.get("size"))))
    return dict(albums[0])

def album_page(album_id: str = "", name: str = "", artist: str = "") -> Dict:
    """专辑页所需的全部数据：专辑信息 + 曲目列表。

    只给名字时先搜 id（点歌曲里的专辑名走这条路），给了 id 就直接取详情。
    返回 ``{"id","name","cover","artist_names","artists","publish_text","company",
    "type_text","version","size","description","songs": [MusicInfo]}``；找不到
    返回 ``{}``。
    """
    album_id = str(album_id or "").strip()
    name = str(name or "").strip()
    if not album_id and not name:
        return {}
    if album_id:
        with _ALBUM_CACHE_LOCK:
            cached = _ALBUM_CACHE.get(album_id)
        if cached is not None:
            return dict(cached)

    page = _safe(lambda: _album_page_uncached(album_id, name, artist), {}) or {}
    if page and page.get("id"):
        with _ALBUM_CACHE_LOCK:
            if len(_ALBUM_CACHE) >= _ARTIST_CACHE_LIMIT:
                _ALBUM_CACHE.clear()
            _ALBUM_CACHE[str(page["id"])] = dict(page)
    return page

def _album_page_uncached(album_id: str, name: str, artist: str) -> Dict:
    src = _wy()
    if src is None:
        return {}

    match = search_albums(name, 10) if name else []
    if not album_id:
        picked = pick_album(match, name)
        album_id = str(picked.get("id") or "")
    if not album_id:
        return {}

    resp = _safe(lambda: src._eapi_post(f"/api/v1/album/{album_id}", {}), {}) or {}  # noqa: SLF001
    album = resp.get("album") or {}
    if not album:
        return {}

    artists = album.get("artists") or album.get("artist") or []
    if isinstance(artists, dict):
        artists = [artists]
    names = [str(a.get("name") or "") for a in artists if isinstance(a, dict)]
    names = [n for n in names if n]

    publish_ms = to_int(album.get("publishTime"))
    page = {
        "id": str(album.get("id") or album_id),
        "name": str(album.get("name") or name or ""),
        "cover": str(album.get("picUrl") or ""),
        "artists": [
            {"id": str(a.get("id") or ""), "name": str(a.get("name") or "")}
            for a in artists
            if isinstance(a, dict) and a.get("name")
        ],
        "artist_names": "、".join(names),
        "artist_hint": artist,
        "publish_time": publish_ms,
        "publish_text": _format_date(publish_ms),
        "company": str(album.get("company") or ""),
        "type_text": ALBUM_TYPE_LABELS.get(
            str(album.get("type") or "").strip().lower(), str(album.get("type") or "")
        ),
        # 「录音室版 / 现场版」这类发行版本；歌曲百科要用（见 song_release）
        "version": str(album.get("subType") or ""),
        "size": to_int(album.get("size")),
        "description": str(album.get("description") or album.get("briefDesc") or ""),
        "songs": _parse_songs(src, resp.get("songs") or []),
    }
    return page

# ──────────────────────────────────────────────────────────────
# 歌曲百科（展开播放页的「百科」标签页）
# ──────────────────────────────────────────────────────────────
#
# 网易云把歌曲百科塞在一个「区块页」接口里：一次请求同时给出「音乐百科」
# 「回忆坐标」「相似歌曲」「相关歌单」四种区块，每个区块里是一串 **creative**
# （曲风 / 语种 / BPM / 推荐标签 / 获奖成就 / 影视节目 / 乐评 …）。
#
# 结构固定但层级很深，而且**取值位置随字段类型而变**：
#
#     creative.uiElement.textLinks[].text              语种 / BPM
#     creative.resources[].uiElement.mainTitle.title   曲风 / 推荐标签 / 获奖 …
#
# 行的**顺序由这里定**，不跟着服务端走：官方客户端把「发行时间 / 发行版本」
# 插在「语种」和「BPM」中间，而这两项压根不在百科接口里 —— 它们来自专辑详情
# （``publishTime`` 与 ``subType``），所以要在这里合流。
#
# 以上都是实测（2026-09-19，eapi 通道）的结果。另有几件当时顺手试过的事值得记：
# ``/api/song/wiki/{summary,detail,get}`` 一类路径在这台机器上全是 404（文档里
# 那个 ``/song/wiki/summary`` 只是 ``block/page`` 的别名），weapi 域名照旧返回
# 空响应，所以这条链路也走 eapi。

WIKI_ENDPOINT = "/api/song/play/about/block/page"
#: 音乐百科所在区块的 code（同一个响应里还有相似歌曲、相关歌单等）
WIKI_BASIC_BLOCK = "SONG_PLAY_ABOUT_SONG_BASIC"

#: 面板里的行顺序：``(取值键, 默认标题)``。
#: 服务端给不出标题时用默认标题，给得出就用服务端的（它改了名字这边跟着变）。
WIKI_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("songTag", "曲风"),
    ("language", "语种"),
    # 这两项由 :func:`song_release` 从专辑详情补进来，不是百科接口的字段
    ("release_time", "发行时间"),
    ("release_version", "发行版本"),
    ("bpm", "BPM"),
    ("songBizTag", "推荐标签"),
    ("songAward", "获奖成就"),
    ("entertainment", "影视节目"),
    ("songComment", "乐评"),
)

#: 「曲风」是 ``父类-子类`` 的两级结构，官方客户端画成 ``父类·子类``
GENRE_LEVEL_SEPARATOR = "-"
GENRE_LEVEL_JOIN = "·"
#: 多值之间的分隔：曲风用「/」分开几条风格链，其余按顿号列举
GENRE_JOIN = " / "
VALUE_JOIN = "、"

_WIKI_CACHE: Dict[str, Dict] = {}
_WIKI_CACHE_LOCK = threading.Lock()

def _node_title(node) -> str:
    """取 ``{"mainTitle": {"title": …}}`` 里的标题（网易云 ``uiElement`` 的通用形状）。"""
    if not isinstance(node, dict):
        return ""
    main = node.get("mainTitle")
    if not isinstance(main, dict):
        return ""
    return str(main.get("title") or "").strip()

def _node_text(node) -> str:
    """取 ``textLinks`` 里的一条文字。"""
    if not isinstance(node, dict):
        return ""
    return str(node.get("text") or "").strip()

def _creative_values(creative: Dict) -> List[str]:
    """从一个 creative 里取出值（取值位置随字段类型而变，两处都扫一遍）。"""
    ui = creative.get("uiElement") or {}
    if not isinstance(ui, dict):
        return []
    out = [_node_text(node) for node in (ui.get("textLinks") or [])]
    for res in creative.get("resources") or []:
        if isinstance(res, dict):
            out.append(_node_title(res.get("uiElement")))
    return [v for v in out if v]

def parse_song_wiki(payload: Dict, release: Optional[Dict] = None) -> Dict:
    """把百科区块页响应解析成「音乐百科」面板要显示的行（**纯函数，不联网**）。

    ``release`` 是 :func:`song_release` 的结果，用来补上接口里没有的
    「发行时间 / 发行版本」。返回 ``{"rows": [{"label", "value"}, …]}``；
    一行都凑不出来时返回 ``{}``，由界面显示空态。
    """
    values: Dict[str, List[str]] = {}
    labels: Dict[str, str] = {}

    blocks = ((payload or {}).get("data") or {}).get("blocks") or []
    for block in blocks:
        if not isinstance(block, dict) or block.get("code") != WIKI_BASIC_BLOCK:
            continue
        for creative in block.get("creatives") or []:
            if not isinstance(creative, dict):
                continue
            key = str(creative.get("creativeType") or "").strip()
            if not key:
                continue
            label = _node_title(creative.get("uiElement"))
            if label and key not in labels:
                labels[key] = label
            found = _creative_values(creative)
            if found:
                values.setdefault(key, []).extend(found)

    release = release or {}
    if str(release.get("publish_text") or "").strip():
        values["release_time"] = [str(release["publish_text"]).strip()]
    if str(release.get("version") or "").strip():
        values["release_version"] = [str(release["version"]).strip()]

    rows: List[Dict[str, str]] = []
    for key, default_label in WIKI_FIELDS:
        items = [str(v).strip() for v in (values.get(key) or []) if str(v).strip()]
        if not items:
            continue
        if key == "songTag":
            # ``二次元-歌声合成`` -> ``二次元·歌声合成``（只换层级那一横杠，
            # 免得把名字里本来就有连字符的风格切坏）
            items = [v.replace(GENRE_LEVEL_SEPARATOR, GENRE_LEVEL_JOIN, 1) for v in items]
            text = GENRE_JOIN.join(items)
        else:
            text = VALUE_JOIN.join(items)
        rows.append({"label": labels.get(key) or default_label, "value": text})

    return {"rows": rows} if rows else {}

def pick_wiki_song(
    songs: List[MusicInfo], name: str, interval: int = 0
) -> Optional[MusicInfo]:
    """在搜索结果里挑出「同一首歌」（核对不过返回 ``None``）。

    别的音源没有网易云的歌曲 id，只能按名字找。同名翻唱满天飞，所以**两道关**
    一起过：歌名对得上（:func:`same_title`）+ 时长在
    :data:`~app.sources.base.DURATION_TOLERANCE` 秒以内。完全同名的优先于
    「名字互相包含」的（「晴天」不该匹配到「晴天（深情版）」，除非只有它）。

    拿不到时长（本地文件没解析出标签就是这种）时不认「包含」关系 ——
    否则「不存在的歌」会匹配到搜索结果里那个碰巧包含这几个字的名字。
    """
    name = str(name or "").strip()
    if not songs or not name:
        return None

    def _flat(text) -> str:
        return "".join(str(text or "").split()).lower()

    wanted = _flat(name)
    has_duration = int(interval or 0) > 0
    exact: List[MusicInfo] = []
    loose: List[MusicInfo] = []
    for mi in songs:
        if mi is None or not same_title(mi.name, name):
            continue
        is_exact = _flat(mi.name) == wanted
        if not is_exact and not has_duration:
            continue
        if not duration_matches(mi, int(interval or 0)):
            continue
        (exact if is_exact else loose).append(mi)

    picked = exact or loose
    return picked[0] if picked else None

def search_song_match(
    name: str, singer: str = "", interval: int = 0, limit: int = 10
) -> Optional[MusicInfo]:
    """按「歌名 + 歌手」搜一首歌并核对，返回匹配到的曲目（没匹配到返回 ``None``）。

    本地音乐的封面 / 歌词靠它定位，所以**核对比搜索本身重要**：歌名对得上
    （:func:`same_title`）**并且**时长在 15 秒容差内才算 —— 翻唱、伴奏、Live
    就是被时长这一关挡掉的（判据见 :func:`pick_wiki_song`）。
    """
    name = str(name or "").strip()
    if not name:
        return None
    src = _wy()
    if src is None:
        return None
    keyword = f"{name} {singer}".strip()
    songs = _safe(lambda: src.search(keyword, 1, int(limit)), []) or []
    return pick_wiki_song(list(songs), name, interval)

def song_release(album_id: str = "", album_name: str = "", artist: str = "") -> Dict:
    """发行信息：发行时间与发行版本（供歌曲百科补「百科接口没有」的两行）。

    官方客户端那两行不是百科数据，而是专辑的 ``publishTime`` 与 ``subType``
    —— 实测（2026-09-19）「塔与少女的无题诗」所在的专辑 αrtist5 正好是
    2018-11-30 / 录音室版，与客户端显示一致。专辑里没有 ``subType`` 时退回
    专辑类型（专辑 / 单曲）。

    **id 要拿名字核对**（与 :class:`~app.bridges.album.AlbumController` 同一套
    判断）：网易云曲目自带的 ``album_id`` 可以直用，别的平台的编号拿去查可能
    撞出一张风马牛不相及的专辑 —— 那会把发行时间安到一首无关的歌上。
    """
    album_id = str(album_id or "").strip()
    album_name = str(album_name or "").strip()

    page = album_page(album_id, "", artist) if album_id else {}
    if not page or (album_name and not same_title(page.get("name"), album_name)):
        page = album_page("", album_name, artist)

    if not page:
        return {}
    return {
        "publish_text": str(page.get("publish_text") or ""),
        "version": str(page.get("version") or page.get("type_text") or ""),
    }

def song_wiki(
    song_id: str = "",
    *,
    name: str = "",
    singer: str = "",
    album_name: str = "",
    album_id: str = "",
    interval: int = 0,
) -> Dict:
    """歌曲百科：「百科」标签页要显示的全部内容。

    ``wy`` 曲目直接给 :attr:`Track.songmid`；其它音源（QQ / 酷我 / 酷狗 / 咪咕 /
    本地文件）没有网易云的歌曲 id，就按「歌名 + 歌手」搜一次再核对（与歌手页 /
    专辑页同一套「按名字定位」思路），核对不过就当作这首歌没有百科。

    返回 ``{"song_id", "rows"}``；查不到返回 ``{}``。结果按歌曲 id 缓存。
    """
    song_id = str(song_id or "").strip()
    name = str(name or "").strip()

    def _cached(key: str) -> Optional[Dict]:
        with _WIKI_CACHE_LOCK:
            hit = _WIKI_CACHE.get(key)
        return dict(hit) if hit is not None else None

    def _store(key: str, page: Dict) -> None:
        with _WIKI_CACHE_LOCK:
            if len(_WIKI_CACHE) >= _ARTIST_CACHE_LIMIT:
                _WIKI_CACHE.clear()
            _WIKI_CACHE[key] = dict(page)

    if song_id:
        hit = _cached(song_id)
        if hit is not None:
            return hit
    else:
        if not name:
            return {}
        matched = search_song_match(name, singer, interval)
        if matched is None or not matched.songmid:
            return {}
        song_id = str(matched.songmid)
        # 核对上的那首歌自带权威专辑信息，比拿名字再搜一遍专辑可靠
        album_id = album_id or str(matched.album_id or "")
        album_name = album_name or str(matched.album_name or "")
        hit = _cached(song_id)
        if hit is not None:
            return hit

    src = _wy()
    if src is None:
        return {}
    payload = _safe(
        lambda: src._eapi_post(WIKI_ENDPOINT, {"songId": song_id}),  # noqa: SLF001
        {},
    ) or {}
    if not isinstance(payload, dict):
        payload = {}

    page = parse_song_wiki(payload, song_release(album_id, album_name, singer))
    page["song_id"] = song_id
    # 请求真的得到回答就缓存，哪怕这首歌确实没有百科 —— 否则每次切回标签页
    # 都要再问一遍。失败响应（只有 code / message，没有 data）不缓存，下次还有机会。
    if isinstance(payload, dict) and payload.get("data") is not None:
        _store(song_id, page)
    return page

def _format_date(ms: int) -> str:
    """毫秒时间戳 -> ``2025-04-01``（0 或非法返回空串）。"""
    if ms <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ms / 1000))
    except (OverflowError, OSError, ValueError):
        return ""

def _parse_songs(src, songs) -> List[MusicInfo]:
    """把网易云歌曲 JSON 转成 ``MusicInfo``（优先复用音源自身的解析器）。"""
    if not songs:
        return []
    try:
        parser = getattr(src, "_parse_song_detail_songs", None)
        if parser is not None:
            parsed = parser(songs)
            if parsed:
                return parsed
    except Exception as e:
        logger.debug("复用网易云解析器失败，改用内置解析: %s", e)

    out: List[MusicInfo] = []
    for item in songs:
        try:
            artists = item.get("ar") or item.get("artists") or []
            album = item.get("al") or item.get("album") or {}
            out.append(
                MusicInfo(
                    name=str(item.get("name") or ""),
                    singer="、".join(str(a.get("name") or "") for a in artists),
                    source="wy",
                    songmid=str(item.get("id") or ""),
                    album_name=str(album.get("name") or ""),
                    album_id=str(album.get("id") or ""),
                    interval=int((item.get("dt") or item.get("duration") or 0) / 1000),
                    img=str(album.get("picUrl") or ""),
                    fee=int((item.get("privilege") or {}).get("fee") or item.get("fee") or 0),
                )
            )
        except Exception:
            continue
    return out
