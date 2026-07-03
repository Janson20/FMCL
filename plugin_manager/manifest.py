"""插件清单模块 - PluginManifest 数据类与 plugin.json 规范"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

from logzero import logger

# plugin.json 顶层必需字段
_REQUIRED_FIELDS = ["id", "name", "version", "author", "min_fmcl_version"]

# plugin.json 顶层可选字段及其默认值
_OPTIONAL_DEFAULTS: Dict[str, Any] = {
    "description": {},
    "max_fmcl_version": None,
    "permissions": [],
    "dependencies": {},
    "conflicts": {},
    "tags": [],
    "homepage": "",
    "license": "",
    "icon": "",
    "exports": [],
    "imports": [],
    "entry": "__init__",
}

PLUGIN_MANIFEST_SCHEMA = {
    "type": "object",
    "required": _REQUIRED_FIELDS,
    "properties": {
        "id": {"type": "string", "pattern": r"^[a-zA-Z][a-zA-Z0-9_\-.]*$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$"},
        "author": {"type": "string", "minLength": 1},
        "min_fmcl_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "max_fmcl_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "permissions": {"type": "array", "items": {"type": "string"}},
        "dependencies": {"type": "object"},
        "conflicts": {"type": "object"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "homepage": {"type": "string", "format": "uri"},
        "license": {"type": "string"},
        "icon": {"type": "string"},
        "exports": {"type": "array", "items": {"type": "string"}},
        "imports": {"type": "array", "items": {"type": "string"}},
        "entry": {"type": "string", "default": "__init__"},
    },
}


@dataclass
class PluginManifest:
    """插件清单数据模型

    Attributes:
        id: 插件唯一标识 (反向域名风格，如 com.example.my-plugin)
        name: 插件显示名称
        version: 插件版本 (SemVer)
        author: 插件作者
        min_fmcl_version: 最低兼容的 FMCL 版本
        description: 多语言描述字典，如 {"zh_CN": "描述", "en_US": "Description"}
        max_fmcl_version: 最高兼容的 FMCL 版本，None 表示无上限
        permissions: 请求的权限列表
        dependencies: 依赖插件及版本约束，如 {"com.other.plugin": ">=1.0,<2.0"}
        conflicts: 冲突插件及版本约束
        tags: 标签列表，如 ["ui", "utility"]
        homepage: 插件主页 URL
        license: 许可证标识
        icon: 图标文件名 (相对于插件根目录)
        exports: 导出的 API 名称列表
        imports: 依赖的其他插件 API 名称列表
        entry: 入口模块名 (不含 .py)，默认为 __init__
    """
    id: str
    name: str
    version: str
    author: str
    min_fmcl_version: str
    description: Dict[str, str] = field(default_factory=dict)
    max_fmcl_version: Optional[str] = None
    permissions: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    conflicts: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    homepage: str = ""
    license: str = ""
    icon: str = ""
    exports: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    entry: str = "__init__"

    # 运行时字段（不由 plugin.json 设置）
    install_path: Optional[Path] = None   # 插件安装路径

    def validate(self) -> List[str]:
        """校验清单字段合法性，返回错误列表。空列表表示校验通过。"""
        errors = []

        # 检查 ID 格式
        if not self.id or not self.id[0].isalpha():
            errors.append(f"插件 ID '{self.id}' 必须以字母开头")
        if not all(c.isalnum() or c in '_-.' for c in self.id):
            errors.append(f"插件 ID '{self.id}' 包含非法字符")

        # 检查 SemVer 格式
        parts = self.version.split("-", 1)
        ver_core = parts[0]
        ver_nums = ver_core.split(".")
        if len(ver_nums) != 3 or not all(n.isdigit() for n in ver_nums):
            errors.append(f"插件版本 '{self.version}' 不符合 SemVer 规范")

        # 检查 min_fmcl_version
        fmcl_parts = self.min_fmcl_version.split(".")
        if len(fmcl_parts) != 3 or not all(n.isdigit() for n in fmcl_parts):
            errors.append(f"min_fmcl_version '{self.min_fmcl_version}' 格式无效")

        # 检查 max_fmcl_version
        if self.max_fmcl_version is not None:
            mx_parts = self.max_fmcl_version.split(".")
            if len(mx_parts) != 3 or not all(n.isdigit() for n in mx_parts):
                errors.append(f"max_fmcl_version '{self.max_fmcl_version}' 格式无效")

        # 检查入口模块名
        if not self.entry or not self.entry.isidentifier():
            errors.append(f"入口模块名 '{self.entry}' 不是有效的 Python 标识符")

        return errors

    def to_dict(self) -> dict:
        """序列化为字典（用于持久化存储）"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "min_fmcl_version": self.min_fmcl_version,
            "max_fmcl_version": self.max_fmcl_version,
            "description": self.description,
            "permissions": self.permissions,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
            "tags": self.tags,
            "homepage": self.homepage,
            "license": self.license,
            "icon": self.icon,
            "exports": self.exports,
            "imports": self.imports,
            "entry": self.entry,
        }

    @classmethod
    def from_dict(cls, data: dict, install_path: Optional[Path] = None) -> "PluginManifest":
        """从字典反序列化"""
        filtered = {}
        for key in _REQUIRED_FIELDS:
            filtered[key] = data.get(key, "")
        for key, default in _OPTIONAL_DEFAULTS.items():
            filtered[key] = data.get(key, default)
        manifest = cls(**filtered)
        manifest.install_path = install_path
        return manifest

    @classmethod
    def from_file(cls, path: Path, install_path: Optional[Path] = None) -> "PluginManifest":
        """从 plugin.json 文件加载"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        manifest = cls.from_dict(data, install_path)
        errors = manifest.validate()
        if errors:
            logger.warning(f"插件清单校验警告 ({path}): {'; '.join(errors)}")
        return manifest

    def get_description(self, lang: str = "zh_CN") -> str:
        """获取指定语言的描述，降级到 zh_CN 或 en_US"""
        if lang in self.description:
            return self.description[lang]
        for fallback in ("zh_CN", "en_US"):
            if fallback in self.description:
                return self.description[fallback]
        # 返回第一个可用语言
        for v in self.description.values():
            return v
        return ""

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PluginManifest):
            return self.id == other.id
        return False
