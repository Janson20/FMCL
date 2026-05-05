"""Minecraft启动器 - 整合包安装模块"""
import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable, Any

from logzero import logger


class MrpackMixin:
    """整合包（mrpack）安装 Mixin 类"""

    _java_scan_cache: Optional[List] = None
    _java_scan_cache_time: float = 0.0

    # 并行下载模组时的线程数
    PARALLEL_DOWNLOADS = 10

    # ─── 整合包（mrpack）安装 ───────────────────────────────────

    def _get_cached_java_runtimes_mrpack(self) -> List:
        import time
        now = time.time()
        if self._java_scan_cache is not None and (now - self._java_scan_cache_time) < 30:
            return self._java_scan_cache
        from launcher.java_scanner import scan_all
        self._java_scan_cache = scan_all(getattr(self, "minecraft_dir", ""))
        self._java_scan_cache_time = now
        return self._java_scan_cache

    def _resolve_java_for_version(self, version_id: str) -> str:
        java_mode = getattr(self.config, 'java_mode', 'auto')
        custom_path = getattr(self.config, 'java_custom_path', None)

        if java_mode == "custom" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用自定义 Java 路径: {custom_path}")
            return custom_path

        if java_mode == "scan" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用扫描选择的 Java 路径: {custom_path}")
            return custom_path

        java_path = self._find_runtime_java(version_id)
        if java_path == "java" or not os.path.isfile(java_path):
            try:
                from launcher.java_scanner import recommend_for_mc
                javas = self._get_cached_java_runtimes_mrpack()
                if javas:
                    best = recommend_for_mc(javas, version_id)
                    if best:
                        logger.info(f"从系统扫描选择 Java: {best.display_name}")
                        return best.path
            except Exception as e:
                logger.debug(f"扫描器 Java 推荐失败: {e}")
        return java_path

    def _check_and_warn_missing_java(self, version_id: str, java_path: str) -> None:
        if java_path == "java" or not os.path.isfile(java_path):
            try:
                from launcher.java_scanner import _min_java_for_mc
                from launcher.java_install import get_java_install_guidance
                min_java = _min_java_for_mc(version_id)
                guidance = get_java_install_guidance(min_java)
                dl_url = guidance.get("download_url") or ""
                install_cmd = guidance.get("install_command") or ""
                logger.warning(
                    f"未找到合适的 Java {min_java}+ 用于版本 {version_id}。"
                    f"请安装 JDK {min_java}。"
                    f"下载: {dl_url}  安装命令: {install_cmd}"
                )
                self._set_status(
                    f"⚠ 未找到 Java {min_java}+，游戏可能无法启动。"
                    f"请安装 JDK {min_java}"
                )
            except Exception:
                pass

    def get_java_mode(self) -> str:
        return getattr(self.config, 'java_mode', 'auto')

    def set_java_mode(self, mode: str) -> None:
        self.config.java_mode = mode
        self.config.save_config()

    def get_java_custom_path(self) -> Optional[str]:
        return getattr(self.config, 'java_custom_path', None)

    def set_java_custom_path(self, path: Optional[str]) -> None:
        self.config.java_custom_path = path
        self.config.save_config()

    def get_mrpack_information(self, mrpack_path: str) -> Dict[str, Any]:
        """
        读取 .mrpack 整合包的元数据信息

        Args:
            mrpack_path: .mrpack 文件的绝对路径

        Returns:
            包含 name, summary, minecraftVersion, optionalFiles 等字段的字典

        Raises:
            ValueError: 文件无效或解析失败
        """
        import minecraft_launcher_lib
        if not os.path.isfile(mrpack_path):
            raise ValueError(f"文件不存在: {mrpack_path}")
        try:
            info = minecraft_launcher_lib.mrpack.get_mrpack_information(mrpack_path)
            logger.info(f"已读取整合包信息: {info.get('name', '未知')}")
            return info
        except Exception as e:
            logger.error(f"读取整合包信息失败: {e}")
            raise ValueError(f"无法解析 .mrpack 文件: {e}") from e

    def _read_mrpack_index(self, mrpack_path: str) -> Dict[str, Any]:
        import minecraft_launcher_lib as _mcllib
        with zipfile.ZipFile(mrpack_path, "r") as zf:
            with zf.open("modrinth.index.json", "r") as f:
                return json.load(f)

    def _download_mrpack_files_parallel(
        self,
        mrpack_path: str,
        minecraft_directory: str,
        modpack_directory: str,
        optional_files: List[str],
        callback: Dict,
    ):
        from minecraft_launcher_lib._helper import download_file, check_path_inside_minecraft_directory

        mp_dir_abs = os.path.abspath(modpack_directory)

        index = self._read_mrpack_index(mrpack_path)
        files_data = index.get("files", [])

        # ── 1. 过滤要安装的文件 ──
        file_list: List[Dict] = []
        for f in files_data:
            env = f.get("env")
            if env is None:
                file_list.append(f)
            elif env.get("client") == "required":
                file_list.append(f)
            elif env.get("client") == "optional" and f["path"] in optional_files:
                file_list.append(f)

        if not file_list:
            logger.info("没有需要下载的整合包文件")
            set_max = callback.get("setMax", lambda x: None)
            set_max(0)

        total = len(file_list)
        set_max = callback.get("setMax", lambda x: None)
        set_status = callback.get("setStatus", lambda x: None)
        set_progress = callback.get("setProgress", lambda x: None)
        set_max(total)

        completed = 0
        lock = threading.Lock()

        def _download_one(file_info: Dict) -> tuple:
            nonlocal completed
            file_path = file_info["path"]
            full_path = os.path.abspath(os.path.join(mp_dir_abs, file_path))

            check_path_inside_minecraft_directory(mp_dir_abs, full_path)

            url = file_info["downloads"][0]
            sha1 = file_info["hashes"].get("sha1")

            result = download_file(url, full_path, sha1=sha1)

            with lock:
                completed += 1
                set_progress(completed)

            return file_path, result

        set_status("并行下载整合包文件")
        logger.info(f"并行下载 {total} 个整合包文件 (线程数: {self.PARALLEL_DOWNLOADS})")

        failed_files: List[str] = []

        with ThreadPoolExecutor(max_workers=self.PARALLEL_DOWNLOADS) as executor:
            futures = {executor.submit(_download_one, f): f for f in file_list}

            for future in as_completed(futures):
                try:
                    file_path, ok = future.result()
                    if not ok:
                        failed_files.append(file_path)
                except Exception as e:
                    file_info = futures[future]
                    failed_files.append(file_info["path"])
                    logger.warning(f"下载文件失败 {file_info['path']}: {e}")

        if failed_files:
            logger.warning(f"{len(failed_files)}/{total} 个文件下载失败: {failed_files}")

        # ── 2. 解压 overrides ──
        set_status("解压整合包配置")
        logger.info("解压 overrides...")
        with zipfile.ZipFile(mrpack_path, "r") as zf:
            for zip_name in zf.namelist():
                if (not zip_name.startswith("overrides/") and not zip_name.startswith("client-overrides/")) \
                        or zf.getinfo(zip_name).file_size == 0:
                    continue

                if zip_name.startswith("client-overrides/"):
                    file_name = zip_name[len("client-overrides/"):]
                else:
                    file_name = zip_name[len("overrides/"):]

                full_path = os.path.abspath(os.path.join(mp_dir_abs, file_name))
                check_path_inside_minecraft_directory(mp_dir_abs, full_path)

                try:
                    os.makedirs(os.path.dirname(full_path))
                except FileExistsError:
                    pass

                with open(full_path, "wb") as f:
                    f.write(zf.read(zip_name))

        logger.info("整合包文件并行下载 + overrides 解压完成")
        return failed_files

    def install_mrpack(
        self,
        mrpack_path: str,
        optional_files: Optional[List[str]] = None,
        modpack_directory: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        安装 .mrpack 整合包（默认启用版本隔离，并行下载优化）

        优化：mrpack 文件下载与 vanilla Minecraft 安装并行执行，
        当两者都完成后安装 mod loader，显著提升安装速度。

        Args:
            mrpack_path: .mrpack 文件的绝对路径
            optional_files: 要安装的可选文件列表（默认全部不安装）
            modpack_directory: 整合包安装目录（默认为版本隔离目录，即 versions/<launch_version>/）

        Returns:
            (是否成功, 启动版本ID 或 错误信息) 元组
        """
        import minecraft_launcher_lib as _mcllib

        if not os.path.isfile(mrpack_path):
            msg = f"文件不存在: {mrpack_path}"
            logger.error(msg)
            return False, msg

        mc_dir = self.minecraft_dir

        try:
            launch_version = self._mcllib.mrpack.get_mrpack_launch_version(mrpack_path)
        except Exception as e:
            msg = f"无法获取整合包启动版本: {e}"
            logger.error(msg)
            return False, msg

        if modpack_directory:
            mp_dir = modpack_directory
        else:
            mp_dir = os.path.join(mc_dir, "versions", launch_version)
            os.makedirs(mp_dir, exist_ok=True)
            logger.info(f"版本隔离模式: 整合包资源将安装到 {mp_dir}")

        optional = optional_files or []

        try:
            callback = self._get_callback()
            mrpack_error: Optional[Exception] = None
            vanilla_error: Optional[Exception] = None

            progress_lock = threading.Lock()

            mp_state = {"current": 0, "max": 1, "label": "等待中"}
            mc_state = {"current": 0, "max": 1, "label": "等待中"}

            self._mp_progress = {
                "mrpack": mp_state,
                "vanilla": mc_state,
                "overall": 0,
                "phase": "parallel",
            }

            def _update_overall():
                mp_pct = (mp_state["current"] / mp_state["max"]) if mp_state["max"] > 0 else 0
                mc_pct = (mc_state["current"] / mc_state["max"]) if mc_state["max"] > 0 else 0
                overall = int((mp_pct + mc_pct) / 2 * 100)
                self._mp_progress["overall"] = overall / 100.0
                status_text = f"整合包: {mp_state['label']} | 原版: {mc_state['label']}"
                self._mp_progress["status_text"] = status_text

            def _make_cb(state: dict, label_prefix: str):
                def _set_status(msg: str):
                    with progress_lock:
                        state["label"] = f"{label_prefix}{msg}"
                        _update_overall()

                def _set_max(n: int):
                    with progress_lock:
                        state["max"] = max(n, 1)
                        _update_overall()

                def _set_progress(n: int):
                    with progress_lock:
                        state["current"] = n
                        _update_overall()

                return {"setStatus": _set_status, "setMax": _set_max, "setProgress": _set_progress}

            mrpack_cb = _make_cb(mp_state, "")
            vanilla_cb = _make_cb(mc_state, "")

            def _install_mrpack_files():
                nonlocal mrpack_error
                try:
                    logger.info("并行任务 A: 并行下载整合包文件...")
                    self._download_mrpack_files_parallel(
                        mrpack_path,
                        mc_dir,
                        mp_dir,
                        optional,
                        mrpack_cb,
                    )
                    with progress_lock:
                        mp_state["current"] = mp_state["max"]
                        mp_state["label"] = "完成"
                    _update_overall()
                    logger.info("并行任务 A: 整合包文件下载完成")
                except Exception as e:
                    mrpack_error = e
                    logger.error(f"并行任务 A 失败: {e}")

            def _install_vanilla():
                nonlocal vanilla_error
                try:
                    index = self._read_mrpack_index(mrpack_path)
                    mc_version = index["dependencies"]["minecraft"]
                    logger.info(f"并行任务 B: 安装 Minecraft {mc_version}...")
                    with progress_lock:
                        mc_state["label"] = f"安装 Minecraft {mc_version}"
                    _update_overall()
                    _mcllib.install.install_minecraft_version(
                        mc_version,
                        mc_dir,
                        callback=vanilla_cb,
                    )
                    with progress_lock:
                        mc_state["current"] = mc_state["max"]
                        mc_state["label"] = "完成"
                    _update_overall()
                    logger.info(f"并行任务 B: Minecraft {mc_version} 安装完成")
                except Exception as e:
                    vanilla_error = e
                    logger.error(f"并行任务 B 失败: {e}")

            logger.info("启动并行安装: 整合包文件下载 + Minecraft 原版安装")
            t_a = threading.Thread(target=_install_mrpack_files, daemon=True)
            t_b = threading.Thread(target=_install_vanilla, daemon=True)
            t_a.start()
            t_b.start()
            t_a.join()
            t_b.join()

            if mrpack_error or vanilla_error:
                err_msg = []
                if mrpack_error:
                    err_msg.append(f"整合包文件: {mrpack_error}")
                if vanilla_error:
                    err_msg.append(f"原版安装: {vanilla_error}")
                combined = "; ".join(err_msg)
                logger.error(f"并行安装失败: {combined}")
                # 清理部分下载的文件
                try:
                    if os.path.isdir(mp_dir):
                        shutil.rmtree(mp_dir)
                        logger.info(f"已清理部分安装目录: {mp_dir}")
                except Exception as cleanup_err:
                    logger.warning(f"清理部分安装目录失败: {cleanup_err}")
                return False, combined

            logger.info("并行阶段完成，开始安装模组加载器...")

            self._mp_progress["phase"] = "loader"
            self._mp_progress["mrpack"] = {"current": 1, "max": 1, "label": "完成"}
            self._mp_progress["vanilla"] = {"current": 1, "max": 1, "label": "完成"}

            index = self._read_mrpack_index(mrpack_path)
            deps = index["dependencies"]
            mc_version = deps.get("minecraft", "")

            if mc_version:
                java_path = self._resolve_java_for_version(mc_version)
                self._check_and_warn_missing_java(mc_version, java_path)

            if "forge" in deps:
                self._mp_progress["loader_label"] = f"安装 Forge {deps['forge']}"
                forge = self._mcllib.mod_loader.get_mod_loader("forge")
                forge.install(deps["minecraft"], mc_dir, loader_version=deps["forge"], callback=callback)

            if "neoforge" in deps:
                self._mp_progress["loader_label"] = f"安装 NeoForge {deps['neoforge']}"
                neoforge = self._mcllib.mod_loader.get_mod_loader("neoforge")
                neoforge.install(deps["minecraft"], mc_dir, loader_version=deps["neoforge"], callback=callback)

            if "fabric-loader" in deps:
                self._mp_progress["loader_label"] = f"安装 Fabric {deps['fabric-loader']}"
                fabric = self._mcllib.mod_loader.get_mod_loader("fabric")
                fabric.install(deps["minecraft"], mc_dir, loader_version=deps["fabric-loader"], callback=callback)

            if "quilt-loader" in deps:
                self._mp_progress["loader_label"] = f"安装 Quilt {deps['quilt-loader']}"
                quilt = self._mcllib.mod_loader.get_mod_loader("quilt")
                quilt.install(deps["minecraft"], mc_dir, loader_version=deps["quilt-loader"], callback=callback)

            self._mp_progress["phase"] = "done"

            logger.info(f"整合包安装完成，启动版本: {launch_version}")
            return True, launch_version

        except Exception as e:
            logger.error(f"整合包安装失败: {e}")
            # 清理部分下载的文件
            try:
                if 'mp_dir' in locals() and os.path.isdir(mp_dir):
                    shutil.rmtree(mp_dir)
                    logger.info(f"已清理部分安装目录: {mp_dir}")
            except Exception as cleanup_err:
                logger.warning(f"清理部分安装目录失败: {cleanup_err}")
            return False, str(e)

    def get_mrpack_launch_version(self, mrpack_path: str) -> str:
        """
        获取整合包的启动版本ID

        Args:
            mrpack_path: .mrpack 文件的绝对路径

        Returns:
            启动版本ID字符串
        """
        return self._mcllib.mrpack.get_mrpack_launch_version(mrpack_path)

    def install_mrpack_server(
        self,
        mrpack_path: str,
        optional_files: Optional[List[str]] = None,
        server_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        安装整合包作为服务器（并行下载优化）

        流程：
        1. 并行：整合包文件下载 + 原版客户端安装
        2. 安装服务端 mod loader 或 vanilla server jar
        3. 自动同意 EULA + 创建 server.properties

        Args:
            mrpack_path: .mrpack 文件的绝对路径
            optional_files: 要安装的可选文件列表
            server_name: 服务器目录名（默认使用整合包名称）

        Returns:
            (是否成功, 服务器名称 或 错误信息) 元组
        """
        import minecraft_launcher_lib as _mcllib

        if not os.path.isfile(mrpack_path):
            msg = f"文件不存在: {mrpack_path}"
            logger.error(msg)
            return False, msg

        optional = optional_files or []

        try:
            mrpack_info = self._mcllib.mrpack.get_mrpack_information(mrpack_path)
            mc_version = mrpack_info.get("minecraftVersion", "")
            pack_name = mrpack_info.get("name", "modpack")

            if not mc_version:
                msg = "无法获取整合包的 Minecraft 版本"
                logger.error(msg)
                return False, msg

            index = self._read_mrpack_index(mrpack_path)
            deps = index.get("dependencies", {})
            mod_loader_info = {}
            if "forge" in deps:
                mod_loader_info = {"type": "forge", "version": deps["forge"]}
            elif "neoforge" in deps:
                mod_loader_info = {"type": "neoforge", "version": deps["neoforge"]}
            elif "fabric-loader" in deps:
                mod_loader_info = {"type": "fabric", "version": deps["fabric-loader"]}
            elif "quilt-loader" in deps:
                mod_loader_info = {"type": "quilt", "version": deps["quilt-loader"]}

            loader_type = mod_loader_info.get("type", "")
            loader_version = mod_loader_info.get("version", "")
            if loader_type:
                logger.info(f"检测到 mod loader: {loader_type} {loader_version}")

            if not server_name:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', pack_name)
                server_name = f"{safe_name}-{mc_version}"

            mc_dir = self.minecraft_dir
            server_dir = self._get_server_versions_dir() / server_name
            server_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"整合包服务器目录: {server_dir}")

            callback = self._get_callback()
            mrpack_error: Optional[Exception] = None
            vanilla_error: Optional[Exception] = None

            progress_lock = threading.Lock()
            mp_state = {"current": 0, "max": 1, "label": "等待中"}
            mc_state = {"current": 0, "max": 1, "label": "等待中"}

            self._mp_progress = {
                "mrpack": mp_state,
                "vanilla": mc_state,
                "overall": 0,
                "phase": "parallel",
            }

            def _update_overall():
                mp_pct = (mp_state["current"] / mp_state["max"]) if mp_state["max"] > 0 else 0
                mc_pct = (mc_state["current"] / mc_state["max"]) if mc_state["max"] > 0 else 0
                overall = int((mp_pct + mc_pct) / 2 * 100)
                self._mp_progress["overall"] = overall / 100.0
                status_text = f"整合包: {mp_state['label']} | 原版: {mc_state['label']}"
                self._mp_progress["status_text"] = status_text

            def _make_cb(state: dict, label_prefix: str):
                def _set_status(msg: str):
                    with progress_lock:
                        state["label"] = f"{label_prefix}{msg}"
                        _update_overall()

                def _set_max(n: int):
                    with progress_lock:
                        state["max"] = max(n, 1)
                        _update_overall()

                def _set_progress(n: int):
                    with progress_lock:
                        state["current"] = n
                        _update_overall()

                return {"setStatus": _set_status, "setMax": _set_max, "setProgress": _set_progress}

            mrpack_cb = _make_cb(mp_state, "")
            vanilla_cb = _make_cb(mc_state, "")

            def _install_mrpack_files():
                nonlocal mrpack_error
                try:
                    logger.info("并行任务 A: 并行下载整合包服务器文件...")
                    self._download_mrpack_files_parallel(
                        mrpack_path,
                        mc_dir,
                        str(server_dir),
                        optional,
                        mrpack_cb,
                    )
                    with progress_lock:
                        mp_state["current"] = mp_state["max"]
                        mp_state["label"] = "完成"
                    _update_overall()
                    logger.info("并行任务 A: 整合包服务器文件下载完成")
                except Exception as e:
                    mrpack_error = e
                    logger.error(f"并行任务 A 失败: {e}")

            def _install_vanilla():
                nonlocal vanilla_error
                try:
                    logger.info(f"并行任务 B: 安装 Minecraft {mc_version}...")
                    with progress_lock:
                        mc_state["label"] = f"安装 Minecraft {mc_version}"
                    _update_overall()
                    _mcllib.install.install_minecraft_version(
                        mc_version,
                        mc_dir,
                        callback=vanilla_cb,
                    )
                    with progress_lock:
                        mc_state["current"] = mc_state["max"]
                        mc_state["label"] = "完成"
                    _update_overall()
                    logger.info(f"并行任务 B: Minecraft {mc_version} 安装完成")
                except Exception as e:
                    vanilla_error = e
                    logger.error(f"并行任务 B 失败: {e}")

            logger.info("启动并行安装: 整合包文件下载 + Minecraft 原版安装")
            t_a = threading.Thread(target=_install_mrpack_files, daemon=True)
            t_b = threading.Thread(target=_install_vanilla, daemon=True)
            t_a.start()
            t_b.start()
            t_a.join()
            t_b.join()

            if mrpack_error or vanilla_error:
                err_msg = []
                if mrpack_error:
                    err_msg.append(f"整合包文件: {mrpack_error}")
                if vanilla_error:
                    err_msg.append(f"原版安装: {vanilla_error}")
                combined = "; ".join(err_msg)
                logger.error(f"并行安装失败: {combined}")
                # 清理部分下载的文件
                try:
                    if 'server_dir' in locals() and server_dir.exists():
                        shutil.rmtree(str(server_dir))
                        logger.info(f"已清理部分安装目录: {server_dir}")
                except Exception as cleanup_err:
                    logger.warning(f"清理部分安装目录失败: {cleanup_err}")
                return False, combined

            self._mp_progress["phase"] = "loader"
            self._mp_progress["mrpack"] = {"current": 1, "max": 1, "label": "完成"}
            self._mp_progress["vanilla"] = {"current": 1, "max": 1, "label": "完成"}

            java_path = self._resolve_java_for_version(mc_version)
            self._check_and_warn_missing_java(mc_version, java_path)
            logger.info(f"使用 Java: {java_path}")

            if loader_type:
                self._mp_progress["loader_label"] = f"安装 {loader_type} 服务端"
                success, err_msg = self._install_server_mod_loader(
                    loader_type, loader_version, mc_version, server_dir, java_path
                )
                if not success:
                    return False, f"服务端 {loader_type} 安装失败: {err_msg}"
            else:
                self._mp_progress["loader_label"] = "下载原版服务器"
                success, err_msg = self._download_vanilla_server_jar(mc_version, server_dir)
                if not success:
                    return False, err_msg

            eula_file = server_dir / "eula.txt"
            eula_file.write_text("eula=true\n", encoding="utf-8")

            props_file = server_dir / "server.properties"
            if not props_file.exists():
                props_file.write_text(
                    "#Minecraft server properties\n"
                    "enable-jmx-monitoring=false\n"
                    "rcon.port=25575\n"
                    "level-seed=\n"
                    "gamemode=survival\n"
                    "enable-command-block=false\n"
                    "enable-query=false\n"
                    "generator-settings={}\n"
                    "enforce-secure-profile=false\n"
                    "level-name=world\n"
                    f"motd={pack_name} Server\n"
                    "query.port=25565\n"
                    "pvp=true\n"
                    "generate-structures=true\n"
                    "max-chained-neighbor-updates=1000000\n"
                    "difficulty=easy\n"
                    "network-compression-threshold=256\n"
                    "max-tick-time=60000\n"
                    "require-resource-pack=false\n"
                    "use-native-transport=true\n"
                    "max-players=20\n"
                    "online-mode=false\n"
                    "enable-status=true\n"
                    "allow-flight=false\n"
                    "initial-disabled-packs=\n"
                    "broadcast-rcon-to-ops=true\n"
                    "view-distance=10\n"
                    "server-ip=\n"
                    "resource-pack-prompt=\n"
                    "allow-nether=true\n"
                    "server-port=25565\n"
                    "enable-rcon=false\n"
                    "sync-chunk-writes=true\n"
                    "op-permission-level=4\n"
                    "prevent-proxy-connections=false\n"
                    "hide-online-players=false\n"
                    "resource-pack=\n"
                    "entity-broadcast-range-percentage=100\n"
                    "simulation-distance=10\n"
                    "rcon.password=\n"
                    "player-idle-timeout=0\n"
                    "force-gamemode=false\n"
                    "rate-limit=0\n"
                    "hardcore=false\n"
                    "white-list=false\n"
                    "broadcast-console-to-ops=true\n"
                    "spawn-npcs=true\n"
                    "spawn-animals=true\n"
                    "function-permission-level=2\n"
                    "initial-enabled-packs=vanilla\n"
                    "level-type=minecraft\\:normal\n"
                    "text-filtering-config=\n"
                    "spawn-monsters=true\n"
                    "enforce-whitelist=false\n"
                    "spawn-protection=16\n"
                    "resource-pack-sha1=\n"
                    "max-world-size=29999984\n",
                    encoding="utf-8"
                )

            self._mp_progress["phase"] = "done"
            logger.info(f"整合包服务器 {server_name} 安装完成: {server_dir}")
            return True, server_name

        except Exception as e:
            logger.error(f"整合包服务器安装失败: {e}")
            # 清理部分下载的文件
            try:
                if 'server_dir' in locals() and server_dir.exists():
                    shutil.rmtree(str(server_dir))
                    logger.info(f"已清理部分安装目录: {server_dir}")
            except Exception as cleanup_err:
                logger.warning(f"清理部分安装目录失败: {cleanup_err}")
            return False, str(e)

    def _install_server_mod_loader(
        self,
        loader_type: str,
        loader_version: str,
        mc_version: str,
        server_dir: Path,
        java_path: str,
    ) -> Tuple[bool, str]:
        """
        下载并安装服务端 mod loader

        Args:
            loader_type: "forge" | "fabric" | "neoforge" | "quilt"
            loader_version: loader 版本号
            mc_version: Minecraft 版本
            server_dir: 服务器目录
            java_path: Java 可执行文件路径

        Returns:
            (是否成功, 错误信息) 元组
        """
        import requests as req
        import tempfile

        try:
            if loader_type == "fabric":
                return self._install_fabric_server(loader_version, mc_version, server_dir, java_path)
            elif loader_type == "quilt":
                return self._install_quilt_server(loader_version, mc_version, server_dir, java_path)
            elif loader_type in ("forge", "neoforge"):
                return self._install_forge_neoforge_server(loader_type, loader_version, mc_version, server_dir, java_path)
            else:
                return False, f"不支持的 mod loader: {loader_type}"
        except Exception as e:
            logger.error(f"安装服务端 {loader_type} 失败: {e}")
            return False, str(e)

    def _install_fabric_server(
        self,
        loader_version: str,
        mc_version: str,
        server_dir: Path,
        java_path: str,
    ) -> Tuple[bool, str]:
        """安装 Fabric 服务端"""
        import requests as req

        self._set_status(f"正在安装 Fabric 服务端 ({loader_version}) ...")
        logger.info(f"正在安装 Fabric {loader_version} for {mc_version}")

        # 获取最新稳定版 installer
        installer_resp = req.get("https://meta.fabricmc.net/v2/versions/installer")
        installer_resp.raise_for_status()
        installers = installer_resp.json()
        stable = [i for i in installers if i.get("stable")]
        if not stable:
            return False, "无法获取 Fabric 安装器信息"
        installer_url = stable[0]["url"]

        # 下载 installer
        installer_path = server_dir / "fabric-installer.jar"
        self._set_status("正在下载 Fabric 安装器...")
        resp = req.get(installer_url, stream=True)
        resp.raise_for_status()
        with open(installer_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Fabric 安装器下载完成: {installer_path}")

        # 运行安装器
        self._set_status(f"正在安装 Fabric {loader_version} for {mc_version}...")
        cmd = [java_path, "-jar", str(installer_path), "server", "install",
               "-mcversion", mc_version, "-loader", loader_version, "-dir", str(server_dir)]
        logger.info(f"运行: {' '.join(cmd)}")

        popen_kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, cwd=str(server_dir),
        )
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        process = subprocess.run(cmd, **popen_kwargs, timeout=300)
        if process.returncode != 0:
            output = process.stdout.decode("utf-8", errors="replace") if process.stdout else ""
            return False, f"Fabric 安装器返回错误码 {process.returncode}\n{output[-500:]}"

        # 清理 installer
        try:
            installer_path.unlink()
        except Exception:
            pass

        # Fabric 需要原版 server.jar 才能启动
        self._set_status(f"正在下载原版服务器 {mc_version} jar（Fabric 依赖）...")
        success, err_msg = self._download_vanilla_server_jar(mc_version, server_dir, filename="server.jar")
        if not success:
            return False, f"原版服务器 jar 下载失败: {err_msg}"

        # 自动下载 Fabric API 及其依赖
        mods_dir = server_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        self._set_status("正在下载 Fabric API...")
        try:
            from modrinth import install_mod_with_deps
            api_ok, api_msg, api_names = install_mod_with_deps(
                project_id="P7dR8mSH",  # Fabric API on Modrinth
                game_version=mc_version,
                mod_loader="fabric",
                mods_dir=str(mods_dir),
                status_callback=self._set_status,
            )
            if api_ok:
                logger.info(f"Fabric API 安装成功: {', '.join(api_names)}")
                self._set_status(f"Fabric API 安装成功: {', '.join(api_names)}")
            else:
                logger.warning(f"Fabric API 安装失败（不影响启动）: {api_msg}")
                self._set_status(f"Fabric API 安装失败: {api_msg}")
        except Exception as e:
            logger.warning(f"Fabric API 下载异常（不影响启动）: {e}")

        logger.info(f"Fabric 服务端安装完成")
        return True, ""

    def _install_quilt_server(
        self,
        loader_version: str,
        mc_version: str,
        server_dir: Path,
        java_path: str,
    ) -> Tuple[bool, str]:
        """安装 Quilt 服务端"""
        import requests as req

        self._set_status(f"正在安装 Quilt 服务端 ({loader_version}) ...")
        logger.info(f"正在安装 Quilt {loader_version} for {mc_version}")

        # 获取最新稳定版 installer
        installer_resp = req.get("https://meta.quiltmc.org/v3/versions/installer")
        installer_resp.raise_for_status()
        installers = installer_resp.json()
        stable = [i for i in installers if i.get("stable")]
        if not stable:
            return False, "无法获取 Quilt 安装器信息"
        installer_url = stable[0]["url"]

        # 下载 installer
        installer_path = server_dir / "quilt-installer.jar"
        self._set_status("正在下载 Quilt 安装器...")
        resp = req.get(installer_url, stream=True)
        resp.raise_for_status()
        with open(installer_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # 运行安装器
        self._set_status(f"正在安装 Quilt {loader_version} for {mc_version}...")
        cmd = [java_path, "-jar", str(installer_path), "install", "server",
               "--mc-version", mc_version, "--loader-version", loader_version,
               "--install-dir", str(server_dir)]
        logger.info(f"运行: {' '.join(cmd)}")

        popen_kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, cwd=str(server_dir),
        )
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        process = subprocess.run(cmd, **popen_kwargs, timeout=300)
        if process.returncode != 0:
            output = process.stdout.decode("utf-8", errors="replace") if process.stdout else ""
            return False, f"Quilt 安装器返回错误码 {process.returncode}\n{output[-500:]}"

        try:
            installer_path.unlink()
        except Exception:
            pass

        # Quilt 需要原版 server.jar 才能启动
        self._set_status(f"正在下载原版服务器 {mc_version} jar（Quilt 依赖）...")
        success, err_msg = self._download_vanilla_server_jar(mc_version, server_dir, filename="server.jar")
        if not success:
            return False, f"原版服务器 jar 下载失败: {err_msg}"

        logger.info(f"Quilt 服务端安装完成")
        return True, ""

    def _install_forge_neoforge_server(
        self,
        loader_type: str,
        loader_version: str,
        mc_version: str,
        server_dir: Path,
        java_path: str,
    ) -> Tuple[bool, str]:
        """安装 Forge/NeoForge 服务端"""
        import requests as req

        self._set_status(f"正在安装 {loader_type} 服务端 ({loader_version}) ...")
        logger.info(f"正在安装 {loader_type} {loader_version} for {mc_version}")

        # 获取 installer 下载链接
        if loader_type == "neoforge":
            # NeoForge: https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar
            installer_url = (
                f"https://maven.neoforged.net/releases/net/neoforged/neoforge/"
                f"{loader_version}/neoforge-{loader_version}-installer.jar"
            )
        else:
            # Forge: https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{version}/forge-{mc_version}-{version}-installer.jar
            installer_url = (
                f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
                f"{mc_version}-{loader_version}/forge-{mc_version}-{loader_version}-installer.jar"
            )

        # 下载 installer
        installer_path = server_dir / f"{loader_type}-installer.jar"
        self._set_status(f"正在下载 {loader_type} 安装器...")
        resp = req.get(installer_url, stream=True)
        if resp.status_code == 404:
            return False, f"找不到 {loader_type} {loader_version} 的安装器 (Minecraft {mc_version})"
        resp.raise_for_status()
        with open(installer_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"{loader_type} 安装器下载完成: {installer_path}")

        # 运行安装器
        self._set_status(f"正在安装 {loader_type} {loader_version} for {mc_version}...")
        cmd = [java_path, "-jar", str(installer_path), "--installServer"]
        logger.info(f"运行: {' '.join(cmd)}")

        popen_kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, cwd=str(server_dir),
        )
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        process = subprocess.run(cmd, **popen_kwargs, timeout=600)
        if process.returncode != 0:
            output = process.stdout.decode("utf-8", errors="replace") if process.stdout else ""
            return False, f"{loader_type} 安装器返回错误码 {process.returncode}\n{output[-500:]}"

        # 清理 installer
        try:
            installer_path.unlink()
        except Exception:
            pass

        logger.info(f"{loader_type} 服务端安装完成")
        return True, ""
