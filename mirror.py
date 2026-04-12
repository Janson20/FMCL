"""国内镜像源模块 - BMCLAPI 镜像加速

基于 bangbang93 的 BMCLAPI 镜像服务，提供国内加速下载支持。
镜像规则参考: https://bmclapi2.bangbang93.com
"""
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

import requests
from logzero import logger


# ─── BMCLAPI 镜像地址映射 ──────────────────────────────────────────
MIRROR_URLS = {
    # 版本清单
    "version_manifest": "https://bmclapi2.bangbang93.com/mc/game/version_manifest.json",
    "version_manifest_v2": "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json",
    # URL 前缀替换规则: (官方前缀, 镜像前缀)
    "launchermeta_mojang": (
        "https://launchermeta.mojang.com/",
        "https://bmclapi2.bangbang93.com/",
    ),
    "launcher_mojang": (
        "https://launcher.mojang.com/",
        "https://bmclapi2.bangbang93.com/",
    ),
    # 资源下载
    "assets": (
        "http://resources.download.minecraft.net",
        "https://bmclapi2.bangbang93.com/assets",
    ),
    # 库文件
    "libraries": (
        "https://libraries.minecraft.net/",
        "https://bmclapi2.bangbang93.com/maven/",
    ),
    # Java 运行时
    "java_runtime": (
        "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
        "https://bmclapi2.bangbang93.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
    ),
    # Forge
    "forge_maven": (
        "https://files.minecraftforge.net/maven",
        "https://bmclapi2.bangbang93.com/maven",
    ),
    # Fabric
    "fabric_meta": (
        "https://meta.fabricmc.net",
        "https://bmclapi2.bangbang93.com/fabric-meta",
    ),
    "fabric_maven": (
        "https://maven.fabricmc.net",
        "https://bmclapi2.bangbang93.com/maven",
    ),
    # NeoForge
    "neoforge_forge": (
        "https://maven.neoforged.net/releases/net/neoforged/forge",
        "https://bmclapi2.bangbang93.com/maven/net/neoforged/forge",
    ),
    "neoforge_neoforge": (
        "https://maven.neoforged.net/releases/net/neoforged/neoforge",
        "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge",
    ),
    # LiteLoader
    "liteloader": (
        "http://dl.liteloader.com/versions/versions.json",
        "https://bmclapi.bangbang93.com/maven/com/mumfrey/liteloader/versions.json",
    ),
    # authlib-injector
    "authlib_injector": (
        "https://authlib-injector.yushi.moe",
        "https://bmclapi2.bangbang93.com/mirrors/authlib-injector",
    ),
}

# 所有 URL 前缀替换规则 (用于批量替换)
URL_REPLACE_RULES: List[tuple] = [
    MIRROR_URLS["launchermeta_mojang"],
    MIRROR_URLS["launcher_mojang"],
    MIRROR_URLS["assets"],
    MIRROR_URLS["libraries"],
    MIRROR_URLS["forge_maven"],
    MIRROR_URLS["fabric_meta"],
    MIRROR_URLS["fabric_maven"],
    MIRROR_URLS["neoforge_forge"],
    MIRROR_URLS["neoforge_neoforge"],
]


class MirrorSource:
    """镜像源管理器"""

    def __init__(self, enabled: bool = True):
        """
        初始化镜像源管理器

        Args:
            enabled: 是否启用镜像源
        """
        self.enabled = enabled
        self._session = requests.Session()
        self._session.timeout = 15

    def rewrite_url(self, url: str) -> str:
        """
        将官方URL转换为镜像URL

        Args:
            url: 原始URL

        Returns:
            转换后的URL，如果镜像未启用则返回原始URL
        """
        if not self.enabled:
            return url

        for official_prefix, mirror_prefix in URL_REPLACE_RULES:
            if url.startswith(official_prefix):
                rewritten = url.replace(official_prefix, mirror_prefix, 1)
                logger.debug(f"镜像URL替换: {url} -> {rewritten}")
                return rewritten

        return url

    def rewrite_version_json_urls(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        递归替换版本JSON中所有Mojang URL为BMCLAPI镜像URL

        Args:
            data: 版本JSON数据

        Returns:
            替换后的数据
        """
        if not self.enabled:
            return data

        if isinstance(data, str):
            return self.rewrite_url(data)
        elif isinstance(data, dict):
            return {k: self.rewrite_version_json_urls(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.rewrite_version_json_urls(item) for item in data]
        return data

    def get_version_manifest(self) -> Dict[str, Any]:
        """
        获取版本清单（优先使用镜像源）

        Returns:
            版本清单数据
        """
        url = MIRROR_URLS["version_manifest"] if self.enabled else \
            "https://launchermeta.mojang.com/mc/game/version_manifest.json"

        logger.info(f"正在获取版本清单: {url}")
        response = self._session.get(url, timeout=15)
        response.raise_for_status()
        return response.json()

    def get_version_manifest_v2(self) -> Dict[str, Any]:
        """
        获取v2版本清单（优先使用镜像源）

        Returns:
            v2版本清单数据
        """
        url = MIRROR_URLS["version_manifest_v2"] if self.enabled else \
            "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

        logger.info(f"正在获取v2版本清单: {url}")
        response = self._session.get(url, timeout=15)
        response.raise_for_status()
        return response.json()

    def get_version_info(self, version_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定版本的详细信息（优先使用镜像源）

        Args:
            version_id: 版本ID

        Returns:
            版本信息字典，失败返回None
        """
        try:
            manifest = self.get_version_manifest()
            for version in manifest.get("versions", []):
                if version.get("id") == version_id:
                    version_url = self.rewrite_url(version.get("url", ""))
                    logger.info(f"正在获取版本信息: {version_url}")
                    resp = self._session.get(version_url, timeout=15)
                    resp.raise_for_status()
                    return resp.json()

            logger.warning(f"未找到版本: {version_id}")
            return None

        except Exception as e:
            logger.error(f"获取版本信息失败: {e}")
            return None

    def get_latest_version(self) -> Dict[str, str]:
        """
        获取最新版本号（优先使用镜像源）

        Returns:
            {"release": "x.x.x", "snapshot": "xx.xx.xx"}
        """
        try:
            manifest = self.get_version_manifest()
            return manifest.get("latest", {})
        except Exception as e:
            logger.error(f"获取最新版本失败: {e}")
            return {}

    def get_forge_download_url(self, version: str) -> Optional[str]:
        """
        获取Forge安装器下载URL（通过BMCLAPI）

        Args:
            version: Minecraft版本号

        Returns:
            Forge安装器下载URL
        """
        if not self.enabled:
            return None

        try:
            # 使用BMCLAPI获取Forge版本列表
            url = f"https://bmclapi2.bangbang93.com/forge/minecraft/{version}"
            logger.info(f"正在获取Forge版本列表: {url}")
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()

            forge_versions = resp.json()
            if not forge_versions:
                logger.warning(f"未找到 Forge {version} 版本")
                return None

            # 取最新版本
            latest = forge_versions[-1]
            # 构建下载URL - BMCLAPI直接提供Forge安装器
            forge_version = latest.get("version", "")
            mc_version = latest.get("mcversion", version)

            if not forge_version:
                return None

            # BMCLAPI Forge安装器下载链接
            download_url = (
                f"https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/"
                f"{mc_version}-{forge_version}-{mc_version}/"
                f"forge-{mc_version}-{forge_version}-{mc_version}-installer.jar"
            )
            logger.info(f"Forge下载链接: {download_url}")
            return download_url

        except Exception as e:
            logger.error(f"获取Forge下载链接失败: {e}")
            return None

    def get_fabric_loader_versions(self, game_version: str) -> List[Dict[str, Any]]:
        """
        获取Fabric Loader版本列表（优先使用镜像源）

        Args:
            game_version: 游戏版本

        Returns:
            Fabric Loader版本列表
        """
        try:
            base = "https://bmclapi2.bangbang93.com/fabric-meta" if self.enabled else \
                "https://meta.fabricmc.net"
            url = f"{base}/v2/versions/loader/{game_version}"
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"获取Fabric版本失败: {e}")
            return []

    def patch_minecraft_launcher_lib(self):
        """
        猴子补丁: 替换 minecraft_launcher_lib 内部的 Mojang URL
        使其使用镜像源进行下载

        需要在调用任何安装函数之前调用此方法
        """
        if not self.enabled:
            logger.info("镜像源未启用，使用官方源")
            return

        try:
            import minecraft_launcher_lib
            from minecraft_launcher_lib import mojang_api

            # 替换 mojang_api 模块中的 URL 常量
            if hasattr(mojang_api, 'MOJANG_API_URL'):
                original = mojang_api.MOJANG_API_URL
                mojang_api.MOJANG_API_URL = "https://bmclapi2.bangbang93.com"
                logger.info(f"已替换 MOJANG_API_URL: {original} -> {mojang_api.MOJANG_API_URL}")

            # 替换 version_manifest URL
            if hasattr(mojang_api, 'VERSION_MANIFEST_URL'):
                original = mojang_api.VERSION_MANIFEST_URL
                mojang_api.VERSION_MANIFEST_URL = MIRROR_URLS["version_manifest"]
                logger.info(f"已替换 VERSION_MANIFEST_URL: {original} -> {mojang_api.VERSION_MANIFEST_URL}")

            if hasattr(mojang_api, 'VERSION_MANIFEST_V2_URL'):
                original = mojang_api.VERSION_MANIFEST_V2_URL
                mojang_api.VERSION_MANIFEST_V2_URL = MIRROR_URLS["version_manifest_v2"]
                logger.info(f"已替换 VERSION_MANIFEST_V2_URL")

            # 替换 Java 运行时 URL
            if hasattr(mojang_api, 'JAVA_RUNTIME_URL'):
                original = mojang_api.JAVA_RUNTIME_URL
                mojang_api.JAVA_RUNTIME_URL = MIRROR_URLS["java_runtime"][1]
                logger.info(f"已替换 JAVA_RUNTIME_URL")

            # 替换 libraries URL - 在 install 模块中
            try:
                from minecraft_launcher_lib import install as _install
                if hasattr(_install, 'LIBRARIES_URL'):
                    _install.LIBRARIES_URL = "https://bmclapi2.bangbang93.com/maven/"
                    logger.info("已替换 LIBRARIES_URL")
            except (ImportError, AttributeError):
                pass

            logger.info("✅ minecraft_launcher_lib 镜像源补丁已应用")

        except ImportError:
            logger.warning("minecraft_launcher_lib 未安装，跳过补丁")
        except Exception as e:
            logger.error(f"应用镜像源补丁失败: {e}")

    def get_mirror_name(self) -> str:
        """获取当前镜像源名称"""
        return "BMCLAPI (bangbang93)" if self.enabled else "Mojang 官方源"

    def test_connection(self) -> bool:
        """
        测试镜像源连接

        Returns:
            连接是否成功
        """
        try:
            url = MIRROR_URLS["version_manifest"] if self.enabled else \
                "https://launchermeta.mojang.com/mc/game/version_manifest.json"
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return "latest" in data
        except Exception as e:
            logger.error(f"连接测试失败 ({self.get_mirror_name()}): {e}")
            return False


# 全局镜像源实例（默认启用）
mirror = MirrorSource(enabled=True)
