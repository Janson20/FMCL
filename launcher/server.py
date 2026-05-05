"""Minecraft启动器 - 服务器管理模块"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable, Any

from logzero import logger

from structured_logger import slog
from validation import validate_version_id, validate_server_ip, validate_server_port, validate_memory


class ServerMixin:
    """服务器管理 Mixin 类"""

    _java_scan_cache: Optional[List] = None
    _java_scan_cache_time: float = 0.0

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

    def _get_cached_java_runtimes(self) -> List:
        import time
        now = time.time()
        if self._java_scan_cache is not None and (now - self._java_scan_cache_time) < 30:
            return self._java_scan_cache
        from launcher.java_scanner import scan_all
        self._java_scan_cache = scan_all(self.minecraft_dir)
        self._java_scan_cache_time = now
        return self._java_scan_cache

    def _find_runtime_java(self, version_id: str) -> str:
        """
        查找适合指定 Minecraft 版本的 Java 运行时

        查找优先级:
        0. 自定义路径（java_mode == "custom"）
        1. Minecraft runtime 目录（mcllib 下载的版本匹配 Java）
        2. 系统安装的 Java（通过 java_scanner 扫描，java_mode != "auto" 时优先）
        3. mcllib 的 get_java_executable() 回退
        4. 最终回退到 "java"

        Args:
            version_id: Minecraft 版本号

        Returns:
            Java 可执行文件路径，找不到则返回 "java"
        """
        java_mode = getattr(self.config, 'java_mode', 'auto')
        custom_path = getattr(self.config, 'java_custom_path', None)

        # 0. 自定义路径优先
        if java_mode == "custom" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用自定义 Java 路径: {custom_path}")
            return custom_path

        if java_mode == "scan" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用扫描选择的 Java 路径: {custom_path}")
            return custom_path

        # 1. 从版本 JSON 获取 javaVersion.component，优先使用 Minecraft runtime
        if java_mode == "auto":
            version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
            jvm_component = None
            if version_json_path.exists():
                try:
                    with open(version_json_path, "r", encoding="utf-8") as f:
                        vdata = json.load(f)
                    jvm_component = vdata.get("javaVersion", {}).get("component")
                except Exception:
                    pass

            if jvm_component:
                java_path = self._mcllib.runtime.get_executable_path(jvm_component, self.minecraft_dir)
                if java_path:
                    logger.info(f"从 runtime 找到 Java ({jvm_component}): {java_path}")
                    return java_path

            # 2. 回退：遍历 mcllib 已安装的 runtime，选最新的
            try:
                installed_runtimes = self._mcllib.runtime.get_installed_jvm_runtimes(self.minecraft_dir)
                if installed_runtimes:
                    latest = sorted(installed_runtimes, key=lambda r: r.get("version", {}).get("name", ""), reverse=True)
                    component = latest[0].get("name", "")
                    if component:
                        java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                        if java_path:
                            logger.info(f"从已安装 runtime 找到 Java ({component}): {java_path}")
                            return java_path
            except Exception:
                pass

        # 3. 使用 java_scanner 扫描系统 Java，推荐最佳匹配
        try:
            from launcher.java_scanner import recommend_for_mc
            javas = self._get_cached_java_runtimes()
            if javas:
                best = recommend_for_mc(javas, version_id)
                if best:
                    logger.info(f"从系统扫描找到最佳 Java: {best.display_name}")
                    return best.path
        except Exception as e:
            logger.debug(f"java_scanner 推荐失败: {e}")

        # 4. mcllib 回退
        logger.warning("未在 runtime 和系统扫描中找到 Java，回退到系统 Java")
        try:
            return self._mcllib.utils.get_java_executable()
        except Exception:
            return "java"

    def scan_system_java(self) -> List[Dict]:
        from launcher.java_scanner import get_java_summary
        javas = self._get_cached_java_runtimes()
        return get_java_summary(javas)

    def get_java_suggestion(self, version_id: str) -> Optional[Dict]:
        from launcher.java_scanner import recommend_for_mc, _min_java_for_mc
        from launcher.java_install import get_java_install_guidance

        javas = self._get_cached_java_runtimes()
        best = recommend_for_mc(javas, version_id)
        if best:
            return {
                "found": True,
                "path": best.path,
                "home": best.home,
                "major_version": best.major_version,
                "version_str": best.version_str,
            }

        min_java = _min_java_for_mc(version_id)
        guidance = get_java_install_guidance(min_java)
        return {
            "found": False,
            "required_java": min_java,
            "download_url": guidance.get("download_url"),
            "install_command": guidance.get("install_command"),
        }

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
        # 验证版本ID合法性
        if not validate_version_id(version_id):
            logger.error(f"非法版本ID格式: {version_id}")
            return False, version_id

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

            java_path = self._find_runtime_java(version_id)
            if java_path == "java" or not os.path.isfile(java_path):
                suggestion = self.get_java_suggestion(version_id)
                if suggestion and not suggestion.get("found"):
                    req_java = suggestion.get("required_java", 17)
                    dl_url = suggestion.get("download_url") or ""
                    install_cmd = suggestion.get("install_command") or ""
                    logger.warning(
                        f"未找到合适的 Java {req_java}+ 用于服务器 {version_id}。"
                        f"请安装 JDK {req_java}。"
                        f"下载: {dl_url}  安装命令: {install_cmd}"
                    )
                    self._set_status(
                        f"⚠ 未找到 Java {req_java}+，服务器可能无法启动。"
                        f"请安装 JDK {req_java}"
                    )
            else:
                logger.info(f"Java 运行时就绪: {java_path}")

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
            slog.info("server_installed", server_version=version_id, eula_accepted=True)
            return True, version_id

        except Exception as e:
            logger.error(f"安装服务器失败: {str(e)}")
            slog.error("server_install_failed", server_version=version_id, error=str(e)[:200])
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
        # 验证内存参数合法性
        if not validate_memory(max_memory):
            logger.error(f"非法内存参数格式: {max_memory}")
            return False, None

        try:
            server_dir = self._get_server_versions_dir() / version_id
            if not server_dir.exists():
                logger.error(f"服务器版本未安装: {version_id}")
                return False, None

            # 解析服务器类型
            _server_type = "vanilla"
            _vl = version_id.lower()
            if "forge" in _vl:
                _server_type = "forge"
            elif "fabric" in _vl:
                _server_type = "fabric"
            elif "neoforge" in _vl:
                _server_type = "neoforge"

            # 统计 mods 数量
            _mods_dir = server_dir / "mods"
            _mods_count = 0
            if _mods_dir.exists():
                _mods_count = len([f for f in _mods_dir.iterdir() if f.suffix == ".jar" and not f.name.endswith(".disabled")])

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

            # NeoForge / Forge 服务器：优先使用 run.bat 或 run.sh 启动
            run_script = server_dir / ("run.bat" if sys.platform == 'win32' else "run.sh")
            if run_script.exists():
                env = os.environ.copy()
                env["JAVA_OPTS"] = f"-Xmx{max_memory} -Xms{max_memory}"
                cmd = [str(run_script)]
                logger.info(f"使用启动脚本: {run_script}")
                popen_kwargs = dict(
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    bufsize=1,
                    cwd=str(server_dir),
                    env=env,
                )
                if sys.platform == 'win32':
                    popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                process = subprocess.Popen(cmd, **popen_kwargs)
                self._server_process = process
                logger.info(f"服务器 {version_id} 已启动 (PID: {process.pid})")
                slog.info("server_start_attempt", server_version=version_id, server_type=_server_type,
                          java_path=str(run_script), eula_accepted=True, mods_count=_mods_count,
                          config_valid=True)
                return True, process

            # 回退：查找服务器 jar 并直接启动（原版 / Fabric / 无启动脚本的 Forge）
            server_jar = self._find_server_jar(server_dir)
            if not server_jar:
                logger.error(f"在 {server_dir} 中未找到服务器 jar")
                return False, None

            # 从 runtime 查找 Java
            java_path = self._find_runtime_java(version_id)

            if java_path == "java" or not os.path.isfile(java_path):
                try:
                    from launcher.java_scanner import recommend_for_mc
                    javas = self._get_cached_java_runtimes()
                    if javas:
                        best = recommend_for_mc(javas, version_id)
                        if best:
                            java_path = best.path
                            logger.info(f"从系统扫描选择 Java: {best.display_name}")
                except Exception as e:
                    logger.debug(f"扫描器 Java 推荐失败: {e}")

            if java_path == "java" or not os.path.isfile(java_path):
                suggestion = self.get_java_suggestion(version_id)
                if suggestion and not suggestion.get("found"):
                    req_java = suggestion.get("required_java", 17)
                    dl_url = suggestion.get("download_url") or ""
                    install_cmd = suggestion.get("install_command") or ""
                    logger.warning(
                        f"未找到合适的 Java {req_java}+ 用于服务器 {version_id}。"
                        f"请安装 JDK {req_java}。"
                        f"下载: {dl_url}  安装命令: {install_cmd}"
                    )

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
            slog.info("server_start_attempt", server_version=version_id, server_type=_server_type,
                      java_path=java_path, eula_accepted=True, mods_count=_mods_count,
                      config_valid=True, jvm_args=[f"-Xmx{max_memory}", f"-Xms{max_memory}"])
            return True, process

        except Exception as e:
            logger.error(f"启动服务器失败: {str(e)}")
            slog.error("server_start_failed", server_version=version_id, server_type=_server_type,
                       error=str(e)[:200])
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
