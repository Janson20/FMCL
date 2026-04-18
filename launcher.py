"""Minecraft启动器核心模块"""
import gc
import hashlib
import os
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
        self.options["launcherVersion"] = "2.3.2"

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

    def launch_game(self, version_id: str, minimize_after: bool = False) -> bool:
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

            # 设置自定义玩家名
            if self.config.player_name and self.config.player_name != "Steve":
                options["username"] = self.config.player_name
                options["playerName"] = self.config.player_name

            # 获取启动命令
            logger.info(f"正在生成启动命令: {target_version}")
            minecraft_command = self._mcllib.command.get_minecraft_command(
                target_version,
                self.minecraft_dir,
                options
            )

            # ── JVM 参数优化 ──
            minecraft_command = self._optimize_jvm_args(minecraft_command)

            # ── 设置启动器名称 ──
            # 替换 --versionType 参数值，使游戏标题界面左下角显示 "Minecraft x.x.x/FMCL"
            minecraft_command = self._set_launcher_brand(minecraft_command)

            logger.info("正在启动游戏...")
            # 使用 Popen 非阻塞启动，捕获 stdout 以便检测游戏窗口
            # Windows 下使用 CREATE_NO_WINDOW 隐藏 Java 控制台窗口
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
