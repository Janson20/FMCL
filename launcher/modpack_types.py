"""整合包类型检测模块 - 统一检测入口

参考 PCL-CE ModModpack.ModpackInstall() 的包类型检测逻辑:
- packType 0: CurseForge (manifest.json 不含 addons)
- packType 1: HMCL (modpack.json)
- packType 2: MultiMC (mmc-pack.json)
- packType 3: MCBBS (mcbbs.packmeta 或 manifest.json 含 addons)
- packType 4: Modrinth (modrinth.index.json)
- packType 9: 带启动器压缩包 (modpack.zip / modpack.mrpack 内嵌)
- default: 通用压缩包 (.minecraft/versions/X/X.json)
"""

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from pathlib import Path


# ─── 整合包类型枚举 ────────────────────────────────────────────

@dataclass
class ModpackType:
    """整合包类型标识"""
    CURSEFORGE = "curseforge"       # packType 0
    HMCL = "hmcl"                   # packType 1
    MULTIMC = "multimc"             # packType 2
    MCBBS = "mcbbs"                 # packType 3
    MODRINTH = "modrinth"           # packType 4
    LAUNCHER_PACK = "launcher_pack"  # packType 9
    GENERIC = "generic"             # default


# ─── 检测结果 ──────────────────────────────────────────────────

@dataclass
class ModpackDetectionResult:
    """整合包检测结果"""
    pack_type: str                                    # ModpackType 值
    format_name: str                                  # 人类可读名称
    archive_base_folder: str = ""                     # ZIP 内根目录前缀（如 "pack/"）
    raw_json: Optional[Dict] = None                   # 主清单 JSON
    description: str = ""                             # 详细描述


# ─── 标记文件表 ────────────────────────────────────────────────

# 根目录标记文件 → 类型（优先级从高到低）
_ROOT_MARKERS: List[Tuple[str, str]] = [
    ("mcbbs.packmeta", ModpackType.MCBBS),
    ("mmc-pack.json", ModpackType.MULTIMC),
    ("modrinth.index.json", ModpackType.MODRINTH),
    ("modpack.json", ModpackType.HMCL),
]

# 需要额外检查 JSON 内容的标记文件
_CONDITIONAL_MARKERS: List[Tuple[str, str, callable]] = [
    # (文件名, 类型, 条件函数：接收 JSON dict → bool)
    ("manifest.json", ModpackType.CURSEFORGE, lambda j: j is not None and "addons" not in j)
    if True else None,
    ("manifest.json", ModpackType.MCBBS, lambda j: j is not None and "addons" in j)
    if True else None,
]

# 内嵌启动器标记文件
_LAUNCHER_PACK_MARKERS = ["modpack.zip", "modpack.mrpack"]

# 先列出条件标记
_CONDITIONAL_MARKERS: List[Tuple[str, str, callable]] = [
    ("manifest.json", ModpackType.CURSEFORGE, lambda j: j is not None and "addons" not in j),
    ("manifest.json", ModpackType.MCBBS, lambda j: j is not None and "addons" in j),
]


# ─── 核心检测函数 ──────────────────────────────────────────────

def detect_modpack_archive(pack_path: str) -> ModpackDetectionResult:
    """检测整合包 ZIP 文件的类型

    按 PCL-CE 的检测顺序:
    1. 根目录标记文件
    2. 一级子目录标记文件
    3. 通用压缩包 (.minecraft 结构)

    Args:
        pack_path: 整合包文件的绝对路径

    Returns:
        ModpackDetectionResult

    Raises:
        ValueError: 文件不存在或无法打开
    """
    if not os.path.isfile(pack_path):
        raise ValueError(f"文件不存在: {pack_path}")

    try:
        with zipfile.ZipFile(pack_path, "r") as zf:
            entries = set(zf.namelist())

            # 检查加密
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    raise ValueError("不支持加密的压缩包")

            # ── 第一步：根目录检测 ──
            result = _detect_root(zf, entries)
            if result is not None:
                return result

            # ── 第二步：一级子目录检测 ──
            result = _detect_subdir(zf, entries)
            if result is not None:
                return result

            # ── 第三步：通用压缩包检测 ──
            result = _detect_generic(zf, entries)
            if result is not None:
                return result

            raise ValueError(
                "无法识别整合包格式。请确认文件为以下格式之一：\n"
                "- Modrinth (.mrpack)\n"
                "- CurseForge (含 manifest.json)\n"
                "- MultiMC (含 mmc-pack.json)\n"
                "- HMCL (含 modpack.json)\n"
                "- MCBBS (含 mcbbs.packmeta)\n"
                "- 带 .minecraft 目录的压缩包"
            )

    except zipfile.BadZipFile:
        raise ValueError("不是有效的 ZIP 文件")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"读取压缩包失败: {e}")


def _detect_root(zf: zipfile.ZipFile, entries: set) -> Optional[ModpackDetectionResult]:
    """从根目录检测标记文件"""
    # 优先检查 mcbbs.packmeta / mmc-pack.json
    for filename, pack_type in _ROOT_MARKERS:
        if filename in entries:
            return _build_result(pack_type, "", zf, filename)

    # 检查 modrinth.index.json（根目录）
    if "modrinth.index.json" in entries:
        return _build_result(ModpackType.MODRINTH, "", zf, "modrinth.index.json")

    # 检查 manifest.json（需要看内容区分 CurseForge 或 MCBBS）
    if "manifest.json" in entries:
        raw = _read_json(zf, "manifest.json")
        if raw is not None:
            if "addons" in raw:
                return _build_result(ModpackType.MCBBS, "", zf, "manifest.json", raw)
            else:
                return _build_result(ModpackType.CURSEFORGE, "", zf, "manifest.json", raw)

    # 检查 modpack.json（HMCL）
    if "modpack.json" in entries:
        return _build_result(ModpackType.HMCL, "", zf, "modpack.json")

    # 检查内嵌启动器
    for marker in _LAUNCHER_PACK_MARKERS:
        if marker in entries:
            return ModpackDetectionResult(
                pack_type=ModpackType.LAUNCHER_PACK,
                format_name="带启动器的压缩包",
                description=f"压缩包内包含 {marker}，将递归提取安装"
            )

    return None


def _detect_subdir(zf: zipfile.ZipFile, entries: set) -> Optional[ModpackDetectionResult]:
    """从一级子目录检测标记文件"""
    # 收集所有一级目录名
    subdirs: Dict[str, set] = {}
    for entry in entries:
        parts = entry.split("/")
        if len(parts) >= 2 and parts[0]:
            base = parts[0] + "/"
            if base not in subdirs:
                subdirs[base] = set()
            subdirs[base].add(parts[1])

    for base_dir, files in subdirs.items():
        # 检查 mcbbs.packmeta
        if "mcbbs.packmeta" in files:
            return _build_result(ModpackType.MCBBS, base_dir, zf, base_dir + "mcbbs.packmeta")

        # 检查 mmc-pack.json
        if "mmc-pack.json" in files:
            return _build_result(ModpackType.MULTIMC, base_dir, zf, base_dir + "mmc-pack.json")

        # 检查 modrinth.index.json
        if "modrinth.index.json" in files:
            return _build_result(ModpackType.MODRINTH, base_dir, zf, base_dir + "modrinth.index.json")

        # 检查 manifest.json
        if "manifest.json" in files:
            raw = _read_json(zf, base_dir + "manifest.json")
            if raw is not None:
                if "addons" in raw:
                    return ModpackDetectionResult(
                        pack_type=ModpackType.MCBBS,
                        format_name="MCBBS 整合包 (一级目录)",
                        archive_base_folder="overrides/",
                        raw_json=raw,
                    )
                else:
                    return _build_result(ModpackType.CURSEFORGE, base_dir, zf, base_dir + "manifest.json", raw)

        # 检查 modpack.json
        if "modpack.json" in files:
            return _build_result(ModpackType.HMCL, base_dir, zf, base_dir + "modpack.json")

        # 检查内嵌启动器
        for marker in _LAUNCHER_PACK_MARKERS:
            if marker in files:
                return ModpackDetectionResult(
                    pack_type=ModpackType.LAUNCHER_PACK,
                    format_name="带启动器的压缩包 (一级目录)",
                    archive_base_folder=base_dir,
                    description=f"压缩包内包含 {marker}，将递归提取安装"
                )

    return None


def _detect_generic(zf: zipfile.ZipFile, entries: set) -> Optional[ModpackDetectionResult]:
    """检测通用压缩包（包含 .minecraft/versions/X/X.json 结构）"""
    pattern = re.compile(r"^(.*/)?\.minecraft/versions/([^/]+)/\2\.json$", re.IGNORECASE)

    for entry in entries:
        match = pattern.match(entry)
        if match:
            prefix = match.group(1) or ""
            version_id = match.group(2)
            return ModpackDetectionResult(
                pack_type=ModpackType.GENERIC,
                format_name="通用压缩包 (.minecraft 结构)",
                archive_base_folder=prefix,
                description=f"检测到 .minecraft 目录结构，Minecraft 版本: {version_id}"
            )

    return None


# ─── 辅助函数 ──────────────────────────────────────────────────

def _read_json(zf: zipfile.ZipFile, entry_name: str) -> Optional[Dict]:
    """读取 ZIP 内的 JSON 文件"""
    try:
        # 尝试 UTF-8
        with zf.open(entry_name, "r") as f:
            return json.loads(f.read().decode("utf-8"))
    except (UnicodeDecodeError, KeyError):
        pass
    try:
        # 尝试 GB18030
        with zf.open(entry_name, "r") as f:
            return json.loads(f.read().decode("gb18030"))
    except Exception:
        return None


def _build_result(
    pack_type: str,
    base_folder: str,
    zf: zipfile.ZipFile,
    entry_name: str,
    raw_json: Optional[Dict] = None,
) -> ModpackDetectionResult:
    """构建检测结果"""
    names = {
        ModpackType.CURSEFORGE: "CurseForge 整合包",
        ModpackType.HMCL: "HMCL 整合包",
        ModpackType.MULTIMC: "MultiMC 整合包",
        ModpackType.MCBBS: "MCBBS 整合包",
        ModpackType.MODRINTH: "Modrinth 整合包",
    }
    if raw_json is None and entry_name:
        raw_json = _read_json(zf, entry_name)

    return ModpackDetectionResult(
        pack_type=pack_type,
        format_name=names.get(pack_type, pack_type),
        archive_base_folder=base_folder,
        raw_json=raw_json,
    )


def is_curseforge_manifest(raw: Optional[Dict]) -> bool:
    """判断 manifest.json 是否为 CurseForge 格式"""
    if raw is None:
        return False
    return "minecraft" in raw and "files" in raw and "addons" not in raw


def is_mcbbs_manifest(raw: Optional[Dict]) -> bool:
    """判断 manifest.json 是否为 MCBBS 格式"""
    if raw is None:
        return False
    return "addons" in raw
