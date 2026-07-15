"""模组分类器 — 共享类型定义"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class LoaderType(str, Enum):
    """模组加载器类型"""

    FABRIC = "fabric"
    QUILT = "quilt"
    FORGE = "forge"
    NEOFORGE = "neoforge"
    UNKNOWN = "unknown"


# 纯客户端入口点关键词
CLIENT_ONLY_ENTRYPOINT_HINTS = frozenset(
    {
        "client",
        "modmenu",
        "rei_client",
        "emi",
        "emi_client",
        "jei",
        "jei_mod_plugin",
        "jade",
        "waila",
        "journeymap",
        "roughlyenoughitems",
    }
)


@dataclass
class ModMeta:
    """从 jar 中提取的模组元数据"""

    file_name: str
    file_path: str
    mod_id: str
    mod_name: str
    description: str
    environment: str  # "*" / "client" / "server"
    entrypoints: List[str]  # Fabric/Quilt 入口点
    depends: List[str]
    loader: str  # LoaderType value
    metadata_source: str  # 元数据来源文件名
    client_side_only: bool = False  # Forge clientSideOnly=true
    dependency_sides: List[str] = field(default_factory=list)
    jar_status: str = "normal"  # normal / damaged / error
    jar_issue: str = ""
    query_tokens: List[str] = field(default_factory=list)  # 远程搜索用关键词


@dataclass
class Classification:
    """单个模组的分类结果"""

    category: str  # "server-keep" / "client-only" / "unknown"
    source: str  # "local" / "modrinth" / "mcmod" / "offline-db"
    reason: str  # 判定理由
    evidence_url: str = ""


CATEGORY_LABELS = {"server-keep": "服务端保留", "client-only": "纯客户端", "unknown": "待人工确认"}
