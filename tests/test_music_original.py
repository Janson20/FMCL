"""网易云原唱识别算法测试脚本

读取 CSV 测试用例，逐条搜索并校验原唱标记结果。

用法:
    python tests/test_music_original.py [测试CSV路径] [--limit 30] [--no-save]

CSV 格式 (UTF-8，首行为表头，兼容 Excel 导出的 BOM):
    歌曲名称,期望原唱,期望结果,更火版本歌手,备注
    春天里,汪峰,标记,旭日阳刚,同名不同曲
    周杰伦,,不标记,,歌手搜索不标记

字段说明:
    歌曲名称: 搜索关键词（必填）
    期望原唱: 期望标记的原唱歌手（支持 "/" 或 "、" 分隔多个别名，如 "阿牛/陈庆祥"）
    期望结果: 标记 / 不标记（默认标记）
    更火版本歌手: 仅作参考展示
    备注: 仅作参考展示

判定规则:
    期望结果=标记: 有原唱标记，且标记行的歌手名或策展徽章名命中期望原唱，且标记行置顶
    期望结果=不标记: 无任何原唱标记

结果文件自动输出到 CSV 同目录: <文件名>_results.csv
退出码: 全部通过为 0，否则为 1
"""

import argparse
import csv
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.music_source.wy import NetEaseMusicSource  # noqa: E402

COLUMN_ALIASES = {
    "name": ["歌曲名称", "歌名", "搜索词", "keyword"],
    "expected": ["期望原唱", "原唱", "expected_original"],
    "result": ["期望结果", "期望行为", "expected_result"],
    "popular": ["更火版本歌手", "更火翻唱", "热门版本", "popular_cover"],
    "note": ["备注", "说明", "note", "remark"],
    "multi": ["期望多原唱", "多原唱", "expected_multi"],
}

# 常见日文/中文名等价别名（网易返回日文名，测试数据用中文名时需匹配）
ARTIST_ALIASES = {
    "初音未来": ["初音ミク"],
    "初音ミク": ["初音未来"],
    "镜音铃": ["鏡音リン"],
    "镜音连": ["鏡音レン"],
    "镜音リン": ["镜音铃"],
    "镜音レン": ["镜音连"],
    "巡音流歌": ["巡音ルカ"],
    "巡音露卡": ["巡音ルカ"],
}

DEFAULT_CSV = Path(__file__).resolve().parent / "music_original_cases.csv"


def _normalize_header(raw_header):
    mapped = {}
    for idx, cell in enumerate(raw_header):
        cell = cell.strip()
        for key, aliases in COLUMN_ALIASES.items():
            if cell in aliases:
                mapped[key] = idx
                break
    return mapped


def _fmt_date(ts):
    if not ts:
        return "N/A"
    try:
        return datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return "BAD"


def _expectations(value):
    parts = [p.strip() for p in re.split(r"[、/|]", str(value)) if p.strip()]
    expanded = []
    for part in parts:
        if part not in expanded:
            expanded.append(part)
        for m in re.finditer(r"[（(]([^（）()]*)[）)]", part):
            alias = m.group(1).strip()
            if alias and alias not in expanded:
                expanded.append(alias)
    for part in list(expanded):
        for alias in ARTIST_ALIASES.get(part, []):
            if alias not in expanded:
                expanded.append(alias)
    return expanded


def _matched(marked, expectations):
    for exp in expectations:
        low = exp.lower()
        if low in marked.singer.lower() or low in (marked.original_name or "").lower():
            return True
    return False


def _run_case(src, keyword, limit):
    try:
        results = src.search(keyword, page=1, limit=limit)
    except Exception as exc:
        return {"error": str(exc)}
    marks = [info for info in results if info.is_original]
    marked = marks[0] if marks else None
    pinned = bool(marked and results and marked.songmid == results[0].songmid)
    return {
        "results": results,
        "marks": marks,
        "marked": marked,
        "pinned": pinned,
    }


def _evaluate(row, outcome):
    expect_result = (row.get("result") or "标记").strip() or "标记"
    expectations = _expectations(row.get("expected"))
    multi_expects = [p.strip() for p in str(row.get("multi") or "").split(",") if p.strip()]
    if "error" in outcome:
        return "ERROR", "搜索异常: %s" % outcome["error"]
    marked = outcome.get("marked")
    if expect_result == "不标记":
        if marked is None:
            return "PASS", ""
        return "FAIL", "期望不标记，实际标记: %s - %s" % (marked.name, marked.singer)
    if marked is None:
        return "FAIL", "无标记 (期望原唱: %s)" % (row.get("expected") or "")
    if not expectations:
        return "FAIL", "期望原唱为空"
    if not _matched(marked, expectations):
        return "FAIL", "标记: %s - %s (期望: %s, 徽章: %s)" % (
            marked.name, marked.singer, row.get("expected"), marked.original_name or "无")
    if not outcome.get("pinned"):
        return "FAIL", "标记未置顶"
    if multi_expects:
        marked_names = [m.singer + (m.original_name or "") for m in outcome.get("marks", [])]
        missing = [
            e for e in multi_expects
            if not any(e.lower() in mn.lower() for mn in marked_names)
        ]
        if missing:
            return "FAIL", "期望多原唱未全部标记: %s (实际: %s)" % (
                missing, [m.singer for m in outcome.get("marks", [])])
    return "PASS", ""


def _format_marked(marked):
    if marked is None:
        return "无"
    badge = " [徽章:%s]" % marked.original_name if marked.original_name else ""
    return "%s - %s | %s%s" % (marked.name, marked.singer, _fmt_date(marked.publish_time), badge)


def main():
    parser = argparse.ArgumentParser(description="网易云原唱识别算法测试")
    parser.add_argument("csv", nargs="?", default=str(DEFAULT_CSV), help="测试用例 CSV 路径")
    parser.add_argument("--limit", type=int, default=30, help="每首歌搜索条数 (默认 30)")
    parser.add_argument("--no-save", action="store_true", help="不输出结果 CSV")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print("错误: CSV 文件不存在: %s" % csv_path)
        print("参考模板: %s" % DEFAULT_CSV)
        return 2

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        raw_rows = list(reader)
    if not raw_rows:
        print("错误: CSV 为空")
        return 2

    header = _normalize_header(raw_rows[0])
    if "name" not in header:
        print("错误: 缺少表头列，需要包含: %s" % " / ".join(COLUMN_ALIASES["name"]))
        return 2

    src = NetEaseMusicSource()
    rows = []
    passed = failed = errored = 0
    total_time = 0.0

    for raw in raw_rows[1:]:
        if not raw or not raw[header["name"]].strip():
            continue
        row = {key: (raw[idx].strip() if idx < len(raw) else "") for key, idx in header.items()}
        keyword = row["name"]
        outcome = _run_case(src, keyword, args.limit)
        status, message = _evaluate(row, outcome)

        marked = outcome.get("marked")
        record = {
            "歌曲名称": keyword,
            "期望原唱": row.get("expected") or "",
            "期望结果": (row.get("result") or "标记").strip() or "标记",
            "状态": status,
            "标记歌曲": marked.name if marked else "",
            "标记歌手": marked.singer if marked else "",
            "徽章原唱": marked.original_name if marked else "",
            "发布时间": _fmt_date(marked.publish_time) if marked else "",
            "更火版本歌手": row.get("popular") or "",
            "备注": row.get("note") or "",
            "详情": message,
        }
        rows.append(record)

        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        else:
            errored += 1

        print("[%s] %s" % (status, keyword))
        print("      标记: %s" % _format_marked(marked))
        if status == "FAIL":
            print("      原因: %s" % message)
            for i, info in enumerate(outcome.get("results", [])[:5]):
                print("         %d. %s" % (i, _format_marked(info)))

    print("-" * 60)
    print("总计: %d 条 | 通过: %d | 失败: %d | 错误: %d" % (passed + failed + errored, passed, failed, errored))

    if not args.no_save:
        out_path = csv_path.with_name(csv_path.stem + "_results.csv")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            if rows:
                writer.writeheader()
                writer.writerows(rows)
        print("结果已保存: %s" % out_path)

    return 0 if failed == 0 and errored == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
