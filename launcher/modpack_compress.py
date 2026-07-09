"""通用压缩包 / 带启动器压缩包安装 Mixin 类

参考 PCL-CE:
- ModModpack.InstallPackCompress() - 检测 .minecraft/versions/X/X.json 结构
- ModModpack.InstallPackLauncherPack() - 检测内嵌 modpack.zip/modpack.mrpack
"""

import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from logzero import logger


class CompressPackMixin:
    """通用压缩包 & 带启动器压缩包安装 Mixin"""

    # ─── 读取压缩包信息 ─────────────────────────────────────

    def get_compress_pack_info(self, zip_path: str) -> Dict:
        """读取通用压缩包的元数据信息

        Args:
            zip_path: .zip 文件的绝对路径

        Returns:
            包含 name, mc_version 等字段的字典
        """
        if not os.path.isfile(zip_path):
            raise ValueError(f"文件不存在: {zip_path}")

        from launcher.modpack_types import ModpackType, detect_modpack_archive

        detection = detect_modpack_archive(zip_path)
        if detection.pack_type not in (ModpackType.GENERIC, ModpackType.LAUNCHER_PACK):
            raise ValueError(f"不是通用压缩包或启动器包: {detection.format_name}")

        # 尝试提取实例名
        instance_name = "imported-pack"
        mc_version = "unknown"

        if detection.pack_type == ModpackType.LAUNCHER_PACK:
            # 检查内嵌包的格式
            inner_format = "unknown"
            with zipfile.ZipFile(zip_path, "r") as zf:
                for marker in ("modpack.mrpack", "modpack.zip"):
                    if marker in {n.split("/")[-1] for n in zf.namelist()}:
                        inner_format = "mrpack" if marker.endswith(".mrpack") else "zip"
                        break

            return {
                "name": os.path.splitext(os.path.basename(zip_path))[0],
                "mc_version": mc_version,
                "format": "launcher_pack",
                "summary": f"带启动器的压缩包 (内嵌 {inner_format})",
                "inner_format": inner_format,
                "description": f"此压缩包包含另一个启动器和整合包文件，将递归提取。",
            }

        # 通用压缩包：检测版本信息
        pattern = re.compile(r"^(.*/)?\.minecraft/versions/([^/]+)/\2\.json$", re.IGNORECASE)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for entry in zf.namelist():
                m = pattern.match(entry)
                if m:
                    mc_version = m.group(2)
                    # 尝试读取版本名
                    try:
                        with zf.open(entry, "r") as f:
                            ver_data = json.loads(f.read())
                        instance_name = ver_data.get("id", mc_version)
                    except Exception:
                        instance_name = mc_version
                    break

        return {
            "name": instance_name,
            "mc_version": mc_version,
            "format": "compress",
            "summary": f"通用压缩包, MC {mc_version}",
            "description": (f"检测到 .minecraft 目录结构。" f"将把内容提取到 versions/{instance_name}/ 下。"),
        }

    # ─── 安装 ──────────────────────────────────────────────────

    def install_compress_pack(self, zip_path: str, instance_name: Optional[str] = None) -> Tuple[bool, str]:
        """
        安装通用压缩包

        流程:
        1. 定位 .minecraft 根目录
        2. 将 .minecraft 内容提取到 versions/<name>/
        3. 如果已有版本 JSON，尝试直接启动

        Args:
            zip_path: 压缩包路径
            instance_name: 实例名称

        Returns:
            (是否成功, 版本ID)
        """
        if not os.path.isfile(zip_path):
            return False, f"文件不存在: {zip_path}"

        mc_dir = self.minecraft_dir

        try:
            # ── 定位 .minecraft 路径 ──
            pattern = re.compile(r"^(.*/)?\.minecraft/versions/([^/]+)/\2\.json$", re.IGNORECASE)
            mc_prefix = ""
            detected_mc_version = "unknown"

            with zipfile.ZipFile(zip_path, "r") as zf:
                for entry in zf.namelist():
                    m = pattern.match(entry)
                    if m:
                        mc_prefix = (m.group(1) or "") + ".minecraft/"
                        detected_mc_version = m.group(2)
                        break

            if not mc_prefix:
                return False, "压缩包内未找到 .minecraft/versions/X/X.json 结构"

            if not instance_name:
                instance_name = self._sanitize_name(os.path.splitext(os.path.basename(zip_path))[0])

            version_dir = os.path.join(mc_dir, "versions", instance_name)
            os.makedirs(version_dir, exist_ok=True)

            # ── 解压 .minecraft 内容 ──
            self._set_status(f"正在解压 .minecraft 内容到 {instance_name}...")
            mc_prefix_norm = mc_prefix.replace("\\", "/")

            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    normalized = name.replace("\\", "/")
                    if not normalized.startswith(mc_prefix_norm) or normalized == mc_prefix_norm:
                        continue

                    info = zf.getinfo(name)
                    if info.file_size == 0 or info.is_dir():
                        continue

                    rel_path = normalized[len(mc_prefix_norm) :]
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

            logger.info(f"通用压缩包安装完成: {instance_name}")
            return True, instance_name

        except Exception as e:
            logger.error(f"通用压缩包安装失败: {e}")
            try:
                if "version_dir" in locals() and os.path.isdir(version_dir):
                    shutil.rmtree(version_dir)
            except Exception:
                pass
            return False, str(e)

    def install_launcher_pack(self, zip_path: str) -> Tuple[bool, str]:
        """
        安装带启动器的压缩包

        提取内嵌的 modpack.zip / modpack.mrpack 后递归安装。
        不解压后运行 .exe（FMCL 为 Python 启动器）。

        Args:
            zip_path: 压缩包路径

        Returns:
            (是否成功, 版本ID)
        """
        if not os.path.isfile(zip_path):
            return False, f"文件不存在: {zip_path}"

        try:
            # ── 查找内嵌的 modpack 文件 ──
            inner_file = None
            with zipfile.ZipFile(zip_path, "r") as zf:
                for marker in ("modpack.mrpack", "modpack.zip"):
                    candidates = [n for n in zf.namelist() if n.endswith(marker)]
                    if candidates:
                        inner_file = candidates[0]
                        break

            if inner_file is None:
                return False, "压缩包内未找到 modpack.mrrepack 或 modpack.zip"

            # 提取内嵌文件到临时目录
            import tempfile

            tmp_dir = tempfile.mkdtemp(prefix="fmcl_launcherpack_")
            inner_path = os.path.join(tmp_dir, os.path.basename(inner_file))

            with zipfile.ZipFile(zip_path, "r") as zf:
                with open(inner_path, "wb") as f:
                    f.write(zf.read(inner_file))

            logger.info(f"提取内嵌整合包: {inner_file} → {inner_path}")

            # 递归安装：调用统一入口
            result = self.install_modpack(inner_path)

            # 清理临时文件
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass

            return result

        except Exception as e:
            logger.error(f"带启动器压缩包安装失败: {e}")
            return False, str(e)

    # ─── 辅助方法 ────────────────────────────────────────────

    def _sanitize_name(self, name: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "imported-pack"
