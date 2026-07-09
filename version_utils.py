"""版本号解析与匹配工具模块

基于 PCL-CE (Plain Craft Launcher 2) 中 RegexPatterns.cs 和 ModMinecraft.cs
的版本匹配逻辑重新设计，提供统一的 Minecraft 版本号、模组加载器版本的
正则匹配、解析和比较功能。

参考文件:
  - PCL-CE/PCL.Core/Utils/RegexPatterns.cs (所有正则表达式集中定义)
  - PCL-CE/Plain Craft Launcher 2/Modules/Minecraft/ModMinecraft.cs (版本解析逻辑)
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════
# 实例信息数据结构（参考 PCL-CE: McInstanceInfo）
# ══════════════════════════════════════════════════════════════════════


@dataclass
class InstanceInfo:
    """Minecraft 实例信息

    从版本 JSON 文件解析得到的结构化实例数据，替代了原有的
    基于文件夹名的正则匹配方式。

    参考 PCL-CE: McInstanceInfo 类和 McInstanceState 枚举。

    Attributes:
        folder_name: 实例文件夹名（即版本 ID，如 "1.20.4" 或 "1.20.4-forge-49.0.26"）
        vanilla_name: Minecraft 游戏版本号（如 "1.20.4", "26.1"），Unknown 表示无法识别
        loader_type: 模组加载器类型（如 "forge", "fabric", "neoforge"），None 表示原版
        loader_version: 模组加载器版本号（如 "49.0.26"），None 表示无加载器
        state: 实例状态，对应 McInstanceState 枚举值
        reliable: 版本号是否可靠（非猜测获得）
    """

    folder_name: str = ""
    vanilla_name: str = "Unknown"
    loader_type: Optional[str] = None
    loader_version: Optional[str] = None
    state: str = "original"
    reliable: bool = True

    @property
    def has_loader(self) -> bool:
        """是否安装了模组加载器（参考 PCL-CE: McInstance.Modable 属性）"""
        return self.loader_type is not None

    @property
    def modable(self) -> bool:
        """是否可以安装模组（同 has_loader，保留为别名）"""
        return self.has_loader


# ══════════════════════════════════════════════════════════════════════
# Minecraft 版本正则表达式
# ══════════════════════════════════════════════════════════════════════

# Minecraft 正常版本号 (如 1.20.4, 1.19.3, 26.1)
# 参考 PCL-CE: RegexPatterns.McNormalVersion
_MC_NORMAL_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$|^\d+\.\d+$")

# Minecraft 快照版本号 (如 24w14a, 15w51b)
# 参考 PCL-CE: RegexPatterns.McSnapshotVersion
_MC_SNAPSHOT_VERSION_RE = re.compile(r"^(?P<year>\d+)w(?P<week>\d+)(?P<rev>[a-z]?)$")

# Minecraft Indev 版本号 (如 in-20091231-2, in-20100130)
# 参考 PCL-CE: RegexPatterns.McIndevVersion
_MC_INDEV_VERSION_RE = re.compile(r"^in-(?P<date>\d{8})(-(?P<rev>\d+))?$")

# Minecraft Infdev 版本号 (如 inf-20100611)
# 参考 PCL-CE: RegexPatterns.McInfdevVersion
_MC_INFDEV_VERSION_RE = re.compile(r"^inf-(?P<date>\d{8})(-(?P<rev>\d+))?$")

# Minecraft JSON 版本号（从 JSON 中提取，支持多种格式）
# 参考 PCL-CE: RegexPatterns.MinecraftJsonVersion
# 支持: 1.20.4, 1.7.10-pre4, 24w14a, 1.14 Pre-Release 2, 26.1-snapshot-1, 26.1, 26.1.1 等
# 完整模式: ((快照格式)|(新旧版本格式))(_unobfuscated)?
_MC_JSON_VERSION_PATTERN = (
    r"(?P<snapshot>(?:[1-9][0-9])w[0-9]{2}[a-g]?)|"
    r"(?P<version>(?:1|[2-9][0-9])\.[0-9]+(?:\.[0-9]+)?"
    r"(?:-(?:pre|rc|snapshot-?)[1-9]*| Pre-Release(?: [1-9])?)?)"
    r"(?:_unobfuscated)?"
)
_MC_JSON_VERSION_RE = re.compile(_MC_JSON_VERSION_PATTERN, re.IGNORECASE)

# Minecraft 新版本格式 (YY.D 或 YY.D.H, YY >= 26)
# 用于判断是否为 2026+ 新命名格式
_MC_NEW_FORMAT_RE = re.compile(r"^(?P<yy>\d{2,})\.(?P<d>\d+)(?:\.(?P<h>\d+))?")

# Minecraft 旧版本格式 (1.X 或 1.X.Y)
_MC_LEGACY_FORMAT_RE = re.compile(r"^(?P<major>1)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?")

# 完整语义化版本号 (SemVer 2.0)
# 参考 PCL-CE: RegexPatterns.SemVer
# 支持: X.Y.Z-prerelease+build, v1.0.0, 1.0.0-alpha.1
_SEMVER_PATTERN = (
    r"^v?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_SEMVER_RE = re.compile(_SEMVER_PATTERN)

# Minecraft 下载 URL 中的版本号
# 参考 PCL-CE: RegexPatterns.MinecraftDownloadUrlVersion
# 注意: Python re 不支持可变宽度的后顾断言，改用捕获组提取
_MC_DOWNLOAD_URL_VERSION_RE = re.compile(r"launcher\.mojang\.com/mc/game/([^/\s]+)", re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════
# 模组加载器版本正则表达式
# ══════════════════════════════════════════════════════════════════════

# Forge 主版本号（位于 "forge:X.Y.Z-" 之后）
# 参考 PCL-CE: RegexPatterns.ForgeMainVersion
# 注意: Python re 不支持可变宽度的后顾断言，改用捕获组提取
_FORGE_MAIN_VERSION_RE = re.compile(r"forge:[0-9.]+(?:_pre[0-9]*)?-([0-9.]+)", re.IGNORECASE)

# Forge Maven 坐标中的版本号
# 参考 PCL-CE: RegexPatterns.ForgeLibVersion
_FORGE_LIB_VERSION_RE = re.compile(r"net\.minecraftforge:(?:forge|fmlloader):[0-9.]+-([0-9a-zA-Z._+-]+)", re.IGNORECASE)

# NeoForge 版本号（从 JSON 参数中提取）
# 参考 PCL-CE: RegexPatterns.NeoForgeVersion
_NEOFORGE_VERSION_RE = re.compile(r'orgeVersion",[^"]*?"([^"]+)"', re.IGNORECASE)

# NeoForge 版本列表中的版本号（包含 beta/alpha/snapshot/pre 后缀）
# 参考 PCL-CE: RegexPatterns.DlNeoForgeVersion
_NEOFORGE_DL_VERSION_RE = re.compile(
    r'"(?:1\.20\.1-)?(\d+\.[^.]+\.[0-9]+(?:\.[0-9]+)?'
    r"(?:-(?:beta|alpha)(?:\.[0-9]+)?)?"
    r'(?:\+snapshot-\d+)?(?:\+pre-\d+)?)"',
    re.IGNORECASE,
)

# Fabric Loader 版本
# 参考 PCL-CE: RegexPatterns.FabricVersion
_FABRIC_VERSION_RE = re.compile(r"net\.fabricmc:fabric-loader:([0-9.]+(?:\+build\.[0-9]+)?)", re.IGNORECASE)

# LegacyFabric 版本
# 参考 PCL-CE: RegexPatterns.LegacyFabricVersion
_LEGACY_FABRIC_VERSION_RE = re.compile(r"net\.fabricmc:fabric-loader:([0-9.]+(?:\+build\.[0-9]+)?)", re.IGNORECASE)

# Quilt Loader 版本
# 参考 PCL-CE: RegexPatterns.QuiltVersion
_QUILT_VERSION_RE = re.compile(
    r"org\.quiltmc:quilt-loader:([0-9.]+(?:\+build\.[0-9]+)?" r"(?:(?:-beta\.)[0-9](?:[0-9]?))?)", re.IGNORECASE
)

# Cleanroom 版本
# 参考 PCL-CE: RegexPatterns.CleanroomVersion
_CLEANROOM_VERSION_RE = re.compile(
    r"com\.cleanroommc:cleanroom:([0-9.]+(?:\+build\.[0-9]+)?(?:-alpha)?)", re.IGNORECASE
)

# Fabric-Like (Fabric/Quilt/LegacyFabric) 中间映射版本
# 参考 PCL-CE: RegexPatterns.FabricLikeLibVersion
_FABRIC_LIKE_LIB_VERSION_RE = re.compile(r"(?:fabricmc|quiltmc|legacyfabric):intermediary:([^\"\s]+)", re.IGNORECASE)

# OptiFine 版本
# 参考 PCL-CE: RegexPatterns.OptiFineVersion, OptiFineLibVersion
_OPTIFINE_VERSION_RE = re.compile(r'HD_U_([^"":/\s]+)', re.IGNORECASE)

# LabyMod 版本
# 参考 PCL-CE: RegexPatterns.LabyModVersion
_LABYMOD_VERSION_RE = re.compile(r"-Dnet\.labymod\.running-version=(1\.[0-9+.]+)", re.IGNORECASE)

# ══════════════════════════════════════════════════════════════════════
# 版本格式判断
# ══════════════════════════════════════════════════════════════════════

# 预发布/快照标识检测
_PRE_RELEASE_CHECK_RE = re.compile(r"-(?:pre|rc|snapshot|beta|alpha)", re.IGNORECASE)
_SNAPSHOT_CHECK_RE = re.compile(r"^(?:\d+w\d+[a-z]?|.*-snapshot.*|.*-pre.*|.*-rc.*|snapshot-.*)$", re.IGNORECASE)


def is_mc_normal_version(version: str) -> bool:
    """检查是否为 Minecraft 正常版本号（如 1.20.4 或 26.1）"""
    return bool(_MC_NORMAL_VERSION_RE.match(version))


def is_mc_snapshot_version(version: str) -> bool:
    """检查是否为 Minecraft 快照版本号（如 24w14a）"""
    return bool(_MC_SNAPSHOT_VERSION_RE.match(version))


def is_mc_indev_version(version: str) -> bool:
    """检查是否为 Minecraft Indev 版本号（如 in-20091231-2）"""
    return bool(_MC_INDEV_VERSION_RE.match(version))


def is_mc_infdev_version(version: str) -> bool:
    """检查是否为 Minecraft Infdev 版本号（如 inf-20100611）"""
    return bool(_MC_INFDEV_VERSION_RE.match(version))


def is_new_version_format(version: str) -> bool:
    """判断版本号是否为新格式 (YY.D/YY.D.H, YY >= 26)

    参考 PCL-CE: ModMinecraft.IsFormatFit
    """
    if version.startswith("1."):
        return False
    m = _MC_NEW_FORMAT_RE.match(version)
    if m:
        try:
            yy = int(m.group("yy"))
            return yy >= 26
        except (ValueError, IndexError):
            pass
    return False


def is_legacy_version_format(version: str) -> bool:
    """判断版本号是否为旧格式 (1.X 或 1.X.Y)"""
    return bool(_MC_LEGACY_FORMAT_RE.match(version))


def is_pre_release(version: str) -> bool:
    """检查版本是否包含预发布/快照标识"""
    return bool(_PRE_RELEASE_CHECK_RE.search(version))


def is_snapshot(version: str) -> bool:
    """检查是否为快照/预发布版本

    参考 PCL-CE: McInstanceInfo 中的版本类型判断逻辑
    支持:
    - 旧快照格式: 23w51a
    - 新快照格式: 26.1-snapshot-1, 26.1-pre-1
    - RC 版本: 26.1-rc-1
    - snapshot- 前缀
    """
    return bool(_SNAPSHOT_CHECK_RE.match(version))


# ══════════════════════════════════════════════════════════════════════
# 版本号提取
# ══════════════════════════════════════════════════════════════════════


def parse_mc_version_from_json(text: str) -> Optional[str]:
    """从 JSON 文本中提取 Minecraft 版本号

    参考 PCL-CE: RegexPatterns.MinecraftJsonVersion 和 McInstance.Info 的版本识别逻辑。

    支持的格式:
    - 1.20.4, 1.19.3, 1.7.10-pre4
    - 24w14a (快照)
    - 1.14 Pre-Release 2
    - 26.1, 26.1.1, 26.1-snapshot-1 (新格式)
    - *_unobfuscated 后缀会自动去除

    Returns:
        提取到的版本号，未找到返回 None
    """
    m = _MC_JSON_VERSION_RE.search(text)
    if m:
        if m.group("snapshot"):
            return m.group("snapshot")
        if m.group("version"):
            return m.group("version")
    return None


def parse_mc_version_from_id(version_id: str) -> Optional[str]:
    """从版本 ID 字符串中提取 Minecraft 游戏版本号

    综合 PCL-CE 的版本识别逻辑，实现了更简洁高效的版本提取。

    支持的版本 ID 格式:
    - Forge:      1.20.4-forge-49.0.26, 26.1-forge-1.0.0
    - Fabric:     fabric-loader-0.15.11-1.20.4, fabric-loader-0.16.0-26.1.1
    - Quilt:      quilt-loader-0.19.2-1.20.4
    - NeoForge:   neoforge-20.6.139 (从 loader 版本推算) 或 1.20.6-neoforge-20.6.139
    - Vanilla:    1.20.4, 26.1, 24w14a

    NeoForge 特殊说明:
    - 旧格式版本 neoforge-{major}.{minor}.{patch}:
      minor=0 时 MC 版本为 1.{major} (如 21.0.x → 1.21)
      否则为 1.{major}.{minor} (如 20.6.x → 1.20.6)

    Args:
        version_id: 版本 ID 字符串

    Returns:
        Minecraft 游戏版本号，无法识别返回 None
    """
    if not version_id:
        return None

    version_lower = version_id.lower()

    # ── 优先检查新格式版本（YY.D 或 YY.D.H 开头, YY >= 26）──
    # 新格式可能在 version_id 开头（如 "26.1-forge-xxx"、"26.1.1-fabric-xxx"）
    # 也可能以带 loader 前缀出现，但新格式年份 >= 26 保证了唯一性
    new_match = _MC_NEW_FORMAT_RE.match(version_id)
    if new_match:
        try:
            yy = int(new_match.group("yy"))
            if yy >= 26:
                d = new_match.group("d")
                h = new_match.group("h")
                return f"{yy}.{d}.{h}" if h else f"{yy}.{d}"
        except (ValueError, IndexError):
            pass

    # ── NeoForge 特殊处理 ──
    if "neoforge" in version_lower:
        # 从 loader 版本推算 MC 版本（标准格式 neoforge-{major}.{minor}.{patch}）
        neoforge_m = re.search(r"neoforge-(\d+)\.(\d+)\.(\d+)", version_lower)
        if neoforge_m:
            major = neoforge_m.group(1)
            minor = neoforge_m.group(2)
            if minor == "0":
                return f"1.{major}"
            else:
                return f"1.{major}.{minor}"

        # 回退：在 ID 中搜索任何版本号
        return _extract_any_mc_version(version_id)
        # Returns the first match or None

    # ── Fabric/Quilt 格式: loader-{loader_version}-{mc_version} ──
    # 游戏版本在末尾
    if "fabric" in version_lower or "quilt" in version_lower:
        return _extract_any_mc_version(version_id, last=True)

    # ── Forge 格式: {mc_version}-forge-{forge_version} ──
    # 或已由上方新格式处理，此处为旧格式回退
    return _extract_any_mc_version(version_id)


def _extract_any_mc_version(text: str, last: bool = False) -> Optional[str]:
    """在文本中搜索 Minecraft 版本号，返回第一个（或最后一个）匹配

    内部辅助函数，实现了从任意文本中提取 MC 版本号的核心逻辑。
    优先匹配新格式 (YY.D.H 或 YY.D, YY >= 26)，否则匹配旧格式 (1.X.Y 或 1.X)。

    Args:
        text: 要搜索的文本
        last: True 时返回最后一个匹配，否则返回第一个

    Returns:
        匹配的版本号，未找到返回 None
    """
    # 搜索所有可能的版本号
    version_pattern = r"\d+\.\d+(?:\.\d+)*"
    matches = re.findall(version_pattern, text)

    candidates = []
    for m in matches:
        if not m:
            continue
        parts = m.split(".")
        if len(parts) < 2:
            continue
        try:
            major = int(parts[0])
            # 新格式 (YY >= 26) 或旧格式 (1.x)
            if major >= 26 or major == 1:
                candidates.append(m)
        except ValueError:
            continue

    if not candidates:
        # 回退：搜索旧快照格式 (如 24w14a)
        snapshot_m = _MC_SNAPSHOT_VERSION_RE.search(text)
        if snapshot_m:
            return snapshot_m.group(0)
        return None

    return candidates[-1] if last else candidates[0]


# ══════════════════════════════════════════════════════════════════════
# 模组加载器识别
# ══════════════════════════════════════════════════════════════════════

# 加载器检测关键词映射
_LOADER_KEYWORDS = {
    "neoforge": "neoforge",
    "forge": "forge",
    "fabric": "fabric",
    "quilt": "quilt",
    "liteloader": "liteloader",
    "legacyfabric": "legacyfabric",
    "cleanroom": "cleanroom",
    "optifine": "optifine",
    "labymod": "labymod",
}

# 检测顺序：先检测更具体的（如 neoforge 必须在 forge 之前）
_LOADER_DETECTION_ORDER = [
    "neoforge",
    "forge",
    "liteloader",
    "legacyfabric",
    "fabric",
    "quilt",
    "cleanroom",
    "optifine",
    "labymod",
]


# 特殊模组加载器兼容映射：将无法被 Modrinth/CurseForge API
# 识别的加载器映射为兼容等效类型，用于搜索与更新检测。
MOD_LOADER_API_COMPAT_MAP: Dict[str, str] = {"legacyfabric": "fabric", "cleanroom": "forge"}


def resolve_search_loader(loader: Optional[str]) -> Optional[str]:
    """将模组加载器映射为 API 可识别的类型

    Modrinth 和 CurseForge 等 API 不识别 legacyfabric、cleanroom
    等加载器，需要映射为兼容的等效类型进行搜索。

    Args:
        loader: 原始加载器类型

    Returns:
        映射后的加载器类型，未找到映射则返回原值
    """
    if loader is None:
        return None
    return MOD_LOADER_API_COMPAT_MAP.get(loader, loader)


def parse_mod_loader_from_version(version_id: str) -> Optional[str]:
    """从版本 ID 中解析模组加载器类型

    参考 PCL-CE: McInstanceInfo 中的 HasForge/HasFabric/HasNeoForge 等属性逻辑
    和 McInstanceState 枚举中的加载器分类。

    支持的加载器:
    - forge:     Forge
    - fabric:    Fabric
    - neoforge:  NeoForge
    - quilt:     Quilt
    - liteloader: LiteLoader
    - legacyfabric: LegacyFabric
    - cleanroom: Cleanroom
    - optifine:  OptiFine
    - labymod:   LabyMod

    Args:
        version_id: 版本 ID 字符串

    Returns:
        加载器类型字符串，未找到返回 None
    """
    if not version_id:
        return None

    version_lower = version_id.lower()

    for loader in _LOADER_DETECTION_ORDER:
        if loader in version_lower:
            return loader

    return None


def has_mod_loader(version_id: str) -> bool:
    """判断版本是否安装了模组加载器

    参考 PCL-CE: McInstance.Modable 属性
    """
    return parse_mod_loader_from_version(version_id) is not None


def get_loaders_from_version(version_id: str) -> List[str]:
    """获取版本中所有模组加载器类型

    返回所有匹配到的加载器类型列表（一个版本可能同时有多个加载器）

    Args:
        version_id: 版本 ID 字符串

    Returns:
        加载器类型列表
    """
    if not version_id:
        return []

    version_lower = version_id.lower()
    found = []

    for loader in _LOADER_DETECTION_ORDER:
        if loader in version_lower:
            found.append(loader)

    return found


# ══════════════════════════════════════════════════════════════════════
# 版本号比较与 Drop 转换（参考 PCL-CE: ModMinecraft）
# ══════════════════════════════════════════════════════════════════════


def parse_semver(version: str) -> Optional[Tuple[Tuple[int, int, int], str]]:
    """语义化版本号解析

    参考 PCL-CE: RegexPatterns.SemVer

    Args:
        version: 版本号字符串（如 "1.20.4", "0.15.11", "1.0.0-alpha.1+build.123"）

    Returns:
        ((major, minor, patch), prerelease) 或 None
    """
    m = _SEMVER_RE.match(version.strip())
    if m:
        major = int(m.group("major"))
        minor = int(m.group("minor"))
        patch = int(m.group("patch"))
        prerelease = m.group("prerelease") or ""
        return (major, minor, patch), prerelease
    return None


def compare_versions(a: str, b: str) -> int:
    """比较两个版本号

    参考 PCL-CE: ModMinecraft.CompareVersion
    支持格式: 1.13.2, 1.7.10-pre4, 1.8_pre, 1.14 Pre-Release 2, 1.14.4 C6, 26.1-snapshot-1

    返回:
        -1: a < b
         0: a == b
         1: a > b
    """
    if a == b:
        return 0

    # 预处理：替换中文预发布标识
    a_norm = a.replace("快照", "snapshot").replace("预览版", "pre").replace("预发布", "pre")
    b_norm = b.replace("快照", "snapshot").replace("预览版", "pre").replace("预发布", "pre")

    # 分词：[a-z]+ | [0-9]+
    a_parts = re.findall(r"[a-z]+|[0-9]+", a_norm.lower())
    b_parts = re.findall(r"[a-z]+|[0-9]+", b_norm.lower())

    i = 0
    while True:
        if i >= len(a_parts) and i >= len(b_parts):
            break

        a_val = a_parts[i] if i < len(a_parts) else "0"
        b_val = b_parts[i] if i < len(b_parts) else "0"

        if a_val == b_val:
            i += 1
            continue

        # 特殊预发布排序: rc > pre > snapshot > experimental
        pre_rank = {"rc": -1, "pre": -2, "snapshot": -3, "experimental": -4}
        a_pre = pre_rank.get(a_val)
        b_pre = pre_rank.get(b_val)

        if a_pre is not None or b_pre is not None:
            a_r = a_pre if a_pre is not None else 0
            b_r = b_pre if b_pre is not None else 0
            if a_r < b_r:
                return -1
            if a_r > b_r:
                return 1
            i += 1
            continue

        # 数值比较
        try:
            a_num = int(a_val)
        except ValueError:
            a_num = 0
        try:
            b_num = int(b_val)
        except ValueError:
            b_num = 0

        if a_num < b_num:
            return -1
        if a_num > b_num:
            return 1

        i += 1

    return 0


def version_to_drop(version: str, allow_snapshot: bool = False) -> int:
    """将版本字符串转换为 Drop 序数

    参考 PCL-CE: McInstanceInfo.VersionToDrop

    Drop 序数规则:
    - 1.X   → X * 10
    - 26.1  → 26 * 10 + 1 = 261
    - 26.2  → 262
    - 1.21  → 210 (1.21.0)
    - 1.20.6 → 206

    Args:
        version: 版本号字符串
        allow_snapshot: 是否允许快照版本（默认否，快照返回 0）

    Returns:
        Drop 序数，无法识别返回 0
    """
    if not version:
        return 0

    if not allow_snapshot and "-" in version:
        return 0

    # 取 - 之前的部分
    base = version.split("-")[0]
    parts = base.split(".")

    if len(parts) < 2:
        return 0

    try:
        major = int(parts[0])
        minor = int(parts[1])
    except (ValueError, IndexError):
        return 0

    # 旧格式: 1.X → X * 10
    if major == 1:
        return minor * 10

    # 未知格式 (major < 25 且 != 1)
    if major < 25:
        return 0

    # 新格式: YY * 10 + D
    return major * 10 + minor


def drop_to_version(drop: int) -> str:
    """将 Drop 序数转换为版本字符串

    参考 PCL-CE: McInstanceInfo.DropToVersion

    Args:
        drop: Drop 序数

    Returns:
        版本字符串
    """
    if drop >= 250:
        return f"{drop // 10}.{drop % 10}"
    return f"1.{drop // 10}"


# ══════════════════════════════════════════════════════════════════════
# 版本 ID 构建
# ══════════════════════════════════════════════════════════════════════


def build_forge_version_id(mc_version: str, forge_version: str) -> str:
    """构建 Forge 版本 ID

    格式: {mc_version}-forge-{forge_version}
    例如: "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0"
    """
    return f"{mc_version}-forge-{forge_version}"


def build_fabric_version_id(loader_version: str, mc_version: str) -> str:
    """构建 Fabric 版本 ID

    格式: fabric-loader-{loader_version}-{mc_version}
    例如: "fabric-loader-0.15.11-1.20.4" 或 "fabric-loader-0.16.0-26.1.1"
    """
    return f"fabric-loader-{loader_version}-{mc_version}"


def build_neoforge_version_id(mc_version: str, neoforge_version: str) -> str:
    """构建 NeoForge 版本 ID

    格式: {mc_version}-neoforge-{neoforge_version}
    例如: "1.20.4-neoforge-20.4.234" 或 "26.1-neoforge-1.0.0"
    """
    return f"{mc_version}-neoforge-{neoforge_version}"


# ══════════════════════════════════════════════════════════════════════
# 下载地址版本提取（用于从 JSON 中解析已安装实例的版本信息）
# ══════════════════════════════════════════════════════════════════════


def parse_forge_version_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 中提取 Forge 版本号

    参考 PCL-CE: RegexPatterns.ForgeMainVersion, ForgeLibVersion
    """
    m = _FORGE_MAIN_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    m = _FORGE_LIB_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    return None


def parse_fabric_version_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 中提取 Fabric 版本号

    参考 PCL-CE: RegexPatterns.FabricVersion
    """
    m = _FABRIC_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    m = _LEGACY_FABRIC_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    return None


def parse_quilt_version_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 中提取 Quilt 版本号

    参考 PCL-CE: RegexPatterns.QuiltVersion
    """
    m = _QUILT_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    return None


def parse_neoforge_version_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 中提取 NeoForge 版本号

    参考 PCL-CE: RegexPatterns.NeoForgeVersion
    """
    m = _NEOFORGE_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    return None


def parse_optifine_version_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 中提取 OptiFine 版本号

    参考 PCL-CE: RegexPatterns.OptiFineLibVersion
    """
    m = _OPTIFINE_VERSION_RE.search(json_text)
    if m:
        return m.group(1)
    return None


# ══════════════════════════════════════════════════════════════════════
# 便捷工具：从版本 JSON 中提取 MC 版本（PCL 式多策略回退）
# ══════════════════════════════════════════════════════════════════════


def parse_mc_version_from_json_full(
    json_text: str, inherit_name: Optional[str] = None, folder_name: Optional[str] = None
) -> str:
    """从版本 JSON 中全面提取 Minecraft 版本号（PCL 式多策略回退）

    模拟 PCL-CE McInstance.Info 的版本识别流程:
    1. clientVersion 字段 (PCL 下载)
    2. patches 中的 game.version (HMCL)
    3. arguments 中的 --fml.mcVersion (Forge/NeoForge)
    4. inherit 实例名
    5. downloads URL 中的版本号
    6. libraries 中的 Forge/OptiFine/Fabric 版本号
    7. jar 字段
    8. JSON id 中的版本号
    9. 文件夹名
    10. 未找到返回 "Unknown"

    Args:
        json_text: 版本 JSON 文本
        inherit_name: 继承实例名称（可选）
        folder_name: 实例文件夹名（可选）

    Returns:
        识别到的版本号，无法识别返回 "Unknown"
    """
    # 策略 1: clientVersion (PCL 格式)
    m = re.search(r'"clientVersion"\s*:\s*"([^"]+)"', json_text)
    if m:
        return m.group(1)

    # 策略 2: patches (HMCL 格式)
    game_version_m = re.search(r'"id"\s*:\s*"game".*?"version"\s*:\s*"([^"]+)"', json_text, re.DOTALL)
    if game_version_m:
        return game_version_m.group(1)

    # 策略 3: --fml.mcVersion (Forge/NeoForge arguments)
    fml_m = re.search(r'"--fml\.mcVersion"\s*,\s*"([^"]+)"', json_text)
    if fml_m:
        return fml_m.group(1)

    # 策略 4: inherit
    if inherit_name:
        mc_v = parse_mc_version_from_id(inherit_name)
        if mc_v:
            return mc_v

    # 策略 5: downloads URL
    m = _MC_DOWNLOAD_URL_VERSION_RE.search(json_text)
    if m:
        return m.group(1)

    # 策略 6: libraries (Forge/OptiFine/Fabric)
    # 6a: Forge
    fv = parse_forge_version_from_json(json_text)
    if fv:
        return fv
    # 6b: OptiFine
    ov = parse_optifine_version_from_json(json_text)
    if ov:
        return ov
    # 6c: Fabric-like
    flm = _FABRIC_LIKE_LIB_VERSION_RE.search(json_text)
    if flm:
        return flm.group(1)

    # 策略 7: jar 字段
    m = re.search(r'"jar"\s*:\s*"([^"]+)"', json_text)
    if m:
        return m.group(1)

    # 策略 8: id 字段
    m = re.search(r'"id"\s*:\s*"([^"]+)"', json_text)
    if m:
        id_value = m.group(1)
        mc_v = parse_mc_version_from_json(id_value)
        if mc_v:
            return mc_v

    # 策略 9: 文件夹名
    if folder_name:
        mc_v = parse_mc_version_from_json(folder_name)
        if mc_v:
            return mc_v

    # 策略 10: 在整个 JSON 中搜索版本号
    mc_v = parse_mc_version_from_json(json_text)
    if mc_v:
        return mc_v

    return "Unknown"


# ══════════════════════════════════════════════════════════════════════
# 从版本 JSON 文本解析加载器信息（参考 PCL-CE: ModMinecraft.Load）
# ══════════════════════════════════════════════════════════════════════

# ── HMCL 风格模组加载器检测规则 ──
# 参考 HMCL LibraryAnalyzer.LibraryType - 通过解析 libraries 数组中的
# Maven 坐标 (groupId:artifactId:version) 来识别加载器类型，比纯文本搜索更准确。

# 每个规则: (loader_type, group_pattern, artifact_pattern, extra_check_fn)
# extra_check_fn(lib_tuples) 返回 True 才匹配，None 表示无额外检查
_LOADER_LIB_RULES = []


def _register_rule(loader_type, group_pattern, artifact_pattern, extra_check=None):
    _LOADER_LIB_RULES.append((loader_type, re.compile(group_pattern), re.compile(artifact_pattern), extra_check))


# 按 HMCL LibraryType 定义注册检测规则（按匹配优先级从高到低）

# LEGACY_FABRIC: group=net.fabricmc, artifact=fabric-loader + 必须存在 net.legacyfabric 组
_register_rule(
    "legacyfabric",
    r"net\.fabricmc",
    r"fabric-loader",
    extra_check=lambda libs: any(g == "net.legacyfabric" for g, a in libs),
)

# FABRIC: group=net.fabricmc, artifact=fabric-loader + 确保没有 net.legacyfabric
_register_rule(
    "fabric",
    r"net\.fabricmc",
    r"fabric-loader",
    extra_check=lambda libs: not any(g == "net.legacyfabric" for g, a in libs),
)

# NEO_FORGE: group=net.neoforged.fancymodloader, artifact=(core|loader)
_register_rule("neoforge", r"net\.neoforged\.fancymodloader", r"(core|loader)")

# FORGE: group=net.minecraftforge, artifact=(forge|fmlloader) + 确保没有 NeoForge
_register_rule(
    "forge",
    r"net\.minecraftforge",
    r"(forge|fmlloader)",
    extra_check=lambda libs: not any(
        re.match(r"net\.neoforged\.fancymodloader", g) and re.match(r"(core|loader)", a) for g, a in libs
    ),
)

# CLEANROOM: group=com.cleanroommc, artifact=cleanroom
_register_rule("cleanroom", r"com\.cleanroommc", r"cleanroom")

# LITELOADER: group=com.mumfrey, artifact=liteloader
_register_rule("liteloader", r"com\.mumfrey", r"liteloader")

# OPTIFINE: group=(net.)?optifine, artifact != launchwrapper
_register_rule("optifine", r"(net\.)?optifine", r"^(?!.*launchwrapper).*$")

# QUILT: group=org.quiltmc, artifact=quilt-loader
_register_rule("quilt", r"org\.quiltmc", r"quilt-loader")

# BOOTSTRAP_LAUNCHER: group=cpw.mods, artifact=bootstraplauncher (Forge 1.17+ 引导)
# 不作为独立加载器，仅用于辅助判断


def _parse_library_parts(name: str) -> tuple:
    """解析 Maven 坐标 (groupId:artifactId:version) 为三元组"""
    parts = name.split(":")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    elif len(parts) == 4:
        # 有 classifier 的情况: group:artifact:version:classifier
        return parts[0], parts[1], parts[2]
    return "", "", ""


def detect_mod_loader_from_libraries(json_obj: dict) -> Optional[str]:
    """从解析后的版本 JSON 对象的 libraries 中检测模组加载器类型

    参考 HMCL LibraryAnalyzer.analyze() - 通过解析 libraries 数组中的
    Maven 坐标 (groupId:artifactId) 来识别加载器，比纯文本搜索更准确可靠。

    Args:
        json_obj: 已合并 inheritsFrom 链的版本 JSON 对象

    Returns:
        加载器类型字符串 ("forge", "fabric", "neoforge", "quilt",
                        "liteloader", "legacyfabric", "cleanroom",
                        "optifine") 或 None
    """
    libraries = json_obj.get("libraries", []) if isinstance(json_obj, dict) else []
    if not libraries:
        return None

    # 提取所有 library 的 (groupId, artifactId)
    lib_tuples = []
    for lib in libraries:
        name = lib.get("name", "") if isinstance(lib, dict) else ""
        g, a, _ = _parse_library_parts(name)
        if g and a:
            lib_tuples.append((g, a))

    if not lib_tuples:
        return None

    # 按规则顺序匹配
    for loader_type, group_re, artifact_re, extra_check in _LOADER_LIB_RULES:
        for g, a in lib_tuples:
            if group_re.search(g) and artifact_re.search(a):
                if extra_check is None or extra_check(lib_tuples):
                    return loader_type

    return None


# JSON 文本中检测加载器的关键词映射（作为后备方案）
# 检测顺序：更具体的先检测（如 neoforge 在 forge 之前）
_JSON_LOADER_DETECTION = [
    ("labymod_data", "labymod"),
    ("net.neoforge", "neoforge"),
    ("minecraftforge", "forge"),
    ("net.fabricmc:fabric-loader", "fabric"),
    ("net.legacyfabric:intermediary", "legacyfabric"),
    ("org.quiltmc:quilt-loader", "quilt"),
    ("com.cleanroommc:cleanroom:", "cleanroom"),
    ("optifine", "optifine"),
    ("liteloader", "liteloader"),
]

# 加载器类型 → 版本号正则提取
_JSON_LOADER_VERSION_EXTRACTORS = {
    "forge": [
        re.compile(r"forge:[0-9.]+(?:_pre[0-9]*)?-([0-9.]+)", re.IGNORECASE),
        re.compile(r"net\.minecraftforge:(?:forge|fmlloader):[0-9.]+-([0-9a-zA-Z._+-]+)", re.IGNORECASE),
    ],
    "neoforge": [re.compile(r'orgeVersion",[^"]*?"([^"]+)"', re.IGNORECASE)],
    "fabric": [re.compile(r"net\.fabricmc:fabric-loader:([0-9.]+(?:\+build\.[0-9]+)?)", re.IGNORECASE)],
    "legacyfabric": [re.compile(r"net\.fabricmc:fabric-loader:([0-9.]+(?:\+build\.[0-9]+)?)", re.IGNORECASE)],
    "quilt": [
        re.compile(
            r"org\.quiltmc:quilt-loader:([0-9.]+(?:\+build\.[0-9]+)?(?:-beta\.[0-9]+(?:[0-9])?)?)", re.IGNORECASE
        )
    ],
    "cleanroom": [re.compile(r"com\.cleanroommc:cleanroom:([0-9.]+(?:\+build\.[0-9]+)?(?:-alpha)?)", re.IGNORECASE)],
    "optifine": [re.compile(r'HD_U_([^"":/\s]+)', re.IGNORECASE)],
    "labymod": [re.compile(r"-Dnet\.labymod\.running-version=(1\.[0-9+.]+)", re.IGNORECASE)],
}


def parse_mod_loader_from_json(json_text: str) -> Optional[str]:
    """从版本 JSON 文本中检测模组加载器类型

    参考 PCL-CE: McInstance.Load() 中的加载器检测逻辑。
    通过搜索 JSON 全文本中的特定关键词来确定加载器类型。

    检测顺序（参考 PCL-CE）:
    - labymod_data → labymod
    - net.neoforge → neoforge
    - minecraftforge (不含 net.neoforge) → forge
    - net.fabricmc:fabric-loader → fabric
    - net.legacyfabric:intermediary → legacyfabric
    - org.quiltmc:quilt-loader → quilt
    - com.cleanroommc:cleanroom: → cleanroom
    - optifine → optifine
    - liteloader → liteloader

    Args:
        json_text: 版本 JSON 文本

    Returns:
        加载器类型字符串，未找到返回 None
    """
    if not json_text:
        return None

    # Forge 有特殊互斥规则：minecraftforge 存在但 net.neoforge 也必须不存在
    for keyword, loader_type in _JSON_LOADER_DETECTION:
        if keyword == "minecraftforge" and "net.neoforge" in json_text.lower():
            continue
        if keyword in json_text.lower():
            return loader_type

    return None


def parse_loader_version_from_json(json_text: str, loader_type: str) -> Optional[str]:
    """从版本 JSON 文本中提取模组加载器版本号

    参考 PCL-CE: ModMinecraft.Load() 中各加载器的版本提取正则。

    Args:
        json_text: 版本 JSON 文本
        loader_type: 加载器类型（如 "forge", "fabric", "neoforge"）

    Returns:
        加载器版本号字符串，未找到返回 None
    """
    extractors = _JSON_LOADER_VERSION_EXTRACTORS.get(loader_type)
    if not extractors:
        return None

    for regex in extractors:
        m = regex.search(json_text)
        if m:
            ver = m.group(1).replace("+build", "")
            return ver

    return None


def parse_instance_from_json(json_text: str, folder_name: str, minecraft_dir: Optional[str] = None) -> InstanceInfo:
    """从版本 JSON 文本中解析完整的实例信息

    参考 PCL-CE: McInstance.Info (getter) 和 McInstance.Load() 的完整流程。

    解析流程:
    1. 处理 HMCL 格式 JSON（patches 数组合并）
    2. 处理 inheritsFrom 继承（递归合并父 JSON）
    3. 多策略回退提取 MC 版本号
    4. 搜索 JSON 全文本检测加载器
    5. 提取加载器版本号

    Args:
        json_text: 版本 JSON 文本
        folder_name: 实例文件夹名（版本 ID）
        minecraft_dir: .minecraft 目录路径（用于继承链解析）

    Returns:
        InstanceInfo 实例
    """
    info = InstanceInfo(folder_name=folder_name)

    try:
        json_obj = json.loads(json_text)
    except json.JSONDecodeError:
        info.state = "error"
        info.reliable = False
        return info

    # ── 处理 HMCL 格式 JSON（patches 数组合并）──
    # 参考 PCL-CE: JsonObject.getter 中的 HMCL 合并逻辑
    if "patches" in json_obj and "time" not in json_obj:
        try:
            merged = _merge_hmcl_patches(json_obj)
            if merged is not None:
                json_obj = merged
                json_text = json.dumps(merged)
        except Exception:
            pass

    # ── 处理 inheritsFrom 继承链 ──
    # 参考 PCL-CE: JsonObject.getter 中的继承实例合并
    if "inheritsFrom" in json_obj and minecraft_dir:
        try:
            merged, _ = _resolve_inherits_chain(json_obj, folder_name, minecraft_dir, max_depth=10)
            if merged is not None:
                json_obj = merged
                json_text = json.dumps(merged)
        except Exception:
            pass

    # ── 提取 MC 版本号（多策略回退）──
    info.vanilla_name = parse_mc_version_from_json_full(
        json_text, inherit_name=json_obj.get("inheritsFrom", "").strip('"'), folder_name=folder_name
    )

    # 修正: 20.X / 21.X → 1.20.X / 1.21.X（PCL 兼容处理）
    if info.vanilla_name not in ("Unknown", "Old", "pending"):
        if re.match(r"^2[01]\.", info.vanilla_name):
            info.vanilla_name = "1." + info.vanilla_name
        info.vanilla_name = info.vanilla_name.replace("_unobfuscated", "").replace(" Unobfuscated", "").strip()

    # ── 检测实例状态 ──
    # 参考 PCL-CE: McInstance.Load() 中的分类逻辑
    release_time = json_obj.get("releaseTime", "")
    if release_time:
        try:
            from datetime import datetime

            rt = datetime.fromisoformat(release_time.replace("Z", "+00:00"))
            if rt.year >= 2000 and rt.year < 2013:
                info.state = "old"
        except (ValueError, OverflowError):
            pass

    if json_obj.get("type") == "pending":
        info.state = "pending"
    elif json_obj.get("type") == "fool":
        info.state = "fool"
    elif info.vanilla_name == "Old":
        info.state = "old"

    # ── 检测加载器类型 ──
    # 参考 HMCL LibraryAnalyzer: 优先通过 libraries 库坐标匹配（更准确），
    # 如果 libraries 被清空或不存在则回退到文本关键词搜索
    info.loader_type = detect_mod_loader_from_libraries(json_obj)
    if info.loader_type is None:
        info.loader_type = parse_mod_loader_from_json(json_text)

    # ── 检测快照状态 ──
    if info.state == "original" and info.loader_type is None:
        vn = info.vanilla_name
        if "-snapshot" in vn or "-pre" in vn or "-rc" in vn or re.match(r"^\d+w\d+", vn):
            info.state = "snapshot"

    # ── 根据加载器设置状态 ──
    if info.loader_type == "forge":
        info.state = "forge"
    elif info.loader_type == "neoforge":
        info.state = "neoforge"
    elif info.loader_type == "fabric":
        info.state = "fabric"
    elif info.loader_type == "legacyfabric":
        info.state = "legacyfabric"
    elif info.loader_type == "quilt":
        info.state = "quilt"
    elif info.loader_type == "cleanroom":
        info.state = "cleanroom"
    elif info.loader_type == "optifine":
        info.state = "optifine"
    elif info.loader_type == "labymod":
        info.state = "labymod"
    elif info.loader_type == "liteloader":
        info.state = "liteloader"

    # ── 提取加载器版本号 ──
    if info.loader_type:
        info.loader_version = parse_loader_version_from_json(json_text, info.loader_type)

    return info


def _merge_hmcl_patches(json_obj: dict) -> Optional[dict]:
    """合并 HMCL 格式 JSON 中的 patches 数组

    参考 PCL-CE: JsonObject.getter 中的 HMCL 合并逻辑:
    - 按 priority 排序 patches
    - 逐步合并到主对象
    - 修改 id 和移除 inheritsFrom

    Args:
        json_obj: 包含 patches 的 JSON 对象

    Returns:
        合并后的 JSON 对象，失败返回 None
    """
    patches = json_obj.get("patches")
    if not isinstance(patches, list) or not patches:
        return None

    try:
        sorted_patches = sorted(patches, key=lambda p: int(str(p.get("priority", "0"))))
    except (ValueError, TypeError):
        sorted_patches = list(patches)

    current = None
    for sub in sorted_patches:
        if not isinstance(sub, dict):
            continue
        sid = sub.get("id")
        if sid:
            if current is not None:
                current = _deep_merge_json(current, sub)
            else:
                current = dict(sub)

    if current is not None:
        current["id"] = json_obj.get("id", current.get("id", ""))
        current.pop("inheritsFrom", None)

    return current


def _resolve_inherits_chain(json_obj: dict, folder_name: str, minecraft_dir: str, max_depth: int = 10) -> tuple:
    """解析 inheritsFrom 继承链，递归合并父实例的 JSON

    参考 PCL-CE: JsonObject.getter 中的继承实例合并逻辑。

    Args:
        json_obj: 当前 JSON 对象
        folder_name: 当前实例文件夹名
        minecraft_dir: .minecraft 目录路径
        max_depth: 最大继承深度（防止循环引用）

    Returns:
        (合并后的 JSON 对象, 父实例名) 元组，失败返回 (None, None)
    """
    inherit_name = json_obj.get("inheritsFrom")
    if not inherit_name or not isinstance(inherit_name, str) or not inherit_name.strip():
        return json_obj, None

    inherit_name = inherit_name.strip()
    if max_depth <= 0 or inherit_name == folder_name:
        return json_obj, inherit_name

    # 读取父实例 JSON
    parent_dir = Path(minecraft_dir) / "versions" / inherit_name
    parent_json_path = parent_dir / f"{inherit_name}.json"
    if not parent_json_path.exists():
        # 尝试查找目录下唯一的 JSON 文件
        try:
            json_files = list(parent_dir.glob("*.json"))
            if len(json_files) == 1:
                parent_json_path = json_files[0]
            else:
                return json_obj, inherit_name
        except Exception:
            return json_obj, inherit_name

    try:
        parent_text = parent_json_path.read_text(encoding="utf-8")
        parent_obj = json.loads(parent_text)
    except Exception:
        return json_obj, inherit_name

    # 递归解析父实例的继承链
    merged_parent, _ = _resolve_inherits_chain(parent_obj, inherit_name, minecraft_dir, max_depth - 1)
    if merged_parent is None:
        merged_parent = parent_obj

    # 合并：父实例优先，子实例覆盖
    result = _deep_merge_json(merged_parent, json_obj)
    return result, inherit_name


def _deep_merge_json(base: dict, overlay: dict) -> dict:
    """深度合并两个 JSON 对象

    overlay 中的键会覆盖 base 中的同名键。
    对于列表类型，overlay 追加到 base 末尾。

    Args:
        base: 基础 JSON 对象
        overlay: 覆盖/追加的 JSON 对象

    Returns:
        合并后的新字典
    """
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_json(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


def has_mod_loader_from_json(version_id: str, minecraft_dir: str) -> bool:
    """通过读取版本 JSON 文件判断是否安装了模组加载器

    读取 versions/{version_id}/{version_id}.json 并搜索加载器关键词。

    Args:
        version_id: 版本 ID
        minecraft_dir: .minecraft 目录路径

    Returns:
        是否安装了模组加载器
    """
    info = _try_read_instance_json(version_id, minecraft_dir)
    if info is None:
        # 回退到名称匹配
        return parse_mod_loader_from_version(version_id) is not None
    return info.has_loader


def resolve_version_jar_path(version_id: str, minecraft_dir: str) -> Optional[str]:
    """解析版本 JAR 的实际路径

    参考 HMCL DefaultGameRepository.getVersionJar():
    - 通过解析 inheritsFrom 链找到实际 JAR 所属的版本 ID
    - 优先使用 version JSON 中的 jar 字段，回退到版本 id
    - 返回 versions/{jar_version_id}/{jar_version_id}.jar 的路径

    Args:
        version_id: 版本 ID（文件夹名）
        minecraft_dir: .minecraft 目录路径

    Returns:
        JAR 文件的绝对路径，或 None（无法解析）
    """
    if not minecraft_dir or not version_id:
        return None

    versions_dir = Path(minecraft_dir) / "versions"
    checked = set()
    current_id = version_id
    max_depth = 10

    while current_id and current_id not in checked and max_depth > 0:
        checked.add(current_id)
        json_path = versions_dir / current_id / f"{current_id}.json"
        if not json_path.exists():
            # 尝试目录下唯一的 JSON
            version_dir = versions_dir / current_id
            if version_dir.exists():
                try:
                    json_files = list(version_dir.glob("*.json"))
                    if len(json_files) == 1:
                        json_path = json_files[0]
                    else:
                        break
                except Exception:
                    break
            else:
                break

        try:
            obj = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            break

        # 检查 jar 字段（HMCL 中 getVersionJar 优先使用 jar 字段）
        jar_field = obj.get("jar")
        if jar_field:
            return str(versions_dir / jar_field / f"{jar_field}.jar")

        inherits_from = obj.get("inheritsFrom")
        if not inherits_from:
            # 没有继承关系，JAR 就是当前版本
            jar_path = versions_dir / current_id / f"{current_id}.jar"
            return str(jar_path) if jar_path.exists() else str(jar_path)

        # 继续向上查找父版本
        current_id = str(inherits_from).strip()
        max_depth -= 1

    # 最终回退：尝试直接使用版本 ID
    jar_path = versions_dir / version_id / f"{version_id}.jar"
    return str(jar_path) if jar_path.exists() else str(jar_path)


def _try_read_instance_json(version_id: str, minecraft_dir: str) -> Optional[InstanceInfo]:
    """尝试读取并解析实例 JSON 文件

    Args:
        version_id: 版本 ID（文件夹名）
        minecraft_dir: .minecraft 目录路径

    Returns:
        InstanceInfo 或 None（读取/解析失败）
    """
    if not minecraft_dir or not version_id:
        return None

    json_path = Path(minecraft_dir) / "versions" / version_id / f"{version_id}.json"
    if not json_path.exists():
        # 尝试查找目录下唯一的 JSON 文件
        version_dir = Path(minecraft_dir) / "versions" / version_id
        if version_dir.exists():
            try:
                json_files = list(version_dir.glob("*.json"))
                if len(json_files) == 1:
                    json_path = json_files[0]
                else:
                    return None
            except Exception:
                return None
        else:
            return None

    try:
        json_text = json_path.read_text(encoding="utf-8")
        return parse_instance_from_json(json_text, version_id, minecraft_dir)
    except Exception:
        return None


def parse_mc_version_from_dir(version_id: str, minecraft_dir: str) -> str:
    """通过读取版本 JSON 提取 Minecraft 版本号

    Args:
        version_id: 版本 ID（文件夹名）
        minecraft_dir: .minecraft 目录路径

    Returns:
        Minecraft 版本号，无法识别返回 version_id 本身
    """
    info = _try_read_instance_json(version_id, minecraft_dir)
    if info is None or info.vanilla_name == "Unknown":
        return parse_mc_version_from_id(version_id) or version_id
    return info.vanilla_name
