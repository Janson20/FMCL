"""文件操作工具 - Read / Write / Edit / Delete / Glob / Grep / List

参考 opencode Tool 设计：
- 所有路径操作限制在启动器工作目录内
- Write/Edit/Delete 需要用户确认（返回 FILE_EDIT_MARKER 供 agent_chat 拦截）
- Read/Search 类直接 allow
"""

import os
import glob
import re
import json
from pathlib import Path
from typing import Dict, Callable, Optional, List
from logzero import logger

from ui.agent.tools.base import ToolInfo

# 类别常量
CATEGORY_FILE = "file"

# 确认标记：写/改/删操作返回此前缀，agent_chat 识别后弹出确认框
FILE_EDIT_MARKER = "__FILE_EDIT__"

# ============ 路径安全校验 ============


def _safe_resolve(file_path: str) -> Optional[Path]:
    """将路径解析为相对于 CWD 的绝对路径，限制在 CWD 内

    Returns:
        安全的 Path 对象，或 None（路径越界或不存在于读操作中）
    """
    cwd = Path(os.getcwd()).resolve()
    target = Path(file_path)
    if not target.is_absolute():
        target = (cwd / target)
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        return None
    # 检查是否在 CWD 子树内
    try:
        resolved.relative_to(cwd)
    except ValueError:
        return None
    return resolved


def _safe_resolve_for_read(file_path: str) -> Optional[Path]:
    """安全解析读操作路径，额外检查文件是否存在"""
    resolved = _safe_resolve(file_path)
    if resolved is None:
        return None
    if not resolved.exists():
        return None
    return resolved


def _safe_resolve_for_write(file_path: str) -> Optional[Path]:
    """安全解析写操作路径，不检查是否已存在（允许新建）"""
    return _safe_resolve(file_path)


def _diff_preview(old_text: str, new_text: str, context_lines: int = 3) -> str:
    """生成简易 diff 预览"""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    result_parts = []
    # 标记不同行
    max_len = max(len(old_lines), len(new_lines))
    for i in range(max_len):
        old_line = old_lines[i].rstrip("\n\r") if i < len(old_lines) else "<none>"
        new_line = new_lines[i].rstrip("\n\r") if i < len(new_lines) else "<none>"
        if old_line != new_line:
            result_parts.append(f"- {old_line[:120]}")
            result_parts.append(f"+ {new_line[:120]}")
        elif i < 3 or i >= max_len - 3:
            result_parts.append(f"  {old_line[:120]}")
        elif i == 3 and max_len > 10:
            result_parts.append("  ...")
    return "\n".join(result_parts[:60])


# ============ 工具函数 ============


def _read_file(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """读取文件内容，支持 offset/limit 行范围"""
    file_path = params.get("filePath", "").strip()
    offset_str = params.get("offset", "1").strip()
    limit_str = params.get("limit", "").strip()

    if not file_path:
        return "错误: 缺少 filePath 参数"

    resolved = _safe_resolve_for_read(file_path)
    if resolved is None:
        return f"错误: 文件不存在或路径越界: {file_path}"

    try:
        with open(str(resolved), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except PermissionError:
        return f"错误: 无权限读取文件: {file_path}"
    except Exception as e:
        return f"❌ 读取文件 '{file_path}' 失败: {e}"

    total = len(lines)
    try:
        offset = int(offset_str) if offset_str else 1
    except ValueError:
        offset = 1
    offset = max(1, min(offset, total))

    if limit_str:
        try:
            limit = int(limit_str)
        except ValueError:
            limit = None
    else:
        limit = None

    if limit is not None and limit > 0:
        selected = lines[offset - 1:offset - 1 + limit]
    else:
        selected = lines[offset - 1:]

    result_lines = []
    for i, line in enumerate(selected):
        line_num = offset + i
        result_lines.append(f"{line_num:6d}|{line.rstrip()}")

    result = "\n".join(result_lines)
    shown = len(selected)
    header = f"文件: {file_path}\n总行数: {total}, 显示: 第{offset}行起共{shown}行\n"
    return header + result


def _write_file(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """写入/创建文件，若文件已存在生成 diff 预览"""
    file_path = params.get("filePath", "").strip()
    content = params.get("content", "")

    if not file_path:
        return "错误: 缺少 filePath 参数"
    if content is None:
        return "错误: 缺少 content 参数"

    resolved = _safe_resolve_for_write(file_path)
    if resolved is None:
        return f"错误: 路径越界，不允许写入: {file_path}"

    existed = resolved.exists()
    old_text = ""
    if existed:
        try:
            with open(str(resolved), "r", encoding="utf-8", errors="replace") as f:
                old_text = f.read()
        except Exception:
            old_text = ""

    if old_text == content:
        return "未做修改：内容与文件一致"

    diff = _diff_preview(old_text, content)

    # 返回确认标记，让 agent_chat 拦截
    confirm_data = {
        "filePath": str(resolved),
        "oldText": old_text,
        "newText": content,
        "existed": existed,
    }
    operation = "覆盖" if existed else "创建"
    summary = f"将在 {str(resolved)} {operation}文件:\n```diff\n{diff}\n```"

    return f"{FILE_EDIT_MARKER}|write|{json.dumps(confirm_data, ensure_ascii=False)}|{summary}"


def _replace_in_file(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """在文件中查找替换"""
    file_path = params.get("filePath", "").strip()
    old_str = params.get("oldStr", "")
    new_str = params.get("newStr", "")
    replace_all = params.get("replaceAll", "false").strip().lower() in ("true", "1", "yes")

    if not file_path:
        return "错误: 缺少 filePath 参数"
    if not old_str:
        return "错误: oldStr 不能为空，请使用 write 工具创建新文件"

    resolved = _safe_resolve_for_read(file_path)
    if resolved is None:
        return f"错误: 文件不存在或路径越界: {file_path}"

    if old_str == new_str:
        return "错误: oldStr 和 newStr 相同，无需修改"

    try:
        with open(str(resolved), "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"❌ 读取文件 '{file_path}' 失败: {e}"

    count = content.count(old_str)
    if count == 0:
        return "错误: 在文件中未找到匹配的 oldStr，请确保精确匹配（包括空白和缩进）"

    if count > 1 and not replace_all:
        return f"找到 {count} 处匹配，请提供更多上下文使 oldStr 唯一，或将 replaceAll 设为 true"

    if replace_all:
        new_content = content.replace(old_str, new_str)
    else:
        new_content = content.replace(old_str, new_str, 1)

    if new_content == content:
        return "未做修改：内容与文件一致"

    diff_text = _diff_preview(content, new_content)
    replacements = count if replace_all else 1

    confirm_data = {
        "filePath": str(resolved),
        "oldText": content,
        "newText": new_content,
        "existed": True,
        "replacements": replacements,
    }

    summary = f"将在 {file_path} 中替换 {replacements} 处:\n```diff\n{diff_text}\n```"
    return f"{FILE_EDIT_MARKER}|replace|{json.dumps(confirm_data, ensure_ascii=False)}|{summary}"


def _delete_file(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """删除文件"""
    file_path = params.get("filePath", "").strip()
    if not file_path:
        return "错误: 缺少 filePath 参数"

    resolved = _safe_resolve_for_read(file_path)
    if resolved is None:
        return f"错误: 文件不存在或路径越界: {file_path}"

    # 读取前 10 行作为预览
    preview = ""
    try:
        with open(str(resolved), "r", encoding="utf-8", errors="replace") as f:
            lines = [f.readline() for _ in range(10)]
            preview = "".join(line.rstrip() + "\n" for line in lines if line)
    except Exception:
        pass

    size = resolved.stat().st_size if resolved.exists() else 0
    size_str = f"{size:,} B"
    if size >= 1048576:
        size_str = f"{size / 1048576:.1f} MB"
    elif size >= 1024:
        size_str = f"{size / 1024:.1f} KB"

    confirm_data = {
        "filePath": str(resolved),
    }

    summary = f"将删除文件: {file_path}\n大小: {size_str}\n内容预览:\n{preview[:500]}"
    return f"{FILE_EDIT_MARKER}|delete|{json.dumps(confirm_data, ensure_ascii=False)}|{summary}"


def _search_files_by_name(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """Glob 模式搜索文件名"""
    pattern = params.get("pattern", "").strip()
    root_dir = params.get("rootDir", os.getcwd()).strip()
    limit_str = params.get("limit", "50").strip()

    if not pattern:
        return "错误: 缺少 pattern 参数"

    resolved_root = _safe_resolve_for_read(root_dir)
    if resolved_root is None or not resolved_root.is_dir():
        resolved_root = Path(os.getcwd()).resolve()

    try:
        limit = int(limit_str) if limit_str else 50
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 500))

    try:
        results = []
        for file_path in glob.glob(str(resolved_root / pattern), recursive=True):
            if os.path.isfile(file_path):
                resolved = _safe_resolve(file_path)
                if resolved is not None:
                    rel_path = os.path.relpath(str(resolved), os.getcwd())
                    results.append(rel_path)
            if len(results) >= limit:
                break
    except Exception as e:
        return f"❌ 搜索失败: {e}"

    if not results:
        return "未找到匹配文件"

    return f"找到 {len(results)} 个匹配文件:\n" + "\n".join(f"  {r}" for r in results)


def _search_files_by_content(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """Grep 正则搜索文件内容"""
    regex = params.get("regex", "").strip()
    file_pattern = params.get("filePattern", "*").strip()
    root_dir = params.get("rootDir", os.getcwd()).strip()
    limit_str = params.get("limit", "100").strip()

    if not regex:
        return "错误: 缺少 regex 参数"

    resolved_root = _safe_resolve_for_read(root_dir)
    if resolved_root is None or not resolved_root.is_dir():
        resolved_root = Path(os.getcwd()).resolve()

    try:
        limit = int(limit_str) if limit_str else 100
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))

    try:
        compiled = re.compile(regex)
    except re.error as e:
        return f"错误: 正则表达式无效: {e}"

    results = []
    try:
        for file_path in glob.glob(str(resolved_root / file_pattern), recursive=True):
            if not os.path.isfile(file_path):
                continue
            resolved = _safe_resolve(file_path)
            if resolved is None:
                continue
            try:
                with open(str(resolved), "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if compiled.search(line):
                            rel_path = os.path.relpath(str(resolved), os.getcwd())
                            results.append({
                                "file": rel_path,
                                "line": line_num,
                                "text": line.strip()[:200],
                            })
                            if len(results) >= limit:
                                break
            except Exception:
                continue
            if len(results) >= limit:
                break
    except Exception as e:
        return f"❌ 搜索失败: {e}"

    if not results:
        return "未找到匹配内容"

    output_lines = [f"找到 {len(results)} 条匹配:"]
    current_file = ""
    for r in results:
        if r["file"] != current_file:
            current_file = r["file"]
            output_lines.append(f"\n{current_file}:")
        output_lines.append(f"  行{r['line']:5d}: {r['text']}")
    return "\n".join(output_lines)


def _list_directory(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """列举目录内容"""
    dir_path = params.get("dirPath", os.getcwd()).strip()
    recursive = params.get("recursive", "false").strip().lower() in ("true", "1", "yes")

    resolved = _safe_resolve_for_read(dir_path)
    if resolved is None or not resolved.is_dir():
        resolved = Path(os.getcwd()).resolve()

    try:
        if recursive:
            items = []
            for root, dirs, files in os.walk(str(resolved)):
                rel_root = os.path.relpath(root, os.getcwd())
                for d in dirs:
                    items.append(f"📁 {os.path.join(rel_root, d)}/")
                for f in files:
                    fpath = os.path.join(root, f)
                    try:
                        fsize = os.path.getsize(fpath)
                    except Exception:
                        fsize = 0
                    items.append(f"📄 {os.path.join(rel_root, f)} ({_format_size(fsize)})")
        else:
            items = []
            for entry in sorted(os.listdir(str(resolved))):
                entry_path = resolved / entry
                if entry_path.is_dir():
                    items.append(f"📁 {entry}/")
                else:
                    try:
                        fsize = entry_path.stat().st_size
                    except Exception:
                        fsize = 0
                    items.append(f"📄 {entry} ({_format_size(fsize)})")
    except PermissionError:
        return f"错误: 无权限访问目录: {dir_path}"
    except Exception as e:
        return f"❌ 列出目录 '{dir_path}' 失败: {e}"

    if not items:
        return f"目录 {dir_path} 为空"
    return f"目录: {dir_path}\n共 {len(items)} 项:\n" + "\n".join(items)


def _format_size(size: int) -> str:
    if size >= 1048576:
        return f"{size / 1048576:.1f} MB"
    elif size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


# ============ 构建工具列表 ============


def _build_file_tools() -> List[ToolInfo]:
    """构建所有文件操作工具"""
    return [
        ToolInfo(
            name="read_file",
            display_name="读取文件",
            description="读取文本文件内容。支持指定行偏移量(offset)和行数限制(limit)。路径相对于启动器工作目录。",
            parameters={
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": "要读取的文件路径（相对于启动器工作目录）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based），不填则从第 1 行开始",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "读取的最大行数，不填则读取全部",
                    },
                },
                "required": ["filePath"],
            },
            category=CATEGORY_FILE,
            execute=_read_file,
            permission_action="read_file",
        ),
        ToolInfo(
            name="write_file",
            display_name="写入文件",
            description="创建或覆盖文件。路径相对于启动器工作目录。若文件已存在，会生成差异预览供确认。写入操作需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": "要写入的文件路径（相对于启动器工作目录）",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["filePath", "content"],
            },
            category=CATEGORY_FILE,
            execute=_write_file,
            permission_action="write_file",
        ),
        ToolInfo(
            name="replace_in_file",
            display_name="替换文件内容",
            description="在文件中精确查找替换文本。oldStr 必须在文件中精确匹配（包括空白和缩进）。若有多处匹配需设置 replaceAll。修改操作需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": "要修改的文件路径（相对于启动器工作目录）",
                    },
                    "oldStr": {
                        "type": "string",
                        "description": "要被替换的精确文本（必须和文件中完全一致，含空白和缩进）",
                    },
                    "newStr": {
                        "type": "string",
                        "description": "替换后的新文本（必须与 oldStr 不同）",
                    },
                    "replaceAll": {
                        "type": "boolean",
                        "description": "是否替换所有匹配项（默认 false，仅替换第一个）",
                    },
                },
                "required": ["filePath", "oldStr", "newStr"],
            },
            category=CATEGORY_FILE,
            execute=_replace_in_file,
            permission_action="replace_in_file",
        ),
        ToolInfo(
            name="delete_file",
            display_name="删除文件",
            description="删除指定文件。路径相对于启动器工作目录。删除操作需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": "要删除的文件路径（相对于启动器工作目录）",
                    },
                },
                "required": ["filePath"],
            },
            category=CATEGORY_FILE,
            execute=_delete_file,
            permission_action="delete_file",
        ),
        ToolInfo(
            name="search_files_by_name",
            display_name="搜索文件名",
            description="按 glob 模式搜索文件名。支持 ** 递归匹配和通配符 *。结果限制在启动器工作目录内。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob 模式，如 '*.py'、'**/*.json'",
                    },
                    "rootDir": {
                        "type": "string",
                        "description": "搜索根目录，不填则默认为启动器工作目录",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 50，最大 500）",
                    },
                },
                "required": ["pattern"],
            },
            category=CATEGORY_FILE,
            execute=_search_files_by_name,
            permission_action="search_files_by_name",
        ),
        ToolInfo(
            name="search_files_by_content",
            display_name="搜索文件内容",
            description="用正则表达式在文件内容中搜索。支持 filePattern 过滤文件类型。结果限制在启动器工作目录内。",
            parameters={
                "type": "object",
                "properties": {
                    "regex": {
                        "type": "string",
                        "description": "正则表达式，如 'class.*App'、'import.*os'",
                    },
                    "filePattern": {
                        "type": "string",
                        "description": "文件过滤 glob，如 '*.py'、'**/*.ts'，默认 '*'",
                    },
                    "rootDir": {
                        "type": "string",
                        "description": "搜索根目录，不填则默认为启动器工作目录",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 100，最大 500）",
                    },
                },
                "required": ["regex"],
            },
            category=CATEGORY_FILE,
            execute=_search_files_by_content,
            permission_action="search_files_by_content",
        ),
        ToolInfo(
            name="list_directory",
            display_name="列举目录",
            description="列举目录中的文件和子目录。支持递归列出。结果限制在启动器工作目录内。",
            parameters={
                "type": "object",
                "properties": {
                    "dirPath": {
                        "type": "string",
                        "description": "要列举的目录路径，不填则默认为启动器工作目录",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出所有子目录（默认 false）",
                    },
                },
                "required": [],
            },
            category=CATEGORY_FILE,
            execute=_list_directory,
            permission_action="list_directory",
        ),
    ]
