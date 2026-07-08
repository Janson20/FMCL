"""Minecraft启动器核心模块"""
import gc
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple

from logzero import logger

from config import Config
from structured_logger import slog
from mirror import MirrorSource
from validation import validate_version_id, validate_server_ip, validate_server_port
from version_utils import (
    parse_mc_version_from_id,
    parse_mc_version_from_dir,
    parse_instance_from_json,
    InstanceInfo,
    has_mod_loader_from_json,
    parse_mod_loader_from_version,
)
from ui.theme_engine import init_theme_engine, get_theme_engine, Theme


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
        java_mode = getattr(self.config, 'java_mode', 'auto')
        custom_path = getattr(self.config, 'java_custom_path', None)

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
            if javas:
                best = recommend_for_mc(javas, target_version)
                if best:
                    logger.info(f"从系统扫描选择最佳 Java: {best.display_name}")
                    return best.path
        except Exception as e:
            logger.debug(f"Java 扫描器推荐失败: {e}")

        try:
            installed_runtimes = self._mcllib.runtime.get_installed_jvm_runtimes(self.minecraft_dir)
            if installed_runtimes:
                latest = sorted(
                    installed_runtimes,
                    key=lambda r: r.get("version", {}).get("name", ""),
                    reverse=True,
                )
                component = latest[0].get("name", "")
                if component:
                    java_path = self._mcllib.runtime.get_executable_path(component, self.minecraft_dir)
                    if java_path and os.path.isfile(java_path):
                        logger.info(f"从 Minecraft runtime 找到 Java ({component}): {java_path}")
                        return java_path
        except Exception as e:
            logger.debug(f"Minecraft runtime Java 查找失败: {e}")

        return current_java

    def _ensure_java_runtime(self, version_id: str) -> str:
        current = self._resolve_java_executable(version_id, "java")
        if current != "java" and os.path.isfile(current):
            return current

        mc_base = self._extract_mc_version(version_id)
        version_json_path = Path(self.minecraft_dir) / "versions" / version_id / f"{version_id}.json"
        if not version_json_path.exists():
            logger.info(f"版本 {mc_base} 未安装，正在安装以获取 Java runtime...")
            self._set_status(f"正在安装 {mc_base}（自动获取 Java runtime）...")
            self._mcllib.install.install_minecraft_version(
                mc_base,
                self.minecraft_dir,
                callback=self._get_callback()
            )

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
        logger.info(f"Java 选择模式已切换为: {mode}")

    def get_java_custom_path(self) -> Optional[str]:
        return getattr(self.config, 'java_custom_path', None)

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
            last = getattr(self, '_last_progress_time', 0)
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
            from datetime import datetime
            import json
            import requests as _req

            # 直接从 Mojang API 获取版本清单，不使用上游缓存（避开破损缓存）
            manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
            resp = _req.get(manifest_url, timeout=30,
                          headers={"User-Agent": "FMCL/2.11.0"})
            if resp.status_code != 200:
                logger.warning(f"获取版本清单失败 HTTP {resp.status_code}")
                return []
            vlist = resp.json()

            version_list = []
            skipped = 0
            for entry in vlist.get("versions", []):
                try:
                    version_list.append({
                        "id": entry["id"],
                        "type": entry.get("type", "release"),
                        "releaseTime": datetime.fromisoformat(entry["releaseTime"]),
                        "complianceLevel": entry.get("complianceLevel", 0),
                    })
                except (KeyError, TypeError, ValueError):
                    skipped += 1

            if skipped:
                logger.warning(f"过滤了 {skipped} 个缺少 releaseTime 的异常版本条目")

            # 合并已安装的本地版本（自带防御，不依赖上游 get_installed_versions）
            installed_ids = {v["id"] for v in version_list}
            versions_dir = self.config.get_versions_dir()
            if versions_dir.exists():
                for folder_name in os.listdir(str(versions_dir)):
                    json_path = versions_dir / folder_name / f"{folder_name}.json"
                    if not json_path.exists():
                        continue
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        vid = data.get("id", folder_name)
                        if vid in installed_ids:
                            continue
                        rt_str = data.get("releaseTime", "2000-01-01T00:00:00+00:00")
                        version_list.append({
                            "id": vid,
                            "type": data.get("type", "release"),
                            "releaseTime": datetime.fromisoformat(rt_str.replace("Z", "+00:00")),
                            "complianceLevel": data.get("complianceLevel", 0),
                        })
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        continue

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
                if vp.is_dir() and v not in ('jre_manifest.json', 'version_manifest_v2.json'):
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
            return parse_instance_from_json(
                json_text,
                folder_name,
                self.minecraft_dir,
            )
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
        if not re.match(r'^[a-zA-Z0-9_.\-+]+$', new_name):
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
            version_ids = {
                v["id"].split()[0] if isinstance(v["id"], str) else v["id"]
                for v in available_versions
            }

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
                            version_id,
                            self.minecraft_dir,
                            callback=self._get_callback(),
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
                slog.info("version_installed", version=version_id, loader=mod_loader,
                          installed_version_id=installed_version_id, loader_version=loader_version)
                self._emit_plugin_hook("version.post_install", version_id=installed_version_id, success=True)
                self.invalidate_instance_cache()
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
                slog.info("version_installed", version=version_id, loader="vanilla",
                          installed_version_id=version_id)
                self._emit_plugin_hook("version.post_install", version_id=version_id, success=True)
                self.invalidate_instance_cache()
                return True, version_id

        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            slog.error("version_install_failed", version=version_id, loader=mod_loader if mod_loader != "无" else "vanilla",
                       error=str(e)[:200])
            self._emit_plugin_hook("version.post_install", version_id=version_id, success=False)
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
                    name for name in all_names
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
                for subdir in ("mods", "config", "saves", "resourcepacks", "shaderpacks",
                               "screenshots", "crash-reports", "logs"):
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
                    target_version,
                    self.minecraft_dir,
                    options
                )

            # Cleanroom classpath 过滤：移除与 Cleanroom 冲突的旧版库
            # 参考 HMCL DefaultLauncher: Cleanroom 需要排除包含 "2.9.4-nightly-20150209" 的库
            if minecraft_command and self._detect_mod_loader_type(target_version) == "cleanroom":
                minecraft_command = self._filter_cleanroom_classpath(minecraft_command)

            # 使用 java_scanner 解析最佳 Java 可执行文件
            if minecraft_command:
                resolved_java = self._resolve_java_executable(
                    target_version, minecraft_command[0]
                )
                if resolved_java != minecraft_command[0]:
                    logger.info(f"Java 可执行文件已替换: {minecraft_command[0]} -> {resolved_java}")
                    minecraft_command[0] = resolved_java
                elif resolved_java == "java" or not os.path.isfile(resolved_java):
                    resolved_java = self._ensure_java_runtime(target_version)
                    if resolved_java != "java" and os.path.isfile(resolved_java):
                        minecraft_command[0] = resolved_java
                        logger.info(f"自动安装 Java runtime 后使用: {resolved_java}")

            # 直连服务器时追加 --quickPlayMultiplayer（1.20.4+，启动后立即加入）
            if server_ip:
                server_addr = f"{server_ip}:{server_port}"
                minecraft_command.append("--quickPlayMultiplayer")
                minecraft_command.append(server_addr)
                logger.info(f"追加 --quickPlayMultiplayer {server_addr}")

            # ── JVM 参数优化 ──
            minecraft_command = self._optimize_jvm_args(minecraft_command, target_version)

            # 结构化日志：记录游戏启动命令
            _java_cmd = minecraft_command[0] if minecraft_command else ""
            _jvm_args = [a for a in minecraft_command[1:] if a.startswith("-")]
            _game_args = [a for a in minecraft_command[1:] if not a.startswith("-")]
            _loader = self._detect_mod_loader_type(target_version)
            slog.info("game_launch_command_generated", version=target_version, loader=_loader,
                      java_cmd=_java_cmd, jvm_args=_jvm_args[:10], game_args_count=len(_game_args))

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
            # ── 插件钩子: game.pre_launch ──
            pre_launch_results = self._emit_plugin_hook("game.pre_launch", version_id=target_version, command=minecraft_command)
            if pre_launch_results:
                for _, mod in pre_launch_results:
                    if isinstance(mod, list):
                        # 插件返回完整的修改后命令列表
                        minecraft_command = mod
                    elif isinstance(mod, dict):
                        additions = mod.get("append_args", [])
                        if additions:
                            minecraft_command.extend(additions)

            self._game_process = subprocess.Popen(
                minecraft_command,
                **popen_kwargs,
            )

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
        is_cleanroom = (loader_type == "cleanroom")

        # ── FML 兼容性参数（Forge/NeoForge/Cleanroom） ──
        # 参考 HMCL DefaultLauncher: 无条件添加以下 FML 参数以兼容旧版 Forge 模块验证
        if is_fml_loader:
            if not has_fml_ignore_cert:
                jvm_opts.append("-Dfml.ignoreInvalidMinecraftCertificates=true")
            if not has_fml_ignore_patch:
                jvm_opts.append("-Dfml.ignorePatchDiscrepancies=true")

        if is_cleanroom:
            # Cleanroom 专用 JVM 优化（参考 Cleanroom Loader 官方文档）
            if not has_gc:
                jvm_opts.append("-XX:+UseZGC")           # ZGC 极低延迟，适合大堆内存
            jvm_opts.append("-XX:+UseCompactObjectHeaders")  # Java 25+ 对象头压缩
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

            jvm_opts.extend([
                "-XX:+ParallelRefProcEnabled",
                "-XX:MaxGCPauseMillis=200",
            ])

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

        HMCL DefaultLauncher 中 Cleanroom 需要排除包含 "2.9.4-nightly-20150209" 的
        asm 库，该库与 Cleanroom 自带的 asm 版本冲突。

        Args:
            command: 启动命令列表

        Returns:
            过滤后的启动命令列表
        """
        path_sep = ";" if sys.platform == "win32" else ":"
        for i, arg in enumerate(command):
            if arg in ("-cp", "-classpath") and i + 1 < len(command):
                entries = command[i + 1].split(path_sep)
                filtered = [e for e in entries if "2.9.4-nightly-20150209" not in e]
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
                if hasattr(e, 'response') and e.response is not None:
                    detail = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            except Exception:
                pass
            logger.warning(f"获取净读用户信息失败: {detail}")
            return None

    def get_language(self) -> str:
        """获取界面语言"""
        return getattr(self.config, 'language', 'zh_CN')

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
