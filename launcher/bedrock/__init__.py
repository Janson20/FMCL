"""基岩版管理器：版本下载、安装、启动、卸载

实现 FMCL 的基岩版（Bedrock Edition）支持，流程对齐 BedrockBoot：
1. 版本库: mcappx.com bedrock.json（多源回退）
2. 下载:   GDK 直链 / UWP SOAP 解析，多线程下载 + MD5 校验，镜像自动回退
3. 安装:   GDK 解包 XVD 容器；UWP 解包 AppX + 修改清单 + 开发模式注册
4. 启动:   GDK 运行 Minecraft.Windows.exe；UWP 注册后激活；环境自动修复
5. 存储:   <minecraft_dir>/bedrock_versions/<名称>/，配置与 BedrockBoot 兼容
"""

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from logzero import logger

from launcher.bedrock import appx as appx_mod
from launcher.bedrock import env as env_mod
from launcher.bedrock import launch as launch_mod
from launcher.bedrock import xvd as xvd_mod
from launcher.bedrock.download import (
    DownloadError,
    check_md5,
    download_file,
    verify_variation_md5,
)
from launcher.bedrock.source import (
    build_mirror_urls,
    get_build_info,
    load_version_db,
    resolve_download_url,
)

CONFIG_SUB_PATH = Path("config") / "BedrockBoot2" / "config.json"
BB_VERSION_FILE = Path("config") / "BedrockBoot2" / ".bb.version"
VERSION_SAVE_DIR = "version_save"

TYPE_NORMALIZE = {"Release": "release", "Preview": "preview", "Beta": "beta"}


class BedrockError(RuntimeError):
    """基岩版操作错误"""


MIN_WINDOWS_BUILD = 19041


def ensure_windows_supported() -> None:
    """校验当前系统支持基岩版（Windows 10 2004 / 19041+，对齐 BedrockLauncher.Core）"""
    import platform

    if platform.system().lower() != "windows":
        raise BedrockError("基岩版仅支持 Windows 系统")
    try:
        build = int(platform.version().split(".")[-1])
    except (ValueError, IndexError):
        build = 0
    if build < MIN_WINDOWS_BUILD:
        raise BedrockError(f"基岩版需要 Windows 10 (build 19041) 及以上版本，当前 build {build}")


class BedrockManager:
    """基岩版版本管理入口（线程安全：安装/启动操作须在后台线程调用）"""

    def __init__(
        self,
        versions_root: Path,
        notify_cb: Optional[Callable[[str], None]] = None,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ):
        self.versions_root = Path(versions_root)
        self.notify_cb = notify_cb
        self.progress_cb = progress_cb
        self._db_cache: Optional[Dict[str, Any]] = None
        self._processes: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ─── 基础工具 ─────────────────────────────────────────────

    def _notify(self, text: str) -> None:
        if self.notify_cb:
            try:
                self.notify_cb(text)
            except Exception as e:
                logger.warning(f"状态回调异常: {e}")

    def _progress(self, current: int, total: int, status: str = "") -> None:
        if self.progress_cb:
            try:
                self.progress_cb(current, total, status)
            except Exception as e:
                logger.warning(f"进度回调异常: {e}")

    def _config_path(self, name: str) -> Path:
        return self.versions_root / name / CONFIG_SUB_PATH

    def _get_version_db(self, refresh: bool = False) -> Dict[str, Any]:
        if self._db_cache is None or refresh:
            self._db_cache = load_version_db(self.versions_root, refresh=refresh)
        return self._db_cache

    # ─── 版本列表 ─────────────────────────────────────────────

    def get_available_versions(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """获取可下载的版本列表（x64 架构，带 MD5 的优先）"""
        db = self._get_version_db(refresh)
        result: List[Dict[str, Any]] = []
        for version, entry in db.get("From_mcappx.com", {}).items():
            if not isinstance(entry, dict):
                continue
            variation = next((v for v in entry.get("Variations", []) if v.get("Arch") == "x64"), None)
            if variation is None:
                continue
            result.append(
                {
                    "version": version,
                    "type": TYPE_NORMALIZE.get(entry.get("Type", ""), "release"),
                    "build_type": entry.get("BuildType", "UWP"),
                    "date": entry.get("Date", ""),
                    "md5": variation.get("MD5", ""),
                    "os_build": variation.get("OSbuild", ""),
                }
            )
        result.sort(key=lambda v: v["version"], reverse=True)
        return result

    def get_installed_versions(self) -> List[Dict[str, Any]]:
        """扫描已安装的基岩版版本"""
        if not self.versions_root.exists():
            return []
        result: List[Dict[str, Any]] = []
        for folder in sorted(self.versions_root.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir() or folder.name in (VERSION_SAVE_DIR, "version_db.json"):
                continue
            info = self._read_installed_info(folder)
            if info:
                result.append(info)
        return result

    def _read_installed_info(self, folder: Path) -> Optional[Dict[str, Any]]:
        """读取单个已安装版本信息（兼容 BedrockBoot config.json 的 {"Data": ...} 包装）"""
        config_file = folder / CONFIG_SUB_PATH
        info: Optional[Dict[str, Any]] = None
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                info = data.get("Data", data) if isinstance(data, dict) else None
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取版本配置失败 {folder.name}: {e}")
        if info is None:
            # 兼容 BedrockBoot 安装但未写配置的目录：从 manifest 推断
            manifest = folder / "appxmanifest.xml"
            if manifest.exists():
                info = self._infer_info_from_manifest(folder, manifest)
            else:
                return None
        info_obj = info.get("Info") or {}
        if not info_obj.get("VersionName") or not info_obj.get("Version"):
            return None
        build_type = info_obj.get("BuildType", "UWP")
        return {
            "name": info_obj.get("VersionName"),
            "version": info_obj.get("Version"),
            "build_type": build_type,
            "game_type": TYPE_NORMALIZE.get(info_obj.get("VersionType", "Release"), "release"),
            "path": str(folder),
            "config": info.get("Config") or {},
        }

    def _infer_info_from_manifest(self, folder: Path, manifest: Path) -> Dict[str, Any]:
        import re

        text = manifest.read_text(encoding="utf-8", errors="replace")
        version = ""
        m = re.search(r'Version="(\d+\.\d+\.\d+\.\d+)"', text)
        if m:
            version = m.group(1)
        pack_name = ""
        m = re.search(r'<Identity[^>]*Name="([^"]+)"', text)
        if m:
            pack_name = m.group(1)
        build_type = "GDK" if (folder / "MicrosoftGame.Config").exists() else "UWP"
        game_type = "preview" if ("preview" in pack_name.lower() or "beta" in pack_name.lower()) else "release"
        return {
            "Info": {
                "BuildType": build_type,
                "Version": version,
                "VersionName": folder.name,
                "VersionType": game_type,
            },
            "Config": {},
        }

    def _write_config(self, folder: Path, info: Dict[str, Any], config_data: Dict[str, Any]) -> None:
        config_file = folder / CONFIG_SUB_PATH
        config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Data": {"Info": info, "Config": config_data}}
        config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / BB_VERSION_FILE).write_text(str(info.get("Version", "")), encoding="utf-8")

    # ─── 安装 ─────────────────────────────────────────────────

    def install_version(
        self,
        version: str,
        name: Optional[str] = None,
        refresh_db: bool = False,
        stop_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """下载并安装指定版本的基岩版

        Args:
            version: 版本号（如 1.21.120.21）
            name: 实例名称（默认 Bedrock <版本>）
            refresh_db: 是否强制刷新版本库
            stop_event: 取消事件

        Returns:
            已安装版本信息字典

        Raises:
            BedrockError: 任一步骤失败
        """
        ensure_windows_supported()
        db = self._get_version_db(refresh=refresh_db)
        build_info = get_build_info(db, version)
        if build_info is None:
            raise BedrockError(f"版本库中不存在版本 {version}")

        from launcher.bedrock.source import find_variation

        variation = find_variation(build_info, "x64")
        if variation is None:
            raise BedrockError(f"版本 {version} 没有 x64 架构的包")
        build_type = build_info.get("BuildType", "UWP")
        game_type = TYPE_NORMALIZE.get(build_info.get("Type", "Release"), "release")
        name = (name or f"Bedrock {version}").strip()
        folder = self.versions_root / name
        if folder.exists():
            raise BedrockError(f"同名版本 '{name}' 已存在")

        self._notify("正在获取下载链接...")
        try:
            url = resolve_download_url(build_info, "x64")
        except Exception as e:
            raise BedrockError(f"获取下载链接失败: {e}") from e

        version_save = self.versions_root / VERSION_SAVE_DIR
        version_save.mkdir(parents=True, exist_ok=True)
        package_path = version_save / f"{version}.insPack"

        try:
            if package_path.exists() and check_md5(package_path, variation.get("MD5")):
                self._notify("检测到缓存包，跳过下载")
                self._progress(100, 100, "使用缓存包")
            else:
                mirrors = build_mirror_urls(url)
                self._notify("正在下载游戏包...")
                download_file(
                    mirrors,
                    package_path,
                    progress_cb=lambda cur, total: self._progress(cur, total, "正在下载游戏包"),
                    stop_event=stop_event,
                )
        except DownloadError as e:
            raise BedrockError(f"游戏包下载失败: {e}") from e

        if not verify_variation_md5(package_path, variation):
            raise BedrockError("游戏包 MD5 校验失败，请重新下载")

        self._notify("正在安装...")
        try:
            if build_type == "GDK":
                self._install_gdk(package_path, folder, game_type, name, stop_event)
            else:
                self._install_uwp(package_path, folder, game_type, name)
        except Exception as e:
            logger.error(f"基岩版安装失败: {e}")
            shutil.rmtree(folder, ignore_errors=True)
            if isinstance(e, BedrockError):
                raise
            raise BedrockError(f"安装失败: {e}") from e

        info = {
            "BuildType": build_type,
            "Version": version,
            "VersionName": name,
            "VersionType": build_type.upper() if build_type == "UWP" else game_type.capitalize(),
        }
        config_data = {"IsVersionIsolated": False, "IsEditModel": False, "OtherCommand": "", "IsModes": False}
        self._write_config(folder, info, config_data)
        self._notify("安装完成")
        self._progress(100, 100, "安装完成")
        return self._read_installed_info(folder) or {"name": name, "version": version, "build_type": build_type}

    def _install_gdk(
        self,
        package_path: Path,
        folder: Path,
        game_type: str,
        name: str,
        stop_event: Optional[threading.Event],
    ) -> None:
        """安装 GDK 版本（解包 XVD 容器）"""
        self._notify("正在解包游戏（GDK）...")
        xvd_mod.extract_gdk_package(
            package_path,
            folder,
            progress_cb=lambda cur, total, fname: self._progress(cur, total, f"正在解包 {fname}"),
            stop_event=stop_event,
        )
        if not (folder / launch_mod.GDK_EXE).exists():
            raise BedrockError("解包结果缺少 Minecraft.Windows.exe，包可能已损坏")

    def _install_uwp(self, package_path: Path, folder: Path, game_type: str, name: str) -> None:
        """安装 UWP 版本（解包 + 修改清单 + 注册）"""
        self._notify("正在解包游戏（UWP）...")
        appx_mod.extract_appx(
            package_path,
            folder,
            progress_cb=lambda cur, total: self._progress(cur, total, "正在解包游戏（UWP）"),
        )
        self._notify("正在修改应用清单...")
        appx_mod.edit_manifest(folder, name)
        ok, msg = env_mod.ensure_developer_mode(self._notify)
        if not ok:
            raise BedrockError(f"UWP 版本需要开发者模式: {msg}")
        self._notify("正在注册游戏包...")
        appx_mod.register_appx(folder)

    # ─── 启动 ─────────────────────────────────────────────────

    def launch_version(
        self,
        name: str,
        args: str = "",
        on_exit: Optional[Callable[[int], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> bool:
        """启动已安装的基岩版版本（阻塞直至游戏退出）

        Args:
            name: 实例名称
            args: 附加启动参数（如 minecraft://creator/?Editor=true）
            on_exit: 游戏退出回调（后台线程触发）
            stop_event: 取消事件

        Returns:
            True 表示启动成功（调用方在 on_exit 中感知退出）
        """
        ensure_windows_supported()
        info = next((v for v in self.get_installed_versions() if v["name"] == name), None)
        if info is None:
            raise BedrockError(f"版本 '{name}' 未安装")
        folder = Path(info["path"])
        build_type = info.get("build_type", "UWP")
        game_type = info.get("game_type", "release")

        self._notify("正在检测运行环境...")
        launch_mod.prepare_environment(build_type, self._notify)

        if build_type == "GDK":
            proc = launch_mod.launch_gdk(folder, args, self._notify)
        else:
            launch_mod.launch_uwp(folder, game_type, args, self._notify)
            proc = None

        self._notify("游戏已启动")
        if proc is not None:
            exit_code = proc.wait()
            if on_exit:
                on_exit(exit_code)
            return True

        # UWP 版本：轮询游戏进程直到退出
        import time

        while not (stop_event and stop_event.is_set()):
            pid = launch_mod.find_game_process(game_type)
            if pid:
                self._notify("游戏已启动")
                break
            time.sleep(1)
        else:
            return False
        if stop_event:
            while not stop_event.is_set():
                if launch_mod.find_game_process(game_type) is None:
                    break
                time.sleep(1)
            else:
                return False
        if on_exit:
            on_exit(0)
        return True

    # ─── 卸载 ─────────────────────────────────────────────────

    def remove_version(self, name: str) -> None:
        """删除已安装的基岩版版本（UWP 时同时注销注册的包）"""
        folder = self.versions_root / name
        if not folder.exists():
            raise BedrockError(f"版本 '{name}' 不存在")
        info = self._read_installed_info(folder)
        if info and info.get("build_type") == "UWP":
            package_name = launch_mod.PACKAGE_NAMES.get(info.get("game_type", "release"))
            if package_name:
                try:
                    installed = appx_mod.get_package_install_location(package_name)
                    if installed and Path(installed).resolve() == folder.resolve():
                        self._notify("正在注销游戏包...")
                        appx_mod.remove_appx_package(package_name)
                except Exception as e:
                    logger.warning(f"注销 AppX 包失败: {e}")
        shutil.rmtree(folder, ignore_errors=True)
        self._notify(f"已删除版本 {name}")

    # ─── 运行状态 ─────────────────────────────────────────────

    def is_game_running(self, game_type: str = "release") -> bool:
        """是否有基岩版游戏进程在运行"""
        try:
            return launch_mod.find_game_process(game_type) is not None
        except Exception:
            return False

