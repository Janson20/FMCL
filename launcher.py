"""Minecraft启动器核心模块"""
import gc
import hashlib
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple

from logzero import logger

from config import Config
from mirror import MirrorSource


def concurrent_file_verify(
    file_hash_pairs: List[Tuple[Path, str, str]],
    max_workers: int = 4,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Tuple[Path, bool]]:
    """
    并发校验文件哈希

    利用 ThreadPoolExecutor 对大量文件进行并发哈希校验，
    I/O 密集场景下比串行校验快 3-5 倍。

    Args:
        file_hash_pairs: [(文件路径, 期望哈希, 哈希算法如"sha1")] 列表
        max_workers: 并发线程数
        progress_callback: 进度回调 (已完成数, 总数)

    Returns:
        [(文件路径, 是否匹配)] 列表
    """
    results: List[Tuple[Path, bool]] = []
    total = len(file_hash_pairs)
    done = 0

    def _verify_one(pair: Tuple[Path, str, str]) -> Tuple[Path, bool]:
        filepath, expected_hash, algorithm = pair
        try:
            h = hashlib.new(algorithm)
            with open(filepath, "rb") as f:
                # 1MB 块读取，平衡内存与速度
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            return filepath, h.hexdigest() == expected_hash.lower()
        except Exception as e:
            logger.debug(f"校验失败 {filepath}: {e}")
            return filepath, False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_one, p): p for p in file_hash_pairs}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1
            if progress_callback:
                progress_callback(done, total)

    return results


class MinecraftLauncher:
    """Minecraft启动器类"""

    def __init__(self, config: Config):
        self.config = config
        self.minecraft_dir = str(config.minecraft_dir)

        # 延迟导入 minecraft_launcher_lib，避免模块加载阶段的阻塞
        import minecraft_launcher_lib
        self._mcllib = minecraft_launcher_lib
        self.options = minecraft_launcher_lib.utils.generate_test_options()
        self.options["launcherName"] = "FMCL"
        self.options["launcherVersion"] = "2.4.3"

        self.current_max = 0

        # UI回调 (可选,用于进度更新)
        self.on_progress: Optional[Callable[[int, int, str], None]] = None

        # 初始化镜像源
        self._mirror = MirrorSource(enabled=config.mirror_enabled)
        self._apply_mirror_patch()

    def _apply_mirror_patch(self):
        """应用镜像源补丁"""
        if self._mirror.enabled:
            self._mirror.patch_minecraft_launcher_lib()
            logger.info(f"已启用镜像源: {self._mirror.get_mirror_name()}")
        else:
            logger.info("使用Mojang官方源")

    def _set_status(self, status: str) -> None:
        """状态回调"""
        logger.info(status)
        if self.on_progress:
            self.on_progress(0, 0, status)

    def _set_progress(self, progress: int) -> None:
        """进度回调"""
        if self.current_max != 0:
            logger.debug(f"进度: {progress}/{self.current_max}")
            if self.on_progress:
                self.on_progress(progress, self.current_max, "")

    def _set_max(self, new_max: int) -> None:
        """设置最大值回调"""
        self.current_max = new_max
        logger.info(f"总任务数: {new_max}")

    def _get_callback(self) -> Dict[str, Callable]:
        """获取回调函数字典"""
        return {
            "setStatus": self._set_status,
            "setProgress": self._set_progress,
            "setMax": self._set_max
        }

    def check_and_setup_environment(self) -> None:
        """检查并设置环境"""
        logger.info("正在检查文件夹...")

        if not self.config.minecraft_dir.exists():
            logger.warning("Minecraft目录不存在")
            logger.info("首次使用,正在初始化...")

            self.config.ensure_directories()
            logger.info("目录创建成功")

            logger.info("正在下载最新正式版...")

            try:
                if self._mirror.enabled:
                    latest = self._mirror.get_latest_version()
                    latest_release = latest.get("release", "")
                    if latest_release:
                        logger.info(f"从镜像源获取最新版本: {latest_release}")
                    else:
                        latest_release = self._mcllib.utils.get_latest_version()["release"]
                else:
                    latest_release = self._mcllib.utils.get_latest_version()["release"]

                self._mcllib.install.install_minecraft_version(
                    latest_release,
                    self.minecraft_dir,
                    callback=self._get_callback()
                )
                logger.info("正式版下载成功")
            except Exception as e:
                logger.error(f"下载初始版本失败: {str(e)}")
                raise
        else:
            logger.info("文件夹检查完成")

    def get_available_versions(self) -> List[Dict[str, str]]:
        """获取可用版本列表"""
        try:
            versions = self._mcllib.utils.get_available_versions(self.minecraft_dir)
            logger.info(f"获取到 {len(versions)} 个版本")
            return versions
        except Exception as e:
            logger.error(f"获取版本列表失败: {str(e)}")
            return []

    def get_installed_versions(self) -> List[str]:
        """获取已安装的版本列表"""
        try:
            versions_dir = self.config.get_versions_dir()
            if not versions_dir.exists():
                return []

            installed = [
                v for v in os.listdir(str(versions_dir))
                if v not in ['jre_manifest.json', 'version_manifest_v2.json']
            ]
            logger.info(f"已安装 {len(installed)} 个版本")
            return installed

        except Exception as e:
            logger.error(f"获取已安装版本失败: {str(e)}")
            return []

    def install_version(self, version_id: str, mod_loader: str = "无") -> Tuple[bool, str]:
        """
        安装Minecraft版本

        安装逻辑：
        - 无模组加载器: 仅安装原版 Minecraft
        - 有模组加载器: mod_loader.install() 会自动先装原版再装loader

        Args:
            version_id: 版本ID (如 "1.20.4" 或 "26.1")
            mod_loader: 模组加载器 ("无", "Forge", "Fabric", "NeoForge")

        Returns:
            (是否成功, 安装后的版本ID) 元组
            安装原版时返回 version_id
            安装模组加载器时返回 loader 创建的版本ID (如 "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0")
        """
        try:
            # 检查版本是否有效 — 用 set 实现 O(1) 查找
            available_versions = self.get_available_versions()
            version_ids = {
                v["id"].split()[0] if isinstance(v["id"], str) else v["id"]
                for v in available_versions
            }

            if version_id not in version_ids:
                logger.error(f"无效的版本ID: {version_id}")
                return False, version_id

            if mod_loader and mod_loader != "无":
                # 安装模组加载器 (会自动安装原版)
                from downloader import install_mod_loader
                logger.info(f"正在安装 {mod_loader} for Minecraft {version_id}")
                installed_version_id, loader_version = install_mod_loader(
                    loader=mod_loader,
                    version=version_id,
                    minecraft_dir=self.minecraft_dir,
                    num_threads=self.config.download_threads,
                    mirror=self._mirror,
                    callback=self._get_callback(),
                )
                logger.info(f"安装完成: {installed_version_id} (Loader: {mod_loader} {loader_version})")
                return True, installed_version_id
            else:
                # 仅安装原版 Minecraft
                logger.info(f"正在安装 Minecraft {version_id}")
                self._mcllib.install.install_minecraft_version(
                    version_id,
                    self.minecraft_dir,
                    callback=self._get_callback()
                )
                logger.info(f"Minecraft {version_id} 安装成功")
                return True, version_id

        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            return False, version_id

    def launch_game(self, version_id: str, minimize_after: bool = False, server_ip: str | None = None, server_port: int = 25565) -> bool:
        """
        启动游戏

        优化点:
        - JVM 参数: 使用 G1GC、固定堆内存（避免动态扩展开销）
        - 启动后: 主动 GC 释放启动器内存

        Args:
            version_id: 版本ID (可以是原版ID如 "1.20.4"，也可以是loader版本ID如 "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0")
            minimize_after: 启动后是否最小化启动器窗口（由 UI 侧监控游戏日志实现）

        Returns:
            (success, target_version) 是否启动成功及实际启动的版本ID
        """
        try:
            # 检查版本是否已安装
            installed_versions = self.get_installed_versions()

            # 用 set 实现 O(1) 查找
            installed_set = set(installed_versions)

            # 精确匹配
            if version_id in installed_set:
                target_version = version_id
            else:
                # 尝试模糊匹配：用户可能选了原版ID，但实际安装的是loader版本
                # 例如用户选 "1.20.4"，但安装的是 "1.20.4-forge-49.0.26"
                # 使用精确前缀匹配：版本ID完全相同 或 以 "版本ID-" 开头
                # 避免新格式下 "26.1" 错误匹配 "26.1.1" (旧格式无此问题)
                matches = [
                    v for v in installed_versions
                    if v == version_id or v.startswith(version_id + "-")
                ]
                if len(matches) == 1:
                    target_version = matches[0]
                    logger.info(f"模糊匹配: {version_id} -> {target_version}")
                elif len(matches) > 1:
                    # 多个匹配，优先选择带 loader 的版本
                    loader_matches = [v for v in matches if "-" in v and v != version_id]
                    if loader_matches:
                        target_version = loader_matches[0]
                        logger.info(f"多个匹配，选择: {target_version}")
                    else:
                        target_version = matches[0]
                else:
                    logger.error(f"版本未安装: {version_id}")
                    return False, None

            # 版本隔离：为模组加载器版本设置 gameDirectory
            # 游戏会从 gameDirectory 读取 mods、config 等资源
            options = dict(self.options)
            if self._has_mod_loader(target_version):
                version_game_dir = os.path.join(self.minecraft_dir, "versions", target_version)
                os.makedirs(version_game_dir, exist_ok=True)
                options["gameDirectory"] = version_game_dir
                logger.info(f"版本隔离已启用: gameDirectory={version_game_dir}")

            # Fabric 游戏：检查 mods/ 中是否有 Fabric API，没有则自动下载
            if "fabric" in target_version.lower():
                game_dir = options.get("gameDirectory", self.minecraft_dir)
                mods_dir = Path(game_dir) / "mods"
                has_fabric_api = False
                if mods_dir.exists():
                    for f in mods_dir.iterdir():
                        if f.name.lower().startswith("fabric-api"):
                            has_fabric_api = True
                            break
                if not has_fabric_api:
                    self._set_status("正在自动下载 Fabric API...")
                    logger.info("Fabric API 未找到，正在自动下载...")
                    try:
                        from modrinth import install_mod_with_deps, parse_game_version_from_version
                        mc_version = parse_game_version_from_version(target_version)
                        ok, msg, names = install_mod_with_deps(
                            project_id="P7dR8mSH",
                            game_version=mc_version or target_version,
                            mod_loader="fabric",
                            mods_dir=str(mods_dir),
                            status_callback=self._set_status,
                        )
                        if ok:
                            logger.info(f"Fabric API 自动安装成功: {', '.join(names)}")
                        else:
                            logger.warning(f"Fabric API 自动安装失败（不影响启动）: {msg}")
                    except Exception as e:
                        logger.warning(f"Fabric API 自动安装异常（不影响启动）: {e}")

            # 设置自定义玩家名（始终应用，不限制默认值）
            if self.config.player_name:
                options["username"] = self.config.player_name
                options["playerName"] = self.config.player_name

            # 皮肤：版本隔离时将皮肤复制到版本目录，确保游戏能找到
            if self.config.skin_path and os.path.exists(self.config.skin_path):
                import shutil
                game_dir = options.get("gameDirectory", self.minecraft_dir)
                skin_dir = os.path.join(game_dir, "skins")
                os.makedirs(skin_dir, exist_ok=True)
                shutil.copy2(self.config.skin_path, os.path.join(skin_dir, os.path.basename(self.config.skin_path)))
                logger.info(f"已复制皮肤到: {skin_dir}")

            # 直连服务器
            if server_ip:
                options["serverIp"] = server_ip
                options["serverPort"] = str(server_port)
                logger.info(f"将直连服务器: {server_ip}:{server_port}")

            # 获取启动命令
            logger.info(f"正在生成启动命令: {target_version}")
            minecraft_command = self._mcllib.command.get_minecraft_command(
                target_version,
                self.minecraft_dir,
                options
            )

            # 直连服务器时追加 --quickPlayMultiplayer（1.20.4+，启动后立即加入）
            if server_ip:
                server_addr = f"{server_ip}:{server_port}"
                minecraft_command.append("--quickPlayMultiplayer")
                minecraft_command.append(server_addr)
                logger.info(f"追加 --quickPlayMultiplayer {server_addr}")

            # ── JVM 参数优化 ──
            minecraft_command = self._optimize_jvm_args(minecraft_command)

            # ── 设置启动器名称 ──
            # 替换 --versionType 参数值，使游戏标题界面左下角显示 "Minecraft x.x.x/FMCL"
            minecraft_command = self._set_launcher_brand(minecraft_command)

            logger.info("正在启动游戏...")
            # 使用 Popen 非阻塞启动，捕获 stdout 以便检测游戏窗口
            # Windows 下使用 CREATE_NO_WINDOW 隐藏 Java 控制台窗口
            # 直连服务器时不捕获 stdout，避免管道缓冲区满导致游戏阻塞
            if server_ip:
                popen_kwargs = {}
                if sys.platform == 'win32':
                    popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs = dict(
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                if sys.platform == 'win32':
                    popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            self._game_process = subprocess.Popen(
                minecraft_command,
                **popen_kwargs,
            )

            # ── 启动后内存释放 ──
            self._release_memory_after_launch()

            logger.info(f"游戏已启动 ({target_version})")
            return True, target_version

        except Exception as e:
            logger.error(f"启动游戏失败: {str(e)}")
            return False, None

    @staticmethod
    def _has_mod_loader(version_id: str) -> bool:
        """判断版本是否安装了模组加载器（需要版本隔离）"""
        v = version_id.lower()
        return any(loader in v for loader in ("forge", "fabric", "neoforge"))

    def _optimize_jvm_args(self, command: List[str]) -> List[str]:
        """
        优化 JVM 启动参数

        - 使用 G1GC 垃圾回收器，减少游戏卡顿
        - 固定堆内存大小，避免动态扩展/收缩的开销
        - 添加性能友好的 JVM 标志
        """
        optimized = []
        has_xms = False
        has_xmx = False
        has_gc = False

        for arg in command:
            # 检测已有的内存参数
            if arg.startswith("-Xms"):
                has_xms = True
                # 如果用户设置了固定值，保留
                optimized.append(arg)
            elif arg.startswith("-Xmx"):
                has_xmx = True
                optimized.append(arg)
            elif arg.startswith("-XX:+Use") and "GC" in arg:
                has_gc = True
                optimized.append(arg)
            else:
                optimized.append(arg)

        # 在 -cp 之前插入优化参数（确保 JVM 能识别）
        # 找到 java 可执行文件的位置
        insert_idx = 1  # 默认在第一个参数后插入
        for i, arg in enumerate(optimized):
            if arg in ("java", "javaw") or arg.endswith("java.exe") or arg.endswith("javaw.exe"):
                insert_idx = i + 1
                break

        jvm_opts = []
        if not has_gc:
            jvm_opts.append("-XX:+UseG1GC")  # G1 垃圾回收器，减少卡顿
        if not has_xms and has_xmx:
            # 如果只设了 -Xmx 没设 -Xms，将 -Xms 设为 -Xmx 的一半
            for arg in optimized:
                if arg.startswith("-Xmx"):
                    try:
                        xmx_val = arg[4:]
                        xmx_bytes = self._parse_memory_string(xmx_val)
                        xms_bytes = xmx_bytes // 2
                        xms_str = self._format_memory(xms_bytes)
                        jvm_opts.append(f"-Xms{xms_str}")
                    except Exception:
                        jvm_opts.append("-Xms1G")
                    break

        # 额外性能优化标志
        jvm_opts.extend([
            "-XX:+ParallelRefProcEnabled",   # 并行引用处理
            "-XX:MaxGCPauseMillis=200",       # 目标 GC 停顿时间
        ])

        if jvm_opts:
            for opt in reversed(jvm_opts):
                optimized.insert(insert_idx, opt)
            logger.info(f"JVM 优化参数: {jvm_opts}")

        return optimized

    def _set_launcher_brand(self, command: List[str]) -> List[str]:
        """
        设置启动器品牌标识

        替换 --versionType 参数值，使游戏标题界面左下角显示
        如 "Minecraft 1.21.1/FMCL" 或 "Minecraft 26.1/FMCL" 而非默认的 "Minecraft 1.21.1/release"
        """
        brand = f"{self.options.get('launcherName', 'FMCL')}/{self.options.get('launcherVersion', '3.2')}"
        for i, arg in enumerate(command):
            if arg == "--versionType" and i + 1 < len(command):
                command[i + 1] = brand
                logger.info(f"启动器品牌标识: --versionType {brand}")
                break
        return command

    @staticmethod
    def _parse_memory_string(s: str) -> int:
        """将 JVM 内存字符串 (如 '4G', '512M') 转换为字节数"""
        s = s.strip()
        multipliers = {"G": 1024**3, "M": 1024**2, "K": 1024, "g": 1024**3, "m": 1024**2, "k": 1024}
        if s[-1] in multipliers:
            return int(s[:-1]) * multipliers[s[-1]]
        return int(s)

    @staticmethod
    def _format_memory(bytes_val: int) -> str:
        """将字节数格式化为 JVM 内存字符串"""
        if bytes_val >= 1024**3:
            return f"{bytes_val // (1024**3)}G"
        elif bytes_val >= 1024**2:
            return f"{bytes_val // (1024**2)}M"
        return f"{bytes_val // 1024}K"

    @staticmethod
    def _release_memory_after_launch():
        """启动游戏后释放启动器内存"""
        try:
            gc.collect()
            logger.debug("已执行 GC 释放内存")
        except Exception as e:
            logger.debug(f"GC 释放失败: {e}")

    def remove_version(self, version_id: str) -> Tuple[bool, str]:
        """
        删除已安装的版本

        删除 versions/{version_id}/ 目录和 versions/{version_id}.json 文件。

        Args:
            version_id: 版本ID (如 "1.20.4" 或 "1.20.4-forge-49.0.26" 或 "26.1")

        Returns:
            (是否成功, 版本ID) 元组
        """
        try:
            versions_dir = self.config.get_versions_dir()
            version_dir = versions_dir / version_id
            version_json = versions_dir / f"{version_id}.json"

            if not version_dir.exists() and not version_json.exists():
                logger.error(f"版本未安装: {version_id}")
                return False, version_id

            # 删除版本目录
            if version_dir.exists():
                shutil.rmtree(str(version_dir))
                logger.info(f"已删除版本目录: {version_dir}")

            # 删除版本JSON文件
            if version_json.exists():
                version_json.unlink()
                logger.info(f"已删除版本JSON: {version_json}")

            logger.info(f"版本 {version_id} 删除成功")
            return True, version_id

        except Exception as e:
            logger.error(f"删除版本失败: {str(e)}")
            return False, version_id

    def get_callbacks(self) -> Dict[str, Callable]:
        """获取供UI调用的回调函数字典"""
        return {
            "check_environment": self.check_and_setup_environment,
            "get_available_versions": self.get_available_versions,
            "get_installed_versions": self.get_installed_versions,
            "install_version": self.install_version,
            "remove_version": self.remove_version,
            "launch_game": self.launch_game,
            "set_mirror_enabled": self.set_mirror_enabled,
            "get_mirror_enabled": self.get_mirror_enabled,
            "test_mirror_connection": self.test_mirror_connection,
            "get_mirror_name": self.get_mirror_name,
            "get_minecraft_dir": self.get_minecraft_dir,
            "verify_installed_version": self.verify_installed_version,
            "set_minimize_on_game_launch": self.set_minimize_on_game_launch,
            "get_minimize_on_game_launch": self.get_minimize_on_game_launch,
            "get_download_threads": self.get_download_threads,
            "set_download_threads": self.set_download_threads,
            "get_game_process": self.get_game_process,
            "kill_game_process": self.kill_game_process,
            "is_game_running": self.is_game_running,
            "get_player_name": self.get_player_name,
            "set_player_name": self.set_player_name,
            "get_skin_path": self.get_skin_path,
            "set_skin_path": self.set_skin_path,
            "get_jdz_token": self.get_jdz_token,
            "set_jdz_token": self.set_jdz_token,
            # 服务器相关
            "get_server_versions": self.get_server_versions,
            "get_installed_servers": self.get_installed_servers,
            "install_server": self.install_server,
            "start_server": self.start_server,
            "stop_server": self.stop_server,
            "is_server_running": self.is_server_running,
            "get_server_process": self.get_server_process,
            "remove_server": self.remove_server,
            "get_server_dir": self.get_server_dir,
            "send_server_command": self.send_server_command,
            # 整合包相关
            "get_mrpack_information": self.get_mrpack_information,
            "install_mrpack": self.install_mrpack,
            "get_mrpack_launch_version": self.get_mrpack_launch_version,
            "install_mrpack_server": self.install_mrpack_server,
        }

    def get_player_name(self) -> str:
        """获取自定义玩家名"""
        return self.config.player_name

    def set_player_name(self, name: str) -> None:
        """设置自定义玩家名"""
        self.config.player_name = name
        self.config.save_config()

    def get_skin_path(self) -> Optional[str]:
        """获取自定义皮肤路径"""
        return self.config.skin_path

    def set_skin_path(self, path: Optional[str]) -> None:
        """设置自定义皮肤路径"""
        self.config.skin_path = path
        self.config.save_config()

    def get_jdz_token(self) -> Optional[str]:
        """获取净读 AI Token"""
        return self.config.jdz_token

    def set_jdz_token(self, token: Optional[str]) -> None:
        """设置净读 AI Token"""
        self.config.jdz_token = token
        self.config.save_config()

    def verify_installed_version(self, version_id: str, max_workers: int = 4) -> Dict[str, Any]:
        """
        并发校验已安装版本的文件完整性

        Args:
            version_id: 版本ID
            max_workers: 并发线程数

        Returns:
            {"total": 总文件数, "valid": 有效文件数, "invalid": 无效文件列表}
        """
        versions_dir = self.config.get_versions_dir()
        version_json = versions_dir / f"{version_id}.json"

        if not version_json.exists():
            logger.error(f"版本 JSON 不存在: {version_json}")
            return {"total": 0, "valid": 0, "invalid": []}

        try:
            # 高性能 JSON 解析
            try:
                import orjson
                version_data = orjson.loads(version_json.read_bytes())
            except ImportError:
                import json
                with open(str(version_json), "r", encoding="utf-8") as f:
                    version_data = json.load(f)

            file_hash_pairs: List[Tuple[Path, str, str]] = []

            # 从版本 JSON 中提取库文件和主程序的校验信息
            libraries = version_data.get("libraries", [])
            for lib in libraries:
                downloads = lib.get("downloads", {})
                artifact = downloads.get("artifact") or downloads.get("classifiers", {}).get("natives-windows")
                if artifact and artifact.get("sha1"):
                    path = self.config.minecraft_dir / "libraries" / artifact.get("path", "")
                    if path.exists():
                        file_hash_pairs.append((path, artifact["sha1"], "sha1"))

            # 主程序 jar
            main_downloads = version_data.get("mainClass", {})
            if isinstance(version_data.get("downloads"), dict):
                client = version_data["downloads"].get("client")
                if client and client.get("sha1"):
                    jar_path = versions_dir / version_id / f"{version_id}.jar"
                    if jar_path.exists():
                        file_hash_pairs.append((jar_path, client["sha1"], "sha1"))

            if not file_hash_pairs:
                return {"total": 0, "valid": 0, "invalid": []}

            logger.info(f"开始并发校验 {len(file_hash_pairs)} 个文件 (workers={max_workers})")
            results = concurrent_file_verify(file_hash_pairs, max_workers=max_workers)

            invalid = [str(p) for p, ok in results if not ok]
            valid_count = len(results) - len(invalid)

            logger.info(f"校验完成: {valid_count}/{len(results)} 有效")
            return {"total": len(results), "valid": valid_count, "invalid": invalid}

        except Exception as e:
            logger.error(f"版本校验失败: {e}")
            return {"total": 0, "valid": 0, "invalid": []}

    def get_minecraft_dir(self) -> str:
        """获取 .minecraft 目录路径"""
        return str(self.config.minecraft_dir)

    def get_game_process(self) -> Optional[subprocess.Popen]:
        """获取当前游戏进程对象（用于监控 stdout）"""
        return getattr(self, "_game_process", None)

    def kill_game_process(self) -> bool:
        """强制结束游戏进程"""
        proc = getattr(self, "_game_process", None)
        if proc is not None and proc.poll() is None:
            proc.kill()
            logger.info("已强制结束游戏进程")
            self._game_process = None
            return True
        logger.warning("没有正在运行的游戏进程")
        return False

    def is_game_running(self) -> bool:
        """检查游戏进程是否正在运行"""
        proc = getattr(self, "_game_process", None)
        return proc is not None and proc.poll() is None

    def set_mirror_enabled(self, enabled: bool) -> None:
        """设置镜像源启用状态"""
        self._mirror.enabled = enabled
        self.config.mirror_enabled = enabled
        self.config.save_config()

        if enabled:
            self._mirror.patch_minecraft_launcher_lib()
            logger.info(f"已启用镜像源: {self._mirror.get_mirror_name()}")
        else:
            logger.info("已切换到Mojang官方源")

    def get_mirror_enabled(self) -> bool:
        """获取镜像源启用状态"""
        return self._mirror.enabled

    def set_minimize_on_game_launch(self, enabled: bool) -> None:
        """设置游戏启动后是否最小化启动器"""
        self.config.minimize_on_game_launch = enabled
        self.config.save_config()
        logger.info(f"游戏启动后最小化: {'已启用' if enabled else '已禁用'}")

    def get_minimize_on_game_launch(self) -> bool:
        """获取游戏启动后是否最小化启动器"""
        return self.config.minimize_on_game_launch

    def get_download_threads(self) -> int:
        """获取下载线程数"""
        return self.config.download_threads

    def set_download_threads(self, threads: int) -> None:
        """设置下载线程数"""
        self.config.download_threads = max(1, min(255, threads))
        self.config.save_config()
        logger.info(f"下载线程数设置为: {self.config.download_threads}")

    def test_mirror_connection(self) -> bool:
        """测试当前镜像源连接"""
        return self._mirror.test_connection()

    def get_mirror_name(self) -> str:
        """获取当前镜像源名称"""
        return self._mirror.get_mirror_name()

    # ─── 服务器管理 ──────────────────────────────────────────

    def get_server_dir(self) -> str:
        """获取服务器根目录路径"""
        return str(self.config.minecraft_dir / "server")

    def _get_server_versions_dir(self) -> Path:
        """获取服务器版本目录路径: .minecraft/server/"""
        return self.config.minecraft_dir / "server"

    def get_server_versions(self) -> List[Dict[str, str]]:
        """
        获取可下载的服务器版本列表（正式版）

        Returns:
            [{"id": "1.21.4", "type": "release"}, ...] 列表
        """
        try:
            versions = self._mcllib.utils.get_available_versions(self.minecraft_dir)
            # 过滤出正式版（服务器只支持正式版）
            release_versions = [v for v in versions if v.get("type") == "release"]
            return release_versions
        except Exception as e:
            logger.error(f"获取服务器版本列表失败: {str(e)}")
            return []

    def get_installed_servers(self) -> List[str]:
        """获取已安装的服务器版本列表"""
        try:
            server_dir = self._get_server_versions_dir()
            if not server_dir.exists():
                return []
            installed = [
                v for v in os.listdir(str(server_dir))
                if v not in ['jre_manifest.json', 'version_manifest_v2.json', 'versions']
                and (server_dir / v).is_dir()
            ]
            logger.info(f"已安装 {len(installed)} 个服务器版本")
            return sorted(installed)
        except Exception as e:
            logger.error(f"获取已安装服务器版本失败: {str(e)}")
            return []

    def _find_runtime_java(self, version_id: str) -> str:
        """
        从 .minecraft/runtime 中查找合适版本的 Java

        先尝试读取版本 JSON 中的 javaVersion 字段获取精确的 runtime 组件名，
        再使用 minecraft_launcher_lib.runtime.get_executable_path 查找可执行文件。
        如果找不到，回退到已安装的 runtime 列表中选择最新的。

        Args:
            version_id: Minecraft 版本号

        Returns:
            Java 可执行文件路径，找不到则返回 "java"
        """
        import json

        # 1. 从版本 JSON 获取 javaVersion.component
        version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
        jvm_component = None
        if version_json_path.exists():
            try:
                with open(version_json_path, "r", encoding="utf-8") as f:
                    vdata = json.load(f)
                jvm_component = vdata.get("javaVersion", {}).get("component")
            except Exception:
                pass

        # 2. 使用 minecraft_launcher_lib 查找 runtime Java
        if jvm_component:
            java_path = self._mcllib.runtime.get_executable_path(jvm_component, self.minecraft_dir)
            if java_path:
                logger.info(f"从 runtime 找到 Java ({jvm_component}): {java_path}")
                return java_path

        # 3. 回退：遍历已安装的 runtime，选最新的
        try:
            installed_runtimes = self._mcllib.runtime.get_installed_jvm_runtimes(self.minecraft_dir)
            if installed_runtimes:
                # 按版本排序取最新的
                latest = sorted(installed_runtimes, key=lambda r: r.get("version", {}).get("name", ""), reverse=True)
                component = latest[0].get("name", "")
                if component:
                    java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                    if java_path:
                        logger.info(f"从已安装 runtime 找到 Java ({component}): {java_path}")
                        return java_path
        except Exception:
            pass

        # 4. 最终回退到系统 Java
        logger.warning("未在 runtime 中找到 Java，回退到系统 Java")
        try:
            return self._mcllib.utils.get_java_executable()
        except Exception:
            return "java"

    def install_server(self, version_id: str) -> Tuple[bool, str]:
        """
        下载并安装 Minecraft 服务器

        流程：
        1. 安装同名客户端版本（自动下载所需 Java runtime 到 .minecraft/runtime/）
        2. 从版本 JSON 获取 server jar 下载链接，下载到 .minecraft/server/<version>/
        3. 重命名为 server-<version>.jar
        4. 自动同意 EULA

        Args:
            version_id: 版本号（如 "1.21.4"）

        Returns:
            (是否成功, 版本号) 元组
        """
        import json
        import requests as req

        try:
            server_dir = self._get_server_versions_dir() / version_id
            server_dir.mkdir(parents=True, exist_ok=True)
            server_jar = server_dir / f"server-{version_id}.jar"

            # 1. 安装同名客户端版本（会自动下载 Java runtime）
            logger.info(f"正在安装客户端版本 {version_id}（自动下载 Java runtime）...")
            self._set_status(f"正在准备环境: 安装 {version_id} ...")
            self._mcllib.install.install_minecraft_version(
                version_id,
                self.minecraft_dir,
                callback=self._get_callback()
            )

            # 2. 从版本 JSON 获取 server jar 下载链接
            version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
            if not version_json_path.exists():
                logger.error(f"版本 JSON 不存在: {version_json_path}")
                return False, version_id

            with open(version_json_path, "r", encoding="utf-8") as f:
                vdata = json.load(f)

            downloads = vdata.get("downloads", {})
            server_info = downloads.get("server")
            if not server_info:
                logger.error(f"版本 {version_id} 不支持服务器端 (无 server downloads)")
                return False, version_id

            server_jar_url = server_info.get("url")
            if not server_jar_url:
                logger.error(f"无法获取服务器 jar 下载链接")
                return False, version_id

            # 3. 下载 server jar
            logger.info(f"正在下载服务器 jar: {server_jar_url}")
            self._set_status(f"正在下载服务器 {version_id} jar ...")
            resp = req.get(server_jar_url, stream=True)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(server_jar, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        self._set_progress(downloaded)
                        self._set_max(total)
            logger.info(f"服务器 jar 下载完成: {server_jar}")

            # 4. 自动同意 EULA
            eula_file = server_dir / "eula.txt"
            eula_file.write_text("eula=true\n", encoding="utf-8")
            logger.info("已自动同意 EULA")

            # 5. 创建基本的 server.properties
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
                    "motd=FMCL Server\n"
                    "query.port=25565\n"
                    "pvp=true\n"
                    "generate-structures=true\n"
                    "max-chained-neighbor-updates=1000000\n"
                    "difficulty=easy\n"
                    "network-compression-threshold=256\n                    \n"
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

            logger.info(f"服务器 {version_id} 安装完成: {server_dir}")
            return True, version_id

        except Exception as e:
            logger.error(f"安装服务器失败: {str(e)}")
            return False, version_id

    def _kill_process_on_port(self, port: int):
        """
        检查并杀掉占用指定端口的进程（仅 Windows）

        Args:
            port: 要检查的端口号
        """
        if sys.platform != 'win32':
            return
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) > 0:
                        logger.info(f"端口 {port} 被进程 {pid} 占用，正在终止...")
                        subprocess.run(
                            ['taskkill', '/F', '/PID', pid],
                            capture_output=True, timeout=10
                        )
                        logger.info(f"已终止进程 {pid}")
                        break
        except Exception as e:
            logger.warning(f"清理端口 {port} 失败: {e}")

    def start_server(self, version_id: str, max_memory: str = "2G") -> Tuple[bool, Optional[subprocess.Popen]]:
        """
        启动 Minecraft 服务器

        Args:
            version_id: 服务器版本号
            max_memory: 最大内存（默认 "2G"）

        Returns:
            (是否成功, 进程对象) 元组
        """
        try:
            server_dir = self._get_server_versions_dir() / version_id
            if not server_dir.exists():
                logger.error(f"服务器版本未安装: {version_id}")
                return False, None

            # 查找服务器 jar（支持 vanilla / Fabric / Forge / NeoForge / Quilt）
            server_jar = self._find_server_jar(server_dir)
            if not server_jar:
                logger.error(f"在 {server_dir} 中未找到服务器 jar")
                return False, None

            # 确保 EULA 已同意
            eula_file = server_dir / "eula.txt"
            if not eula_file.exists() or "eula=true" not in eula_file.read_text(encoding="utf-8"):
                eula_file.write_text("eula=true\n", encoding="utf-8")
                logger.info("已自动同意 EULA")

            # Fabric 服务器：检查 mods/ 中是否有 Fabric API，没有则自动下载
            version_lower = version_id.lower()
            if "fabric" in version_lower:
                mods_dir = server_dir / "mods"
                has_fabric_api = False
                if mods_dir.exists():
                    for f in mods_dir.iterdir():
                        if f.name.lower().startswith("fabric-api"):
                            has_fabric_api = True
                            break
                if not has_fabric_api:
                    self._set_status("正在自动下载 Fabric API...")
                    logger.info("Fabric API 未找到，正在自动下载...")
                    try:
                        from modrinth import install_mod_with_deps, parse_game_version_from_version
                        mc_version = parse_game_version_from_version(version_id)
                        ok, msg, names = install_mod_with_deps(
                            project_id="P7dR8mSH",
                            game_version=mc_version or version_id,
                            mod_loader="fabric",
                            mods_dir=str(mods_dir),
                            status_callback=self._set_status,
                        )
                        if ok:
                            logger.info(f"Fabric API 自动安装成功: {', '.join(names)}")
                            self._set_status(f"Fabric API 安装成功: {', '.join(names)}")
                        else:
                            logger.warning(f"Fabric API 自动安装失败（不影响启动）: {msg}")
                    except Exception as e:
                        logger.warning(f"Fabric API 自动安装异常（不影响启动）: {e}")

            # 清理残留的 session.lock（上次异常退出可能导致锁文件残留）
            world_dir = server_dir / "world"
            lock_file = world_dir / "session.lock"
            if lock_file.exists():
                try:
                    lock_file.unlink()
                    logger.info("已清理残留的 session.lock")
                except Exception as e:
                    logger.warning(f"清理 session.lock 失败: {e}")

            # 清理占用 25565 端口的残留进程
            self._kill_process_on_port(25565)

            # 从 runtime 查找 Java
            java_path = self._find_runtime_java(version_id)
            logger.info(f"使用 Java: {java_path}")

            cmd = [java_path, f"-Xmx{max_memory}", f"-Xms{max_memory}", "-jar", str(server_jar), "nogui"]
            logger.info(f"正在启动服务器 {version_id}: {' '.join(cmd)}")

            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                bufsize=1,
                cwd=str(server_dir),
            )
            if sys.platform == 'win32':
                popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(cmd, **popen_kwargs)
            self._server_process = process
            logger.info(f"服务器 {version_id} 已启动 (PID: {process.pid})")
            return True, process

        except Exception as e:
            logger.error(f"启动服务器失败: {str(e)}")
            return False, None

    def stop_server(self) -> bool:
        """停止服务器（发送 stop 命令）"""
        proc = getattr(self, "_server_process", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(b"stop\n")
                proc.stdin.flush()
                logger.info("已发送 stop 命令到服务器")
                return True
            except Exception as e:
                logger.error(f"发送 stop 命令失败: {e}")
                try:
                    proc.kill()
                except Exception:
                    pass
                self._server_process = None
                return False
        logger.warning("没有正在运行的服务器")
        return False

    def send_server_command(self, command: str) -> bool:
        """
        向服务器发送控制台命令

        Args:
            command: 要发送的命令字符串

        Returns:
            是否发送成功
        """
        proc = getattr(self, "_server_process", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write((command + "\n").encode("utf-8"))
                proc.stdin.flush()
                logger.info(f"已发送服务器命令: {command}")
                return True
            except Exception as e:
                logger.error(f"发送服务器命令失败: {e}")
                return False
        return False

    def is_server_running(self) -> bool:
        """检查服务器是否正在运行"""
        proc = getattr(self, "_server_process", None)
        return proc is not None and proc.poll() is None

    def get_server_process(self) -> Optional[subprocess.Popen]:
        """获取服务器进程对象"""
        return getattr(self, "_server_process", None)

    def remove_server(self, version_id: str) -> Tuple[bool, str]:
        """删除已安装的服务器版本"""
        try:
            server_dir = self._get_server_versions_dir() / version_id
            if not server_dir.exists():
                logger.error(f"服务器版本未安装: {version_id}")
                return False, version_id

            # 如果正在运行则先停止
            if self.is_server_running():
                self.stop_server()

            shutil.rmtree(str(server_dir))
            logger.info(f"服务器 {version_id} 已删除")
            return True, version_id

        except Exception as e:
            logger.error(f"删除服务器版本失败: {str(e)}")
            return False, version_id

    # ─── 整合包（mrpack）安装 ───────────────────────────────────

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

    def install_mrpack(
        self,
        mrpack_path: str,
        optional_files: Optional[List[str]] = None,
        modpack_directory: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        安装 .mrpack 整合包（默认启用版本隔离）

        整合包的模组、配置文件等资源默认安装到
        .minecraft/versions/<launch_version>/ 目录，实现版本隔离。
        启动游戏时 launch_game 会自动为含模组加载器的版本设置 gameDirectory。

        Args:
            mrpack_path: .mrpack 文件的绝对路径
            optional_files: 要安装的可选文件列表（默认全部不安装）
            modpack_directory: 整合包安装目录（默认为版本隔离目录，即 versions/<launch_version>/）

        Returns:
            (是否成功, 启动版本ID 或 错误信息) 元组
        """
        if not os.path.isfile(mrpack_path):
            msg = f"文件不存在: {mrpack_path}"
            logger.error(msg)
            return False, msg

        mc_dir = self.minecraft_dir

        # 先获取启动版本ID，用于确定版本隔离目录
        try:
            launch_version = self._mcllib.mrpack.get_mrpack_launch_version(mrpack_path)
        except Exception as e:
            msg = f"无法获取整合包启动版本: {e}"
            logger.error(msg)
            return False, msg

        # 默认使用版本隔离目录：versions/<launch_version>/
        if modpack_directory:
            mp_dir = modpack_directory
        else:
            mp_dir = os.path.join(mc_dir, "versions", launch_version)
            os.makedirs(mp_dir, exist_ok=True)
            logger.info(f"版本隔离模式: 整合包资源将安装到 {mp_dir}")

        install_options: dict = {
            "optionalFiles": optional_files or [],
        }

        try:
            logger.info(f"正在安装整合包: {mrpack_path} -> {mc_dir}")
            self._mcllib.mrpack.install_mrpack(
                mrpack_path,
                mc_dir,
                modpack_directory=mp_dir,
                mrpack_install_options=install_options,
                callback=self._get_callback(),
            )
            logger.info(f"整合包安装完成，启动版本: {launch_version}")
            return True, launch_version
        except Exception as e:
            logger.error(f"整合包安装失败: {e}")
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
        安装整合包作为服务器

        流程：
        1. 读取整合包信息，获取 Minecraft 版本和 mod loader 信息
        2. 用 mrpack 模块将整合包文件（mods、configs 等）安装到服务器目录
        3. 安装同名客户端版本（自动下载 Java runtime）
        4. 如果有 mod loader，自动下载并安装服务端 mod loader
        5. 如果没有 mod loader，下载 vanilla server jar
        6. 自动同意 EULA，创建基本 server.properties

        Args:
            mrpack_path: .mrpack 文件的绝对路径
            optional_files: 要安装的可选文件列表
            server_name: 服务器目录名（默认使用整合包名称）

        Returns:
            (是否成功, 服务器名称 或 错误信息) 元组
        """
        import json
        import requests as req
        import zipfile

        if not os.path.isfile(mrpack_path):
            msg = f"文件不存在: {mrpack_path}"
            logger.error(msg)
            return False, msg

        try:
            # 1. 读取整合包信息 + mod loader 信息
            mrpack_info = self._mcllib.mrpack.get_mrpack_information(mrpack_path)
            mc_version = mrpack_info.get("minecraftVersion", "")
            pack_name = mrpack_info.get("name", "modpack")

            if not mc_version:
                msg = "无法获取整合包的 Minecraft 版本"
                logger.error(msg)
                return False, msg

            # 从 mrpack 内部的 modrinth.index.json 读取 mod loader 依赖
            mod_loader_info = {}  # {"type": "forge"|"fabric"|"neoforge"|"quilt", "version": "..."}
            with zipfile.ZipFile(mrpack_path, "r") as zf:
                if "modrinth.index.json" in zf.namelist():
                    with zf.open("modrinth.index.json", "r") as f:
                        index = json.load(f)
                    deps = index.get("dependencies", {})
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

            # 服务器目录名
            if not server_name:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', pack_name)
                server_name = f"{safe_name}-{mc_version}"

            server_dir = self._get_server_versions_dir() / server_name
            server_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"整合包服务器目录: {server_dir}")

            # 2. 安装整合包文件到服务器目录
            install_options: dict = {"optionalFiles": optional_files or []}
            self._set_status(f"正在安装整合包文件到服务器目录...")
            self._mcllib.mrpack.install_mrpack(
                mrpack_path,
                self.minecraft_dir,
                modpack_directory=str(server_dir),
                mrpack_install_options=install_options,
                callback=self._get_callback(),
            )

            # 3. 安装同名客户端版本（自动下载 Java runtime）
            logger.info(f"正在安装客户端版本 {mc_version}（自动下载 Java runtime）...")
            self._set_status(f"正在准备环境: 安装 {mc_version} ...")
            self._mcllib.install.install_minecraft_version(
                mc_version,
                self.minecraft_dir,
                callback=self._get_callback()
            )

            # 4. 安装服务端 mod loader 或 vanilla server jar
            java_path = self._find_runtime_java(mc_version)
            logger.info(f"使用 Java: {java_path}")

            if loader_type:
                success, err_msg = self._install_server_mod_loader(
                    loader_type, loader_version, mc_version, server_dir, java_path
                )
                if not success:
                    return False, f"服务端 {loader_type} 安装失败: {err_msg}"
            else:
                # 下载 vanilla server jar
                success, err_msg = self._download_vanilla_server_jar(mc_version, server_dir)
                if not success:
                    return False, err_msg

            # 5. 自动同意 EULA
            eula_file = server_dir / "eula.txt"
            eula_file.write_text("eula=true\n", encoding="utf-8")

            # 6. 创建基本 server.properties（如果不存在）
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

            logger.info(f"整合包服务器 {server_name} 安装完成: {server_dir}")
            return True, server_name

        except Exception as e:
            logger.error(f"整合包服务器安装失败: {e}")
            return False, str(e)

    def _find_server_jar(self, server_dir: Path) -> Optional[Path]:
        """
        在服务器目录中查找可用的服务器 jar

        查找优先级：
        1. fabric-server-launch.jar（Fabric）
        2. quilt-server-launch.jar（Quilt）
        3. run.bat（Forge/NeoForge 生成的启动脚本，提取其中的 jar 路径）
        4. forge-*.jar（Forge 旧版）
        5. server-*.jar（vanilla 或整合包开服创建的）
        """
        # Fabric
        fabric_jar = server_dir / "fabric-server-launch.jar"
        if fabric_jar.exists():
            logger.info(f"找到 Fabric 服务器 jar: {fabric_jar}")
            return fabric_jar

        # Quilt
        quilt_jar = server_dir / "quilt-server-launch.jar"
        if quilt_jar.exists():
            logger.info(f"找到 Quilt 服务器 jar: {quilt_jar}")
            return quilt_jar

        # Forge / NeoForge: 检查 run.bat 中引用的 jar
        run_bat = server_dir / "run.bat"
        if run_bat.exists():
            try:
                content = run_bat.read_text(encoding="utf-8", errors="replace")
                # 查找 @libraries/... 或 *.jar 引用
                # Forge/NeoForge 通常用 @libraries/.../xxx.jar 或直接 java -jar xxx.jar nogui
                jar_match = re.search(r'@"?(libraries\\[^"]+\.jar)"?', content)
                if jar_match:
                    jar_path = server_dir / jar_match.group(1).lstrip("@").replace("\\", os.sep)
                    if jar_path.exists():
                        logger.info(f"从 run.bat 找到服务器 jar: {jar_path}")
                        return jar_path
                # 也尝试匹配直接 -jar xxx.jar
                direct_match = re.search(r'-jar\s+"?([^"\s]+\.jar)"?', content)
                if direct_match:
                    jar_path = server_dir / direct_match.group(1)
                    if jar_path.exists():
                        logger.info(f"从 run.bat 找到服务器 jar: {jar_path}")
                        return jar_path
            except Exception:
                pass

        # run.sh (Linux)
        run_sh = server_dir / "run.sh"
        if run_sh.exists():
            try:
                content = run_sh.read_text(encoding="utf-8", errors="replace")
                jar_match = re.search(r'@"?(libraries/[^"]+\.jar)"?', content)
                if jar_match:
                    jar_path = server_dir / jar_match.group(1).lstrip("@").replace("/", os.sep)
                    if jar_path.exists():
                        logger.info(f"从 run.sh 找到服务器 jar: {jar_path}")
                        return jar_path
                direct_match = re.search(r'-jar\s+"?([^"\s]+\.jar)"?', content)
                if direct_match:
                    jar_path = server_dir / direct_match.group(1)
                    if jar_path.exists():
                        logger.info(f"从 run.sh 找到服务器 jar: {jar_path}")
                        return jar_path
            except Exception:
                pass

        # forge-*.jar（旧版 Forge）
        forge_jars = list(server_dir.glob("forge-*.jar"))
        if forge_jars:
            logger.info(f"找到 Forge 服务器 jar: {forge_jars[0]}")
            return forge_jars[0]

        # server-*.jar（vanilla 或整合包开服）
        server_jars = list(server_dir.glob("server-*.jar"))
        if server_jars:
            logger.info(f"找到服务器 jar: {server_jars[0]}")
            return server_jars[0]

        # 最后尝试任何 *.jar
        all_jars = [j for j in server_dir.glob("*.jar") if j.name not in ("fabric-installer.jar", "quilt-installer.jar", "forge-installer.jar", "neoforge-installer.jar")]
        if all_jars:
            logger.info(f"使用目录中的 jar: {all_jars[0]}")
            return all_jars[0]

        return None

    def _download_vanilla_server_jar(self, mc_version: str, server_dir: Path, filename: str = "") -> Tuple[bool, str]:
        """下载 vanilla 服务器 jar 到指定目录

        Args:
            mc_version: Minecraft 版本号
            server_dir: 服务器目录
            filename: 输出文件名，默认为 server-{mc_version}.jar
        """
        import json
        import requests as req

        version_json_path = Path(self.minecraft_dir) / "versions" / mc_version / f"{mc_version}.json"
        if not version_json_path.exists():
            return False, f"版本 JSON 不存在: {version_json_path}"

        with open(version_json_path, "r", encoding="utf-8") as f:
            vdata = json.load(f)

        downloads = vdata.get("downloads", {})
        server_info = downloads.get("server")
        if not server_info:
            return False, f"版本 {mc_version} 不支持服务器端 (无 server downloads)"

        server_jar_url = server_info.get("url")
        if not server_jar_url:
            return False, f"无法获取服务器 jar 下载链接"

        server_jar = server_dir / (filename or f"server-{mc_version}.jar")
        logger.info(f"正在下载服务器 jar: {server_jar_url} -> {server_jar}")
        self._set_status(f"正在下载服务器 {mc_version} jar ...")
        resp = req.get(server_jar_url, stream=True)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(server_jar, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    self._set_progress(downloaded)
                    self._set_max(total)
        logger.info(f"服务器 jar 下载完成: {server_jar}")
        return True, ""

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
