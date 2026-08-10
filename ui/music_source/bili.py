"""哔哩哔哩 音源插件（仅用于跨源兜底，不参与在线搜索主界面）

从 bilibili 搜索视频，通过 dash 接口提取纯音频流播放。
歌词接口不可用，返回 None。

说明: 搜索接口需要 WBI 签名（密钥从 /x/web-interface/nav 匿名获取并缓存），
部分接口需要 buvid3 匿名 cookie。
"""

import hashlib
import logging
import random
import re
import time
import urllib.parse
import uuid
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name

logger = logging.getLogger("music_source.bili")

BILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/wbi/search/type"
BILI_NAV_API = "https://api.bilibili.com/x/web-interface/nav"
BILI_PAGELIST_API = "https://api.bilibili.com/x/player/pagelist"
BILI_PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
BILI_RISK_REGISTER_API = "https://api.bilibili.com/x/gaia-vgate/v1/register"
BILI_RISK_VALIDATE_API = "https://api.bilibili.com/x/gaia-vgate/v1/validate"

# 搜索结果标题中的 <em class="keyword"> 高亮标签
_TITLE_TAG_RE = re.compile(r"<[^>]+>")

# 风控验证交互用的请求头
_RISK_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://search.bilibili.com/",
    "Origin": "https://search.bilibili.com",
}


class RiskControlError(Exception):
    """B站触发风控，需要用户完成 geetest 滑块验证（无法自动通过）"""

    def __init__(self, v_voucher: str):
        super().__init__("B站触发 v_voucher 风控，需要完成滑块验证")
        self.v_voucher = v_voucher

# WBI 签名密钥打乱表（B站公开算法）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
# WBI 密钥缓存有效期（秒）
_WBI_KEYS_TTL = 6 * 3600


class BiliBiliMusicSource(BaseMusicSource):
    source_id = "bili"
    source_name = "哔哩哔哩"

    def __init__(self):
        super().__init__()
        self._session.headers.update({"Referer": "https://www.bilibili.com"})
        self._init_anonymous_cookies()
        # bvid -> cid 缓存，避免重复请求 pagelist
        self._cid_cache: dict = {}
        # WBI 密钥缓存
        self._wbi_keys: Optional[tuple] = None
        self._wbi_keys_expire: float = 0
        # 最近一次触发风控的验证信息（待 UI 处理），处理完清除
        self._pending_risk: Optional[dict] = None

    def _init_anonymous_cookies(self):
        """获取匿名 cookie（buvid3）

        随机生成的 buvid3 会触发 v_voucher 风控，必须先从首页 Set-Cookie
        获取官方下发的 buvid3；失败时回退为随机生成。
        """
        try:
            resp = self._session.get("https://www.bilibili.com", timeout=12)
            resp.raise_for_status()
            if "buvid3" in self._session.cookies:
                return
        except Exception as e:
            logger.warning(f"B站获取匿名 cookie 失败: {e}")
        self._session.cookies.set(
            "buvid3", str(uuid.uuid4()).replace("-", "") + "infoc", domain=".bilibili.com"
        )

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        keys = self._get_wbi_keys()
        if not keys:
            return []
        img_key, sub_key = keys
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": limit,
            "order": "totalrank",
            "platform": "pc",  # 缺失时更容易触发 v_voucher 风控
            "web_location": 1430654,
        }
        try:
            resp = self.http_get(
                BILI_SEARCH_API,
                params=_wbi_sign(params, img_key, sub_key),
                headers={
                    "Origin": "https://search.bilibili.com",
                    "Referer": f"https://search.bilibili.com/video?keyword={urllib.parse.quote(keyword)}",
                },
                timeout=12,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"B站搜索返回错误码: {data.get('code')} {data.get('message')}")
                return []
            data = data.get("data") or {}
            # v_voucher 风控（需 geetest 交互验证码）：登记验证信息并抛异常，
            # 由调用方（UI 层）弹验证码，完成后带 grisk_id 重试
            if "v_voucher" in data:
                vv = data["v_voucher"]
                logger.warning("B站搜索触发 v_voucher 风控，等待用户完成滑块验证")
                risk = self.register_risk(vv)
                if risk:
                    self._pending_risk = risk
                raise RiskControlError(vv)
            raw_list = data.get("result") or []
            # 总结果数：numResults 优先，缺失时用 numPages * 每页条数估算
            num_results = data.get("numResults") or 0
            num_pages = data.get("numPages") or 0
            self.set_search_total(num_results or (num_pages * limit if num_pages else 0))
            return self._parse_search_result(raw_list)
        except Exception as e:
            logger.warning(f"B站搜索失败: {e}")
            return []

    def _get_wbi_keys(self) -> Optional[tuple]:
        """获取 WBI 签名密钥（img_key, sub_key），匿名 nav 接口获取并缓存"""
        if self._wbi_keys and time.time() < self._wbi_keys_expire:
            return self._wbi_keys
        try:
            resp = self.http_get(BILI_NAV_API, timeout=12)
            wbi = ((resp.json().get("data") or {}).get("wbi_img") or {})
            img_url = wbi.get("img_url") or ""
            sub_url = wbi.get("sub_url") or ""
            img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
            sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
            if not img_key or not sub_key:
                logger.warning("B站 nav 接口未返回 WBI 密钥")
                return None
            self._wbi_keys = (img_key, sub_key)
            self._wbi_keys_expire = time.time() + _WBI_KEYS_TTL
            return self._wbi_keys
        except Exception as e:
            logger.warning(f"B站获取 WBI 密钥失败: {e}")
            return None

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        for item in raw_list or []:
            try:
                bvid = item.get("bvid") or ""
                if not bvid:
                    continue
                title = _TITLE_TAG_RE.sub("", item.get("title") or "")
                title = decode_name(title).strip()
                results.append(
                    MusicInfo(
                        name=title,
                        singer=item.get("author") or "",
                        source=self.source_id,
                        songmid=bvid,
                        interval=self._parse_duration(item.get("duration") or ""),
                        img=(item.get("pic") or "").replace("http://", "https://"),
                        play_count=int(item.get("play") or 0),
                    )
                )
            except Exception as e:
                logger.debug(f"解析B站视频失败: {e}")
                continue
        return results

    @staticmethod
    def _parse_duration(raw: str) -> int:
        """解析 "04:05" / "1:02:03" 为秒数，无法解析返回 0"""
        if not raw:
            return 0
        parts = [int(p) for p in raw.strip().split(":") if p.isdigit()]
        if not parts or len(parts) > 3:
            return 0
        seconds = 0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        bvid = info.songmid
        if not bvid:
            return None
        cid = self._get_cid(bvid)
        if not cid:
            return None
        try:
            resp = self.http_get(
                BILI_PLAYURL_API,
                params={"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1},
                timeout=12,
            )
            data = resp.json().get("data") or {}
            # 优先 dash 纯音频流
            audio_list = (data.get("dash") or {}).get("audio") or []
            if audio_list:
                best = max(audio_list, key=lambda a: a.get("bandwidth") or a.get("id") or 0)
                url = best.get("baseUrl") or best.get("base_url")
                if url:
                    return url.replace("http://", "https://")
            # 回退到 durl（视频流，播放器可解音频）
            durl = data.get("durl") or []
            if durl and durl[0].get("url"):
                return durl[0]["url"].replace("http://", "https://")
        except Exception as e:
            logger.warning(f"B站获取播放URL失败 [{bvid}]: {e}")
        return None

    def _get_cid(self, bvid: str) -> Optional[int]:
        if bvid in self._cid_cache:
            return self._cid_cache[bvid]
        try:
            resp = self.http_get(BILI_PAGELIST_API, params={"bvid": bvid}, timeout=12)
            pages = resp.json().get("data") or []
            if pages and pages[0].get("cid"):
                cid = int(pages[0]["cid"])
                self._cid_cache[bvid] = cid
                return cid
        except Exception as e:
            logger.warning(f"B站获取cid失败 [{bvid}]: {e}")
        return None

    # ── 歌词 ─────────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        return None

    # ── 封面 ─────────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        return info.img

    # ── 风控验证（gaia-vgate）────────────────────────

    def register_risk(self, v_voucher: str) -> Optional[dict]:
        """风控 register：获取 geetest 滑块参数。

        Returns:
            {"v_voucher", "token", "gt", "challenge"} 或 None
        """
        try:
            resp = self._session.post(
                BILI_RISK_REGISTER_API,
                data={"v_voucher": v_voucher},
                headers=_RISK_HEADERS,
                timeout=12,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"B站风控 register 失败: {data.get('code')} {data.get('message')}")
                return None
            d = data.get("data") or {}
            geetest = d.get("geetest") or {}
            token = str(d.get("token") or "")
            gt = str(geetest.get("gt") or "")
            challenge = str(geetest.get("challenge") or "")
            if not (token and gt and challenge):
                logger.warning("B站风控 register 未返回完整 geetest 参数")
                return None
            return {"v_voucher": v_voucher, "token": token, "gt": gt, "challenge": challenge}
        except Exception as e:
            logger.warning(f"B站风控 register 请求失败: {e}")
            return None

    def validate_risk(self, token: str, challenge: str, seccode: str, validate: str) -> Optional[str]:
        """滑块通过后 validate：换取 grisk_id。

        Returns:
            grisk_id 或 None（验证未通过）
        """
        try:
            resp = self._session.post(
                BILI_RISK_VALIDATE_API,
                data={
                    "challenge": challenge,
                    "seccode": seccode,
                    "token": token,
                    "validate": validate,
                },
                headers=_RISK_HEADERS,
                timeout=12,
            )
            data = resp.json()
            d = data.get("data") or {}
            if d.get("is_valid") == 1 and d.get("grisk_id"):
                return str(d["grisk_id"])
            logger.warning(f"B站风控 validate 未通过: {data.get('code')} {data.get('message')}")
            return None
        except Exception as e:
            logger.warning(f"B站风控 validate 请求失败: {e}")
            return None

    def set_gaia_vtoken(self, grisk_id: str):
        """设置验证凭证 cookie，后续搜索请求自动携带"""
        self._session.cookies.set("x-bili-gaia-vtoken", grisk_id, domain=".bilibili.com")

    def take_pending_risk(self) -> Optional[dict]:
        """取走待处理的验证信息（取后清空，UI 处理完应调用 set_gaia_vtoken）"""
        risk = self._pending_risk
        self._pending_risk = None
        return risk

    # ── 下载附加请求头/ Cookie ────────────────────────

    def get_download_headers(self) -> dict:
        """upos CDN 校验 Referer，缺失返回 403"""
        return {"Referer": "https://www.bilibili.com"}

    def get_download_cookies(self) -> dict:
        """dash URL 内的 buvid 参数必须与附带的 buvid3 cookie 一致"""
        return dict(self._session.cookies)


def _get_mixin_key(orig: str) -> str:
    """WBI mixin key：按打乱表重排并截取前 32 位"""
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def _wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    """B站 WBI 签名：加入 wts 时间戳，剔除特殊字符后按 key 排序，md5 生成 w_rid"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    signed = {}
    for key, value in params.items():
        cleaned = "".join(c for c in str(value) if c not in "!'()*")
        signed[key] = cleaned
    signed["wts"] = str(int(time.time()))
    signed = dict(sorted(signed.items()))
    signed.pop("w_rid", None)
    query = urllib.parse.urlencode(signed)
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed
