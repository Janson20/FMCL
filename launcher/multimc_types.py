"""MultiMC 整合包数据模型定义

参考 HMCL (Hello Minecraft! Launcher) 的 MultiMC 格式实现。
MultiMC 整合包是一种基于组件的 Minecraft 实例格式，使用 ZIP 打包。

数据模型层次：
    MultiMCManifest (mmc-pack.json)
        └── MultiMCManifestComponent[]
            └── MultiMCManifestRequire[]

    MultiMCInstanceConfig (instance.cfg)
    └── 关联 MultiMCManifest

    MultiMCInstancePatch (patches/{uid}.json)
        └── MultiMCManifestRequire[] (requires)

    ModpackConfiguration (持久化的增量更新元数据)
        └── FileInfo[] (overrides)
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from logzero import logger


# ════════════════════════════════════════════════════════════════
# 组件 UID 注册表
# ════════════════════════════════════════════════════════════════

# 组件 UID → Minecraft Loader 类型
COMPONENT_TO_LOADER: Dict[str, str] = {
    "net.minecraft": "vanilla",
    "net.minecraftforge": "forge",
    "net.neoforged": "neoforge",
    "net.fabricmc.fabric-loader": "fabric",
    "org.quiltmc.quilt-loader": "quilt",
    "com.mumfrey.liteloader": "liteloader",
}

# Loader 类型 → 组件 UID（反向映射）
LOADER_TO_COMPONENT: Dict[str, str] = {
    v: k for k, v in COMPONENT_TO_LOADER.items()
}

# 非 Loader 但重要的组件 UID 集合
IMPORTANT_UIDS: set = {
    "net.minecraft",
    "net.minecraftforge",
    "net.neoforged",
    "net.fabricmc.fabric-loader",
    "org.quiltmc.quilt-loader",
    "com.mumfrey.liteloader",
    "net.fabricmc.intermediary",
    "org.quiltmc.hashed",
    "org.lwjgl",
    "org.lwjgl3",
}

META_BASE_URL: str = "https://meta.multimc.org/v1"

# 特殊组件默认版本（从 HMCL 移植）
SPECIAL_DEFAULT_VERSIONS: Dict[str, str] = {
    "org.lwjgl": "2.9.1",
    "org.lwjgl3": "3.1.2",
}


def get_meta_url(component_uid: str, version: Optional[str], mc_version: str) -> str:
    """构造 MultiMC 组件元数据 URL。

    Args:
        component_uid: 组件 UID，如 "net.minecraft"
        version: 组件版本，可为 None
        mc_version: Minecraft 版本号

    Returns:
        完整的元数据 URL
    """
    if version is None:
        if component_uid in SPECIAL_DEFAULT_VERSIONS:
            version = SPECIAL_DEFAULT_VERSIONS[component_uid]
        elif component_uid in ("net.fabricmc.intermediary", "org.quiltmc.hashed"):
            version = mc_version

    if version is None:
        raise ValueError(f"无法确定组件 {component_uid} 的版本，且无默认值")

    return f"{META_BASE_URL}/{component_uid}/{version}.json"


def get_component_loader(uid: str) -> Optional[str]:
    """获取组件 UID 对应的 Loader 类型。"""
    return COMPONENT_TO_LOADER.get(uid)


def is_minecraft_component(uid: str) -> bool:
    """判断是否是 Minecraft 原版组件。"""
    return uid == "net.minecraft"


# ════════════════════════════════════════════════════════════════
# 格式检测
# ════════════════════════════════════════════════════════════════


def detect_multimc_format(zip_path: str) -> Tuple[bool, Optional[str]]:
    """检测 .zip 文件是否为 MultiMC 整合包格式。

    递归搜索所有目录层级，查找 instance.cfg。

    Args:
        zip_path: .zip 文件路径

    Returns:
        (是否为 MultiMC 格式, 根目录名前缀（空字符串表示根目录）)
    """
    if not os.path.isfile(zip_path):
        return False, None

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()

            # 检查根目录
            for name in names:
                if name == "instance.cfg" or name.endswith("/instance.cfg"):
                    prefix = name[:-len("instance.cfg")]
                    return True, prefix

    except (zipfile.BadZipFile, OSError) as e:
        logger.warning(f"无法打开 zip 文件 {zip_path}: {e}")
        return False, None

    return False, None


def find_root_entry(zip_path: str) -> str:
    """在 MultiMC zip 中定位根目录（返回前缀路径，如 "" 或 "MyPack/"）。

    递归搜索所有目录层级。

    Raises:
        ValueError: 不是有效的 MultiMC 整合包
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        for name in names:
            if name == "instance.cfg" or name.endswith("/instance.cfg"):
                if name == "instance.cfg":
                    return ""
                return name[:-len("instance.cfg")]

    raise ValueError("不是有效的 MultiMC 整合包：未找到 instance.cfg")


# ════════════════════════════════════════════════════════════════
# 数据模型：mmc-pack.json
# ════════════════════════════════════════════════════════════════


@dataclass
class MultiMCManifestRequire:
    """组件依赖声明"""
    uid: str                                    # 依赖组件 UID
    equals_version: Optional[str] = None        # 精确版本要求
    suggests: Optional[str] = None              # 建议版本

    @staticmethod
    def from_dict(data: dict) -> "MultiMCManifestRequire":
        return MultiMCManifestRequire(
            uid=data.get("uid", ""),
            equals_version=data.get("equals"),
            suggests=data.get("suggests"),
        )


@dataclass
class MultiMCManifestComponent:
    """mmc-pack.json 中的单个组件"""
    uid: str                                    # 组件唯一标识
    version: str                                # 组件版本
    important: bool = True                      # 是否重要组件
    dependency_only: bool = False               # 是否仅为依赖
    cached_name: Optional[str] = None           # 缓存名称
    cached_requires: List[MultiMCManifestRequire] = field(default_factory=list)
    cached_version: Optional[str] = None        # 缓存版本

    @staticmethod
    def from_dict(data: dict) -> "MultiMCManifestComponent":
        cached_requires = [
            MultiMCManifestRequire.from_dict(r)
            for r in data.get("cachedRequires", []) or []
        ]
        return MultiMCManifestComponent(
            uid=data.get("uid", ""),
            version=data.get("version", ""),
            important=data.get("important", True),
            dependency_only=data.get("dependencyOnly", False),
            cached_name=data.get("cachedName"),
            cached_requires=cached_requires,
            cached_version=data.get("cachedVersion"),
        )


@dataclass
class MultiMCManifest:
    """mmc-pack.json 的完整数据模型"""
    format_version: int                         # 格式版本（当前为 1）
    components: List[MultiMCManifestComponent]  # 组件列表

    def get_minecraft_version(self) -> Optional[str]:
        """获取 Minecraft 版本号"""
        for comp in self.components:
            if comp.uid == "net.minecraft":
                return comp.version
        return None

    def get_loader_info(self) -> Optional[Tuple[str, str]]:
        """获取第一个非 Minecraft 组件的 Loader 类型和版本。

        Returns:
            (loader_type, loader_version) 或 None
        """
        for comp in self.components:
            if comp.uid != "net.minecraft":
                loader_type = get_component_loader(comp.uid)
                if loader_type and loader_type != "vanilla":
                    return loader_type, comp.version
        return None

    def get_components_by_type(self, loader_type: str) -> List[MultiMCManifestComponent]:
        """获取指定 loader 类型的所有组件。"""
        uid = LOADER_TO_COMPONENT.get(loader_type)
        if not uid:
            return []
        return [c for c in self.components if c.uid == uid]

    @staticmethod
    def from_dict(data: dict) -> "MultiMCManifest":
        components = [
            MultiMCManifestComponent.from_dict(c)
            for c in data.get("components", []) or []
        ]
        return MultiMCManifest(
            format_version=data.get("formatVersion", 1),
            components=components,
        )

    @staticmethod
    def from_json(text: str) -> "MultiMCManifest":
        """从 JSON 字符串解析"""
        data = json.loads(text)
        mm = MultiMCManifest.from_dict(data)
        if not mm.components:
            raise ValueError("mmc-pack.json 格式错误：缺少 components")
        return mm


def read_mmc_pack_json(zip_path: str, root_entry: str = "") -> MultiMCManifest:
    """从 MultiMC zip 中读取 mmc-pack.json。

    Args:
        zip_path: .zip 文件路径
        root_entry: 根目录前缀

    Returns:
        MultiMCManifest 实例

    Raises:
        ValueError: 解析失败
    """
    manifest_path = f"{root_entry}mmc-pack.json"
    with zipfile.ZipFile(zip_path, "r") as zf:
        if manifest_path not in zf.namelist():
            raise ValueError(f"mmc-pack.json 不存在于 {manifest_path}")
        text = zf.read(manifest_path).decode("utf-8")

    return MultiMCManifest.from_json(text)


# ════════════════════════════════════════════════════════════════
# 数据模型：instance.cfg
# ════════════════════════════════════════════════════════════════


@dataclass
class MultiMCInstanceConfig:
    """MultiMC instance.cfg 的完整数据模型

    instance.cfg 使用 INI/Properties 格式，包含以下段：
    [General] - 名称、图标、类型、描述
    [MCLaunch] - 内存、Java、JVM 参数、窗口
    """
    name: str                                   # 实例名称
    icon_key: Optional[str] = None              # 图标文件名（不含扩展名）
    notes: str = ""                             # 实例描述
    instance_type: Optional[str] = None         # 实例类型

    # ── Java 配置 ──
    java_path: Optional[str] = None             # Java 可执行文件路径
    jvm_args: Optional[str] = None              # 自定义 JVM 参数
    max_memory: Optional[int] = None            # 最大内存 (MB)
    min_memory: Optional[int] = None            # 最小内存 (MB)
    perm_gen: Optional[int] = None              # PermGen 大小 (MB)

    # ── 窗口配置 ──
    fullscreen: bool = False                    # 全屏模式
    width: Optional[int] = None                 # 窗口宽度
    height: Optional[int] = None                # 窗口高度

    # ── 控制台配置 ──
    show_console: bool = False                  # 显示控制台
    show_console_on_error: bool = False         # 出错时显示控制台
    auto_close_console: bool = False            # 自动关闭控制台

    # ── 命令配置 ──
    wrapper_command: Optional[str] = None       # JVM 包装命令
    pre_launch_command: Optional[str] = None    # 启动前命令
    post_exit_command: Optional[str] = None     # 退出后命令

    # ── 覆盖标志 ──
    override_memory: bool = False
    override_java_location: bool = False
    override_java_args: bool = False
    override_console: bool = False
    override_commands: bool = False
    override_window: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于持久化）"""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MultiMCInstanceConfig":
        return MultiMCInstanceConfig(**{
            k: v for k, v in data.items()
            if k in MultiMCInstanceConfig.__dataclass_fields__
        })

    def to_properties(self) -> str:
        """导出为 instance.cfg 格式的 Properties 文本"""
        lines: List[str] = []
        lines.append("[General]")
        if self.instance_type:
            lines.append(f"InstanceType={self.instance_type}")
        lines.append(f"name={self.name}")
        if self.icon_key:
            lines.append(f"iconKey={self.icon_key}")
        if self.notes:
            lines.append(f"notes={self.notes}")
        lines.append("")
        lines.append("[MCLaunch]")
        if self.max_memory is not None:
            lines.append(f"MaxMemAlloc={self.max_memory}")
        if self.min_memory is not None:
            lines.append(f"MinMemAlloc={self.min_memory}")
        if self.perm_gen is not None:
            lines.append(f"PermGen={self.perm_gen}")
        if self.java_path:
            lines.append(f"JavaPath={self.java_path}")
        if self.jvm_args:
            lines.append(f"JvmArgs={self.jvm_args}")
        lines.append(f"LaunchMaximized={'true' if self.fullscreen else 'false'}")
        if self.width is not None:
            lines.append(f"MinecraftWinWidth={self.width}")
        if self.height is not None:
            lines.append(f"MinecraftWinHeight={self.height}")
        lines.append(f"ShowConsole={'true' if self.show_console else 'false'}")
        lines.append(f"ShowConsoleOnError={'true' if self.show_console_on_error else 'false'}")
        lines.append(f"AutoCloseConsole={'true' if self.auto_close_console else 'false'}")
        if self.wrapper_command:
            lines.append(f"WrapperCommand={self.wrapper_command}")
        if self.pre_launch_command:
            lines.append(f"PreLaunchCommand={self.pre_launch_command}")
        if self.post_exit_command:
            lines.append(f"PostExitCommand={self.post_exit_command}")
        lines.append(f"OverrideMemory={'true' if self.override_memory else 'false'}")
        lines.append(f"OverrideJavaLocation={'true' if self.override_java_location else 'false'}")
        lines.append(f"OverrideJavaArgs={'true' if self.override_java_args else 'false'}")
        lines.append(f"OverrideConsole={'true' if self.override_console else 'false'}")
        lines.append(f"OverrideCommands={'true' if self.override_commands else 'false'}")
        lines.append(f"OverrideWindow={'true' if self.override_window else 'false'}")
        return "\n".join(lines) + "\n"


def read_instance_cfg(
    zip_path: str,
    root_entry: str = "",
    default_name: Optional[str] = None,
) -> MultiMCInstanceConfig:
    """从 MultiMC zip 中读取 instance.cfg。

    使用 configparser 解析 INI 格式，并处理特殊字符问题
    （参考 HMCL issue #2991：末尾带冒号的引号值）。

    Args:
        zip_path: .zip 文件路径
        root_entry: 根目录前缀
        default_name: 默认实例名称（从路径推导）

    Returns:
        MultiMCInstanceConfig 实例

    Raises:
        ValueError: 解析失败
    """
    cfg_path = f"{root_entry}instance.cfg"
    with zipfile.ZipFile(zip_path, "r") as zf:
        if cfg_path not in zf.namelist():
            raise ValueError(f"instance.cfg 不存在于 {cfg_path}")
        text = zf.read(cfg_path).decode("utf-8", errors="replace")

    # 使用 configparser 解析 Properties 格式
    # instance.cfg 缺少标准的 section 头，需要添加默认 section
    parser = configparser.ConfigParser()
    # 保持键名大小写
    parser.optionxform = lambda option: option
    try:
        # 添加一个默认 section 头以便 parser 工作
        parser.read_string("[DEFAULT]\n" + text)
    except configparser.Error as e:
        raise ValueError(f"解析 instance.cfg 失败: {e}") from e

    def _get(key: str, default: Any = None) -> Any:
        return parser["DEFAULT"].get(key, default)

    def _get_bool(key: str, default: bool = False) -> bool:
        val = _get(key, str(default).lower())
        return val.lower() in ("true", "1", "yes")

    def _get_int(key: str, default: Any = None) -> Optional[int]:
        val = _get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _read_value(key: str) -> Optional[str]:
        """读取键值，处理 HMCL issue #2991 的边界情况。

        某些 instance.cfg 的值格式为 `"value":`（末尾多一个冒号），
        需要去除尾部冒号。
        """
        value = _get(key)
        if value is None:
            return None
        l = len(value)
        if l >= 2 and value[0] == '"' and value[-1] == ':':
            return value[:-1]
        return value

    name = default_name or _get("name", "MultiMC Instance")
    instance_cfg = MultiMCInstanceConfig(
        name=name,
        icon_key=_get("iconKey"),
        notes=_get("notes", ""),
        instance_type=_get("InstanceType"),
        java_path=_get("JavaPath"),
        jvm_args=_read_value("JvmArgs"),
        max_memory=_get_int("MaxMemAlloc"),
        min_memory=_get_int("MinMemAlloc"),
        perm_gen=_get_int("PermGen"),
        fullscreen=_get_bool("LaunchMaximized"),
        width=_get_int("MinecraftWinWidth"),
        height=_get_int("MinecraftWinHeight"),
        show_console=_get_bool("ShowConsole"),
        show_console_on_error=_get_bool("ShowConsoleOnError"),
        auto_close_console=_get_bool("AutoCloseConsole"),
        wrapper_command=_read_value("WrapperCommand"),
        pre_launch_command=_read_value("PreLaunchCommand"),
        post_exit_command=_read_value("PostExitCommand"),
        override_memory=_get_bool("OverrideMemory"),
        override_java_location=_get_bool("OverrideJavaLocation"),
        override_java_args=_get_bool("OverrideJavaArgs"),
        override_console=_get_bool("OverrideConsole"),
        override_commands=_get_bool("OverrideCommands"),
        override_window=_get_bool("OverrideWindow"),
    )
    return instance_cfg


# ════════════════════════════════════════════════════════════════
# 数据模型：JSON Patch（patches/{uid}.json）
# ════════════════════════════════════════════════════════════════


@dataclass
class MultiMCInstancePatch:
    """MultiMC 组件 JSON Patch 的数据模型

    每个组件（如 net.minecraft、net.fabricmc.fabric-loader）在 meta.multimc.org
    上维护一个 JSON 文件，描述该组件提供的 libraries、JVM args、mainClass 等。
    安装时需要将这些 patches 合并为标准的 version.json。
    """
    format_version: int                         # 格式版本（必须为 1）
    uid: str                                    # 组件 UID
    version: str                                # 组件版本
    name: Optional[str] = None                  # 可读名称

    # ── Minecraft 参数 ──
    main_class: Optional[str] = None            # 主类
    minecraft_arguments: Optional[str] = None   # 游戏参数
    asset_index: Optional[Dict] = None          # 资源索引信息 {id, url, sha1, ...}
    compatible_java_majors: List[int] = field(default_factory=list)

    # ── JVM 配置 ──
    jvm_args: List[str] = field(default_factory=list)

    # ── 库和文件 ──
    main_jar: Optional[Dict] = None             # 主 JAR 的 Library 描述
    libraries: List[Dict] = field(default_factory=list)
    maven_files: List[Dict] = field(default_factory=list)
    jar_mods: List[Dict] = field(default_factory=list)

    # ── 元数据 ──
    traits: List[str] = field(default_factory=list)
    tweakers: List[str] = field(default_factory=list)
    requires: List[MultiMCManifestRequire] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict, uid: str) -> "MultiMCInstancePatch":
        """从 JSON 字典构建实例。

        Args:
            data: 从 JSON 解析的字典
            uid: 组件 UID（JSON 中可能也有 "uid" 字段，但用参数覆盖更可靠）

        Returns:
            MultiMCInstancePatch 实例
        """
        # 合并 libraries 的两个来源：+libraries 和 libraries
        libs0 = data.get("+libraries", []) or []
        libs1 = data.get("libraries", []) or []
        all_libs = list(libs0) + list(libs1)

        # 解析 jar mods 文件名
        jar_mods_raw = data.get("jarMods", []) or []

        requires = [
            MultiMCManifestRequire.from_dict(r)
            for r in data.get("requires", []) or []
        ]

        return MultiMCInstancePatch(
            format_version=data.get("formatVersion", 1),
            uid=data.get("uid", uid),
            version=data.get("version", ""),
            name=data.get("name"),
            main_class=data.get("mainClass"),
            minecraft_arguments=data.get("minecraftArguments"),
            asset_index=data.get("assetIndex"),
            compatible_java_majors=list(data.get("compatibleJavaMajors", []) or []),
            jvm_args=list(data.get("+jvmArgs", []) or []),
            main_jar=data.get("mainJar"),
            libraries=all_libs,
            maven_files=list(data.get("mavenFiles", []) or []),
            jar_mods=list(jar_mods_raw),
            traits=list(data.get("+traits", []) or []),
            tweakers=list(data.get("+tweakers", []) or []),
            requires=requires,
        )

    @staticmethod
    def from_json(text: str, uid: str) -> "MultiMCInstancePatch":
        """从 JSON 字符串解析。

        Args:
            text: JSON 字符串
            uid: 组件 UID

        Returns:
            MultiMCInstancePatch 实例
        """
        data = json.loads(text)
        return MultiMCInstancePatch.from_dict(data, uid)

    def get_library_artifacts(self) -> List[str]:
        """获取所有 library 的 Maven artifact 路径。

        Returns:
            artifact 路径列表，如 ["com.example:mod:1.0"]
        """
        artifacts: List[str] = []
        for lib in self.libraries:
            name = lib.get("name", "")
            if name:
                artifacts.append(name)
        return artifacts

    def get_jar_mod_file_names(self) -> List[str]:
        """获取所有 jar mod 文件名。"""
        names: List[str] = []
        for jm in self.jar_mods:
            name = jm.get("name", "")
            if name:
                parts = name.split(":")
                if len(parts) >= 3:
                    names.append(f"{parts[1]}-{parts[2]}.jar")
                else:
                    names.append(name)
        return names


# ════════════════════════════════════════════════════════════════
# 数据模型：ModpackConfiguration（增量更新持久化）
# ════════════════════════════════════════════════════════════════


@dataclass
class FileInfo:
    """文件信息（用于增量更新比对）"""
    path: str                                   # 相对路径
    hash: str                                   # SHA-1 哈希
    download_url: Optional[str] = None          # 下载 URL

    @staticmethod
    def from_dict(data: dict) -> "FileInfo":
        return FileInfo(
            path=data["path"],
            hash=data["hash"],
            download_url=data.get("downloadURL"),
        )

    def to_dict(self) -> dict:
        d = {"path": self.path, "hash": self.hash}
        if self.download_url:
            d["downloadURL"] = self.download_url
        return d


@dataclass
class ModpackConfiguration:
    """整合包配置持久化（用于增量更新）

    参考 HMCL 的 ModpackConfiguration<T>，存储：
    - 整合包 manifest（MultiMCInstanceConfig）
    - 整合包类型（"MultiMC"）
    - 版本名称
    - 覆盖文件列表及 SHA-1 哈希
    """
    manifest: Dict[str, Any]                    # MultiMCInstanceConfig 序列化
    type: str = "MultiMC"                       # 整合包类型标识
    name: str = ""                              # 版本名称
    version: str = ""                           # 整合包版本号
    overrides: List[FileInfo] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict) -> "ModpackConfiguration":
        overrides = [
            FileInfo.from_dict(f)
            for f in data.get("overrides", []) or []
        ]
        return ModpackConfiguration(
            manifest=data.get("manifest", {}),
            type=data.get("type", "MultiMC"),
            name=data.get("name", ""),
            version=data.get("version", ""),
            overrides=overrides,
        )

    def to_dict(self) -> dict:
        return {
            "manifest": self.manifest,
            "type": self.type,
            "name": self.name,
            "version": self.version,
            "overrides": [f.to_dict() for f in self.overrides],
        }

    @staticmethod
    def load_from_file(filepath: str) -> Optional["ModpackConfiguration"]:
        """从 JSON 文件加载配置。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return ModpackConfiguration.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None

    def save_to_file(self, filepath: str) -> None:
        """保存配置到 JSON 文件。"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════


def compute_file_sha1(filepath: str) -> str:
    """计算文件的 SHA-1 哈希。"""
    h = hashlib.sha1()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def compute_overrides_hashes(
    base_dir: str,
    file_list: List[str],
) -> List[FileInfo]:
    """计算覆盖文件的 SHA-1 哈希列表。

    Args:
        base_dir: 基础目录
        file_list: 相对路径列表

    Returns:
        FileInfo 列表
    """
    result: List[FileInfo] = []
    for rel_path in file_list:
        abs_path = os.path.join(base_dir, rel_path)
        if os.path.isfile(abs_path):
            sha1 = compute_file_sha1(abs_path)
            result.append(FileInfo(path=rel_path, hash=sha1))
    return result
