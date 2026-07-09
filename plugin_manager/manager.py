"""插件管理器 - 统一入口，组合所有子模块

提供:
    - 插件发现、加载、启用、禁用、卸载（全部支持热操作）
    - 插件安装（从 .fmpl 文件）
    - 插件更新 + 失败回滚
    - 权限管理与运行时确认
    - 钩子总线代理
    - 崩溃隔离（单插件异常不影响其他插件和启动器）
    - 插件间 API 导出与导入
"""

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from plugin_manager.base import HookPoint, PluginBase, PluginState
from plugin_manager.dependency import DependencyResolver
from plugin_manager.hook_bus import HookBus
from plugin_manager.installer import PluginInstaller
from plugin_manager.loader import PluginLoader
from plugin_manager.manifest import PluginManifest
from plugin_manager.permissions import (
    PermissionRiskLevel,
    PluginPermission,
    PluginPermissionState,
    classify_permissions,
    get_permission_risk,
)


class PluginManager:
    """插件管理器

    用法:
        pm = PluginManager(plugins_root=Path("plugins"))
        pm.scan()
        pm.load_plugin("com.example.my-plugin")
        pm.enable_plugin("com.example.my-plugin")
        pm.emit(HookPoint.APP_STARTUP)
    """

    # 插件 ID 最大长度
    MAX_PLUGIN_ID_LENGTH = 128

    def __init__(self, plugins_root: Path):
        """
        Args:
            plugins_root: plugins/ 根目录
        """
        self._root = Path(plugins_root)
        self._installed_dir = self._root / "installed"
        self._disabled_dir = self._root / "disabled"
        self._config_dir = self._root / "configs"
        self._data_dir = self._root / "data"
        self._temp_dir = self._root / "temp"
        self._cache_dir = self._root / "cache"

        for d in [
            self._root,
            self._installed_dir,
            self._disabled_dir,
            self._config_dir,
            self._data_dir,
            self._temp_dir,
            self._cache_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # 子模块
        self._loader = PluginLoader(self._installed_dir)
        self._installer = PluginInstaller(self._installed_dir, self._disabled_dir, self._temp_dir)
        self._hook_bus = HookBus()
        self._dependency = DependencyResolver()

        # 运行时状态
        self._lock = threading.RLock()
        self._manifests: Dict[str, PluginManifest] = {}  # 已扫描的插件清单
        self._instances: Dict[str, PluginBase] = {}  # 已加载的插件实例
        self._states: Dict[str, PluginState] = {}  # 插件状态
        self._configs: Dict[str, dict] = {}  # 插件配置缓存
        self._perm_states: Dict[str, PluginPermissionState] = {}  # 权限状态
        self._exported_apis: Dict[str, Dict[str, Any]] = {}  # 插件导出的 API {plugin_id: {api_name: obj}}
        self._error_reasons: Dict[str, str] = {}  # 禁用/错误原因

        # 通知回调（由主 UI 设置）
        self._notify_callback: Optional[Callable[[str, str, str, str], None]] = None

        # 权限确认回调（由主 UI 设置，实现弹窗）
        self._perm_confirm_callback: Optional[Callable[[str, PluginPermission], bool]] = None

        # 插件状态持久化文件
        self._state_file = self._config_dir / "plugin_states.json"

    # ═══════════════════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════════════════

    @property
    def hook_bus(self) -> HookBus:
        """获取钩子总线（供外部直接注册/触发钩子）"""
        return self._hook_bus

    def set_notify_callback(self, callback: Callable[[str, str, str, str], None]):
        """设置通知回调

        Args:
            callback: (plugin_id, title, message, level) -> None
        """
        self._notify_callback = callback

    def set_perm_confirm_callback(self, callback: Callable[[str, PluginPermission], bool]):
        """设置权限确认回调

        Args:
            callback: (plugin_id, permission) -> bool (用户是否批准)
        """
        self._perm_confirm_callback = callback

    # ── 扫描 ──

    def scan(self) -> List[str]:
        """扫描 installed/ 目录，发现所有插件。

        会自动恢复上次持久化的插件启用/禁用状态。

        Returns:
            发现的插件 ID 列表
        """
        with self._lock:
            discovered = self._loader.scan_installed()
            self._manifests.update(discovered)

            # 加载持久化状态
            persisted = self._load_plugin_states()

            for pid in discovered:
                if pid not in self._states:
                    self._states[pid] = PluginState.SCANNED
                    self._load_config(pid)
                    self._load_perm_state(pid)

            # 清理已删除的插件（包括清理持久化文件中的无效条目）
            for pid in list(self._states.keys()):
                if pid not in self._manifests and self._states[pid] == PluginState.SCANNED:
                    del self._states[pid]

            # 清理持久化文件中已不存在的插件
            stale_pids = [pid for pid in persisted if pid not in discovered]
            if stale_pids:
                self._save_plugin_states()

            # 自动启用上次处于 ENABLED 状态的插件
            for pid in discovered:
                if persisted.get(pid) == PluginState.ENABLED.value:
                    if self._states.get(pid) == PluginState.SCANNED:
                        logger.info(f"恢复插件启用状态: {pid}")
                        # 恢复启用前自动授权权限（持久化 ENABLED 状态表示用户此前已确认）
                        perm_state = self._perm_states.get(pid)
                        if perm_state is None or perm_state.get_ungranted_permissions():
                            self.grant_all_permissions(pid)
                        self.load_plugin(pid)
                        ok, msg = self.enable_plugin(pid)
                        if not ok:
                            logger.warning(f"自动启用插件 {pid} 失败: {msg}")

            return list(discovered.keys())

    # ── 加载 / 启用 / 禁用 / 卸载 ──

    def load_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """加载插件模块（import 模块 + 创建实例）

        Args:
            plugin_id: 插件 ID

        Returns:
            (是否成功, 错误信息)
        """
        with self._lock:
            if plugin_id not in self._manifests:
                return False, f"插件未发现: {plugin_id}"

            manifest = self._manifests[plugin_id]

            # 检查版本兼容性
            ok, msg = self._check_fmcl_version(manifest)
            if not ok:
                self._states[plugin_id] = PluginState.INCOMPATIBLE
                self._error_reasons[plugin_id] = msg
                return False, msg

            # 加载模块
            self._states[plugin_id] = PluginState.LOADING
            instance, error = self._loader.load_module(manifest)
            if instance is None:
                self._states[plugin_id] = PluginState.INIT_ERROR
                self._error_reasons[plugin_id] = error
                return False, error

            # 注入属性
            data_dir = self._data_dir / plugin_id
            perm_state = self._perm_states.get(plugin_id, PluginPermissionState(plugin_id))
            self._loader.inject_attributes(instance, manifest, data_dir, self._configs.get(plugin_id), self, perm_state)

            self._instances[plugin_id] = instance
            self._states[plugin_id] = PluginState.LOADED

            # 调用 on_load
            try:
                instance.on_load()
            except Exception as e:
                logger.warning(f"插件 {plugin_id} on_load 异常: {e}")

            return True, ""

    def enable_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """启用插件（调用 on_enable）

        对热启用：直接从 disabled 状态复用已加载实例。
        对新插件：先 load 再 enable。
        """
        with self._lock:
            state = self._states.get(plugin_id)
            if state is None:
                return False, f"插件未发现: {plugin_id}"

            # 如果之前被禁用，重新加载
            if state == PluginState.DISABLED:
                ok, msg = self.load_plugin(plugin_id)
                if not ok:
                    return False, msg

            # 如果尚未加载
            if state in (PluginState.SCANNED,):
                ok, msg = self.load_plugin(plugin_id)
                if not ok:
                    return False, msg

            if state == PluginState.ENABLED:
                return True, ""  # 已经启用

            instance = self._instances.get(plugin_id)
            if instance is None:
                return False, "插件实例不存在"

            # 检查未授权的权限
            perm_state = self._perm_states.get(plugin_id)
            if perm_state:
                ungranted = perm_state.get_ungranted_permissions()
                if ungranted:
                    high_risk_ungranted = [p for p in ungranted if get_permission_risk(p) == PermissionRiskLevel.HIGH]
                    # 只有高风险才阻止启用（低中风险可以在运行时处理）
                    if high_risk_ungranted:
                        return False, f"存在未授权的高风险权限: {[p.value for p in high_risk_ungranted]}"

            # 调用 on_enable
            try:
                instance.on_enable()
                self._states[plugin_id] = PluginState.ENABLED
                self._error_reasons.pop(plugin_id, None)
                self._save_plugin_states()
                logger.info(f"插件已启用: {plugin_id}")
                return True, ""
            except Exception as e:
                self._states[plugin_id] = PluginState.ERROR
                self._error_reasons[plugin_id] = str(e)
                logger.error(f"插件 {plugin_id} on_enable 异常: {e}")
                return False, f"启用失败: {e}"

    def disable_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """禁用插件（调用 on_disable + 注销钩子）"""
        with self._lock:
            instance = self._instances.get(plugin_id)
            if instance is None:
                return False, "插件未加载"

            # 调用 on_disable（异常不影响流程）
            try:
                instance.on_disable()
            except Exception as e:
                logger.error(f"插件 {plugin_id} on_disable 异常: {e}")

            # 注销全部钩子
            self._hook_bus.unregister_all(plugin_id)

            # 清除导出 API
            self._exported_apis.pop(plugin_id, None)

            self._states[plugin_id] = PluginState.DISABLED
            self._save_plugin_states()
            logger.info(f"插件已禁用: {plugin_id}")
            return True, ""

    def uninstall_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """卸载插件（禁用 + 删除文件）"""
        with self._lock:
            self._states[plugin_id] = PluginState.UNINSTALLING

            # 先禁用
            if plugin_id in self._instances:
                self.disable_plugin(plugin_id)

            # 调用 on_uninstall
            instance = self._instances.pop(plugin_id, None)
            if instance:
                try:
                    instance.on_uninstall()
                except Exception as e:
                    logger.error(f"插件 {plugin_id} on_uninstall 异常: {e}")

            # 从运行时状态中移除
            self._instances.pop(plugin_id, None)
            self._manifests.pop(plugin_id, None)
            self._states.pop(plugin_id, None)
            self._configs.pop(plugin_id, None)
            self._perm_states.pop(plugin_id, None)
            self._error_reasons.pop(plugin_id, None)
            self._exported_apis.pop(plugin_id, None)

            # 持久化状态同步
            self._save_plugin_states()

            # 删除文件（清除 sys.modules）
            import sys

            safe_id = plugin_id.replace(".", "_").replace("-", "_")
            module_prefix = f"fmcl_plugin_{safe_id}"
            mods_to_remove = [m for m in sys.modules if m.startswith(module_prefix)]
            for m in mods_to_remove:
                del sys.modules[m]

            return self._installer.uninstall(plugin_id)

    # ── 安装 ──

    def install_from_file(self, fmpl_path: str, plugin_id: str) -> Tuple[bool, str]:
        """从 .fmpl 文件安装插件"""
        ok, error = self._installer.install_from_fmpl(fmpl_path, plugin_id)
        if not ok:
            return False, error
        # 重新扫描并加载
        self.scan()
        return True, ""

    def update_plugin(self, plugin_id: str, fmpl_path: str) -> Tuple[bool, str]:
        """更新插件（备份 → 禁用 → 安装 → 启用；失败则回滚）

        Args:
            plugin_id: 插件 ID
            fmpl_path: 新版 .fmpl 文件路径

        Returns:
            (成功与否, 错误信息)
        """
        with self._lock:
            # 1. 备份旧版本
            backup_path, backup_error = self._installer.backup_existing(plugin_id)
            if backup_path is None and self._instances.get(plugin_id) is not None:
                logger.warning(f"备份失败但继续: {backup_error}")

            # 2. 禁用旧版本
            if plugin_id in self._instances:
                self.disable_plugin(plugin_id)

            # 3. 安装新版本
            ok, error = self._installer.install_from_fmpl(fmpl_path, plugin_id)
            if not ok:
                # 回滚
                if backup_path is not None:
                    logger.warning(f"安装失败，执行回滚: {plugin_id}")
                    self._installer.rollback(plugin_id)
                    self._installer.cleanup_backup(plugin_id)
                return False, f"安装新版本失败: {error}"

            # 4. 重新扫描并启用
            self.scan()
            load_ok, load_error = self.load_plugin(plugin_id)
            if not load_ok:
                logger.warning(f"加载新版本失败，执行回滚: {load_error}")
                self._installer.rollback(plugin_id)
                self._installer.cleanup_backup(plugin_id)
                self.scan()
                return False, f"加载新版本失败: {load_error}"

            enable_ok, enable_error = self.enable_plugin(plugin_id)
            if not enable_ok:
                logger.warning(f"启用新版本失败，执行回滚: {enable_error}")
                self._installer.rollback(plugin_id)
                self._installer.cleanup_backup(plugin_id)
                self.scan()
                return False, f"启用新版本失败: {enable_error}"

            # 5. 清理备份
            self._installer.cleanup_backup(plugin_id)
            return True, ""

    def update_plugin_from_market(
        self, plugin_id: str, progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> Tuple[bool, str]:
        """从市场下载并更新已安装的插件

        Args:
            plugin_id: 插件 ID
            progress_callback: 下载进度回调 (stage, cur, total)

        Returns:
            (是否成功, 错误信息)
        """
        market = self.market
        if market is None:
            return False, "插件市场暂不可用，请检查网络连接"

        # 1. 从市场下载最新版 .fmpl
        fmpl_path, error = market.download_plugin(plugin_id, progress_callback=progress_callback)
        if error:
            return False, f"下载失败: {error}"

        # 2. 使用已有的 update_plugin 方法更新
        return self.update_plugin(plugin_id, fmpl_path)

    # ── 查询 ──

    def get_loaded_plugins(self) -> List[str]:
        """获取已加载的插件 ID 列表"""
        return list(self._instances.keys())

    def get_installed_versions_map(self) -> Dict[str, str]:
        """获取已安装插件的版本映射 {plugin_id: version}"""
        result = {}
        for pid, manifest in self._manifests.items():
            result[pid] = manifest.version
        return result

    def get_enabled_plugins(self) -> List[str]:
        """获取已启用的插件 ID 列表"""
        return [pid for pid, state in self._states.items() if state == PluginState.ENABLED]

    def get_plugin_state(self, plugin_id: str) -> Optional[PluginState]:
        """获取插件状态"""
        return self._states.get(plugin_id)

    def get_plugin_error(self, plugin_id: str) -> Optional[str]:
        """获取插件错误原因"""
        return self._error_reasons.get(plugin_id)

    def get_all_plugin_meta(self) -> Dict[str, dict]:
        """获取所有已知插件的元信息（供 UI 管理页使用）

        Returns:
            {plugin_id: {manifest_data, state, error}}
        """
        result = {}
        for pid, manifest in self._manifests.items():
            result[pid] = {
                "manifest": manifest.to_dict(),
                "state": self._states.get(pid, PluginState.SCANNED).value,
                "error": self._error_reasons.get(pid, ""),
                "permissions": self._perm_states.get(pid, PluginPermissionState(pid)).to_dict(),
            }
        return result

    # ── 权限 ──

    def get_permission_state(self, plugin_id: str) -> Optional[PluginPermissionState]:
        """获取插件的权限状态"""
        return self._perm_states.get(plugin_id)

    def grant_permission(self, plugin_id: str, permission: PluginPermission, always: bool = False):
        """授权单个权限"""
        ps = self._perm_states.get(plugin_id)
        if ps:
            ps.grant(permission, always)
            self._save_perm_state(plugin_id)

    def revoke_permission(self, plugin_id: str, permission: PluginPermission):
        """撤销单个权限"""
        ps = self._perm_states.get(plugin_id)
        if ps:
            ps.revoke(permission)
            self._save_perm_state(plugin_id)

    def grant_all_permissions(self, plugin_id: str):
        """授予所有权限（用户在安装确认时同意）"""
        ps = self._perm_states.get(plugin_id)
        if ps:
            for perm in PluginPermission:
                risk = get_permission_risk(perm)
                ps.grant(perm, always=(risk == PermissionRiskLevel.HIGH))
            self._save_perm_state(plugin_id)

    def check_permission(self, plugin_id: str, permission: PluginPermission) -> str:
        """检查权限: 'granted' | 'need_confirm' | 'denied'"""
        ps = self._perm_states.get(plugin_id)
        if ps is None:
            return "denied"
        return ps.check_or_request(permission)

    def request_permission(self, plugin_id: str, permission: PluginPermission) -> bool:
        """请求权限（检查 + 必要时通过回调弹窗确认）

        Returns:
            用户是否批准
        """
        status = self.check_permission(plugin_id, permission)
        if status == "granted":
            return True
        if status == "denied":
            return False
        # need_confirm
        if self._perm_confirm_callback:
            approved = self._perm_confirm_callback(plugin_id, permission)
            if approved:
                self.grant_permission(plugin_id, permission)
            return approved
        return False

    # ── 插件间 API ──

    def export_api(self, plugin_id: str, api_name: str, api_obj: Any):
        """导出 API（由插件调用）"""
        if plugin_id not in self._exported_apis:
            self._exported_apis[plugin_id] = {}
        self._exported_apis[plugin_id][api_name] = api_obj
        logger.debug(f"插件 {plugin_id} 导出 API: {api_name}")

    def get_plugin_api(self, plugin_id: str, api_name: str) -> Optional[Any]:
        """获取其他插件导出的 API"""
        apis = self._exported_apis.get(plugin_id, {})
        return apis.get(api_name)

    # ── 钩子代理 ──

    def emit(self, hook_point: HookPoint, **kwargs) -> Any:
        """触发钩子（委托给 HookBus）"""
        return self._hook_bus.emit(hook_point, **kwargs)

    def register_hook(self, plugin_id: str, hook_point: HookPoint, callback: Callable, priority: int = 100):
        """注册钩子（供插件调用）"""
        # 检查权限: 核心钩子需要相应权限
        self._hook_bus.register(hook_point, callback, plugin_id, priority)

    # ── 配置管理 ──

    def init_market(self):
        """初始化插件市场（延迟初始化，确保网络模块就绪）

        Returns:
            PluginMarket 实例，或 None（初始化失败时）
        """
        if hasattr(self, "_market") and self._market is not None:
            return self._market
        try:
            from plugin_manager.market import PluginMarket

            self._market = PluginMarket(cache_dir=self._cache_dir)
            return self._market
        except Exception as e:
            logger.warning(f"插件市场初始化失败: {e}")
            return None

    @property
    def market(self):
        """获取插件市场实例（懒初始化）"""
        if not hasattr(self, "_market") or self._market is None:
            return self.init_market()
        return self._market

    def get_plugin_config(self, plugin_id: str) -> dict:
        """获取插件配置"""
        return self._configs.get(plugin_id, {})

    def save_plugin_config(self, plugin_id: str):
        """持久化保存插件配置"""
        config = self._configs.get(plugin_id)
        if config is None:
            return
        config_path = self._config_dir / f"{plugin_id}.json"
        try:
            from config import _json_dumps

            config_path.write_text(_json_dumps(config, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存插件配置失败 ({plugin_id}): {e}")

    # ── 内部方法 ──

    def _load_plugin_states(self) -> Dict[str, str]:
        """从 plugin_states.json 加载上次持久化的插件状态

        Returns:
            {plugin_id: state_value} 字典
        """
        if not self._state_file.exists():
            return {}
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as e:
            logger.warning(f"加载插件状态文件失败: {e}")
            return {}

    def _save_plugin_states(self):
        """持久化当前插件状态到 plugin_states.json

        仅保存 ENABLED / DISABLED 两种稳定状态，
        过滤掉 SCANNED / LOADING / UNINSTALLING 等临时状态。
        """
        persistable = {}
        for pid, state in self._states.items():
            if state in (PluginState.ENABLED, PluginState.DISABLED):
                persistable[pid] = state.value
        try:
            self._state_file.write_text(json.dumps(persistable, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存插件状态文件失败: {e}")

    def _notify_user(self, plugin_id: str, title: str, message: str, level: str = "info"):
        """由 PluginBase.notify() 调用"""
        if self._notify_callback:
            self._notify_callback(plugin_id, title, message, level)

    def _load_config(self, plugin_id: str):
        """从 configs/ 目录加载插件配置"""
        config_path = self._config_dir / f"{plugin_id}.json"
        if config_path.exists():
            try:
                self._configs[plugin_id] = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"加载插件配置失败 ({plugin_id}): {e}")
                self._configs[plugin_id] = {}

    def _load_perm_state(self, plugin_id: str):
        """从 configs/ 目录加载权限状态"""
        perm_path = self._config_dir / f"{plugin_id}_perms.json"
        if perm_path.exists():
            try:
                data = json.loads(perm_path.read_text(encoding="utf-8"))
                self._perm_states[plugin_id] = PluginPermissionState.from_dict(plugin_id, data)
            except Exception as e:
                logger.warning(f"加载权限状态失败 ({plugin_id}): {e}")

    def _save_perm_state(self, plugin_id: str):
        """持久化权限状态"""
        ps = self._perm_states.get(plugin_id)
        if ps is None:
            return
        perm_path = self._config_dir / f"{plugin_id}_perms.json"
        try:
            perm_path.write_text(json.dumps(ps.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存权限状态失败 ({plugin_id}): {e}")

    def _check_fmcl_version(self, manifest: PluginManifest) -> Tuple[bool, str]:
        """检查 FMCL 版本兼容性"""
        from updater import get_current_version

        fmcl_ver = get_current_version()

        # 最低版本检查
        if self._dependency.compare_versions(fmcl_ver, manifest.min_fmcl_version) < 0:
            return False, (f"FMCL 版本 {fmcl_ver} 低于插件最低要求 {manifest.min_fmcl_version}")

        # 最高版本检查
        if manifest.max_fmcl_version is not None:
            if self._dependency.compare_versions(fmcl_ver, manifest.max_fmcl_version) > 0:
                return False, (f"FMCL 版本 {fmcl_ver} 超过插件最高支持 {manifest.max_fmcl_version}")

        return True, ""
