"""插件加载器 - 从 installed/ 目录扫描并动态加载插件"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from logzero import logger

from plugin_manager.base import PluginBase, PluginState
from plugin_manager.manifest import PluginManifest

# 指纹文件名
_FINGERPRINT_FILE = "_fingerprints.json"


def _compute_plugin_fingerprint(plugin_dir: Path) -> str:
    """计算插件目录中所有 .py 文件的 SHA-256 聚合指纹

    将所有 .py 文件按文件名排序后拼接哈希，作为插件完整性指纹。
    """
    py_files = sorted(plugin_dir.rglob("*.py"))
    h = hashlib.sha256()
    for f in py_files:
        # 包含相对路径以确保文件结构变化也能被检测
        rel = f.relative_to(plugin_dir)
        h.update(str(rel).encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def save_plugin_fingerprint(plugins_dir: Path, plugin_id: str, plugin_dir: Path) -> None:
    """安装时保存插件指纹"""
    fp_file = plugins_dir / _FINGERPRINT_FILE
    fingerprints = {}
    if fp_file.exists():
        try:
            fingerprints = json.loads(fp_file.read_text(encoding="utf-8"))
        except Exception:
            fingerprints = {}
    fingerprints[plugin_id] = _compute_plugin_fingerprint(plugin_dir)
    fp_file.write_text(json.dumps(fingerprints, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug(f"已保存插件指纹: {plugin_id}")


def verify_plugin_fingerprint(plugins_dir: Path, plugin_id: str, plugin_dir: Path) -> Tuple[bool, str]:
    """加载前验证插件完整性

    Returns:
        (是否通过, 错误信息)
    """
    fp_file = plugins_dir / _FINGERPRINT_FILE
    if not fp_file.exists():
        # 无指纹文件 — 可能是旧版安装，允许加载但记录警告
        logger.warning(f"插件指纹文件不存在，跳过完整性校验: {plugin_id}")
        return True, ""

    try:
        fingerprints = json.loads(fp_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取插件指纹文件失败: {e}")
        return True, ""

    stored = fingerprints.get(plugin_id)
    if stored is None:
        # 该插件无指纹记录（可能是手动放入目录的）
        logger.warning(f"插件无指纹记录，跳过完整性校验: {plugin_id}")
        return True, ""

    current = _compute_plugin_fingerprint(plugin_dir)
    if current != stored:
        return False, f"插件文件完整性校验失败 (SHA-256 不匹配)，文件可能被篡改"

    return True, ""


class PluginLoader:
    """插件加载器

    负责:
        - 扫描 installed/ 目录发现所有插件
        - 读取 plugin.json 并校验
        - 加载前验证插件完整性（SHA-256 指纹）
        - 通过 importlib 动态加载插件模块
        - 实例化 PluginBase 子类并注入属性
    """

    def __init__(self, plugins_dir: Path):
        """
        Args:
            plugins_dir: plugins/installed/ 目录路径
        """
        self._plugins_dir = Path(plugins_dir)
        self._plugins_dir.mkdir(parents=True, exist_ok=True)

    def scan_installed(self) -> Dict[str, PluginManifest]:
        """扫描已安装目录，返回 {plugin_id: PluginManifest}

        不加载模块，仅读取 manifest。
        """
        manifests: Dict[str, PluginManifest] = {}
        if not self._plugins_dir.exists():
            return manifests

        for item in sorted(self._plugins_dir.iterdir()):
            if not item.is_dir():
                continue
            plugin_json = item / "plugin.json"
            if not plugin_json.exists():
                continue

            try:
                manifest = PluginManifest.from_file(plugin_json, install_path=item)
                if manifest.id:
                    manifests[manifest.id] = manifest
                    logger.debug(f"发现插件: {manifest.id} v{manifest.version} ({item})")
            except Exception as e:
                logger.error(f"读取插件清单失败 ({plugin_json}): {e}")

        return manifests

    def load_module(self, manifest: PluginManifest) -> Tuple[Optional[PluginBase], str]:
        """加载单个插件模块并返回 PluginBase 实例

        Args:
            manifest: 插件清单（需含 install_path）

        Returns:
            (实例或 None, 错误信息)
            - 成功: (PluginBase 实例, "")
            - 失败: (None, "错误原因")
        """
        if manifest.install_path is None:
            return None, "manifest.install_path 未设置"

        plugin_dir = manifest.install_path
        entry_module = manifest.entry
        entry_path = plugin_dir / f"{entry_module}.py"

        if not entry_path.exists():
            return None, f"入口模块 {entry_module}.py 不存在"

        # 完整性校验：验证插件文件指纹
        fp_ok, fp_err = verify_plugin_fingerprint(self._plugins_dir, manifest.id, plugin_dir)
        if not fp_ok:
            logger.error(f"插件完整性校验失败: {manifest.id} — {fp_err}")
            return None, fp_err

        # 构造唯一的模块名（防止同名插件冲突）
        safe_id = manifest.id.replace(".", "_").replace("-", "_")
        module_name = f"fmcl_plugin_{safe_id}"

        try:
            # 如果之前已加载，先卸载
            if module_name in sys.modules:
                del sys.modules[module_name]

            # 动态加载
            spec = importlib.util.spec_from_file_location(
                module_name, entry_path, submodule_search_locations=[str(plugin_dir)]
            )
            if spec is None or spec.loader is None:
                return None, f"无法创建模块 spec: {entry_path}"

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # 查找 PluginBase 子类
            plugin_class = self._find_plugin_class(module)
            if plugin_class is None:
                del sys.modules[module_name]
                return None, "模块中未找到继承自 PluginBase 的类"

            # 实例化
            instance = plugin_class()
            return instance, ""

        except Exception as e:
            if module_name in sys.modules:
                del sys.modules[module_name]
            return None, f"加载模块异常: {e}"

    def reload_module(self, manifest: PluginManifest) -> Tuple[Optional[PluginBase], str]:
        """热重载插件模块

        与 load_module 类似，但确保清除缓存。
        """
        if manifest.install_path is None:
            return None, "manifest.install_path 未设置"

        safe_id = manifest.id.replace(".", "_").replace("-", "_")
        module_name = f"fmcl_plugin_{safe_id}"

        # 清除所有相关模块缓存
        modules_to_remove = [m for m in sys.modules if m == module_name or m.startswith(module_name + ".")]
        for m in modules_to_remove:
            del sys.modules[m]

        return self.load_module(manifest)

    def inject_attributes(
        self,
        instance: PluginBase,
        manifest: PluginManifest,
        data_dir: Path,
        config: Optional[dict],
        manager,  # "PluginManager"
        perm_state,
    ):
        """向 PluginBase 实例注入运行时属性"""
        instance.manifest = manifest
        instance.plugin_dir = manifest.install_path
        instance.data_dir = data_dir
        instance.data_dir.mkdir(parents=True, exist_ok=True)
        instance.config = dict(config) if config else instance.get_default_config()
        instance._manager = manager
        instance._perm_state = perm_state

    # ── 私有方法 ──

    def _find_plugin_class(self, module) -> Optional[type]:
        """在模块中查找继承自 PluginBase 的类"""
        from plugin_manager.base import PluginBase as _PluginBase

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, _PluginBase) and attr is not _PluginBase:
                return attr
        return None
