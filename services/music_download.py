"""多源回退下载编排与临时文件规则（阶段 1 任务 1.4-B，形态 2：逻辑与界面切分）。

对应宿主是 `ui/app_music.py` 的 `MusicPlayerMixin` 里**取流 + 跨源兜底**那一段：
`_fetch_online_song` / `_try_download_from_source` / `_download_fallback_result` /
`_resolve_fallback` / `_music_download_to_temp` / `_music_get_download_headers` /
`_music_get_download_cookies` / `_discard_temp_file` / `_music_cleanup_temp_files` /
`_music_try_bili_risk_retry`。

## 切缝判据（与 1.4-A 同一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件？** 全部不是 → 整段进服务。
唯一需要界面配合的是"提示已切换音源"（原文 `self.after(0, lambda: self.set_status(...))`）
—— 它既有 i18n 文案又有 `after`，所以**留在界面**，服务通过注入的
``notify_source`` 回调在正确时机叫一声（调用时机与原文字节对应）。

## 服务不做什么

不弹窗、不起线程、不调 `after`、不碰控件、不 import 任何 GUI 栈、不 import `ui.*`、
不用 `ui.i18n` 的文案函数。

### B 站风控重试为什么能住在服务里

原文 `_music_try_bili_risk_retry` 自己**不起线程**：它是在下载线程（
`_music_play_online_url` 起的那个）里同步跑的阻塞流程。滚动滑块验证页由
`services.music_risk_captcha.run_captcha_flow` 提供（本地 HTTP 服务 + 系统浏览器），
本模块只负责**编排**：取风控参数 → 通知界面开/关提示窗 → 跑验证 → 换 grisk_id →
带 grisk_id 重试兜底。界面把三个对话框动作作为 sink 注入（每个 sink 内部自己
`after(0, ...)` 切主线程），服务侧只调用注入进来的对象 —— 与 `services/music_smtc.py`
的"在注入对象上调 `after`"是同一条契约，区别是本模块连 `after` 都不碰。

## 临时文件的所有权

`self._music_temp_files` 由 `__init_music` 创建，`poc/_probe_1_4b_attrs.py` 量过：
范围外的读写点只有那一次初始化，所以它可以交给服务管。1.4-A 踩过"两份缓存各自
为政"的坑（`_music_metadata_cache` 必须采纳**同一个 OrderedDict**），这里用同一条
办法：界面把**自己的那个 list 对象**交给 `DownloadContext.temp_files`，服务就地
增删，范围外的旧读者看到的仍是同一份内容。

## 离线可测性

`DownloadContext` 把四个注入缝收在一起，默认值就是真实实现（`requests.get` /
`services.music_source.MUSIC_SOURCES` / `services.music_audio.transcode_audio_to_wav`），
**行为与原文一字不差**；测试传假对象即可全程离线 —— 不联网、不真下载、不转码。
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

import requests
from logzero import logger

from services import music_audio, music_risk_captcha, music_source

# ── 下载参数（原文 `_music_download_to_temp` 里写死的字面量）──────────────
#: 下载请求 UA（原文写死的完整字符串）
DOWNLOAD_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_CHUNK_SIZE = 65536
#: 临时文件前缀（原文 f"fmcl_{safe_name}_"）
TEMP_PREFIX = "fmcl_"
#: 文件名提示里保留的最大字符数（原文 `[:50]`）
TEMP_NAME_MAX = 50
#: 文件名提示里额外允许的标点（原文 `c.isalnum() or c in "._- "`）
TEMP_NAME_PUNCT = "._- "
#: 临时文件数量上限（原文 `while len(...) > 10`）
MAX_TEMP_FILES = 10
#: 无法从 Content-Type 判断时的默认后缀
DEFAULT_EXTENSION = ".mp3"

#: 请求档位失败时的回退顺序（原文 `for fallback_q in ["flac", "320k", "128k"]`）
QUALITY_RETRY_ORDER = ("flac", "320k", "128k")

#: B 站音源 id（原文 `MUSIC_SOURCES.get("bili")`）
BILI_SOURCE_ID = "bili"


def is_non_audio_response(content_type: str) -> bool:
    """快速失败的判据：HTML 错误页/JSON 响应不浪费带宽（原文那一行 if）。"""
    return (
        "text/html" in content_type
        or content_type.startswith("text/")
        or "json" in content_type
    )


def pick_extension(url: str, content_type: str) -> str:
    """按 Content-Type 与 URL 路径决定临时文件后缀。

    用 `urlparse` 取路径判断后缀：播放 URL 常带查询参数，`endswith` 会失效
    （原文注释里写明了这个坑）。判断顺序与原文一致：flac → ogg → m4a → mp3。
    """
    url_path = urlparse(url).path.lower()
    if "flac" in content_type or url_path.endswith(".flac"):
        return ".flac"
    if "ogg" in content_type or url_path.endswith(".ogg"):
        return ".ogg"
    if "m4a" in content_type or url_path.endswith(".m4a") or url_path.endswith(".m4s"):
        return ".m4a"
    return DEFAULT_EXTENSION


def safe_temp_name(name_hint: str) -> str:
    """文件名提示 → 只留字母数字与 ``._- `` 的安全片段（原文那行 join + [:50]）。"""
    return "".join(c for c in name_hint if c.isalnum() or c in TEMP_NAME_PUNCT)[:TEMP_NAME_MAX]


def trim_temp_files(temp_files: List[str], limit: int = MAX_TEMP_FILES) -> List[str]:
    """把临时文件表裁到上限，返回**被删掉**的文件路径（便于日志与测试）。

    原文：``while len(self._music_temp_files) > 10: old = pop(0)`` 后
    ``os.remove(old)``（失败只吞掉）。注意原文的裁剪**只在没有转码时**发生 ——
    这条由 `download_to_temp` 的调用点保留，不在这里做。
    """
    removed: List[str] = []
    while len(temp_files) > limit:
        old = temp_files.pop(0)
        removed.append(old)
        try:
            os.remove(old)
        except Exception:
            pass
    return removed


def discard_temp_file(temp_path: str, temp_files: Optional[List[str]] = None) -> None:
    """删除无效的临时文件并移出缓存列表（原文 `_discard_temp_file`）。"""
    try:
        os.remove(temp_path)
    except Exception:
        pass
    if temp_files is not None and temp_path in temp_files:
        temp_files.remove(temp_path)


def cleanup_temp_files(temp_files: List[str]) -> None:
    """清理所有缓存的临时文件（原文 `_music_cleanup_temp_files`）。

    逐个 `os.path.exists` 再删（失败吞掉），最后 `clear()`（**无论删成功没有**
    都清空列表，与原文一致）。
    """
    for fp in temp_files:
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass
    temp_files.clear()


@dataclass
class DownloadContext:
    """下载编排的注入缝与共享状态。

    * ``temp_files``：界面交进来的**同一个 list 对象**（`self._music_temp_files`）；
    * ``sources``：音源表，None 表示读 ``services.music_source.MUSIC_SOURCES``；
    * ``http_get``：``requests.get`` 的替身，None 表示用真的；
    * ``transcoder``：``music_audio.transcode_audio_to_wav`` 的替身，None 表示用真的。
    """

    temp_files: List[str] = field(default_factory=list)
    sources: Optional[Mapping[str, Any]] = None
    http_get: Optional[Callable[..., Any]] = None
    transcoder: Optional[Callable[[str], Optional[str]]] = None
    timeout: int = DOWNLOAD_TIMEOUT
    chunk_size: int = DOWNLOAD_CHUNK_SIZE
    max_temp_files: int = MAX_TEMP_FILES


def get_source(source_id: str, *, sources: Optional[Mapping[str, Any]] = None) -> Any:
    """取音源单例；不存在返回 None。

    默认实现**每次调用**都读 ``services.music_source.MUSIC_SOURCES`` 这个模块级
    全局名，所以 monkeypatch 那个名字能真的换掉音源表。
    """
    table = music_source.MUSIC_SOURCES if sources is None else sources
    return table.get(source_id)


def get_download_headers(source_id: str, *, sources: Optional[Mapping[str, Any]] = None) -> Dict:
    """音源下载所需的附加请求头（如 B站 upos CDN 的 Referer）。

    音源缺失或抛异常 → 空 dict（原文两段 try/except 都吞掉）。
    """
    src = get_source(source_id, sources=sources)
    if src is not None:
        try:
            return src.get_download_headers()
        except Exception:
            pass
    return {}


def get_download_cookies(
    source_id: str, *, sources: Optional[Mapping[str, Any]] = None
) -> Optional[Dict]:
    """音源下载所需的附加 cookies（如 B站 dash URL 的 buvid 一致性）。

    音源缺失或抛异常 → None（原文返回 None 而不是空 dict，语义不同必须保留：
    `requests` 的 `cookies=None` 与 `cookies={}` 都会清空会话 cookie，但原文是 None）。
    """
    src = get_source(source_id, sources=sources)
    if src is not None:
        try:
            return src.get_download_cookies()
        except Exception:
            pass
    return None


def source_display_name(source_id: str, *, sources: Optional[Mapping[str, Any]] = None) -> str:
    """音源的展示名（原文 `getattr(src, "source_name", None) or source_id`）。

    音源缺失时回退成 id 本身 —— 状态栏提示仍然有内容，不会显示空白。
    """
    src = get_source(source_id, sources=sources)
    return getattr(src, "source_name", None) or source_id


def download_to_temp(
    url: str,
    name_hint: str = "",
    extra_headers: Optional[Dict] = None,
    extra_cookies: Optional[Dict] = None,
    *,
    ctx: DownloadContext,
) -> Optional[str]:
    """下载在线音频流到临时文件。

    逐条照搬原文 `_music_download_to_temp`：**整个函数只有一个 try** —— 任何一步
    失败（含 mkstemp 之后写盘失败）都走同一个 except，记 warning 后返回 None。
    写盘中途失败留下的半截临时文件原文也不清理，此处一致。

    pygame 的 SDL_mixer 不支持 m4a 容器（B站 dash/部分平台音源）：下载后自动转码
    为 wav，转码成功则删原文件、换成转码结果入表；转码失败保留原文件交由 pygame
    尝试。**数量裁剪只在未转码的分支发生**（原文如此，顺序不能挪）。
    """
    try:
        headers = {"User-Agent": DOWNLOAD_USER_AGENT}
        if extra_headers:
            headers.update(extra_headers)
        getter = requests.get if ctx.http_get is None else ctx.http_get
        resp = getter(
            url, timeout=ctx.timeout, stream=True, headers=headers, cookies=extra_cookies
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        # 快速失败：HTML 错误页/非音频响应不浪费带宽
        if is_non_audio_response(content_type):
            logger.warning(f"下载响应非音频（Content-Type: {content_type}）: {url[:120]}")
            return None
        ext = pick_extension(url, content_type)
        fd, temp_path = tempfile.mkstemp(
            suffix=ext, prefix=f"{TEMP_PREFIX}{safe_temp_name(name_hint)}_"
        )
        os.close(fd)
        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=ctx.chunk_size):
                if chunk:
                    f.write(chunk)
        if os.path.getsize(temp_path) == 0:
            os.remove(temp_path)
            return None
        ctx.temp_files.append(temp_path)
        convert = music_audio.transcode_audio_to_wav if ctx.transcoder is None else ctx.transcoder
        converted = convert(temp_path)
        if converted:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            if temp_path in ctx.temp_files:
                ctx.temp_files.remove(temp_path)
            ctx.temp_files.append(converted)
            return converted
        # 限制临时文件数量
        trim_temp_files(ctx.temp_files, ctx.max_temp_files)
        return temp_path
    except Exception as e:
        logger.warning(f"下载音频流失败: {e}")
        return None


def resolve_fallback(
    online_info: Any, quality: str, *, resolver: Optional[Callable[..., Any]] = None
) -> Optional[Tuple[Any, str]]:
    """跨源兜底解析：返回 (匹配歌曲, 播放URL) 或 None（原文 `_resolve_fallback`）。

    真正的搜索 + 候选筛选 + 逐档位试 URL 在 `services.music_source.resolve_track`
    （1.4 已搬走的音源包）；本函数只保留原文那一层 try/except 与日志。
    """
    try:
        fn = music_source.resolve_track if resolver is None else resolver
        return fn(online_info, quality)
    except Exception as e:
        logger.warning(f"跨源兜底解析失败: {e}")
        return None


def download_fallback_result(
    fallback: Tuple[Any, str],
    quality: str,
    *,
    ctx: DownloadContext,
    notify_source: Optional[Callable[[str], None]] = None,
) -> Optional[Tuple[str, Any, str]]:
    """下载兜底结果并校验，成功返回 (临时文件路径, 实际歌曲信息, 音质)。

    跨源兜底内部会尝试多个音质，无法精确得知最终命中档位，此处沿用用户请求的
    音质用于显示（原文注释如此）。

    文件头与时长**双重校验**用的是 `services/music_audio` 里那两个函数（1.4-A 已搬走），
    两者是 `and` 短路关系 —— 文件头不过就不查时长，此处保持一致（`validate_audio_duration`
    会读元数据缓存，多调一次会改变缓存内容）。
    """
    fb_info, fb_url = fallback
    logger.info(f"跨源兜底命中 [{fb_info.source}]: {fb_info.name} - {fb_info.singer}")
    result_path = download_to_temp(
        fb_url,
        fb_info.name,
        extra_headers=get_download_headers(fb_info.source, sources=ctx.sources),
        extra_cookies=get_download_cookies(fb_info.source, sources=ctx.sources),
        ctx=ctx,
    )
    if result_path:
        if (
            music_audio.validate_audio_file_header(result_path)
            and music_audio.validate_audio_duration(result_path, fb_info.interval)
        ):
            if notify_source is not None:
                notify_source(fb_info.source)
            return result_path, fb_info, quality
        discard_temp_file(result_path, ctx.temp_files)
    return None


def try_download_from_source(
    online_info: Any, quality: str, *, ctx: DownloadContext
) -> Tuple[Optional[str], Optional[str], str]:
    """尝试从指定音源获取 URL 并下载，校验文件有效后返回 (临时文件路径, 实际URL, 实际音质)。

    逐条照搬原文 `_try_download_from_source`：
    * 音源不存在 → ``(None, None, quality)``；
    * 请求档位失败（音源对不可用音质返回 None）→ 按 ``flac → 320k → 128k`` 回退，
      命中即记下 `actual_quality`；
    * 取 URL 抛异常 → 记 warning 后当没取到（**注意此时返回值里的 quality 是原始档位**）；
    * 下载失败/文件头无效/时长超差 → 删掉临时文件并返回 None（触发跨源兜底）。
    """
    src = get_source(online_info.source, sources=ctx.sources)
    if src is None:
        return None, None, quality
    url = None
    actual_quality = quality
    try:
        url = src.get_music_url(online_info, quality)
        if not url:
            # 请求档位失败时按高到低回退（各音源对不可用音质返回 None）
            for fallback_q in QUALITY_RETRY_ORDER:
                if fallback_q != quality:
                    url = src.get_music_url(online_info, fallback_q)
                    if url:
                        actual_quality = fallback_q
                        break
    except Exception as e:
        logger.warning(f"获取在线URL失败 [{online_info.source}]: {e}")
    if not url:
        logger.warning(f"无法获取播放URL [{online_info.source}]: {online_info.name}")
        return None, None, quality
    temp_path = download_to_temp(
        url,
        online_info.name,
        extra_headers=get_download_headers(online_info.source, sources=ctx.sources),
        extra_cookies=get_download_cookies(online_info.source, sources=ctx.sources),
        ctx=ctx,
    )
    if not temp_path:
        return None, url, actual_quality
    # 文件头 + 时长双重校验：无效文件视为获取失败（触发跨源兜底）
    if not music_audio.validate_audio_file_header(temp_path):
        logger.warning(f"下载文件无效（非音频文件头）[{online_info.source}]: {online_info.name}")
        discard_temp_file(temp_path, ctx.temp_files)
        return None, url, actual_quality
    if not music_audio.validate_audio_duration(temp_path, online_info.interval):
        logger.warning(f"下载文件为试听/截断片段 [{online_info.source}]: {online_info.name}")
        discard_temp_file(temp_path, ctx.temp_files)
        return None, url, actual_quality
    return temp_path, url, actual_quality


def try_bili_risk_retry(
    online_info: Any,
    quality: str,
    fallback_quality: Optional[str] = None,
    *,
    ctx: DownloadContext,
    resolve: Optional[Callable[..., Any]] = None,
    download_fallback: Optional[Callable[..., Any]] = None,
    run_flow: Optional[Callable[..., Any]] = None,
    open_dialog: Optional[Callable[..., None]] = None,
    update_dialog: Optional[Callable[..., None]] = None,
    close_dialog: Optional[Callable[..., None]] = None,
    event_factory: Optional[Callable[[], Any]] = None,
    notify_source: Optional[Callable[[str], None]] = None,
) -> Optional[Tuple[str, Any, str]]:
    """B站风控验证：通知界面弹提示窗 + 浏览器滑块验证，通过后带 grisk_id 自动重试兜底。

    Returns:
        验证通过且兜底成功: (临时文件路径, 实际歌曲信息, 音质)；否则 None

    三个对话框动作（开窗/改状态文字/关窗）由界面注入：服务侧在**与原文相同的时刻**
    调用它们（开窗在起验证流程之前、关窗在 finally 里），切主线程由注入方负责。
    """
    src = get_source(BILI_SOURCE_ID, sources=ctx.sources)
    if src is None:
        return None
    try:
        risk = src.take_pending_risk()
    except Exception:
        return None
    if not risk:
        return None

    dialog_ref: Dict[str, object] = {}
    stop_event = (event_factory or threading.Event)()

    def on_status(text: str):
        if update_dialog is not None:
            update_dialog(dialog_ref, text)

    if open_dialog is not None:
        open_dialog(dialog_ref, stop_event)
    try:
        flow = music_risk_captcha.run_captcha_flow if run_flow is None else run_flow
        result = flow(
            risk["gt"], risk["challenge"], on_status=on_status, stop_event=stop_event
        )
    except Exception as e:
        logger.warning(f"风控验证流程异常: {e}")
        result = None
    finally:
        if close_dialog is not None:
            close_dialog(dialog_ref)
    if not result:
        return None

    # 下面两步与原文一样**不吞异常**：validate_risk / set_gaia_vtoken 抛错时
    # 会一路冒到下载线程顶部，与原文的异常边界一致（不要好心加 try）。
    grisk_id = src.validate_risk(
        risk["token"],
        result["geetest_challenge"],
        result["geetest_seccode"],
        result["geetest_validate"],
    )
    if not grisk_id:
        logger.warning("B站风控验证未通过")
        return None
    src.set_gaia_vtoken(grisk_id)
    logger.info("B站风控验证通过，自动重试跨源兜底")

    def _download_fallback(fb, q):
        if download_fallback is not None:
            return download_fallback(fb, q)
        return download_fallback_result(fb, q, ctx=ctx, notify_source=notify_source)

    fallback = (resolve or resolve_fallback)(online_info, fallback_quality or quality)
    if not fallback:
        return None
    return _download_fallback(fallback, quality)


def fetch_online_song(
    online_info: Any,
    quality: str,
    fallback_quality: Optional[str] = None,
    *,
    risk_retry: Callable[..., Any],
    ctx: DownloadContext,
    try_download: Optional[Callable[..., Any]] = None,
    resolve: Optional[Callable[..., Any]] = None,
    download_fallback: Optional[Callable[..., Any]] = None,
    notify_source: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[str], Any, str]:
    """获取在线歌曲并下载到临时文件，失败时自动跨源兜底。

    Args:
        online_info: 用户点播的歌曲信息
        quality: 原音源实际尝试的音质（"auto" 已解析为具体档位）
        fallback_quality: 跨源兜底的音质选择（可为 "auto"，表示从最高可用音质开始
            尝试；None 时沿用 quality）
        risk_retry: **必填**的 B站风控重试入口（签名 ``(info, quality, fallback_q)``）。
            原文在这里无条件调 `self._music_try_bili_risk_retry(...)`，而那条路要弹窗、
            要开浏览器，本质上离不开界面，所以不做默认值 —— 由界面传自己的
            `_music_try_bili_risk_retry`，测试传替身。

    Returns:
        (临时文件路径, 实际播放的歌曲信息, 实际音质)：
        全部失败时路径为 None（info 保持原值）
    """
    result_path = None
    result_info = online_info
    result_quality = quality
    if online_info is None:
        return None, result_info, result_quality

    def _try_download(info, q):
        if try_download is not None:
            return try_download(info, q)
        return try_download_from_source(info, q, ctx=ctx)

    def _download_fallback(fb, q):
        if download_fallback is not None:
            return download_fallback(fb, q)
        return download_fallback_result(fb, q, ctx=ctx, notify_source=notify_source)

    result_path, _, result_quality = _try_download(online_info, quality)
    if result_path:
        return result_path, result_info, result_quality

    fallback_q = fallback_quality or quality
    # 原音源失败 -> 跨源兜底（仅尝试一次，不递归）
    logger.info(
        f"原音源不可用 [{online_info.source}]: {online_info.name} - {online_info.singer}，开始跨源兜底"
    )
    fallback = (resolve or resolve_fallback)(online_info, fallback_q)
    if fallback:
        result = _download_fallback(fallback, quality)
        if result:
            return result

    # B站触发风控时：通知界面弹验证码，用户手动完成后带 grisk_id 自动重试兜底
    risk_retry_result = risk_retry(online_info, quality, fallback_q)
    if risk_retry_result:
        return risk_retry_result

    # 全部音源均失败：汇总一条日志（单源失败细节已在 resolve_track 内降为 debug）
    logger.warning(
        f"跨源兜底失败，所有音源均不可用: {online_info.name} - {online_info.singer} [{online_info.source}]"
    )
    return None, result_info, result_quality


__all__ = [
    "BILI_SOURCE_ID",
    "DEFAULT_EXTENSION",
    "DOWNLOAD_CHUNK_SIZE",
    "DOWNLOAD_TIMEOUT",
    "DOWNLOAD_USER_AGENT",
    "DownloadContext",
    "MAX_TEMP_FILES",
    "QUALITY_RETRY_ORDER",
    "TEMP_NAME_MAX",
    "TEMP_NAME_PUNCT",
    "TEMP_PREFIX",
    "cleanup_temp_files",
    "discard_temp_file",
    "download_fallback_result",
    "download_to_temp",
    "fetch_online_song",
    "get_download_cookies",
    "get_download_headers",
    "get_source",
    "is_non_audio_response",
    "pick_extension",
    "resolve_fallback",
    "safe_temp_name",
    "source_display_name",
    "trim_temp_files",
    "try_bili_risk_retry",
    "try_download_from_source",
]
