"""Minecraft启动器包"""
import os
import threading
from typing import Optional, Tuple

from logzero import logger

from launcher.core import MinecraftLauncher as MinecraftLauncherCore
from launcher.server import ServerMixin
from launcher.mrpack import MrpackMixin
from launcher.multimc import MultiMCMixin
from launcher.modpack_curseforge import CurseForgePackMixin
from launcher.modpack_hmcl import HMCLPackMixin
from launcher.modpack_mcbbs import MCBBSPackMixin
from launcher.modpack_compress import CompressPackMixin
from launcher.verify import concurrent_file_verify


class MinecraftLauncher(
    MultiMCMixin,
    CompressPackMixin,
    MCBBSPackMixin,
    HMCLPackMixin,
    CurseForgePackMixin,
    MrpackMixin,
    ServerMixin,
    MinecraftLauncherCore,
):
    """Minecraft启动器类 - 组合自核心模块、服务器模块和整合包模块"""

    # ─── 统一整合包安装入口 ──────────────────────────────────

    def install_modpack(
        self,
        pack_path: str,
        *,
        optional_file_ids: Optional[list] = None,
        instance_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        统一整合包安装入口 — 自动检测格式并分发到对应安装器

        支持的格式:
        - Modrinth (.mrpack)
        - MultiMC (.zip 含 mmc-pack.json)
        - CurseForge (.zip 含 manifest.json)
        - HMCL (.zip 含 modpack.json)
        - MCBBS (.zip 含 mcbbs.packmeta)
        - 带启动器压缩包 (.zip 含 modpack.mrrepack / modpack.zip)
        - 通用压缩包 (.zip 含 .minecraft 结构)

        Args:
            pack_path: 整合包文件路径
            optional_file_ids: 可选文件的 fileID 列表 (CurseForge 专用)
            instance_name: 实例名称（默认自动检测）

        Returns:
            (是否成功, 版本ID 或 错误信息)
        """
        from launcher.modpack_types import detect_modpack_archive, ModpackType

        try:
            detection = detect_modpack_archive(pack_path)
            logger.info(
                f"[Modpack] 检测整合包格式: {detection.format_name} "
                f"(base={detection.archive_base_folder or '(root)'})"
            )
        except Exception as e:
            return False, f"无法识别整合包格式: {e}"

        # 发送进度事件（与 UI 通信）
        self._mp_progress = {
            "phase": "detected",
            "format": detection.pack_type,
            "format_name": detection.format_name,
            "overall": 0,
        }

        dispatch = {
            ModpackType.MODRINTH: self._install_modrinth_wrapper,
            ModpackType.MULTIMC: self._install_multimc_wrapper,
            ModpackType.CURSEFORGE: self._install_curseforge_wrapper,
            ModpackType.HMCL: self._install_hmcl_wrapper,
            ModpackType.MCBBS: self._install_mcbbs_wrapper,
            ModpackType.LAUNCHER_PACK: self._install_launcher_pack_wrapper,
            ModpackType.GENERIC: self._install_compress_wrapper,
        }

        handler = dispatch.get(detection.pack_type)
        if handler is None:
            return False, f"整合包类型 '{detection.format_name}' 暂不支持"

        return handler(pack_path, optional_file_ids, instance_name)

    # ─── 格式分发包装 ───────────────────────────────────────

    def _install_modrinth_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_mrpack(path, optional_files=optional_ids or [])

    def _install_multimc_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_multimc_pack(path, instance_name=name)

    def _install_curseforge_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_curseforge_pack(path, optional_file_ids=optional_ids, instance_name=name)

    def _install_hmcl_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_hmcl_pack(path, instance_name=name)

    def _install_mcbbs_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_mcbbs_pack(path, instance_name=name)

    def _install_launcher_pack_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_launcher_pack(path)

    def _install_compress_wrapper(
        self, path: str, optional_ids: Optional[list], name: Optional[str]
    ) -> Tuple[bool, str]:
        return self.install_compress_pack(path, instance_name=name)


__all__ = ["MinecraftLauncher", "concurrent_file_verify"]
