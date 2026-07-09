"""插件安装器 - .fmpl 包解压、校验与安装"""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from logzero import logger


class PluginInstaller:
    """插件安装器

    负责:
        - 解压 .fmpl 文件到临时目录
        - 校验 plugin.json 合法性
        - 安装到 installed/ 目录
        - 卸载插件（移动到 disabled/ 或删除）
        - 更新插件（备份旧版本 → 安装新版本 → 失败回滚）
    """

    def __init__(self, installed_dir: Path, disabled_dir: Path, temp_dir: Path):
        """
        Args:
            installed_dir: plugins/installed/ 目录
            disabled_dir: plugins/disabled/ 目录（用于备份/禁用）
            temp_dir: plugins/temp/ 目录（解压和回滚备份）
        """
        self._installed_dir = Path(installed_dir)
        self._disabled_dir = Path(disabled_dir)
        self._temp_dir = Path(temp_dir)
        for d in (self._installed_dir, self._disabled_dir, self._temp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def install_from_fmpl(self, fmpl_path: str, plugin_id: str) -> Tuple[bool, str]:
        """从 .fmpl 文件安装插件

        Args:
            fmpl_path: .fmpl 文件路径
            plugin_id: 插件 ID（从 manifest 读取后传入）

        Returns:
            (成功与否, 错误信息)
        """
        fmpl_file = Path(fmpl_path)
        if not fmpl_file.exists():
            return False, f"文件不存在: {fmpl_path}"

        if not fmpl_file.suffix.lower() == ".fmpl":
            return False, f"文件不是 .fmpl 格式: {fmpl_path}"

        # 安全检查: plugin_id 必须合法
        safe_plugin_id = self._sanitize_id(plugin_id)
        if safe_plugin_id != plugin_id:
            return False, f"插件 ID 包含不安全字符: {plugin_id}"

        # 目标路径
        target_dir = self._installed_dir / safe_plugin_id

        # 1. 如果已存在，先卸载旧版
        if target_dir.exists():
            logger.info(f"覆盖安装插件: {safe_plugin_id}")
            shutil.rmtree(target_dir, ignore_errors=True)

        # 2. 解压到临时目录
        extract_temp = self._temp_dir / f"_extract_{safe_plugin_id}"
        if extract_temp.exists():
            shutil.rmtree(extract_temp, ignore_errors=True)

        try:
            with zipfile.ZipFile(fmpl_path, "r") as zf:
                # 安全检查: Zip Slip 防护
                for member in zf.namelist():
                    resolved = (extract_temp / member).resolve()
                    if not str(resolved).startswith(str(extract_temp.resolve())):
                        return False, f"检测到 Zip Slip 攻击: {member}"
                zf.extractall(extract_temp)

            # 3. 校验 plugin.json 存在
            plugin_json = extract_temp / "plugin.json"
            if not plugin_json.exists():
                return False, "压缩包中缺少 plugin.json"

            # 4. 读取 manifest 并校验 ID 一致
            from plugin_manager.manifest import PluginManifest

            manifest = PluginManifest.from_file(plugin_json)
            if manifest.id != plugin_id:
                return False, f"manifest ID ({manifest.id}) 与预期 ({plugin_id}) 不一致"

            # 5. 校验入口模块存在
            entry_file = extract_temp / f"{manifest.entry}.py"
            if not entry_file.exists():
                return False, f"入口模块 {manifest.entry}.py 不存在"

            # 6. 移动到正式安装目录
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.move(str(extract_temp), str(target_dir))

            logger.info(f"插件安装成功: {plugin_id} v{manifest.version}")
            return True, ""

        except zipfile.BadZipFile:
            return False, "压缩包格式无效"
        except Exception as e:
            logger.error(f"安装插件异常 ({plugin_id}): {e}")
            # 清理临时目录
            if extract_temp.exists():
                shutil.rmtree(extract_temp, ignore_errors=True)
            return False, f"安装失败: {e}"

    def uninstall(self, plugin_id: str) -> Tuple[bool, str]:
        """卸载插件（完全删除 installed/ 中的目录）"""
        target_dir = self._installed_dir / plugin_id
        if not target_dir.exists():
            return False, f"插件目录不存在: {target_dir}"

        try:
            shutil.rmtree(target_dir, ignore_errors=True)
            logger.info(f"插件已卸载: {plugin_id}")
            return True, ""
        except Exception as e:
            logger.error(f"卸载插件异常 ({plugin_id}): {e}")
            return False, f"卸载失败: {e}"

    def disable(self, plugin_id: str) -> Tuple[bool, str]:
        """禁用插件（移动到 disabled/ 目录）"""
        src = self._installed_dir / plugin_id
        dst = self._disabled_dir / plugin_id
        if not src.exists():
            return False, f"插件目录不存在: {src}"

        try:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))
            logger.info(f"插件已禁用: {plugin_id}")
            return True, ""
        except Exception as e:
            logger.error(f"禁用插件异常 ({plugin_id}): {e}")
            return False, f"禁用失败: {e}"

    def enable(self, plugin_id: str) -> Tuple[bool, str]:
        """启用插件（从 disabled/ 移回 installed/）"""
        src = self._disabled_dir / plugin_id
        dst = self._installed_dir / plugin_id
        if not src.exists():
            return False, f"已禁用的插件目录不存在: {src}"

        try:
            if dst.exists():
                return False, f"同名插件已存在: {dst}"
            shutil.move(str(src), str(dst))
            logger.info(f"插件已启用: {plugin_id}")
            return True, ""
        except Exception as e:
            logger.error(f"启用插件异常 ({plugin_id}): {e}")
            return False, f"启用失败: {e}"

    def backup_existing(self, plugin_id: str) -> Tuple[Optional[Path], str]:
        """备份已有插件到 temp 目录（更新前使用）

        Returns:
            (backup_path 或 None, 错误信息)
        """
        src = self._installed_dir / plugin_id
        if not src.exists():
            return None, f"插件不存在: {plugin_id}"

        backup_dir = self._temp_dir / f"_backup_{plugin_id}"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        try:
            shutil.copytree(src, backup_dir)
            logger.debug(f"插件备份成功: {plugin_id} -> {backup_dir}")
            return backup_dir, ""
        except Exception as e:
            logger.error(f"备份插件异常 ({plugin_id}): {e}")
            return None, str(e)

    def rollback(self, plugin_id: str) -> Tuple[bool, str]:
        """回滚插件（从 temp 备份恢复）

        删除当前 installed/ 中的版本，从备份恢复。
        """
        backup_dir = self._temp_dir / f"_backup_{plugin_id}"
        if not backup_dir.exists():
            return False, f"备份不存在: {backup_dir}"

        target_dir = self._installed_dir / plugin_id

        try:
            # 删除当前版本
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            # 从备份恢复
            shutil.move(str(backup_dir), str(target_dir))
            logger.info(f"插件回滚成功: {plugin_id}")
            return True, ""
        except Exception as e:
            logger.error(f"回滚插件异常 ({plugin_id}): {e}")
            return False, f"回滚失败: {e}"

    def cleanup_backup(self, plugin_id: str):
        """清理备份目录（更新成功后调用）"""
        backup_dir = self._temp_dir / f"_backup_{plugin_id}"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

    def _sanitize_id(self, plugin_id: str) -> str:
        """净化插件 ID，移除不安全字符"""
        import re

        # 只允许字母、数字、点、连字符、下划线
        return re.sub(r"[^a-zA-Z0-9._\-]", "_", plugin_id)
