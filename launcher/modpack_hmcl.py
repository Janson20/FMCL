"""HMCL 整合包安装 Mixin 类

参考 PCL-CE ModModpack.InstallPackHMCL() 实现:
- 解析 modpack.json (name, gameVersion, description)
- 解压 minecraft/ 目录到实例目录作为 overrides
- 安装 Minecraft 原版（HMCL 格式不指定 mod loader）
"""

import json
import os
import re
import shutil
import zipfile
from typing import Dict, Optional, Tuple

from logzero import logger


class HMCLPackMixin:
    """HMCL 整合包安装 Mixin"""

    # ─── 读取整合包信息 ──────────────────────────────────────

    def get_hmcl_pack_info(self, zip_path: str) -> Dict:
        """读取 HMCL 整合包的元数据信息

        Args:
            zip_path: .zip 文件的绝对路径

        Returns:
            包含 name, mc_version 等字段的字典
        """
        if not os.path.isfile(zip_path):
            raise ValueError(f"文件不存在: {zip_path}")

        from launcher.modpack_types import ModpackType, detect_modpack_archive

        detection = detect_modpack_archive(zip_path)
        if detection.pack_type != ModpackType.HMCL:
            raise ValueError("不是有效的 HMCL 整合包")

        raw = detection.raw_json
        if raw is None:
            raise ValueError("无法解析 modpack.json")

        return {
            "name": raw.get("name", ""),
            "mc_version": raw.get("gameVersion", ""),
            "format": "hmcl",
            "summary": f"HMCL 整合包, MC {raw.get('gameVersion', '?')}",
            "manifest": raw,
            "description": raw.get("description", ""),
        }

    # ─── 安装 ──────────────────────────────────────────────────

    def install_hmcl_pack(self, zip_path: str, instance_name: Optional[str] = None) -> Tuple[bool, str]:
        """
        安装 HMCL 整合包

        流程:
        1. 解析 modpack.json
        2. 解压 minecraft/ 到实例目录
        3. 安装 Minecraft 原版

        Args:
            zip_path: 整合包 .zip 文件路径
            instance_name: 实例名称（默认使用整合包名）

        Returns:
            (是否成功, 版本ID 或 错误信息)
        """
        from launcher.modpack_types import ModpackType, detect_modpack_archive

        if not os.path.isfile(zip_path):
            return False, f"文件不存在: {zip_path}"

        mc_dir = self.minecraft_dir

        try:
            detection = detect_modpack_archive(zip_path)
            if detection.pack_type != ModpackType.HMCL:
                return False, f"不是 HMCL 整合包: {detection.format_name}"

            raw = detection.raw_json
            if raw is None:
                return False, "无法解析 modpack.json"

            base = detection.archive_base_folder
            mc_version = raw.get("gameVersion", "")
            pack_name = raw.get("name", "HMCL-Modpack")

            if not mc_version:
                return False, "modpack.json 未提供 gameVersion"

            if not instance_name:
                instance_name = self._sanitize_name(pack_name)

            version_dir = os.path.join(mc_dir, "versions", instance_name)
            os.makedirs(version_dir, exist_ok=True)

            # ── 解压 minecraft/ 目录 (HMCL 的 overrides) ──
            self._set_status("正在解压整合包文件...")
            self._extract_hmcl_minecraft(zip_path, base, version_dir)

            # ── 安装 Minecraft 原版 ──
            self._set_status(f"正在安装 Minecraft {mc_version}...")
            cb = self._get_callback()

            if not self._is_mc_installed(mc_version):
                self._mcllib.install.install_minecraft_version(mc_version, mc_dir, callback=cb)
            logger.info(f"Minecraft {mc_version} 安装完成")

            # HMCL 格式通常不指定 mod loader，版本 ID 就是实例名
            logger.info(f"HMCL 整合包安装完成: {instance_name}")
            return True, instance_name

        except Exception as e:
            logger.error(f"HMCL 整合包安装失败: {e}")
            try:
                if "version_dir" in locals() and os.path.isdir(version_dir):
                    shutil.rmtree(version_dir)
            except Exception:
                pass
            return False, str(e)

    # ─── 辅助方法 ────────────────────────────────────────────

    def _extract_hmcl_minecraft(self, zip_path: str, base: str, target_dir: str):
        """解压 HMCL 的 minecraft/ 覆盖目录"""
        override_prefix = (base + "minecraft/").replace("\\", "/")

        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                normalized = name.replace("\\", "/")
                if not normalized.startswith(override_prefix) or normalized == override_prefix:
                    continue

                info = zf.getinfo(name)
                if info.file_size == 0 or info.is_dir():
                    continue

                rel_path = normalized[len(override_prefix) :]
                if not rel_path:
                    continue

                full_path = os.path.join(target_dir, rel_path)
                if not os.path.abspath(full_path).startswith(os.path.abspath(target_dir)):
                    logger.warning(f"跳过越界文件: {name} → {full_path}")
                    continue

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                try:
                    with open(full_path, "wb") as f:
                        f.write(zf.read(name))
                except Exception as e:
                    logger.warning(f"解压文件失败 {name}: {e}")

    def _is_mc_installed(self, mc_version: str) -> bool:
        """检查 Minecraft 原版是否已安装"""
        version_json = os.path.join(self.minecraft_dir, "versions", mc_version, f"{mc_version}.json")
        return os.path.isfile(version_json)

    def _sanitize_name(self, name: str) -> str:
        """清理名称中的非法字符"""
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "unknown-modpack"
