"""模组分类器 — 从 jar 文件中读取模组元数据

支持读取:
- fabric.mod.json (Fabric)
- quilt.mod.json (Quilt)
- META-INF/neoforge.mods.toml (NeoForge)
- META-INF/mods.toml (Forge)
"""

import json
import re
import zipfile
from pathlib import Path
from typing import List, Optional

from logzero import logger

from launcher.mod_classifier.shared import LoaderType, ModMeta


def _read_zip_entry(zf: zipfile.ZipFile, name: str) -> Optional[str]:
    """安全读取 zip 内文本条目"""
    try:
        with zf.open(name) as fp:
            return fp.read().decode("utf-8", errors="ignore")
    except (KeyError, Exception):
        return None


def _build_query_tokens(*values: str) -> List[str]:
    """构建用于远程搜索的查询词"""
    tokens: List[str] = []
    seen: set = set()
    for v in values:
        v = v.strip().lower()
        if v and v not in seen:
            tokens.append(v)
            seen.add(v)
    return tokens


def read_jar_metadata(jar_path: Path) -> ModMeta:
    """读取一个 .jar 文件的所有可用元数据

    从 jar 中查找 fabric.mod.json → quilt.mod.json → META-INF/neoforge.mods.toml
    → META-INF/mods.toml 依次尝试。

    Args:
        jar_path: .jar 文件路径

    Returns:
        ModMeta 对象，包含从 jar 中提取的所有元数据
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = set(zf.namelist())

            # Fabric
            if "fabric.mod.json" in names:
                return _read_fabric_meta(jar_path, zf)

            # Quilt
            if "quilt.mod.json" in names:
                return _read_quilt_meta(jar_path, zf)

            # Forge / NeoForge mods.toml
            for toml_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if toml_name in names:
                    return _read_forge_toml_meta(jar_path, zf, toml_name)

    except (zipfile.BadZipFile, Exception) as e:
        logger.debug(f"读取 jar 元数据失败 [{jar_path.name}]: {e}")
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue=str(e)[:80])

    return _build_fallback_meta(jar_path)


def _read_fabric_meta(jar_path: Path, zf: zipfile.ZipFile) -> ModMeta:
    text = _read_zip_entry(zf, "fabric.mod.json")
    if text is None:
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue="fabric.mod.json 读取失败")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue=f"fabric.mod.json 解析失败: {e}")

    mod_id = str(data.get("id") or "").strip()
    mod_name = str(data.get("name") or jar_path.stem).strip()
    description = str(data.get("description") or "").strip()
    environment = str(data.get("environment") or "*").strip()
    entrypoints = list((data.get("entrypoints") or {}).keys())
    depends = list((data.get("depends") or {}).keys())

    return ModMeta(
        file_name=jar_path.name,
        file_path=str(jar_path),
        mod_id=mod_id,
        mod_name=mod_name,
        description=description,
        environment=environment,
        entrypoints=entrypoints,
        depends=depends,
        loader=LoaderType.FABRIC.value,
        metadata_source="fabric.mod.json",
        query_tokens=_build_query_tokens(jar_path.name, mod_id, mod_name),
    )


def _read_quilt_meta(jar_path: Path, zf: zipfile.ZipFile) -> ModMeta:
    text = _read_zip_entry(zf, "quilt.mod.json")
    if text is None:
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue="quilt.mod.json 读取失败")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue=f"quilt.mod.json 解析失败: {e}")

    loader_block = data.get("quilt_loader") or {}
    metadata_block = loader_block.get("metadata") or {}
    mod_id = str(loader_block.get("id") or "").strip()
    mod_name = str(metadata_block.get("name") or mod_id or jar_path.stem).strip()
    description = str(metadata_block.get("description") or "").strip()
    entrypoints = list((loader_block.get("entrypoints") or {}).keys())
    depends = [str(d["id"]) for d in (loader_block.get("depends") or []) if isinstance(d, dict) and d.get("id")]

    return ModMeta(
        file_name=jar_path.name,
        file_path=str(jar_path),
        mod_id=mod_id,
        mod_name=mod_name,
        description=description,
        environment=str(data.get("environment") or metadata_block.get("environment") or "*").strip(),
        entrypoints=entrypoints,
        depends=depends,
        loader=LoaderType.QUILT.value,
        metadata_source="quilt.mod.json",
        query_tokens=_build_query_tokens(jar_path.name, mod_id, mod_name),
    )


def _read_forge_toml_meta(jar_path: Path, zf: zipfile.ZipFile, toml_name: str) -> ModMeta:
    text = _read_zip_entry(zf, toml_name)
    if text is None or not text.strip():
        return _build_fallback_meta(jar_path, jar_status="damaged", jar_issue=f"{toml_name} 读取失败或为空")

    mod_ids = re.findall(r'(?m)^\s*modId\s*=\s*"([^"]+)"', text)
    display_names = re.findall(r'(?m)^\s*displayName\s*=\s*"([^"]+)"', text)
    desc_match = re.search(r'(?ms)^\s*description\s*=\s*(?:"""(.+?)"""|\'\'\'(.+?)\'\'\'|"([^"]*)")', text)

    mod_id = mod_ids[0].strip() if mod_ids else ""
    mod_name = display_names[0].strip() if display_names else mod_id or jar_path.stem
    description = ""
    if desc_match:
        description = next((g.strip() for g in desc_match.groups() if g), "")

    client_side_only = bool(re.search(r"(?m)^\s*clientSideOnly\s*=\s*true\b", text))
    dependency_sides: List[str] = []
    for dep_block in re.split(r"(?m)^\s*\[\[dependencies\.[^\]]+\]\]\s*", text)[1:]:
        side_match = re.search(r'(?m)^\s*side\s*=\s*"([A-Z_]+)"', dep_block)
        if side_match:
            dependency_sides.append(side_match.group(1).upper())

    is_neoforge = "neoforge" in toml_name.lower()
    if not is_neoforge:
        is_neoforge = bool(re.search(r'(?im)^\s*license\s*=\s*".*neoforge', text))
    loader = LoaderType.NEOFORGE.value if is_neoforge else LoaderType.FORGE.value

    return ModMeta(
        file_name=jar_path.name,
        file_path=str(jar_path),
        mod_id=mod_id,
        mod_name=mod_name,
        description=description,
        environment="*",
        entrypoints=[],
        depends=[],
        loader=loader,
        metadata_source=toml_name,
        query_tokens=_build_query_tokens(jar_path.name, mod_id, mod_name),
        client_side_only=client_side_only,
        dependency_sides=dependency_sides,
    )


def _build_fallback_meta(jar_path: Path, jar_status: str = "normal", jar_issue: str = "") -> ModMeta:
    """无元数据时的兜底"""
    return ModMeta(
        file_name=jar_path.name,
        file_path=str(jar_path),
        mod_id="",
        mod_name=jar_path.stem,
        description="",
        environment="",
        entrypoints=[],
        depends=[],
        loader=LoaderType.UNKNOWN.value,
        metadata_source="filename-only",
        query_tokens=_build_query_tokens(jar_path.name, jar_path.stem),
        jar_status=jar_status,
        jar_issue=jar_issue,
    )
