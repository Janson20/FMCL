"""MCBBS 整合包安装 Mixin 类

参考 PCL-CE ModModpack.InstallPackMCBBS() 实现:
- 支持 V1 (manifest.json 含 addons) 和 V2 (mcbbs.packmeta) 格式
- 解析 addons: game, forge, neoforge, fabric, quilt, optifine
- 可选解析 launchInfo 自定义 JVM/游戏参数
- 解压 overrides + 安装 Minecraft + Mod Loader
"""

import json
import os
import re
import shutil
import zipfile
from typing import Dict, List, Optional, Tuple

from logzero import logger


class MCBBSPackMixin:
    """MCBBS 整合包安装 Mixin"""

    # ─── 读取整合包信息 ──────────────────────────────────────

    def get_mcbbs_pack_info(self, zip_path: str) -> Dict:
        """读取 MCBBS 整合包的元数据信息

        Args:
            zip_path: .zip 文件的绝对路径

        Returns:
            包含 name, mc_version, addons 等字段的字典
        """
        if not os.path.isfile(zip_path):
            raise ValueError(f"文件不存在: {zip_path}")

        from launcher.modpack_types import detect_modpack_archive, ModpackType

        detection = detect_modpack_archive(zip_path)
        if detection.pack_type != ModpackType.MCBBS:
            raise ValueError("不是有效的 MCBBS 整合包")

        raw = detection.raw_json
        if raw is None:
            raise ValueError("无法解析 MCBBS 整合包清单")

        addons = self._parse_mcbbs_addons(raw)
        loader_info = self._parse_mcbbs_loader(addons)
        components = []

        for addon_id, addon_ver in sorted(addons.items()):
            label = {"game": "Minecraft", "forge": "Forge", "neoforge": "NeoForge",
                     "fabric": "Fabric", "quilt": "Quilt", "optifine": "OptiFine"}.get(addon_id, addon_id)
            components.append({
                "uid": addon_id,
                "version": addon_ver,
                "name": f"{label} {addon_ver}",
            })

        return {
            "name": raw.get("name", ""),
            "mc_version": addons.get("game", ""),
            "format": "mcbbs",
            "loader_type": loader_info[0] if loader_info else None,
            "loader_version": loader_info[1] if loader_info else None,
            "summary": f"MCBBS 整合包{' (V2)' if detection.archive_base_folder == '' and 'mcbbs.packmeta' in str(detection.raw_json) else ''}",
            "components": components,
            "addons": addons,
            "manifest": raw,
        }

    # ─── 安装 ──────────────────────────────────────────────────

    def install_mcbbs_pack(
        self,
        zip_path: str,
        instance_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        安装 MCBBS 整合包

        流程:
        1. 解析 mcbbs.packmeta / manifest.json
        2. 解压 overrides 到实例目录
        3. 安装 Minecraft + Mod Loader

        Args:
            zip_path: 整合包 .zip 文件路径
            instance_name: 实例名称（默认使用整合包名）

        Returns:
            (是否成功, 版本ID 或 错误信息)
        """
        from launcher.modpack_types import detect_modpack_archive, ModpackType

        if not os.path.isfile(zip_path):
            return False, f"文件不存在: {zip_path}"

        mc_dir = self.minecraft_dir

        try:
            detection = detect_modpack_archive(zip_path)
            if detection.pack_type != ModpackType.MCBBS:
                return False, f"不是 MCBBS 整合包: {detection.format_name}"

            raw = detection.raw_json
            if raw is None:
                return False, "无法解析 MCBBS 整合包清单"

            base = detection.archive_base_folder

            # ── 解析 addons ──
            addons = self._parse_mcbbs_addons(raw)
            if "game" not in addons:
                return False, "MCBBS 整合包未指定 Minecraft 版本 (缺少 game addon)"

            mc_version = addons["game"]
            pack_name = raw.get("name", "MCBBS-Modpack")

            if not instance_name:
                instance_name = self._sanitize_name(pack_name)

            # ── 解析 JVM 参数 ──
            jvm_args = None
            game_args = None
            launch_info = raw.get("launchInfo")
            if launch_info and isinstance(launch_info, dict):
                ja = launch_info.get("javaArgument")
                if isinstance(ja, list):
                    jvm_args = " ".join(str(x) for x in ja)
                elif isinstance(ja, str):
                    jvm_args = ja
                la = launch_info.get("launchArgument")
                if isinstance(la, list):
                    game_args = " ".join(str(x) for x in la)
                elif isinstance(la, str):
                    game_args = la

            version_dir = os.path.join(mc_dir, "versions", instance_name)
            os.makedirs(version_dir, exist_ok=True)

            # ── 解压 overrides ──
            self._set_status("正在解压整合包文件...")
            override_prefix = base + "overrides/"

            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    normalized = name.replace("\\", "/")
                    if not normalized.startswith(override_prefix) or normalized == override_prefix:
                        continue

                    info = zf.getinfo(name)
                    if info.file_size == 0 or info.is_dir():
                        continue

                    rel_path = normalized[len(override_prefix):]
                    if not rel_path:
                        continue

                    full_path = os.path.join(version_dir, rel_path)
                    if not os.path.abspath(full_path).startswith(os.path.abspath(version_dir)):
                        logger.warning(f"跳过越界文件: {name} → {full_path}")
                        continue

                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    try:
                        with open(full_path, "wb") as f:
                            f.write(zf.read(name))
                    except Exception as e:
                        logger.warning(f"解压文件失败 {name}: {e}")

            # ── 安装 Minecraft 原版 ──
            self._set_status(f"正在安装 Minecraft {mc_version}...")
            cb = self._get_callback()

            if not self._is_mc_installed(mc_version):
                self._mcllib.install.install_minecraft_version(
                    mc_version, mc_dir, callback=cb
                )
            logger.info(f"Minecraft {mc_version} 安装完成")

            # ── 安装 Mod Loader ──
            loader_type = None
            loader_version = None
            for lid in ("forge", "neoforge", "fabric", "quilt"):
                if lid in addons:
                    loader_type = lid
                    loader_version = addons[lid]
                    break

            if loader_type and loader_version:
                self._set_status(f"正在安装 {loader_type} {loader_version}...")
                try:
                    self._install_mcbbs_loader(loader_type, loader_version, mc_version, mc_dir, cb)
                except Exception as e:
                    logger.error(f"Mod Loader 安装失败: {e}")
                    shutil.rmtree(version_dir, ignore_errors=True)
                    return False, f"{loader_type} 安装失败: {e}"

            # ── OptiFine（如果有） ──
            if "optifine" in addons:
                logger.info(f"MCBBS 整合包含 OptiFine {addons['optifine']}，需手动安装")

            # ── 保存自定义参数 ──
            if jvm_args:
                self._save_mcbbs_jvm_args(version_dir, jvm_args, game_args)

            launch_version = instance_name
            logger.info(f"MCBBS 整合包安装完成: {instance_name}")
            return True, launch_version

        except Exception as e:
            logger.error(f"MCBBS 整合包安装失败: {e}")
            try:
                if 'version_dir' in locals() and os.path.isdir(version_dir):
                    shutil.rmtree(version_dir)
            except Exception:
                pass
            return False, str(e)

    # ─── 辅助方法 ────────────────────────────────────────────

    def _parse_mcbbs_addons(self, raw: Dict) -> Dict[str, str]:
        """从 MCBBS 清单解析 addons"""
        addons = {}
        addons_list = raw.get("addons")
        if addons_list is None:
            return addons
        if isinstance(addons_list, list):
            for entry in addons_list:
                if isinstance(entry, dict):
                    aid = entry.get("id", "")
                    aver = entry.get("version", "")
                    if aid and aver:
                        addons[str(aid).lower()] = str(aver)
        elif isinstance(addons_list, dict):
            for k, v in addons_list.items():
                addons[str(k).lower()] = str(v)
        return addons

    def _parse_mcbbs_loader(self, addons: Dict[str, str]) -> Optional[Tuple[str, str]]:
        """从 addons 中解析主 mod loader"""
        for lid in ("forge", "neoforge", "fabric", "quilt"):
            if lid in addons:
                return (lid, addons[lid])
        return None

    def _install_mcbbs_loader(
        self, loader_type: str, loader_version: str,
        mc_version: str, mc_dir: str, callback: Dict,
    ):
        """安装 MCBBS 整合包的 Mod Loader"""
        loader_map = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}
        key = loader_map.get(loader_type.lower())
        if not key:
            raise ValueError(f"不支持的 mod loader: {loader_type}")

        loader = self._mcllib.mod_loader.get_mod_loader(key)
        loader.install(mc_version, mc_dir, loader_version=loader_version, callback=callback)
        logger.info(f"{loader_type} {loader_version} 安装完成")

    def _save_mcbbs_jvm_args(self, version_dir: str, jvm_args: str, game_args: Optional[str]):
        """保存 MCBBS 自定义 JVM/游戏参数"""
        try:
            pcl_dir = os.path.join(version_dir, "PCL")
            os.makedirs(pcl_dir, exist_ok=True)
            cfg = {}
            if jvm_args:
                cfg["jvmArgs"] = jvm_args
            if game_args:
                cfg["gameArgs"] = game_args
            with open(os.path.join(pcl_dir, "mcbbs_launch.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 MCBBS 启动参数失败: {e}")

    def _is_mc_installed(self, mc_version: str) -> bool:
        version_json = os.path.join(
            self.minecraft_dir, "versions", mc_version, f"{mc_version}.json"
        )
        return os.path.isfile(version_json)

    def _sanitize_name(self, name: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "unknown-modpack"
