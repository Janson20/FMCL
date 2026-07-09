"""CurseForge 整合包安装 Mixin 类

参考 PCL-CE ModModpack.InstallPackCurseForge() 实现:
- 解析 manifest.json (minecraft version + modLoaders + files)
- 通过 CurseForge API 批量获取模组下载地址
- 下载模组文件 + 解压 overrides 到实例目录
- 安装 Minecraft 原版 + Mod Loader
"""

import json
import os
import re
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests as req
from logzero import logger

DEFAULT_UA = "FMCL-CurseForge-Installer/1.0"


class CurseForgePackMixin:
    """CurseForge 整合包安装 Mixin"""

    PARALLEL_DOWNLOADS = 8

    # ─── 读取整合包信息 ──────────────────────────────────────

    def get_cf_pack_info(self, zip_path: str) -> Dict[str, Any]:
        """读取 CurseForge 整合包的元数据信息

        Args:
            zip_path: .zip 文件的绝对路径

        Returns:
            包含 name, mc_version, loader_type, loader_version 等字段的字典
        """
        if not os.path.isfile(zip_path):
            raise ValueError(f"文件不存在: {zip_path}")

        from launcher.modpack_types import ModpackType, detect_modpack_archive

        detection = detect_modpack_archive(zip_path)
        if detection.pack_type != ModpackType.CURSEFORGE:
            raise ValueError("不是有效的 CurseForge 整合包")

        raw = detection.raw_json
        if raw is None:
            raise ValueError("无法解析 manifest.json")

        # 解析 modLoaders
        loader_type = None
        loader_version = None
        for loader_entry in raw.get("minecraft", {}).get("modLoaders", []):
            lid = (loader_entry.get("id", "") or "").lower()
            if lid.startswith("forge-"):
                loader_type = "forge"
                loader_version = lid[len("forge-") :]
            elif lid.startswith("neoforge-"):
                loader_type = "neoforge"
                loader_version = lid[len("neoforge-") :]
            elif lid.startswith("fabric-"):
                loader_type = "fabric"
                loader_version = lid[len("fabric-") :]
            elif lid.startswith("quilt-"):
                loader_type = "quilt"
                loader_version = lid[len("quilt-") :]

        files = raw.get("files", [])
        required_count = sum(1 for f in files if f.get("required", True))
        optional_count = sum(1 for f in files if not f.get("required", True))

        return {
            "name": raw.get("name", ""),
            "mc_version": raw.get("minecraft", {}).get("version", ""),
            "loader_type": loader_type,
            "loader_version": loader_version,
            "format": "curseforge",
            "summary": f"CurseForge 整合包, MC {raw.get('minecraft', {}).get('version', '?')}",
            "total_files": len(files),
            "required_files": required_count,
            "optional_files": optional_count,
            "manifest": raw,
        }

    # ─── 安装 ──────────────────────────────────────────────────

    def install_curseforge_pack(
        self, zip_path: str, optional_file_ids: Optional[List[int]] = None, instance_name: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        安装 CurseForge 整合包

        流程:
        1. 解析 manifest.json
        2. 批量获取 CurseForge 文件信息
        3. 并行下载所有模组文件
        4. 解压 overrides 到实例目录
        5. 安装 Minecraft + Mod Loader

        Args:
            zip_path: 整合包 .zip 文件路径
            optional_file_ids: 要安装的可选文件 fileID 列表
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
            if detection.pack_type != ModpackType.CURSEFORGE:
                return False, f"不是 CurseForge 整合包: {detection.format_name}"

            manifest = detection.raw_json
            if manifest is None:
                return False, "无法解析 manifest.json"

            base = detection.archive_base_folder

            # ── 解析 manifest ──
            mc_version = manifest.get("minecraft", {}).get("version", "")
            pack_name = manifest.get("name", "CF-Modpack")
            if not mc_version:
                return False, "manifest.json 未提供 Minecraft 版本信息"

            if not instance_name:
                instance_name = self._sanitize_name(pack_name)

            # ── 解析 mod loader ──
            loader_type, loader_version = self._parse_cf_loader(manifest)

            # ── 解析文件列表 ──
            selected = optional_file_ids or []
            files_to_download: List[Dict] = []
            for f in manifest.get("files", []):
                fid = f.get("fileID")
                pid = f.get("projectID")
                required = f.get("required", True)
                if pid is None or fid is None:
                    continue
                if required or fid in selected:
                    files_to_download.append({"projectID": pid, "fileID": fid, "required": required})

            version_dir = os.path.join(mc_dir, "versions", instance_name)
            os.makedirs(version_dir, exist_ok=True)

            self._set_status(f"准备安装 {len(files_to_download)} 个 CurseForge 模组...")

            # ── 批量获取下载信息 ──
            file_info_map = self._cf_batch_get_file_info(files_to_download)

            # ── 构建下载列表 ──
            download_tasks: List[Dict] = []
            mods_dir = os.path.join(version_dir, "mods")
            os.makedirs(mods_dir, exist_ok=True)

            for f in files_to_download:
                fid = f["fileID"]
                pid = f["projectID"]
                info = file_info_map.get(fid)
                if info is None:
                    logger.warning(f"无法获取文件信息: projectID={pid}, fileID={fid}")
                    continue

                # 确定目标子目录（默认 mods）
                target_folder = "mods"
                modules = info.get("modules", [])
                if modules:
                    mod_names = [m.get("name", "") for m in modules]
                    if any(n in ("pack.mcmeta",) for n in mod_names):
                        target_folder = "resourcepacks"
                    elif any(n in ("level.dat",) for n in mod_names):
                        target_folder = "saves"

                target_dir = os.path.join(version_dir, target_folder)
                os.makedirs(target_dir, exist_ok=True)

                filename = info.get("fileName", f"{pid}-{fid}")
                save_path = os.path.join(target_dir, filename)

                download_tasks.append(
                    {
                        "projectID": pid,
                        "fileID": fid,
                        "save_path": save_path,
                        "filename": filename,
                        "display_name": info.get("displayName", filename),
                    }
                )

            # ── 并行下载 ──
            total = len(download_tasks)
            self._set_status(f"正在下载 {total} 个模组文件...")
            logger.info(f"并行下载 {total} 个 CurseForge 模组 (线程数: {self.PARALLEL_DOWNLOADS})")

            completed = 0
            failed: List[str] = []
            lock = threading.Lock()

            def _download_one(task: Dict) -> bool:
                nonlocal completed
                try:
                    from curseforge import download_file as cf_download

                    ok, msg = cf_download(
                        task["projectID"], task["fileID"], os.path.dirname(task["save_path"]), filename=task["filename"]
                    )
                    if not ok:
                        logger.warning(f"下载失败: {task['display_name']}: {msg}")
                    with lock:
                        completed += 1
                    return ok
                except Exception as e:
                    logger.warning(f"下载异常: {task['display_name']}: {e}")
                    with lock:
                        completed += 1
                    return False

            with ThreadPoolExecutor(max_workers=self.PARALLEL_DOWNLOADS) as executor:
                futures = {executor.submit(_download_one, t): t for t in download_tasks}
                for future in as_completed(futures):
                    ok = future.result()
                    if not ok:
                        t = futures[future]
                        failed.append(t["display_name"])

            if failed:
                logger.warning(f"{len(failed)}/{total} 个文件下载失败: {failed}")

            # ── 解压 overrides ──
            self._set_status("正在解压整合包配置文件...")
            self._extract_cf_overrides(zip_path, base, manifest, version_dir)

            # ── 安装 Minecraft（复用 mrpack 逻辑） ──
            self._set_status(f"正在安装 Minecraft {mc_version}...")
            try:
                cb = self._get_callback()
                if not self._is_mc_installed(mc_version):
                    self._mcllib.install.install_minecraft_version(mc_version, mc_dir, callback=cb)
                logger.info(f"Minecraft {mc_version} 安装完成")
            except Exception as e:
                logger.error(f"Minecraft 安装失败: {e}")
                self._cleanup(version_dir)
                return False, f"Minecraft {mc_version} 安装失败: {e}"

            # ── 安装 Mod Loader ──
            if loader_type and loader_version:
                self._set_status(f"正在安装 {loader_type} {loader_version}...")
                try:
                    self._install_cf_mod_loader(loader_type, loader_version, mc_version, mc_dir, cb)
                except Exception as e:
                    logger.error(f"Mod Loader 安装失败: {e}")
                    self._cleanup(version_dir)
                    return False, f"{loader_type} 安装失败: {e}"

            # ── 获取启动版本 ID ──
            launch_version = self._get_cf_launch_version(instance_name, mc_version, loader_type, loader_version)

            logger.info(f"CurseForge 整合包安装完成: {instance_name} → {launch_version}")
            return True, launch_version

        except Exception as e:
            logger.error(f"CurseForge 整合包安装失败: {e}")
            try:
                if "version_dir" in locals() and os.path.isdir(version_dir):
                    shutil.rmtree(version_dir)
            except Exception:
                pass
            return False, str(e)

    # ─── 辅助方法 ────────────────────────────────────────────

    def _parse_cf_loader(self, manifest: Dict) -> Tuple[Optional[str], Optional[str]]:
        """从 manifest 解析 mod loader 类型和版本"""
        loader_type = None
        loader_version = None
        for entry in manifest.get("minecraft", {}).get("modLoaders", []):
            lid = (entry.get("id", "") or "").lower()
            if loader_type is None:
                if lid.startswith("forge-"):
                    loader_type, loader_version = "forge", lid[len("forge-") :]
                elif lid.startswith("neoforge-"):
                    loader_type, loader_version = "neoforge", lid[len("neoforge-") :]
                elif lid.startswith("fabric-"):
                    loader_type, loader_version = "fabric", lid[len("fabric-") :]
                elif lid.startswith("quilt-"):
                    loader_type, loader_version = "quilt", lid[len("quilt-") :]
        return loader_type, loader_version

    def _cf_batch_get_file_info(self, files: List[Dict]) -> Dict[int, Dict]:
        """批量获取 CurseForge 文件信息 (POST /v1/mods/files)

        参考 PCL-CE: 批量获取 mod 文件信息以构建下载列表
        """
        file_ids = [f["fileID"] for f in files]
        if not file_ids:
            return {}

        try:
            from curseforge import CURSEFORGE_API_BASE, _get_session

            session = _get_session()
            resp = session.post(f"{CURSEFORGE_API_BASE}/mods/files", json={"fileIds": file_ids}, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return {item.get("id"): item for item in data if isinstance(item, dict)}
        except Exception as e:
            logger.warning(f"批量获取 CurseForge 文件信息失败: {e}")
            return {}

    def _extract_cf_overrides(self, zip_path: str, base: str, manifest: Dict, target_dir: str):
        """解压 CurseForge overrides 到目标目录"""
        override_home = manifest.get("overrides", "overrides")
        if override_home in (".", "./", ""):
            override_home = "overrides"

        override_prefix = (base + override_home).rstrip("/") + "/"

        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if not name.startswith(override_prefix) or name == override_prefix:
                    continue
                info = zf.getinfo(name)
                if info.file_size == 0:
                    continue

                rel_path = name[len(override_prefix) :]
                if not rel_path:
                    continue

                full_path = os.path.join(target_dir, rel_path)
                # 安全检查：确保不会解压到目标目录之外
                if not os.path.abspath(full_path).startswith(os.path.abspath(target_dir)):
                    logger.warning(f"跳过越界文件: {name} → {full_path}")
                    continue

                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                try:
                    with open(full_path, "wb") as f:
                        f.write(zf.read(name))
                except Exception as e:
                    logger.warning(f"解压文件失败 {name}: {e}")

    def _install_cf_mod_loader(
        self, loader_type: str, loader_version: str, mc_version: str, mc_dir: str, callback: Dict
    ):
        """安装 CurseForge 整合包的 Mod Loader"""
        loader_map = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}
        key = loader_map.get(loader_type.lower())
        if not key:
            logger.warning(f"不支持的 mod loader: {loader_type}")
            return

        try:
            loader = self._mcllib.mod_loader.get_mod_loader(key)
            loader.install(mc_version, mc_dir, loader_version=loader_version, callback=callback)
            logger.info(f"{loader_type} {loader_version} 安装完成")
        except Exception as e:
            logger.error(f"{loader_type} 安装失败: {e}")
            raise

    def _is_mc_installed(self, mc_version: str) -> bool:
        """检查 Minecraft 原版是否已安装"""
        version_json = os.path.join(self.minecraft_dir, "versions", mc_version, f"{mc_version}.json")
        return os.path.isfile(version_json)

    def _get_cf_launch_version(
        self, instance_name: str, mc_version: str, loader_type: Optional[str], loader_version: Optional[str]
    ) -> str:
        """生成启动版本 ID"""
        parts = [instance_name]
        if loader_type and loader_version:
            parts.append(f"{loader_type}-{loader_version}")
        return "-".join(parts)

    def _sanitize_name(self, name: str) -> str:
        """清理名称中的非法字符"""
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "unknown-modpack"

    def _cleanup(self, dir_path: str):
        """清理失败安装的目录"""
        try:
            if os.path.isdir(dir_path):
                shutil.rmtree(dir_path)
        except Exception as e:
            logger.warning(f"清理目录失败: {e}")
