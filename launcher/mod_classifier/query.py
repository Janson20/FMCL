"""模组分类器 — Modrinth API 远程查询

通过 Modrinth API 查询模组的 client_side / server_side 字段，
判断该模组是否可在服务端运行。
"""

import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

import requests as req
from logzero import logger

from launcher.mod_classifier.shared import Classification, ModMeta

# 发送请求的最小间隔（秒）
_REQUEST_INTERVAL = 0.1
_last_request_time: float = 0.0


def _rate_limited_get(url: str, cache: Dict[str, str]) -> Optional[str]:
    """带限速的 GET 请求"""
    global _last_request_time

    if url in cache:
        return cache[url]

    now = time.time()
    since_last = now - _last_request_time
    if since_last < _REQUEST_INTERVAL:
        time.sleep(_REQUEST_INTERVAL - since_last)

    try:
        resp = req.get(url, timeout=10, headers={"User-Agent": "FMCL/2.11 (+ModClassifier)"})
        _last_request_time = time.time()
        if resp.status_code != 200:
            return None
        text = resp.text
        cache[url] = text
        return text
    except Exception as e:
        logger.debug(f"Modrinth 请求失败 [{url}]: {e}")
        _last_request_time = time.time()
        return None


def _collect_search_values(meta: ModMeta) -> List[str]:
    """收集用于搜索的关键词"""
    values = []
    for token in meta.query_tokens:
        cleaned = re.sub(r"[^a-zA-Z0-9_\-. ]", "", token).strip().lower()
        if cleaned and cleaned not in ("mod", "mods", "minecraft"):
            values.append(cleaned)
    if meta.mod_id and meta.mod_id not in values:
        cleaned = re.sub(r"[^a-zA-Z0-9_\-. ]", "", meta.mod_id).strip().lower()
        if cleaned:
            values.append(cleaned)
    return values


def _score_modrinth_hit(meta: ModMeta, hit: dict) -> int:
    """对 Modrinth 搜索命中结果打分"""
    score = 0
    slug = str(hit.get("slug", "")).lower()
    title = str(hit.get("title", "")).lower()
    mod_id_lower = meta.mod_id.lower()
    file_name_lower = meta.file_name.lower()

    # 完全匹配 slug
    if slug == mod_id_lower:
        score += 200
    elif slug and mod_id_lower and slug in mod_id_lower:
        score += 150

    # 标题匹配
    if title == mod_id_lower:
        score += 180
    elif title == meta.mod_name.lower():
        score += 160

    # 文件名匹配
    for token in _collect_search_values(meta):
        if token and (slug == token or title == token):
            score += 100

    # 修正：长度差异扣分
    len_diff = abs(len(slug) - len(mod_id_lower))
    if len_diff > 5:
        score -= 20

    return score


def _classify_from_payload(payload: dict, source_label: str) -> Classification:
    """根据 Modrinth API 返回的 project 信息判断类别"""
    client_side = str(payload.get("client_side", "unknown"))
    server_side = str(payload.get("server_side", "unknown"))
    reason = f"{source_label}: client_side={client_side}, server_side={server_side}"
    url = f"https://modrinth.com/mod/{payload.get('slug', '')}" if payload.get("slug") else ""

    if server_side == "unsupported":
        return Classification("client-only", "modrinth", reason, url)
    if server_side in ("required", "optional"):
        return Classification("server-keep", "modrinth", reason, url)
    return Classification("unknown", "modrinth", reason, url)


def lookup_modrinth(meta: ModMeta, cache: Optional[Dict[str, str]] = None) -> Optional[Classification]:
    """通过 Modrinth API 查询模组分类

    先尝试 mod_id/slug 直连，如果失败则搜索。

    Args:
        meta: 模组元数据
        cache: 可选的请求缓存字典

    Returns:
        Classification 或 None（查询无结果）
    """
    if cache is None:
        cache = {}

    # ── 1. 直连 ──
    if meta.mod_id:
        slug = meta.mod_id.strip().lower()
        url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(slug)}"
        text = _rate_limited_get(url, cache)
        if text:
            try:
                import json

                payload = json.loads(text)
                payload_slug = str(payload.get("slug", "")).lower()
                payload_title = str(payload.get("title", "")).lower()
                # 验证确实匹配
                search_vals = [v.lower() for v in _collect_search_values(meta)]
                if payload_slug in search_vals or payload_title in search_vals:
                    score = _score_modrinth_hit(meta, payload)
                    if score >= 190:
                        return _classify_from_payload(payload, "Modrinth(直连)")
            except Exception:
                pass

    # ── 2. 搜索 ──
    queries = _collect_search_values(meta)
    if not queries:
        return None

    candidates: Dict[str, Tuple[int, dict]] = {}
    for query in queries[:3]:  # 最多 3 个查询
        url = (
            "https://api.modrinth.com/v2/search?"
            f"query={urllib.parse.quote(query)}&limit=5"
            "&facets=%5B%5B%22project_type%3Amod%22%5D%5D"
        )
        text = _rate_limited_get(url, cache)
        if not text:
            continue
        try:
            import json

            data = json.loads(text)
            for hit in data.get("hits", []):
                score = _score_modrinth_hit(meta, hit)
                slug_key = str(hit.get("slug") or hit.get("project_id") or "")
                if not slug_key:
                    continue
                if slug_key in candidates:
                    if score > candidates[slug_key][0]:
                        candidates[slug_key] = (score, hit)
                else:
                    candidates[slug_key] = (score, hit)
        except Exception:
            continue

    if not candidates:
        return None

    sorted_cands = sorted(candidates.values(), key=lambda x: x[0], reverse=True)
    top_score, top_hit = sorted_cands[0]
    runner_up_score = sorted_cands[1][0] if len(sorted_cands) > 1 else 0

    # 分数不足以确信
    if top_score < 180 or (top_score - runner_up_score) < 35:
        return None

    return _classify_from_payload(top_hit, "Modrinth(搜索)")
