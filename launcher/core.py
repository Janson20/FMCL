"""Minecraft启动器核心模块"""

import gc
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from config import Config
from mirror import MirrorSource
from structured_logger import slog
from ui.constants import USER_AGENT
from ui.theme_engine import Theme, get_theme_engine, init_theme_engine
from validation import validate_server_ip, validate_server_port, validate_version_id
from version_utils import (
    InstanceInfo,
    has_mod_loader_from_json,
    parse_instance_from_json,
    parse_mc_version_from_dir,
    parse_mc_version_from_id,
    parse_mod_loader_from_version,
    resolve_version_jar_path,
)


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


def _read_project_version(pyproject_path: Path) -> str:
    """读取 pyproject.toml 中的项目版本号。

    优先使用 tomllib(Python 3.11+) 或 tomli 解析；若运行环境缺少 toml
    解析库（例如 Python 3.10 未安装 tomli），则降级为正则从 [project]
    段提取版本号，避免因无法读取版本号而导致启动器核心初始化失败。
    """
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(pyproject_path, "rb") as _f:
            return tomllib.load(_f)["project"]["version"]
    except Exception as e:
        logger.warning(f"toml 解析失败，改用正则提取版本号: {e}")
        try:
            import re

            text = Path(pyproject_path).read_text(encoding="utf-8")
            # 仅在 [project] 段内匹配 version 字段，避免误取其他段的 version
            section = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
            scope = section.group(1) if section else text
            m = re.search(r"""(?m)^\s*version\s*=\s*["']([^"']+)["']""", scope)
            if m:
                return m.group(1)
        except Exception as e2:
            logger.warning(f"正则提取版本号失败: {e2}")
        return "unknown"


class MinecraftLauncher:
    """Minecraft启动器类"""

    _java_scan_cache: Optional[List] = None
    _java_scan_cache_time: float = 0.0

    def __init__(self, config: Config):
        self.config = config
        self.minecraft_dir = str(config.minecraft_dir)

        logger.info("MinecraftLauncher.__init__: 1. 正在导入 minecraft_launcher_lib...")
        import minecraft_launcher_lib

        logger.info("MinecraftLauncher.__init__: 2. minecraft_launcher_lib 导入完成")
        self._mcllib = minecraft_launcher_lib
        self.options = minecraft_launcher_lib.utils.generate_test_options()
        logger.info("MinecraftLauncher.__init__: 3. generate_test_options 完成")
        self.options["launcherName"] = "FMCL"
        _pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        logger.info(f"MinecraftLauncher.__init__: 4. pyproject.toml 路径: {_pyproject}")
        self.options["launcherVersion"] = _read_project_version(_pyproject)
        logger.info("MinecraftLauncher.__init__: 5. pyproject.toml 读取完成")

        self.current_max = 0

        # 实例信息缓存: {文件夹名 → InstanceInfo}
        # 参考 PCL-CE: mcInstanceList 和 PCL.ini 缓存
        self._instance_info_cache: Dict[str, InstanceInfo] = {}
        self._instance_cache_valid: bool = False

        # 账号系统引用（由外部设置）
        self._account_system = None

        # UI回调 (可选,用于进度更新)
        self.on_progress: Optional[Callable[[int, int, str], None]] = None

        # 初始化镜像源
        logger.info("MinecraftLauncher.__init__: 6. 正在初始化 MirrorSource...")
        self._mirror = MirrorSource(enabled=config.mirror_enabled)
        logger.info("MinecraftLauncher.__init__: 7. MirrorSource 初始化完成，正在应用补丁...")
        self._apply_mirror_patch()
        logger.info("MinecraftLauncher.__init__: 8. 正在初始化主题引擎...")
        engine = init_theme_engine(str(config.base_dir))
        saved_theme = engine.load_theme(config.theme_name)
        if saved_theme:
            engine.apply_theme(saved_theme, config.accent_color)
        logger.info("MinecraftLauncher.__init__: 9. 初始化完成")

    def set_account_system(self, account_system):
        self._account_system = account_system

    def _get_cached_java_runtimes(self) -> List:
        import time

        now = time.time()
        if self._java_scan_cache is not None and (now - self._java_scan_cache_time) < 30:
            return self._java_scan_cache
        from launcher.java_scanner import scan_all

        self._java_scan_cache = scan_all(self.minecraft_dir)
        self._java_scan_cache_time = now
        return self._java_scan_cache

    def _resolve_java_executable(self, target_version: str, current_java: str) -> str:
        java_mode = getattr(self.config, "java_mode", "auto")
        custom_path = getattr(self.config, "java_custom_path", None)

        if java_mode == "custom" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用自定义 Java 路径: {custom_path}")
            return custom_path

        if java_mode == "scan" and custom_path and os.path.isfile(custom_path):
            logger.info(f"使用扫描选择的 Java 路径: {custom_path}")
            return custom_path

        if current_java and os.path.isfile(current_java):
            return current_java
        if os.sep in current_java or ("/" in current_java and platform.system().lower() != "windows"):
            return current_java

        try:
            from launcher.java_scanner import recommend_for_mc

            javas = self._get_cached_java_runtimes()
            # 提前检测加载器类型，Minecraft runtime 查找也需要
            _loader_type = self._detect_mod_loader_type(target_version)
            logger.info(f"_resolve_java_executable: target_version={target_version}, _loader_type={_loader_type}")
            if javas:

                best = None

                if _loader_type == "cleanroom":
                    _min_java = self._get_cleanroom_min_java(target_version)
                    _java_versions = [j for j in javas if j.major_version >= _min_java]
                    if _java_versions:
                        best = min(_java_versions, key=lambda j: j.major_version)
                        logger.info(f"Cleanroom 需要 Java {_min_java}+，选择: {best.display_name}")
                        return best.path
                    # 系统扫描未找到合适的 Java，跳过 recommend_for_mc（会推荐 Java 8）
                    # 继续往下尝试 Minecraft runtime
                elif self._is_mmc_instance(target_version):
                    _min_java = self._get_mmc_min_java(target_version) or 0
                    if _min_java:
                        _java_versions = [j for j in javas if j.major_version >= _min_java]
                        if _java_versions:
                            best = min(_java_versions, key=lambda j: j.major_version)
                            logger.info(f"MMC 实例需要 Java {_min_java}+，选择: {best.display_name}")
                            return best.path
                        # 未找到合适 Java，继续尝试 Minecraft runtime
                        logger.warning(f"系统未找到 Java {_min_java}+，继续尝试 Minecraft runtime...")
                else:
                    best = recommend_for_mc(javas, target_version)
                if best:
                    logger.info(f"从系统扫描选择最佳 Java: {best.display_name}")
                    return best.path
        except Exception as e:
            logger.debug(f"Java 扫描器推荐失败: {e}")

        try:
            installed_runtimes = self._mcllib.runtime.get_installed_jvm_runtimes(self.minecraft_dir)
            if installed_runtimes:
                # installed_runtimes 是组件名称字符串列表，如 ["java-runtime-delta", "jre-legacy"]
                # 按组件名排序（更高版本排在后面），取最新的
                _sorted_runtimes = sorted(installed_runtimes)
                component = _sorted_runtimes[-1] if _sorted_runtimes else ""
                if component:
                    java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                    if java_path and os.path.isfile(java_path):
                        # 对 Cleanroom / MMC 检查 Java 版本是否满足要求
                        _skip_runtime = False
                        _min_java = 0
                        if _loader_type == "cleanroom":
                            _min_java = self._get_cleanroom_min_java(target_version)
                        elif self._is_mmc_instance(target_version):
                            _min_java = self._get_mmc_min_java(target_version) or 0

                        if _min_java > 0:
                            try:
                                import re as _re

                                result = subprocess.run(
                                    [java_path, "-version"], capture_output=True, text=True, timeout=10
                                )
                                _ver_output = result.stderr or result.stdout
                                _m = _re.search(r'version "(\d+)', _ver_output)
                                if _m:
                                    _runtime_major = int(_m.group(1))
                                    if _runtime_major < _min_java:
                                        logger.warning(
                                            f"Minecraft runtime Java ({component}) 版本 {_runtime_major} < {_min_java}，不满足 Cleanroom 要求，跳过"
                                        )
                                        component = ""  # 不满足，清空以继续后续逻辑
                                    else:
                                        logger.info(
                                            f"Minecraft runtime Java ({component}) 版本 {_runtime_major} 满足 Cleanroom 要求"
                                        )
                            except Exception:
                                pass
                        if component:
                            logger.info(f"从 Minecraft runtime 找到 Java ({component}): {java_path}")
                            return java_path
        except Exception as e:
            logger.debug(f"Minecraft runtime Java 查找失败: {e}")

        return current_java

    def _ensure_java_runtime(self, version_id: str) -> str:
        current = self._resolve_java_executable(version_id, "java")
        if current != "java" and os.path.isfile(current):
            return current

        # ── Cleanroom: 安装正确的 Java 运行时（非 vanilla 1.12.2 的 Java 8） ──
        # 参考 HMCL GameJavaVersion.getCleanroomJavaVersion():
        # - Cleanroom < 0.5.0 → java-runtime-delta (Java 21)
        # - Cleanroom >= 0.5.0 → java-runtime-epsilon (Java 25)
        _loader_type = self._detect_mod_loader_type(version_id)
        if _loader_type == "cleanroom":
            _min_java = self._get_cleanroom_min_java(version_id)
            _jvm_component = "java-runtime-epsilon" if _min_java >= 25 else "java-runtime-delta"
            self._set_status(f"正在为 Cleanroom 安装 Java {_min_java} 运行时...")
            logger.info(f"Cleanroom: 安装 JVM 运行时 {_jvm_component} (Java {_min_java})")
            try:
                self._mcllib.runtime.install_jvm_runtime(
                    _jvm_component, self.minecraft_dir, callback=self._get_callback()
                )
                # 安装完成，重新解析 Java 路径
                current = self._resolve_java_executable(version_id, "java")
                if current != "java" and os.path.isfile(current):
                    self._set_status(f"Java {_min_java} 运行时就绪: {current}")
                    return current
            except Exception as e:
                logger.error(f"安装 Cleanroom JVM 运行时失败: {e}")
            return "java"

        mc_base = self._extract_mc_version(version_id)
        version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
        if not version_json_path.exists():
            logger.info(f"版本 {mc_base} 未安装，正在安装以获取 Java runtime...")
            self._set_status(f"正在安装 {mc_base}（自动获取 Java runtime）...")
            self._mcllib.install.install_minecraft_version(mc_base, self.minecraft_dir, callback=self._get_callback())

        current = self._resolve_java_executable(version_id, "java")
        if current != "java" and os.path.isfile(current):
            self._set_status(f"Java runtime 就绪: {current}")
            return current

        logger.error(f"无法为 {version_id} 自动安装 Java runtime")
        return "java"

    @staticmethod
    def _extract_mc_version(version_id: str) -> str:
        """从版本 ID 提取 Minecraft 游戏版本号

        优先读取版本 JSON 文件解析，回退到版本 ID 字符串匹配。

        参考 PCL-CE: McInstanceInfo 的版本识别逻辑。
        支持 Forge/Fabric/Quilt/NeoForge 及新旧 MC 版本格式。
        """
        return parse_mc_version_from_id(version_id) or version_id

    def scan_system_java(self) -> List[Dict]:
        from launcher.java_scanner import get_java_summary

        javas = self._get_cached_java_runtimes()
        return get_java_summary(javas)

    def get_java_suggestion(self, version_id: str) -> Optional[Dict]:
        from launcher.java_install import get_java_install_guidance
        from launcher.java_scanner import _min_java_for_mc, recommend_for_mc

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
        return getattr(self.config, "java_mode", "auto")

    def set_java_mode(self, mode: str) -> None:
        self.config.java_mode = mode
        self.config.save_config()
        logger.info(f"Java 选择模式已切换为: {mode}")

    def get_java_custom_path(self) -> Optional[str]:
        return getattr(self.config, "java_custom_path", None)

    def set_java_custom_path(self, path: Optional[str]) -> None:
        self.config.java_custom_path = path
        self.config.save_config()
        logger.info(f"自定义 Java 路径已设置: {path}")

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
        """进度回调（节流：大量文件下载时避免高频回调导致UI卡死）"""
        if self.current_max != 0:
            now = time.time()
            last = getattr(self, "_last_progress_time", 0)
            if progress != self.current_max and now - last < 0.1:
                return
            self._last_progress_time = now
            logger.debug(f"进度: {progress}/{self.current_max}")
            if self.on_progress:
                self.on_progress(progress, self.current_max, "")

    def _set_max(self, new_max: int) -> None:
        """设置最大值回调"""
        self.current_max = new_max
        logger.info(f"总任务数: {new_max}")

    def _get_callback(self) -> Dict[str, Callable]:
        """获取回调函数字典"""
        return {"setStatus": self._set_status, "setProgress": self._set_progress, "setMax": self._set_max}

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
                    latest_release, self.minecraft_dir, callback=self._get_callback()
                )
                logger.info("正式版下载成功")
            except Exception as e:
                logger.error(f"下载初始版本失败: {str(e)}")
                raise
        else:
            logger.info("文件夹检查完成")

    def get_available_versions(self) -> List[Dict[str, str]]:
        """获取可用版本列表

        对上覆 get_version_list() 中的 releaseTime 缺失做防御处理，
        避免 Mojang 版本清单中偶发的数据异常导致整个列表获取失败。
        """
        try:
            versions = self._mcllib.utils.get_available_versions(self.minecraft_dir)
            logger.info(f"获取到 {len(versions)} 个版本")
            return versions
        except Exception as e:
            logger.error(f"获取版本列表失败 (upstream): {str(e)}，回退到安全实现")
            return self._get_available_versions_safe()

    def _get_available_versions_safe(self) -> List[Dict[str, str]]:
        """安全版获取可用版本列表 — 过滤缺失 releaseTime 的异常条目"""
        try:
            import json
            from datetime import datetime

            import requests as _req

            # 直接从 Mojang API 获取版本清单，不使用上游缓存（避开破损缓存）
            manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
            resp = _req.get(manifest_url, timeout=30, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                logger.warning(f"获取版本清单失败 HTTP {resp.status_code}")
                return []
            vlist = resp.json()

            version_list = []
            skipped = 0
            for entry in vlist.get("versions", []):
                try:
                    version_list.append(
                        {
                            "id": entry["id"],
                            "type": entry.get("type", "release"),
                            "releaseTime": datetime.fromisoformat(entry["releaseTime"]),
                            "complianceLevel": entry.get("complianceLevel", 0),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    skipped += 1

            if skipped:
                logger.warning(f"过滤了 {skipped} 个缺少 releaseTime 的异常版本条目")

            logger.info(f"安全模式获取到 {len(version_list)} 个版本")
            return version_list
        except Exception as e:
            logger.error(f"安全回退也失败: {str(e)}")
            return []

    def get_installed_versions(self) -> List[InstanceInfo]:
        """获取已安装的版本列表（返回结构化实例信息）

        遍历 .minecraft/versions/ 目录，读取每个实例的版本 JSON，
        解析为 InstanceInfo 对象。

        参考 PCL-CE: InitMcInstanceList() 和 McInstance 类。

        Returns:
            InstanceInfo 列表，按文件夹名排序
        """
        try:
            versions_dir = self.config.get_versions_dir()
            if not versions_dir.exists():
                self._instance_info_cache = {}
                self._instance_cache_valid = False
                return []

            # 获取当前目录列表用于缓存校验
            current_folders = set()
            for v in os.listdir(str(versions_dir)):
                vp = versions_dir / v
                if vp.is_dir() and v not in ("jre_manifest.json", "version_manifest_v2.json"):
                    current_folders.add(v)

            # 缓存校验：检查缓存是否与当前目录一致
            if self._instance_cache_valid and set(self._instance_info_cache.keys()) == current_folders:
                return sorted(self._instance_info_cache.values(), key=lambda x: x.folder_name)

            # 重建缓存
            self._instance_info_cache = {}
            for folder_name in current_folders:
                info = self._read_instance_info(folder_name)
                if info is None:
                    info = InstanceInfo(folder_name=folder_name, state="error", reliable=False)
                self._instance_info_cache[folder_name] = info

            self._instance_cache_valid = True
            result = sorted(self._instance_info_cache.values(), key=lambda x: x.folder_name)
            logger.info(f"已安装 {len(result)} 个版本（从 JSON 解析）")
            return result

        except Exception as e:
            logger.error(f"获取已安装版本失败: {str(e)}")
            self._instance_cache_valid = False
            return []

    def get_installed_version_ids(self) -> List[str]:
        """获取已安装的版本 ID 列表（向后兼容旧接口）

        Returns:
            版本 ID（文件夹名）字符串列表
        """
        instances = self.get_installed_versions()
        return [i.folder_name for i in instances]

    def _read_instance_info(self, folder_name: str) -> Optional[InstanceInfo]:
        """读取单个实例的 JSON 并解析为 InstanceInfo

        Args:
            folder_name: 实例文件夹名

        Returns:
            InstanceInfo 或 None（读取失败）
        """
        versions_dir = self.config.get_versions_dir()
        json_path = versions_dir / folder_name / f"{folder_name}.json"
        if not json_path.exists():
            # 尝试查找目录下唯一的 JSON 文件
            version_dir = versions_dir / folder_name
            if version_dir.exists():
                try:
                    json_files = list(version_dir.glob("*.json"))
                    if len(json_files) == 1:
                        json_path = json_files[0]
                    else:
                        return None
                except Exception:
                    return None
            else:
                return None

        try:
            json_text = json_path.read_text(encoding="utf-8")
            return parse_instance_from_json(json_text, folder_name, self.minecraft_dir)
        except Exception as e:
            logger.debug(f"解析实例 JSON 失败 ({folder_name}): {e}")
            return None

    def get_instance_info(self, version_id: str) -> Optional[InstanceInfo]:
        """获取单个版本的实例信息（优先从缓存读取）

        Args:
            version_id: 版本 ID（文件夹名）

        Returns:
            InstanceInfo 或 None
        """
        if version_id in self._instance_info_cache:
            return self._instance_info_cache[version_id]
        info = self._read_instance_info(version_id)
        if info is not None:
            self._instance_info_cache[version_id] = info
        return info

    def invalidate_instance_cache(self):
        """使实例信息缓存失效，下次调用 get_installed_versions() 会重新解析"""
        self._instance_cache_valid = False
        self._instance_info_cache = {}
        logger.debug("实例信息缓存已失效")

    @staticmethod
    def get_supported_loaders() -> Dict[str, bool]:
        """获取所有支持的模组加载器列表

        返回 PCL-CE 支持的所有加载器类型及其是否可安装的标志。

        参考 PCL-CE: McInstanceState 枚举。

        Returns:
            {loader_name: installable} 字典
            installable=True 的加载器可通过 install_mod_loader() 安装
            installable=False 的加载器仅支持检测，需外部安装工具
        """
        return {
            "Forge": True,
            "Fabric": True,
            "NeoForge": True,
            "Quilt": True,
            "LiteLoader": True,
            "LegacyFabric": True,
            "Cleanroom": True,
            "OptiFine": True,
            "LabyMod": False,
        }

    def rename_instance(self, old_name: str, new_name: str) -> Tuple[bool, str]:
        """重命名 Minecraft 实例

        重命名 versions/{old_name}/ 文件夹及其中的 JSON 文件，
        同时更新 JSON 中的 id 字段。

        参考 PCL-CE: 版本重命名通过重命名文件夹实现。

        Args:
            old_name: 旧实例名称
            new_name: 新实例名称

        Returns:
            (是否成功, 消息) 元组
        """
        import json
        import shutil

        if not old_name or not new_name:
            return False, "rename_instance_invalid"

        # 验证新名称合法性（只允许字母数字下划线短横线点号）
        if not re.match(r"^[a-zA-Z0-9_.\-+]+$", new_name):
            return False, "rename_instance_invalid"

        versions_dir = self.config.get_versions_dir()
        old_dir = versions_dir / old_name
        new_dir = versions_dir / new_name

        if not old_dir.exists():
            return False, f"实例 '{old_name}' 不存在"

        if new_dir.exists():
            return False, "rename_instance_exists"

        # 找到要重命名的 JSON 文件
        old_json_path = old_dir / f"{old_name}.json"
        if not old_json_path.exists():
            json_files = list(old_dir.glob("*.json"))
            if len(json_files) == 1:
                old_json_path = json_files[0]
            else:
                return False, "找不到实例 JSON 文件"

        new_json_path = new_dir / f"{new_name}.json"

        try:
            # 1. 更新 JSON 中的 id 字段
            json_data = json.loads(old_json_path.read_text(encoding="utf-8"))
            json_data["id"] = new_name

            # 2. 创建新目录
            os.makedirs(str(new_dir), exist_ok=False)

            # 3. 移动所有非 JSON 文件（jar、natives 等）
            for item in os.listdir(str(old_dir)):
                src = old_dir / item
                dst = new_dir / item.name
                if src.is_file() and src.name != old_json_path.name:
                    shutil.move(str(src), str(dst))
                elif src.is_dir() and src.name != new_name:
                    shutil.move(str(src), str(dst))

            # 4. 写入新的 JSON 文件
            new_json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

            # 5. 删除旧 JSON
            old_json_path.unlink()

            # 6. 如果旧目录为空，删除
            if old_dir.exists():
                try:
                    remaining = list(old_dir.iterdir())
                    if not remaining:
                        old_dir.rmdir()
                    else:
                        for leftover in remaining:
                            if leftover.is_file():
                                leftover.unlink()
                            elif leftover.is_dir():
                                shutil.rmtree(str(leftover))
                        if not any(old_dir.iterdir()):
                            old_dir.rmdir()
                except Exception as e:
                    logger.debug(f"清理旧目录失败 (不影响重命名): {e}")

            # 7. 重命名版本 JSON（如果存在独立 JSON 文件）
            version_json = versions_dir / f"{old_name}.json"
            if version_json.exists():
                shutil.move(str(version_json), str(versions_dir / f"{new_name}.json"))

            # 8. 失效缓存
            self.invalidate_instance_cache()

            logger.info(f"实例重命名成功: {old_name} → {new_name}")
            slog.info("instance_renamed", old_name=old_name, new_name=new_name)
            return True, new_name

        except FileExistsError:
            return False, "rename_instance_exists"
        except Exception as e:
            logger.error(f"重命名实例失败 ({old_name} → {new_name}): {e}")
            # 尝试回滚
            try:
                if new_dir.exists():
                    shutil.rmtree(str(new_dir))
            except Exception:
                pass
            return False, str(e)

    def install_version(self, version_id: str, mod_loader: str = "无") -> Tuple[bool, str]:
        """
        安装Minecraft版本

        安装逻辑：
        - 无模组加载器: 仅安装原版 Minecraft
        - 有模组加载器: 原版安装和模组加载器安装并行等待（线程 A 安装原版，
          线程 B 等待原版完成后调用 `install_mod_loader()`），
          避免并发下载同一主机（`resources.download.minecraft.net`）导致 SSL 连接池耗尽，
          同时保持安装流程清晰、复用经过充分测试的安装路径。

        Args:
            version_id: 版本ID (如 "1.20.4" 或 "26.1")
            mod_loader: 模组加载器 ("无", "Forge", "Fabric", "NeoForge")

        Returns:
            (是否成功, 安装后的版本ID) 元组
            安装原版时返回 version_id
            安装模组加载器时返回 loader 创建的版本ID (如 "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0")
        """
        # 验证版本ID合法性
        if not validate_version_id(version_id):
            logger.error(f"非法版本ID格式: {version_id}")
            return False, version_id

        # ── 插件钩子: version.pre_install ──
        self._emit_plugin_hook("version.pre_install", version_id=version_id, mod_loader=mod_loader)

        try:
            # 检查版本是否有效 — 用 set 实现 O(1) 查找
            available_versions = self.get_available_versions()
            version_ids = {v["id"].split()[0] if isinstance(v["id"], str) else v["id"] for v in available_versions}

            if version_id not in version_ids:
                logger.error(f"无效的版本ID: {version_id}")
                return False, version_id

            if mod_loader and mod_loader != "无":
                logger.info(f"正在并行安装 {mod_loader} for Minecraft {version_id}")

                vanilla_done = threading.Event()
                vanilla_error = [None]
                loader_result = [None]
                loader_error = [None]

                def _install_vanilla():
                    try:
                        self._mcllib.install.install_minecraft_version(
                            version_id, self.minecraft_dir, callback=self._get_callback()
                        )
                        vanilla_done.set()
                    except Exception as e:
                        vanilla_error[0] = e
                        vanilla_done.set()

                def _install_loader():
                    try:
                        vanilla_done.wait()

                        from downloader import install_mod_loader as _install_mod_loader

                        java_path = self._resolve_java_executable(version_id, "java")
                        if java_path == "java" or not os.path.isfile(java_path):
                            java_path = self._ensure_java_runtime(version_id)

                        result = _install_mod_loader(
                            loader=mod_loader,
                            version=version_id,
                            minecraft_dir=self.minecraft_dir,
                            num_threads=self.config.download_threads,
                            mirror=self._mirror,
                            callback=self._get_callback(),
                            java=java_path if java_path != "java" and os.path.isfile(java_path) else None,
                        )
                        loader_result[0] = result
                    except Exception as e:
                        loader_error[0] = e

                t1 = threading.Thread(target=_install_vanilla, daemon=True)
                t2 = threading.Thread(target=_install_loader, daemon=True)
                t1.start()
                t2.start()
                t2.join()

                if loader_error[0]:
                    raise loader_error[0]

                installed_version_id, loader_version = loader_result[0]

                if vanilla_error[0]:
                    logger.warning(f"原版安装失败（模组加载器安装已自行处理）: {vanilla_error[0]}")

                logger.info(f"安装完成: {installed_version_id} (Loader: {mod_loader} {loader_version})")
                slog.info(
                    "version_installed",
                    version=version_id,
                    loader=mod_loader,
                    installed_version_id=installed_version_id,
                    loader_version=loader_version,
                )
                self._emit_plugin_hook("version.post_install", version_id=installed_version_id, success=True)
                self.invalidate_instance_cache()
                return True, installed_version_id
            else:
                # 仅安装原版 Minecraft
                logger.info(f"正在安装 Minecraft {version_id}")
                self._mcllib.install.install_minecraft_version(
                    version_id, self.minecraft_dir, callback=self._get_callback()
                )
                logger.info(f"Minecraft {version_id} 安装成功")
                slog.info("version_installed", version=version_id, loader="vanilla", installed_version_id=version_id)
                self._emit_plugin_hook("version.post_install", version_id=version_id, success=True)
                self.invalidate_instance_cache()
                return True, version_id

        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            slog.error(
                "version_install_failed",
                version=version_id,
                loader=mod_loader if mod_loader != "无" else "vanilla",
                error=str(e)[:200],
            )
            self._emit_plugin_hook("version.post_install", version_id=version_id, success=False)
            return False, version_id

    def launch_game(
        self, version_id: str, minimize_after: bool = False, server_ip: str | None = None, server_port: int = 25565
    ) -> bool:
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
        # 验证服务器IP和端口
        if server_ip:
            if not validate_server_ip(server_ip):
                logger.error(f"非法服务器IP格式: {server_ip}")
                return False, None
        if not validate_server_port(server_port):
            logger.error(f"非法服务器端口: {server_port}")
            return False, None

        try:
            # 检查版本是否已安装
            installed_versions = self.get_installed_versions()

            # 用 set 实现 O(1) 查找（提取 folder_name）
            installed_set = {v.folder_name for v in installed_versions}

            # 精确匹配
            if version_id in installed_set:
                target_version = version_id
            else:
                # 尝试模糊匹配：用户可能选了原版ID，但实际安装的是loader版本
                # 例如用户选 "1.20.4"，但安装的是 "1.20.4-forge-49.0.26"
                # 或选 "26.1"，但安装的是 "fabric-loader-0.16.0-26.1"
                # 前缀匹配 (如 Forge/NeoForge: 26.1-forge-xxx)
                # 后缀匹配 (如 Fabric/Quilt: fabric-loader-0.16.0-26.1)
                # 使用 "-" 做边界避免新格式下 "26.1" 错误匹配 "26.1.1"
                all_names = [v.folder_name for v in installed_versions]
                matches = [
                    name
                    for name in all_names
                    if name == version_id or name.startswith(version_id + "-") or name.endswith("-" + version_id)
                ]
                if len(matches) == 1:
                    target_version = matches[0]
                    logger.info(f"模糊匹配: {version_id} -> {target_version}")
                elif len(matches) > 1:
                    # 多个匹配，优先选择带 loader 的版本
                    loader_matches = [name for name in matches if "-" in name and name != version_id]
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
                for subdir in (
                    "mods",
                    "config",
                    "saves",
                    "resourcepacks",
                    "shaderpacks",
                    "screenshots",
                    "crash-reports",
                    "logs",
                ):
                    os.makedirs(os.path.join(version_game_dir, subdir), exist_ok=True)
                options["gameDirectory"] = version_game_dir
                logger.info(f"版本隔离已启用: gameDirectory={version_game_dir}")
            else:
                version_game_dir = None

            # 模组加载器 API 自动下载
            # Fabric → Fabric API, Quilt → QSL, LegacyFabric → Legacy Fabric API
            _loader_type = self._detect_mod_loader_type(target_version)
            _auto_download_configs = {
                "fabric": ("Fabric API", "P7dR8mSH", "fabric"),
                "quilt": ("QSL (Quilt Standard Libraries)", "qvPxCk3h", "quilt"),
                "legacyfabric": ("Legacy Fabric API", "9CJED7xi", "legacyfabric"),
            }
            if _loader_type in _auto_download_configs:
                _display_name, _project_id, _mod_loader = _auto_download_configs[_loader_type]
                _api_prefix = _loader_type.replace("legacyfabric", "legacy-fabric")
                game_dir = options.get("gameDirectory", self.minecraft_dir)
                mods_dir = Path(game_dir) / "mods"
                has_api = False
                if mods_dir.exists():
                    for f in mods_dir.iterdir():
                        if f.name.lower().startswith(_api_prefix):
                            has_api = True
                            break
                if not has_api:
                    self._set_status(f"正在自动下载 {_display_name}...")
                    logger.info(f"{_display_name} 未找到，正在自动下载...")
                    try:
                        from modrinth import install_mod_with_deps

                        mc_version = self._extract_mc_version(target_version)
                        if mc_version == target_version:
                            from modrinth import parse_game_version_from_version

                            mc_version = parse_game_version_from_version(target_version)
                        ok, msg, names = install_mod_with_deps(
                            project_id=_project_id,
                            game_version=mc_version or target_version,
                            mod_loader=_mod_loader,
                            mods_dir=str(mods_dir),
                            status_callback=self._set_status,
                        )
                        if ok:
                            logger.info(f"{_display_name} 自动安装成功: {', '.join(names)}")
                        else:
                            logger.warning(f"{_display_name} 自动安装失败（不影响启动）: {msg}")
                    except Exception as e:
                        logger.warning(f"{_display_name} 自动安装异常（不影响启动）: {e}")

            # 设置玩家凭据（优先使用账号系统）
            account_options = {}
            if self._account_system:
                account = self._account_system.current_account
                if account:
                    # 微软账号：启动前刷新 Token
                    if account.account_type.value == "microsoft":
                        self._set_status("正在验证微软账号 Token...")
                        self._account_system.ensure_valid_token(account)
                    account_options = self._account_system.build_launch_options(account)
                    logger.info(f"使用账号凭据: {account.name} ({account.account_type.value})")

            if account_options:
                options["username"] = account_options.get("username", options.get("username", ""))
                if "uuid" in account_options:
                    options["uuid"] = account_options["uuid"]
                if "token" in account_options:
                    options["token"] = account_options["token"]
            elif self.config.player_name:
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

            # 确保版本 JAR 存在（参考 HMCL getVersionJar）
            # 自定义安装的加载器只创建 JSON，需从 inheritsFrom 父版本复制 JAR
            self._ensure_version_jar(target_version)

            # 确保 natives 目录存在且包含原生库
            # 自定义安装的版本没有独立的 natives 目录，需要回退到父版本的 natives
            # 但 Cleanroom 使用 LWJGL3，不能使用父版本（1.12.2）的 LWJGL2 natives
            if "nativesDirectory" not in options:
                # minecraft_launcher_lib 默认使用 versions/{id}/natives
                # 但自定义版本没有 natives，所以指向父版本的 natives
                default_natives = os.path.join(self.minecraft_dir, "versions", target_version, "natives")
                _loader_type = self._detect_mod_loader_type(target_version)
                if _loader_type != "cleanroom":
                    parent_natives = None
                    try:
                        version_json_path = (
                            Path(self.minecraft_dir) / "versions" / target_version / f"{target_version}.json"
                        )
                        if version_json_path.exists():
                            import json as _json

                            obj = _json.loads(version_json_path.read_text(encoding="utf-8"))
                            inherits_from = obj.get("inheritsFrom")
                            if inherits_from:
                                parent_natives_path = (
                                    Path(self.minecraft_dir) / "versions" / str(inherits_from).strip() / "natives"
                                )
                                if parent_natives_path.exists() and any(parent_natives_path.iterdir()):
                                    parent_natives = str(parent_natives_path)
                    except Exception:
                        pass

                    if parent_natives:
                        options["nativesDirectory"] = parent_natives
                        logger.info(f"使用父版本 natives 目录: {parent_natives}")
                    elif not os.path.isdir(default_natives) or not any(Path(default_natives).iterdir()):
                        os.makedirs(default_natives, exist_ok=True)
                        options["nativesDirectory"] = default_natives
                else:
                    # Cleanroom: 使用自己的 LWJGL3 natives 目录
                    if os.path.isdir(default_natives) and any(Path(default_natives).iterdir()):
                        options["nativesDirectory"] = default_natives
                        logger.info(f"Cleanroom: 使用自带 LWJGL3 natives: {default_natives}")
                    else:
                        os.makedirs(default_natives, exist_ok=True)
                        options["nativesDirectory"] = default_natives

            # ── retrofuturabootstrap: 提前下载并补充到版本 JSON ──
            # GTNH 2.8+ 需要 retrofuturabootstrap 在类路径中。
            # 在生成启动命令前将 JAR 加入 version.json 的 libraries，
            # 确保 minecraft_launcher_lib 将其纳入 classpath。
            # 此处 _ensure 仅下载 + 补充 JSON，不修改 command。
            self._ensure_retrofuturabootstrap_early(target_version)

            # 获取启动命令（Yggdrasil 账号使用 authlib-injector）
            logger.info(f"正在生成启动命令: {target_version}")
            if self._account_system and account_options:
                account = self._account_system.current_account
                if account and account.account_type.value == "yggdrasil" and account.yggdrasil_server_url:
                    injector = self._account_system.authlib_injector
                    if injector.is_installed or injector.download(status_callback=self._set_status):
                        minecraft_command = self._account_system.build_launch_command(
                            target_version, self.minecraft_dir, account
                        )
                        logger.info(f"已注入 authlib-injector: {injector.jar_path}")
                    else:
                        minecraft_command = self._mcllib.command.get_minecraft_command(
                            target_version, self.minecraft_dir, options
                        )
                else:
                    minecraft_command = self._mcllib.command.get_minecraft_command(
                        target_version, self.minecraft_dir, options
                    )
            else:
                minecraft_command = self._mcllib.command.get_minecraft_command(
                    target_version, self.minecraft_dir, options
                )

            # Cleanroom classpath 过滤：移除与 Cleanroom 冲突的旧版库
            # 参考 HMCL DefaultLauncher: Cleanroom 需要排除包含 "2.9.4-nightly-20150209" 的库
            if minecraft_command and self._detect_mod_loader_type(target_version) == "cleanroom":
                minecraft_command = self._filter_cleanroom_classpath(minecraft_command)

            # 使用 java_scanner 解析最佳 Java 可执行文件
            if minecraft_command:
                resolved_java = self._resolve_java_executable(target_version, minecraft_command[0])
                if resolved_java != minecraft_command[0]:
                    logger.info(f"Java 可执行文件已替换: {minecraft_command[0]} -> {resolved_java}")
                    minecraft_command[0] = resolved_java
                elif resolved_java == "java" or not os.path.isfile(resolved_java):
                    resolved_java = self._ensure_java_runtime(target_version)
                    if resolved_java != "java" and os.path.isfile(resolved_java):
                        minecraft_command[0] = resolved_java
                        logger.info(f"自动安装 Java runtime 后使用: {resolved_java}")

                # ── Cleanroom / MMC: 最终 Java 版本检查 ──
                # _resolve_java_executable 可能回退到系统 PATH 的 Java 8，
                # 此处强制验证并覆盖
                logger.info(f"[JavaCheck] 开始检查: {target_version}")
                _is_mmc = False
                if self._detect_mod_loader_type(target_version) == "cleanroom":
                    _min_java = self._get_cleanroom_min_java(target_version)
                    logger.info(f"[JavaCheck] Cleanroom: min_java={_min_java}")
                elif self._is_mmc_instance(target_version):
                    _min_java = self._get_mmc_min_java(target_version) or 0
                    _is_mmc = _min_java > 0
                    logger.info(f"[JavaCheck] MMC: min_java={_min_java}, is_mmc={_is_mmc}")
                else:
                    _min_java = 0
                    logger.info(f"[JavaCheck] 非 Cleanroom/MMC, 跳过")

                if _min_java > 0:
                    logger.info(f"[JavaCheck] 需要 Java {_min_java}+, 当前: {minecraft_command[0]}")
                    if not self._verify_java_version(minecraft_command[0], _min_java):
                        logger.warning(f"当前 Java 不满足要求 (Java {_min_java})，尝试自动安装...")

                        # MMC: 直接从 version.json 读取 component 并安装 JVM runtime
                        if _is_mmc:
                            _new_java = self._install_mmc_java_runtime(target_version, _min_java)
                        else:
                            _new_java = None

                        if not _new_java:
                            _new_java = self._ensure_java_runtime(target_version)

                        if (
                            _new_java
                            and _new_java != "java"
                            and os.path.isfile(_new_java)
                            and self._verify_java_version(_new_java, _min_java)
                        ):
                            minecraft_command[0] = _new_java
                            logger.info(f"Java 已修正为: {_new_java}")
                        else:
                            logger.error(f"无法找到 Java {_min_java}+，启动可能失败")

            # 直连服务器时追加 --quickPlayMultiplayer（1.20.4+，启动后立即加入）
            if server_ip:
                server_addr = f"{server_ip}:{server_port}"
                minecraft_command.append("--quickPlayMultiplayer")
                minecraft_command.append(server_addr)
                logger.info(f"追加 --quickPlayMultiplayer {server_addr}")

            # ── GTNH 检测 ──
            if self._is_gtnh_instance(target_version):
                try:
                    import tkinter.messagebox as _mb

                    _mb.showwarning(
                        "GTNH 兼容性提示",
                        "检测到 GT New Horizons 整合包。\n\n"
                        "GTNH 使用了自定义系统类加载器 (retrofuturabootstrap)，"
                        "在当前启动器中可能存在兼容性问题。\n\n"
                        "建议使用以下启动器之一：\n"
                        "  - HMCL (Hello Minecraft! Launcher)\n"
                        "  - Prism Launcher (官方推荐)\n\n"
                        "游戏仍会尝试启动，但可能会崩溃。",
                    )
                except Exception:
                    pass

            # ── JVM 参数优化 ──
            minecraft_command = self._optimize_jvm_args(minecraft_command, target_version)

            # 结构化日志：记录游戏启动命令
            _java_cmd = minecraft_command[0] if minecraft_command else ""
            _jvm_args = [a for a in minecraft_command[1:] if a.startswith("-")]
            _game_args = [a for a in minecraft_command[1:] if not a.startswith("-")]
            _loader = self._detect_mod_loader_type(target_version)
            slog.info(
                "game_launch_command_generated",
                version=target_version,
                loader=_loader,
                java_cmd=_java_cmd,
                jvm_args=_jvm_args[:10],
                game_args_count=len(_game_args),
            )

            # ── 设置启动器名称 ──
            # 替换 --versionType 参数值，使游戏标题界面左下角显示 "Minecraft x.x.x/FMCL"
            minecraft_command = self._set_launcher_brand(minecraft_command)

            logger.info("正在启动游戏...")
            # 使用 PIPE 捕获 + 同步输出到终端
            import sys as _sys

            popen_kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            # ── Windows 命令行长度限制绕过 ──
            # cmd.exe 限制为 8191 字符，classpath 可能超限。
            # 使用 Java @参数文件 绕过限制（Java 9+ 原生支持）。
            _args_file = None
            if sys.platform == "win32":
                # java.exe 本身不算在命令长度内（通过 Popen 列表传递）
                # 但 classpath 作为单个参数可能接近或超过限制，统一用 @file
                if any(len(a) > 2000 for a in minecraft_command):
                    import tempfile

                    _args_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                    for a in minecraft_command[1:]:  # 跳过 java.exe
                        _args_file.write(a + "\n")
                    _args_file.flush()
                    _args_file.close()
                    _args_path = _args_file.name
                    minecraft_command = [minecraft_command[0], f"@{_args_path}"]
                    logger.info(f"使用 @参数文件绕过命令行长度限制: {_args_path}")

            # ── Cleanroom 环境变量 ──
            # 参考 HMCL DefaultLauncher: 设置 INST_CLEANROOM=1 让
            # Cleanroom Loader 识别自身运行环境
            if self._detect_mod_loader_type(target_version) == "cleanroom":
                env = os.environ.copy()
                env["INST_CLEANROOM"] = "1"
                popen_kwargs["env"] = env
                logger.info("已设置 INST_CLEANROOM=1 环境变量")
            # ── 插件钩子: game.pre_launch ──
            pre_launch_results = self._emit_plugin_hook(
                "game.pre_launch", version_id=target_version, command=minecraft_command
            )
            if pre_launch_results:
                for _, mod in pre_launch_results:
                    if isinstance(mod, list):
                        # 插件返回完整的修改后命令列表
                        minecraft_command = mod
                    elif isinstance(mod, dict):
                        additions = mod.get("append_args", [])
                        if additions:
                            minecraft_command.extend(additions)

            self._game_process = subprocess.Popen(minecraft_command, **popen_kwargs)

            # ── 启动子进程输出采集线程 ──
            _early_output_lines: List[str] = []

            def _read_subprocess_output():
                try:
                    stdout = self._game_process.stdout
                    if stdout is None:
                        return
                    for line_bytes in stdout:
                        line = line_bytes.decode("utf-8", errors="replace").rstrip()
                        if line:
                            _early_output_lines.append(line)
                            _sys.stdout.write(line + "\n")
                            _sys.stdout.flush()
                except (ValueError, OSError):
                    pass
                except Exception:
                    pass

            _reader_thread = threading.Thread(target=_read_subprocess_output, daemon=True)
            _reader_thread.start()

            # ── 等待 2 秒检测进程是否立即退出 ──
            try:
                self._game_process.wait(timeout=2)
                exit_code = self._game_process.returncode
                _reader_thread.join(timeout=1)
                try:
                    remaining = self._game_process.stdout.read().decode("utf-8", errors="replace")
                    if remaining:
                        for line in remaining.splitlines():
                            line = line.rstrip()
                            if line:
                                _early_output_lines.append(line)
                                _sys.stdout.write(line + "\n")
                except Exception:
                    pass
                _sys.stdout.flush()
                if _early_output_lines:
                    logger.error(f"游戏进程在 2 秒内退出 (退出码 {exit_code})，输出({len(_early_output_lines)}行)")
                    for line in _early_output_lines[-30:]:
                        logger.error(f"  {line}")
                else:
                    logger.error(f"游戏进程在 2 秒内退出 (退出码 {exit_code})，无输出")
                slog.error("game_early_exit", version=target_version, exit_code=exit_code)
                return False, target_version
            except subprocess.TimeoutExpired:
                pass  # 进程运行超过 2 秒，正常

            # ── 插件钩子: game.post_launch ──
            self._emit_plugin_hook("game.post_launch", version_id=target_version, pid=self._game_process.pid)

            # ── 启动后内存释放 ──
            self._release_memory_after_launch()

            logger.info(f"游戏已启动 ({target_version})")
            return True, target_version

        except Exception as e:
            logger.error(f"启动游戏失败: {str(e)}")
            return False, None

    def _has_mod_loader(self, version_id: str) -> bool:
        """判断版本是否安装了模组加载器（需要版本隔离）

        优先读取版本 JSON 文件判断，回退到版本 ID 字符串匹配。

        参考 PCL-CE: McInstance.Modable 属性。
        """
        return has_mod_loader_from_json(version_id, self.minecraft_dir)

    def _detect_mod_loader_type(self, version_id: str) -> str:
        """检测模组加载器具体类型

        先尝试从版本 ID 字符串匹配加载器类型，适用于 launch_game 中的
        日志记录、参数调整等场景。

        Returns:
            加载器类型字符串: "forge", "fabric", "neoforge", "quilt",
                              "liteloader", "legacyfabric", "cleanroom",
                              "optifine", "labymod" 或 ""
        """
        return parse_mod_loader_from_version(version_id) or ""

    def _get_cleanroom_min_java(self, version_id: str) -> int:
        """获取 Cleanroom 所需的最低 Java 主版本号

        参考 HMCL GameJavaVersion.getCleanroomJavaVersion():
        - Cleanroom < 0.5.0 → Java 21
        - Cleanroom >= 0.5.0 → Java 25

        从版本 JSON 中提取 Cleanroom 版本号，回退到 21。

        Args:
            version_id: 版本 ID

        Returns:
            最低 Java 主版本号 (21 或 25)
        """
        try:
            from version_utils import parse_loader_version_from_json

            json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
            if os.path.isfile(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    cr_ver = parse_loader_version_from_json(f.read(), "cleanroom")
                    if cr_ver:
                        # 去掉 -alpha 后缀进行版本比较
                        cr_ver_clean = cr_ver.replace("-alpha", "")
                        parts = cr_ver_clean.split(".")
                        major = int(parts[0]) if parts else 0
                        minor = int(parts[1]) if len(parts) > 1 else 0
                        if major > 0 or minor >= 5:
                            logger.info(f"Cleanroom {cr_ver} 需要 Java 25+")
                            return 25
                        else:
                            logger.info(f"Cleanroom {cr_ver} 需要 Java 21+")
                            return 21
        except Exception as e:
            logger.debug(f"获取 Cleanroom 最低 Java 版本失败: {e}")
        # 回退到 Java 21
        return 21

    def _is_mmc_instance(self, version_id: str) -> bool:
        """检测是否为 MultiMC 整合包实例。

        检测方式：
        1. mmc_config.json 存在（最可靠）
        2. version.json 中有 inheritsFrom 且非标准 loader 名称模式
           （MMC 实例从原版继承，不以 forge/fabric 等 loader 前缀命名）

        Args:
            version_id: 版本 ID

        Returns:
            True 如果该版本是 MMC 整合包安装的实例
        """
        # 1. mmc_config.json
        config_path = os.path.join(self.minecraft_dir, "versions", version_id, "mmc_config.json")
        if os.path.isfile(config_path):
            return True

        # 2. inheritsFrom 兜底（MMC 实例克隆自原版版本）
        try:
            json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
            logger.info(f"[MMC-minJava] 检查: {json_path}, exists={os.path.isfile(json_path)}")
            if os.path.isfile(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    vj = json.loads(f.read())
                # 有 inheritsFrom 且不是标准 loader 版本（forge/fabric/neoforge/quilt）
                if vj.get("inheritsFrom"):
                    loader_prefixes = ("forge-", "fabric-", "neoforge-", "quilt-")
                    if not version_id.lower().startswith(loader_prefixes):
                        logger.info(f"通过 inheritsFrom 检测到 MMC 实例: {version_id}")
                        return True
        except Exception:
            pass

        return False

    def _is_gtnh_instance(self, version_id: str) -> bool:
        """检测是否为 GT New Horizons 整合包实例。

        通过检查版本 JSON 的 mainClass 或 JVM 参数中是否包含
        retrofuturabootstrap 来判断。

        Args:
            version_id: 版本 ID

        Returns:
            True 如果该版本是 GTNH 实例
        """
        try:
            json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
            if not os.path.isfile(json_path):
                return False
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.loads(f.read())
            mc = vj.get("mainClass", "")
            if "retrofuturabootstrap" in mc.lower():
                return True
            for a in vj.get("arguments", {}).get("jvm", []):
                if isinstance(a, str) and "retrofuturabootstrap" in a.lower():
                    return True
        except Exception:
            pass
        return False

    def _get_mmc_min_java(self, version_id: str) -> Optional[int]:
        """获取 MultiMC 整合包所需的最低 Java 主版本号。

        从版本 JSON 的 javaVersion.majorVersion 字段读取。
        参照 Cleanroom 的 _get_cleanroom_min_java 模式。

        Args:
            version_id: 版本 ID

        Returns:
            最低 Java 主版本号，或 None（无特殊要求）
        """
        try:
            json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
            if os.path.isfile(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    obj = json.loads(f.read())
                java_ver = obj.get("javaVersion")
                if java_ver and isinstance(java_ver, dict):
                    major = java_ver.get("majorVersion")
                    if isinstance(major, int) and major >= 8:
                        # GTNH 用 Java 17 更稳定，cap 到 17
                        if major > 17:
                            logger.info(
                                f"MMC 实例 {version_id} 要求 Java {major}+，" f"使用 Java 17（GTNH 兼容性更好）"
                            )
                            major = 17
                        logger.info(f"MMC 实例 {version_id} 需要 Java {major}+")
                        return major

                # 兜底: 检查当前 version.json 的 JVM 参数
                jvm_args = obj.get("arguments", {}).get("jvm", [])
                logger.info(
                    f"[MMC-minJava] {version_id}: javaVersion={'有' if java_ver else '无'}, "
                    f"jvm_args_count={len(jvm_args)}"
                )
                for arg in jvm_args:
                    arg_str = arg if isinstance(arg, str) else str(arg)
                    if "--add-opens" in arg_str or "--add-exports" in arg_str:
                        logger.info(f"MMC 实例 {version_id} JVM 含 --add-opens，推断需要 Java 17+")
                        return 17
        except Exception as e:
            logger.warning(f"获取 MMC 最低 Java 版本失败 ({version_id}): {e}")
        return None

    def _install_mmc_java_runtime(self, version_id: str, min_java: int) -> Optional[str]:
        """为 MMC 实例安装所需的 JVM 运行时。

        从 version.json 读取 javaVersion.component，通过
        minecraft_launcher_lib.runtime 安装对应的 JRE。

        Args:
            version_id: 版本 ID
            min_java: 最低 Java 主版本号

        Returns:
            Java 可执行文件路径，或 None
        """
        try:
            json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
            if not os.path.isfile(json_path):
                return None

            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.loads(f.read())

            jv = vj.get("javaVersion", {})
            component = jv.get("component", "")
            if not component:
                # 旧版安装无 component 字段，从 min_java 推导
                if min_java >= 21:
                    component = "java-runtime-delta"
                elif min_java >= 17:
                    component = "java-runtime-gamma"
                else:
                    component = "java-runtime-alpha"
                logger.info(f"MMC: 从 min_java={min_java} 推导 component={component}")
            elif component == "java-runtime-delta" and min_java <= 17:
                # GTNH: 强制使用 Java 17 以获得更好兼容性
                component = "java-runtime-gamma"
                logger.info(f"MMC: GTNH 强制使用 Java 17 运行时 (gamma)")

            # 检查是否已安装
            try:
                java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                if java_path and os.path.isfile(java_path) and self._verify_java_version(java_path, min_java):
                    logger.info(f"MMC JVM runtime 已安装 ({component}): {java_path}")
                    return java_path
            except Exception:
                pass

            # 安装 JVM runtime
            self._set_status(f"正在安装 Java {min_java} 运行时 ({component})...")
            logger.info(f"MMC: 安装 JVM 运行时 {component} (Java {min_java})")
            try:
                self._mcllib.runtime.install_jvm_runtime(
                    component,
                    self.minecraft_dir,
                    callback=self._get_callback() if hasattr(self, "_get_callback") else {},
                )
            except Exception as e:
                logger.warning(f"JVM 运行时安装 ({component}) 失败: {e}")
                return None

            # 再次尝试获取路径
            try:
                java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                if java_path and os.path.isfile(java_path):
                    logger.info(f"MMC JVM runtime 安装完成 ({component}): {java_path}")
                    return java_path
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"安装 MMC Java 运行时失败: {e}")
        return None

    @staticmethod
    def _verify_java_version(java_path: str, min_major: int) -> bool:
        """验证 Java 可执行文件的主版本号是否 >= min_major

        通过执行 `java -version` 读取 stderr 输出来解析版本号。

        Args:
            java_path: Java 可执行文件路径
            min_major: 最低主版本号

        Returns:
            True 如果版本满足要求
        """
        if not java_path or not os.path.isfile(java_path):
            return False
        try:
            import re as _re

            result = subprocess.run([java_path, "-version"], capture_output=True, text=True, timeout=10)
            _ver_output = result.stderr or result.stdout
            _m = _re.search(r'version "(\d+)', _ver_output)
            if _m:
                major = int(_m.group(1))
                logger.info(f"_verify_java_version: {java_path} → Java {major}, 需要 >= {min_major}")
                return major >= min_major
        except Exception as e:
            logger.debug(f"_verify_java_version 失败: {e}")
        return False

    def _ensure_retrofuturabootstrap_early(self, version_id: str) -> None:
        """提前准备 retrofuturabootstrap（下载 + 补充版本 JSON）。

        在 minecraft_launcher_lib 生成启动命令之前调用，
        将 JAR 加入 version.json 的 libraries 中，使其自动纳入 classpath。

        Args:
            version_id: 版本 ID
        """
        json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
        if not os.path.isfile(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.loads(f.read())
        except Exception:
            return

        main_class = vj.get("mainClass", "")
        jvm_args = vj.get("arguments", {}).get("jvm", [])

        needs_rfb = "retrofuturabootstrap" in main_class.lower()
        if not needs_rfb:
            for arg in jvm_args:
                if isinstance(arg, str) and "retrofuturabootstrap" in arg.lower():
                    needs_rfb = True
                    break
        if not needs_rfb:
            return

        # 下载 JAR
        rfb_jar = self._ensure_retrofuturabootstrap(version_id, [])
        if not rfb_jar:
            return

        # 追加到版本 JSON 的 libraries（Maven 格式，Forge 可自动解析）
        # 从 JAR 路径提取版本号：.../retrofuturabootstrap/1.0.12/retrofuturabootstrap-1.0.12.jar
        rfb_ver = "1.0.12"
        for part in rfb_jar.replace("\\", "/").split("/"):
            if part.startswith("retrofuturabootstrap-") and part.endswith(".jar"):
                rfb_ver = part[len("retrofuturabootstrap-") : -len(".jar")]
                break
        lib_name = f"com.gtnewhorizons:retrofuturabootstrap:{rfb_ver}"
        maven_path = f"com/gtnewhorizons/retrofuturabootstrap/{rfb_ver}/retrofuturabootstrap-{rfb_ver}.jar"
        new_lib = {
            "name": lib_name,
            "downloads": {
                "artifact": {
                    "path": maven_path,
                    "url": (
                        f"https://github.com/GTNewHorizons/RetroFuturaBootstrap"
                        f"/releases/download/{rfb_ver}/RetroFuturaBootstrap-{rfb_ver}.jar"
                    ),
                }
            },
        }

        libraries = vj.get("libraries", [])
        existing_names = {lib.get("name", "") for lib in libraries}
        if lib_name not in existing_names:
            libraries.append(new_lib)
            vj["libraries"] = libraries

        # 修复 --add-opens/--add-exports 格式：展开为每个值一个 flag
        # version.json 中为 ["--add-opens", "val1", "val2", "--add-opens", "val3"]，
        # 展开为 ["--add-opens", "val1", "--add-opens", "val2", "--add-opens", "val3"]
        jvm_args = vj.get("arguments", {}).get("jvm", [])
        if jvm_args:
            fixed_jvm = []
            pending = None
            for a in jvm_args:
                if isinstance(a, str) and a in ("--add-opens", "--add-exports"):
                    pending = a.lstrip("-")
                    continue
                if pending:
                    if isinstance(a, str) and not a.startswith("-"):
                        fixed_jvm.append(f"--{pending}")
                        fixed_jvm.append(a)
                        continue  # 保持 pending，可处理连续多个值
                    pending = None  # 非值参数，丢弃 pending
                fixed_jvm.append(a)
            vj["arguments"]["jvm"] = fixed_jvm
            # 移除末尾孤立的 --add-opens/--add-exports（之前版本可能遗留）
            while fixed_jvm and fixed_jvm[-1] in ("--add-opens", "--add-exports"):
                fixed_jvm.pop()
            logger.info(f"已修复版本 JSON 中 --add-opens/--add-exports 格式")

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(vj, f, ensure_ascii=False, indent=2)
            logger.info(f"retrofuturabootstrap 已添加到版本 JSON: {version_id}")
        except Exception as e:
            logger.warning(f"写入版本 JSON 失败: {e}")

    def _ensure_retrofuturabootstrap(self, version_id: str, command: List[str]) -> Optional[str]:
        """确保 retrofuturabootstrap JAR 存在并返回其路径。

        GTNH 2.8+ 等 MMC 实例使用 retrofuturabootstrap 作为 mainClass 和
        system class loader（-Djava.system.class.loader=RfbSystemClassLoader）。
        该类必须在 JVM bootstrap classpath 上才能被加载。

        Args:
            version_id: 版本 ID
            command: 当前启动命令列表（用于检测是否已存在）

        Returns:
            retrofuturabootstrap JAR 路径，或 None（不需要或失败）
        """
        # 快速检查：命令中是否已含 -Xbootclasspath/a 指向 retrofuturabootstrap
        for arg in command:
            if arg.startswith("-Xbootclasspath/a:") and "retrofuturabootstrap" in arg.lower():
                return None

        json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
        if not os.path.isfile(json_path):
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.loads(f.read())
        except Exception:
            return None

        main_class = vj.get("mainClass", "")
        jvm_args = vj.get("arguments", {}).get("jvm", [])

        needs_rfb = "retrofuturabootstrap" in main_class.lower()
        if not needs_rfb:
            for arg in jvm_args:
                if isinstance(arg, str) and "retrofuturabootstrap" in arg.lower():
                    needs_rfb = True
                    break
        if not needs_rfb:
            return None

        # 目标：Maven 标准路径（Forge/mclib 可从 libraries 解析）
        # .minecraft/libraries/com/gtnewhorizons/retrofuturabootstrap/<ver>/
        maven_dir = os.path.join(self.minecraft_dir, "libraries", "com", "gtnewhorizons", "retrofuturabootstrap")
        os.makedirs(maven_dir, exist_ok=True)

        # 检查 Maven 路径是否已存在
        if os.path.isdir(maven_dir):
            for root, _dirs, files in os.walk(maven_dir):
                for f in files:
                    if f.endswith(".jar") and "sources" not in f and "javadoc" not in f:
                        return os.path.join(root, f)

        # 也检查旧的实例 libraries 目录中的缓存
        instance_dir = os.path.join(self.minecraft_dir, "versions", version_id)
        old_lib_dir = os.path.join(instance_dir, "libraries")
        if os.path.isdir(old_lib_dir):
            for existing in os.listdir(old_lib_dir):
                if existing.lower().startswith("retrofuturabootstrap") and existing.endswith(".jar"):
                    jar_path = os.path.join(old_lib_dir, existing)
                    logger.info(f"retrofuturabootstrap 已存在于实例目录: {jar_path}")
                    # 复制到 Maven 路径
                    dest_dir = os.path.join(
                        maven_dir, existing.replace("retrofuturabootstrap-", "").replace(".jar", "")
                    )
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_path = os.path.join(dest_dir, existing)
                    if not os.path.isfile(dest_path):
                        import shutil

                        shutil.copy2(jar_path, dest_path)
                        logger.info(f"retrofuturabootstrap 已复制到 Maven 路径: {dest_path}")
                    return dest_path

        # 下载 retrofuturabootstrap（从旧版本开始尝试，兼容 GTNH 2.8.4）
        # 通过 -cp 加载到 AppClassLoader，由 Main 类在运行时创建 class loader
        versions_to_try = ["1.1.0", "1.0.17", "1.0.16", "1.0.15", "1.0.14", "1.0.13", "1.0.12"]
        base_url = (
            "https://github.com/GTNewHorizons/RetroFuturaBootstrap"
            "/releases/download/{ver}/RetroFuturaBootstrap-{ver}.jar"
        )

        import requests as _req

        for ver in versions_to_try:
            url = base_url.format(ver=ver)
            # Maven 标准路径：libraries/com/gtnewhorizons/retrofuturabootstrap/<ver>/<jar>
            target_dir = os.path.join(maven_dir, ver)
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, f"retrofuturabootstrap-{ver}.jar")
            try:
                logger.info(f"下载 retrofuturabootstrap {ver}: {url}")
                resp = _req.get(url, headers={"User-Agent": "FMCL/2.0"}, timeout=60)
                resp.raise_for_status()
                with open(target_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"retrofuturabootstrap 下载完成: {target_path}")
                return target_path
            except Exception as e:
                logger.warning(f"下载 retrofuturabootstrap {ver} 失败: {e}")
                if os.path.isfile(target_path):
                    try:
                        os.remove(target_path)
                    except OSError:
                        pass
                continue

        logger.error("无法下载 retrofuturabootstrap，启动可能失败")
        return None

    def _add_library_to_version_json(self, version_id: str, jar_path: str) -> None:
        """将 retrofuturabootstrap JAR 追加到版本 JSON 的 libraries 列表中。

        确保 minecraft_launcher_lib 生成启动命令时将 JAR 包含在 classpath 中。

        Args:
            version_id: 版本 ID
            jar_path: JAR 文件的绝对路径
        """
        json_path = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")
        if not os.path.isfile(json_path):
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.loads(f.read())
        except Exception:
            return

        lib_name = "com.gtnewhorizons:retrofuturabootstrap:1.0.0"
        jar_url = f"file:///{jar_path.replace(os.sep, '/')}"
        new_lib = {"name": lib_name, "downloads": {"artifact": {"path": jar_path, "url": jar_url}}}

        libraries = vj.get("libraries", [])
        existing_names = {lib.get("name", "") for lib in libraries}
        if lib_name not in existing_names:
            libraries.append(new_lib)
            vj["libraries"] = libraries
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(vj, f, ensure_ascii=False, indent=2)
                logger.info(f"已将 retrofuturabootstrap 添加到版本 JSON libraries: {version_id}")
            except Exception as e:
                logger.warning(f"写入版本 JSON 失败: {e}")

    def _ensure_version_jar(self, version_id: str) -> bool:
        """确保版本 JAR 文件存在

        参考 HMCL DefaultGameRepository.getVersionJar():
        自定义安装的加载器（LiteLoader、LegacyFabric、Cleanroom、OptiFine）
        只创建了版本 JSON，没有创建版本 JAR。该方法通过解析 inheritsFrom 链
        找到实际 JAR 文件，如果当前版本目录下不存在则从父版本复制。

        Args:
            version_id: 版本 ID

        Returns:
            是否确保了 JAR 存在
        """
        try:
            jar_path_str = resolve_version_jar_path(version_id, self.minecraft_dir)
            if not jar_path_str:
                logger.warning(f"无法解析版本 JAR 路径: {version_id}")
                return False

            jar_path = Path(jar_path_str)
            if jar_path.exists() and jar_path.stat().st_size > 0:
                return True

            # JAR 不存在，尝试从父版本复制
            version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
            if not version_json_path.exists():
                return False

            import json as _json

            try:
                obj = _json.loads(version_json_path.read_text(encoding="utf-8"))
            except Exception:
                return False

            inherits_from = obj.get("inheritsFrom")
            if not inherits_from:
                logger.warning(f"版本 {version_id} 的 JAR 不存在且无 inheritsFrom")
                return False

            # 在父版本目录下找 JAR
            parent_id = str(inherits_from).strip()
            parent_jar = Path(self.minecraft_dir) / "versions" / parent_id / f"{parent_id}.jar"
            if not parent_jar.exists() or parent_jar.stat().st_size == 0:
                logger.warning(f"父版本 {parent_id} 的 JAR 也不存在")
                return False

            # 复制父版本 JAR 到当前版本目录
            jar_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(str(parent_jar), str(jar_path))
            logger.info(f"已从父版本 {parent_id} 复制 JAR 到 {jar_path}")
            return True

        except Exception as e:
            logger.warning(f"确保版本 JAR 时出错: {e}")
            return False

    def _optimize_jvm_args(self, command: List[str], version_id: str = "") -> List[str]:
        """
        优化 JVM 启动参数

        - 默认: 使用 G1GC 垃圾回收器，减少游戏卡顿
        - Cleanroom: 使用 ZGC + CompactObjectHeaders (Java 25+)
        - Forge/NeoForge/Cleanroom: 添加 FML 兼容性参数
        - 固定堆内存大小，避免动态扩展/收缩的开销

        参考 HMCL DefaultLauncher.generateCommandLine() 中的 JVM 参数策略。
        """
        optimized = []
        has_xms = False
        has_xmx = False
        has_gc = False
        has_fml_ignore_cert = False
        has_fml_ignore_patch = False

        loader_type = self._detect_mod_loader_type(version_id)
        is_fml_loader = loader_type in ("forge", "neoforge", "cleanroom")

        for arg in command:
            if arg.startswith("-Xms"):
                has_xms = True
                optimized.append(arg)
            elif arg.startswith("-Xmx"):
                has_xmx = True
                optimized.append(arg)
            elif arg.startswith("-XX:+Use") and "GC" in arg:
                has_gc = True
                optimized.append(arg)
            elif arg == "-Dfml.ignoreInvalidMinecraftCertificates=true":
                has_fml_ignore_cert = True
                optimized.append(arg)
            elif arg == "-Dfml.ignorePatchDiscrepancies=true":
                has_fml_ignore_patch = True
                optimized.append(arg)
            else:
                optimized.append(arg)

        # 找到 java 可执行文件的位置
        insert_idx = 1
        for i, arg in enumerate(optimized):
            if arg in ("java", "javaw") or arg.endswith("java.exe") or arg.endswith("javaw.exe"):
                insert_idx = i + 1
                break

        jvm_opts = []
        is_cleanroom = loader_type == "cleanroom"

        # ── FML 兼容性参数（Forge/NeoForge/Cleanroom） ──
        # 参考 HMCL DefaultLauncher: 无条件添加以下 FML 参数以兼容旧版 Forge 模块验证
        if is_fml_loader:
            if not has_fml_ignore_cert:
                jvm_opts.append("-Dfml.ignoreInvalidMinecraftCertificates=true")
            if not has_fml_ignore_patch:
                jvm_opts.append("-Dfml.ignorePatchDiscrepancies=true")

        # ── LiteLoader ASM 兼容性 ──
        # Minecraft 1.12.x 的 LaunchClassLoader 不包含 org.objectweb.asm 在 classloader 排除列表中，
        # 导致 ASM 类加载失败（ClassNotFoundException）。通过 -Xbootclasspath/a 将 ASM jar
        # 添加到 bootstrap classloader 路径，使所有 classloader（包括 LaunchClassLoader）可访问。
        # 参考 HMCL MaintainTask / Forge CoreMod 的处理方式。
        if loader_type == "liteloader":
            asm_jar_path = os.path.join(
                self.minecraft_dir, "libraries", "org", "ow2", "asm", "asm-all", "5.2", "asm-all-5.2.jar"
            )
            if os.path.isfile(asm_jar_path):
                jvm_opts.append(f"-Xbootclasspath/a:{asm_jar_path}")
                logger.info(f"LiteLoader ASM 兼容: 已添加 -Xbootclasspath/a:{asm_jar_path}")
            else:
                # 尝试查找其他版本的 ASM jar
                asm_dir = Path(self.minecraft_dir) / "libraries" / "org" / "ow2" / "asm"
                if asm_dir.exists():
                    for asm_subdir in sorted(asm_dir.iterdir(), reverse=True):
                        jar_candidates = list(asm_subdir.glob("*.jar"))
                        if jar_candidates:
                            jvm_opts.append(f"-Xbootclasspath/a:{jar_candidates[0]}")
                            logger.info(f"LiteLoader ASM 兼容: 已添加 -Xbootclasspath/a:{jar_candidates[0]}")
                            break

        if is_cleanroom:
            # Cleanroom 专用 JVM 优化
            # 参考 HMCL: 不添加额外 GC 参数，允许用户自定义
            # ZGC 和 CompactObjectHeaders 对 Cleanroom 非必需，交给用户自行配置
            # Cleanroom 要求 -Xms == -Xmx，确保堆固定
            if has_xmx and not has_xms:
                for arg in optimized:
                    if arg.startswith("-Xmx"):
                        jvm_opts.append(arg.replace("-Xmx", "-Xms"))
                        break
            elif has_xms and not has_xmx:
                for arg in optimized:
                    if arg.startswith("-Xms"):
                        jvm_opts.append(arg.replace("-Xms", "-Xmx"))
                        break
        else:
            # 标准优化：G1GC
            if not has_gc:
                jvm_opts.append("-XX:+UseG1GC")
            if not has_xms and has_xmx:
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

            jvm_opts.extend(["-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200"])

        if jvm_opts:
            for opt in reversed(jvm_opts):
                optimized.insert(insert_idx, opt)
            logger.info(f"JVM 优化参数 ({'Cleanroom/ZGC' if is_cleanroom else 'G1GC'}): {jvm_opts}")

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

    @staticmethod
    def _filter_cleanroom_classpath(command: List[str]) -> List[str]:
        """过滤 Cleanroom 的 classpath，移除与 Cleanroom 冲突的旧版库

        参考 HMCL DefaultLauncher 和 PCL-CE ModLaunch.McLaunchLibPath():
        - 排除旧版 LWJGL 2.9.4（包括 nightly 版本）
        - 排除旧版 JNA platform 3.4.0
        - 排除旧版 ICU4J mojang fork 51.2
        Cleanroom 自带 LWJGL 3 + 现代 JNA/ICU4J，旧版库会导致类加载冲突。

        Args:
            command: 启动命令列表

        Returns:
            过滤后的启动命令列表
        """
        path_sep = ";" if sys.platform == "win32" else ":"
        for i, arg in enumerate(command):
            if arg in ("-cp", "-classpath") and i + 1 < len(command):
                entries = command[i + 1].split(path_sep)
                filtered = [
                    e
                    for e in entries
                    if not any(
                        conflict in e
                        for conflict in (
                            "2.9.4-nightly-20150209",
                            "lwjgl:lwjgl:2.9.4",
                            "jna:platform:3.4.0",
                            "icu4j-core-mojang:51.2",
                        )
                    )
                ]
                if len(filtered) != len(entries):
                    removed = len(entries) - len(filtered)
                    command[i + 1] = path_sep.join(filtered)
                    logger.info(f"Cleanroom classpath 过滤: 移除了 {removed} 个冲突库")
                break
        return command

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
            # ── 插件钩子: version.pre_remove ──
            self._emit_plugin_hook("version.pre_remove", version_id=version_id)

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
            self.invalidate_instance_cache()
            return True, version_id

        except Exception as e:
            logger.error(f"删除版本失败: {str(e)}")
            return False, version_id

    def _emit_plugin_hook(self, hook_name: str, **kwargs):
        """发射插件钩子（安全包装，不影响主流程）

        Args:
            hook_name: 钩子名称，如 "version.post_install"
            **kwargs: 传递给钩子处理器的参数
        """
        try:
            pm = getattr(self, "_plugin_manager", None)
            if pm is None:
                return
            from plugin_manager.base import HookPoint

            hook_map = {
                "game.pre_launch": HookPoint.GAME_PRE_LAUNCH,
                "game.post_launch": HookPoint.GAME_POST_LAUNCH,
                "game.stopped": HookPoint.GAME_STOPPED,
                "game.crashed": HookPoint.GAME_CRASHED,
                "version.pre_install": HookPoint.VERSION_PRE_INSTALL,
                "version.post_install": HookPoint.VERSION_POST_INSTALL,
                "version.pre_remove": HookPoint.VERSION_PRE_REMOVE,
                "server.pre_start": HookPoint.SERVER_PRE_START,
                "server.post_start": HookPoint.SERVER_POST_START,
                "server.stopped": HookPoint.SERVER_STOPPED,
                "download.pre_download": HookPoint.DOWNLOAD_PRE_DOWNLOAD,
                "download.post_download": HookPoint.DOWNLOAD_POST_DOWNLOAD,
            }
            hook_point = hook_map.get(hook_name)
            if hook_point:
                return pm.emit(hook_point, **kwargs)
        except Exception as e:
            from logzero import logger

            logger.warning(f"插件钩子发射异常 ({hook_name}): {e}")

    def get_callbacks(self) -> Dict[str, Callable]:
        """获取供UI调用的回调函数字典"""
        return {
            "check_environment": self.check_and_setup_environment,
            "get_available_versions": self.get_available_versions,
            "get_installed_versions": self.get_installed_versions,
            "get_installed_version_ids": self.get_installed_version_ids,
            "get_instance_info": self.get_instance_info,
            "invalidate_instance_cache": self.invalidate_instance_cache,
            "rename_instance": self.rename_instance,
            "get_supported_loaders": self.get_supported_loaders,
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
            "get_jdz_username": self.get_jdz_username,
            "set_jdz_username": self.set_jdz_username,
            "get_jdz_user_info": self.get_jdz_user_info,
            "fetch_jdz_user_info": self.fetch_jdz_user_info,
            "get_language": self.get_language,
            "set_language": self.set_language,
            # 主题相关
            "get_theme_engine": self.get_theme_engine,
            "get_theme_name": self.get_theme_name,
            "set_theme_name": self.set_theme_name,
            "get_accent_color": self.get_accent_color,
            "set_accent_color": self.set_accent_color,
            "get_dynamic_version_theme": self.get_dynamic_version_theme,
            "set_dynamic_version_theme": self.set_dynamic_version_theme,
            "apply_version_theme": self.apply_version_theme,
            "reapply_theme": self.reapply_theme,
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
            # MultiMC 整合包
            "get_multimc_pack_info": self.get_multimc_pack_info,
            "install_multimc_pack": self.install_multimc_pack,
            "install_multimc_pack_server": self.install_multimc_pack_server,
            "update_multimc_pack": self.update_multimc_pack,
            "get_instance_launch_overrides": self._get_instance_launch_overrides,
            # 统一整合包入口
            "install_modpack": self.install_modpack,
            # CurseForge 整合包
            "get_cf_pack_info": self.get_cf_pack_info,
            # HMCL 整合包
            "get_hmcl_pack_info": self.get_hmcl_pack_info,
            # MCBBS 整合包
            "get_mcbbs_pack_info": self.get_mcbbs_pack_info,
            # 通用压缩包/启动器包
            "get_compress_pack_info": self.get_compress_pack_info,
            # Java 运行时相关
            "scan_system_java": self.scan_system_java,
            "get_java_suggestion": self.get_java_suggestion,
            "get_java_mode": self.get_java_mode,
            "set_java_mode": self.set_java_mode,
            "get_java_custom_path": self.get_java_custom_path,
            "set_java_custom_path": self.set_java_custom_path,
            "save_music_state": self.save_music_state,
            "load_music_state": self.load_music_state,
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
        if token is None:
            self.config.jdz_user_info = None
        self.config.save_config()

    def get_jdz_username(self) -> Optional[str]:
        """获取净读 AI 用户名（优先从 API 缓存，回退到本地存储）"""
        if self.config.jdz_user_info and self.config.jdz_user_info.get("username"):
            return self.config.jdz_user_info["username"]
        return self.config.jdz_username

    def set_jdz_username(self, username: Optional[str]) -> None:
        """设置净读 AI 用户名（加密存储）"""
        self.config.jdz_username = username
        self.config.save_config()

    def get_jdz_user_info(self) -> Optional[dict]:
        """获取净读 AI 用户信息缓存"""
        return self.config.jdz_user_info

    def fetch_jdz_user_info(self) -> Optional[dict]:
        """从净读 API 获取用户信息并缓存到内存"""
        token = self.config.jdz_token
        if not token:
            return None
        try:
            import requests

            resp = requests.get(
                "https://jingdu.qzz.io/api/user/info",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
                },
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            info = result.get("data", result)
            self.config.jdz_user_info = info
            return info
        except Exception as e:
            from logzero import logger

            detail = str(e)
            try:
                if hasattr(e, "response") and e.response is not None:
                    detail = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            except Exception:
                pass
            logger.warning(f"获取净读用户信息失败: {detail}")
            return None

    def get_language(self) -> str:
        """获取界面语言"""
        return getattr(self.config, "language", "zh_CN")

    def set_language(self, language: str) -> None:
        """设置界面语言"""
        self.config.language = language
        self.config.save_config()

    def get_theme_engine(self):
        """获取主题引擎实例"""
        return get_theme_engine()

    def get_theme_name(self) -> str:
        """获取当前主题名称"""
        return self.config.theme_name

    def set_theme_name(self, theme_name: str) -> None:
        """设置主题名称并应用"""
        self.config.theme_name = theme_name
        engine = get_theme_engine()
        theme = engine.load_theme(theme_name)
        if theme:
            engine.apply_theme(theme, self.config.accent_color)
        self.config.save_config()

    def get_accent_color(self) -> Optional[str]:
        """获取自定义强调色"""
        return self.config.accent_color

    def set_accent_color(self, color: Optional[str]) -> None:
        """设置自定义强调色"""
        self.config.accent_color = color
        engine = get_theme_engine()
        theme = engine.load_theme(self.config.theme_name)
        if theme:
            engine.apply_theme(theme, color)
        self.config.save_config()

    def get_dynamic_version_theme(self) -> bool:
        """获取是否启用版本动态主题"""
        return self.config.dynamic_version_theme

    def set_dynamic_version_theme(self, enabled: bool) -> None:
        """设置是否启用版本动态主题"""
        self.config.dynamic_version_theme = enabled
        self.config.save_config()

    def apply_version_theme(self, version_id: str) -> Optional[Dict[str, str]]:
        """根据 Minecraft 版本应用动态主题"""
        if not self.config.dynamic_version_theme:
            return None
        engine = get_theme_engine()
        version_colors = engine.get_version_accent(version_id)
        if version_colors:
            theme = engine.load_theme(self.config.theme_name)
            if theme:
                colors = dict(theme.colors)
                colors["accent"] = version_colors["accent"]
                colors["accent_hover"] = version_colors["accent_hover"]
                modified_theme = Theme(
                    name=theme.name,
                    author=theme.author,
                    description=theme.description,
                    version=theme.version,
                    colors=colors,
                )
                engine.apply_theme(modified_theme, version_colors["accent"])
                try:
                    from achievement_engine import get_achievement_engine

                    ach_engine = get_achievement_engine()
                    if ach_engine:
                        ach_engine.update_progress("personalize_version_theme")
                except Exception:
                    pass
                return dict(engine.get_current_colors())
        return None

    def reapply_theme(self):
        """通知主窗口重新应用主题颜色"""
        pass

    def verify_installed_version(self, version_id: str, max_workers: int = 4) -> Dict[str, Any]:
        """
        并发校验已安装版本的文件完整性

        Args:
            version_id: 版本ID
            max_workers: 并发线程数

        Returns:
            {"total": 总文件数, "valid": 有效文件数, "invalid": 无效文件列表}
        """
        from launcher.verify import concurrent_file_verify

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

    def save_music_state(self, state: dict) -> None:
        self.config.music_state = state
        self.config.save_config()

    def load_music_state(self) -> dict:
        return self.config.music_state if self.config.music_state else {}
