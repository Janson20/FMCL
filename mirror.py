"""国内镜像源模块 - BMCLAPI 镜像加速

基于 bangbang93 的 BMCLAPI 镜像服务，提供国内加速下载支持。
镜像规则参考: https://bmclapi2.bangbang93.com
"""
import os
from typing import Dict, List, Optional, Any
from pathlib import Path

import requests
from logzero import logger

# 高性能 JSON 解析
try:
    import orjson as _json_mod

    def _json_loads(data):
        return _json_mod.loads(data)

except ImportError:
    import json as _json_mod  # type: ignore[no-redef]

    def _json_loads(data):
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return _json_mod.loads(data)


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
    "forge_maven_direct": (
        "https://maven.minecraftforge.net",
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
    MIRROR_URLS["forge_maven_direct"],
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

        # URL 重写缓存：避免对同一 URL 重复匹配规则
        self._url_cache: Dict[str, str] = {}

    def rewrite_url(self, url: str) -> str:
        """
        将官方URL转换为镜像URL（带缓存）

        Args:
            url: 原始URL

        Returns:
            转换后的URL，如果镜像未启用则返回原始URL
        """
        if not self.enabled:
            return url

        # 缓存命中
        cached = self._url_cache.get(url)
        if cached is not None:
            return cached

        # Java runtime API 不走镜像 (BMCLAPI 此端点 SSL 不稳定)
        if "/v1/products/java-runtime/" in url:
            self._url_cache[url] = url
            return url

        # 按前缀长度降序排列，优先匹配更精确的规则
        # 缓存排序结果避免重复排序
        _sorted_rules = getattr(self.__class__, '_sorted_rules', None)
        if _sorted_rules is None:
            _sorted_rules = sorted(URL_REPLACE_RULES, key=lambda r: len(r[0]), reverse=True)
            self.__class__._sorted_rules = _sorted_rules

        for official_prefix, mirror_prefix in _sorted_rules:
            if url.startswith(official_prefix):
                rewritten = url.replace(official_prefix, mirror_prefix, 1)
                logger.debug(f"镜像URL替换: {url} -> {rewritten}")
                # 限制缓存大小，防止内存泄漏
                if len(self._url_cache) > 10000:
                    self._url_cache.clear()
                self._url_cache[url] = rewritten
                return rewritten

        self._url_cache[url] = url
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
            # URL格式: /maven/net/minecraftforge/forge/{mcversion}-{forgeversion}/forge-{mcversion}-{forgeversion}-installer.jar
            full_version = f"{mc_version}-{forge_version}"
            download_url = (
                f"https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/"
                f"{full_version}/forge-{full_version}-installer.jar"
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
        猴子补丁: 替换 minecraft_launcher_lib 内部硬编码的官方 URL
        使其使用 BMCLAPI 镜像源进行下载

        需要在调用任何安装函数之前调用此方法

        补丁覆盖范围:
        - mojang_api: MOJANG_API_URL, VERSION_MANIFEST_URL, VERSION_MANIFEST_V2_URL, JAVA_RUNTIME_URL
        - install: libraries fallback URL, assets URL, version_manifest_v2 URL
        - mod_loader/_forge: _MAVEN_METADATA_URL, Forge.get_installer_url()
        - mod_loader/_fabric: Fabric 实例的 _maven_url, _game_url, _loader_url
        - mod_loader/_neoforge: _API_URL, Neoforge.get_installer_url()
        """
        if not self.enabled:
            logger.info("镜像源未启用，使用官方源")
            return

        try:
            import minecraft_launcher_lib

            # ── 1. mojang_api 模块 (v7.x 及更早版本) ──
            try:
                from minecraft_launcher_lib import mojang_api

                if hasattr(mojang_api, 'MOJANG_API_URL'):
                    mojang_api.MOJANG_API_URL = "https://bmclapi2.bangbang93.com"
                    logger.info(f"已替换 MOJANG_API_URL")
                if hasattr(mojang_api, 'VERSION_MANIFEST_URL'):
                    mojang_api.VERSION_MANIFEST_URL = MIRROR_URLS["version_manifest"]
                    logger.info(f"已替换 VERSION_MANIFEST_URL")
                if hasattr(mojang_api, 'VERSION_MANIFEST_V2_URL'):
                    mojang_api.VERSION_MANIFEST_V2_URL = MIRROR_URLS["version_manifest_v2"]
                    logger.info(f"已替换 VERSION_MANIFEST_V2_URL")
                if hasattr(mojang_api, 'JAVA_RUNTIME_URL'):
                    mojang_api.JAVA_RUNTIME_URL = MIRROR_URLS["java_runtime"][1]
                    logger.info(f"已替换 JAVA_RUNTIME_URL")
            except ImportError:
                # v8.0+ 已移除 mojang_api 模块，URL 在 install.py 和 _helper.py 中硬编码
                # 通过 _patch_install_module 和 _patch_helper_module 处理
                pass

            # ── 2. install 模块硬编码 URL ──
            self._patch_install_module()

            # ── 3. mod_loader 子模块 ──
            self._patch_mod_loader_module()

            # ── 4. _helper 模块中的 version_manifest 缓存 ──
            self._patch_helper_module()

            logger.info("minecraft_launcher_lib 镜像源补丁已应用")

        except ImportError:
            logger.warning("minecraft_launcher_lib 未安装，跳过补丁")
        except Exception as e:
            logger.error(f"应用镜像源补丁失败: {e}")

    def _patch_install_module(self):
        """修补 install 模块中硬编码的 URL

        核心策略: 全局 patch _helper.download_file，所有下载都会经过 URL 重写
        """
        try:
            from minecraft_launcher_lib import _helper

            # 全局 patch download_file — 这是所有文件下载的底层函数
            _original_download_file = _helper.download_file

            def _patched_download_file(url, *args, **kwargs):
                rewritten_url = self.rewrite_url(url)
                return _original_download_file(rewritten_url, *args, **kwargs)

            _helper.download_file = _patched_download_file
            logger.info("已全局修补 _helper.download_file (URL 重写)")

            # 全局 patch install_minecraft_version 中 requests.get 的 version_manifest URL
            from minecraft_launcher_lib import install as _install
            _original_install_minecraft_version = _install.install_minecraft_version

            def _patched_install_minecraft_version(version, minecraft_directory, callback=None):
                """重写 version_manifest_v2 请求 URL"""
                if callback is None:
                    callback = {}
                if isinstance(minecraft_directory, os.PathLike):
                    minecraft_directory = str(minecraft_directory)

                _original_requests_get = _install.requests.get

                def _patched_requests_get(url, *args, **kwargs):
                    rewritten_url = self.rewrite_url(url)
                    return _original_requests_get(rewritten_url, *args, **kwargs)

                _install.requests.get = _patched_requests_get
                try:
                    _original_install_minecraft_version(version, minecraft_directory, callback)
                finally:
                    _install.requests.get = _original_requests_get

            _install.install_minecraft_version = _patched_install_minecraft_version
            logger.info("已修补 install_minecraft_version (version_manifest URL 重写)")

        except (ImportError, AttributeError) as e:
            logger.debug(f"修补 install 模块跳过: {e}")

    def _patch_mod_loader_module(self):
        """为 mod_loader 模块中的各子模块应用镜像源补丁"""
        try:
            # ── Forge ──
            try:
                from minecraft_launcher_lib.mod_loader._forge import Forge, _MAVEN_METADATA_URL
                import minecraft_launcher_lib.mod_loader._forge as _forge_mod

                # 注意: _MAVEN_METADATA_URL 不替换，保留官方源用于版本查询
                # BMCLAPI 的 maven-metadata.xml 可能不是最新的，导致新版本(如1.21)查询失败
                # 只有实际下载文件时才需要镜像源加速 (通过 get_installer_url 重写)

                # Monkey-patch get_installer_url 方法
                _original_get_installer_url = Forge.get_installer_url

                def _patched_forge_get_installer_url(self_forge, minecraft_version, loader_version):
                    original_url = _original_get_installer_url(self_forge, minecraft_version, loader_version)
                    return self.rewrite_url(original_url)

                Forge.get_installer_url = _patched_forge_get_installer_url
                logger.info("已修补 Forge.get_installer_url (URL 重写)")
            except (ImportError, AttributeError) as e:
                logger.debug(f"修补 Forge 模块跳过: {e}")

            # ── Fabric ──
            try:
                from minecraft_launcher_lib.mod_loader._fabric import Fabric

                # 获取 Fabric 单例实例并替换实例变量
                try:
                    from minecraft_launcher_lib.mod_loader import get_mod_loader
                    fabric_instance = get_mod_loader("fabric")
                    fabric_instance._maven_url = "https://bmclapi2.bangbang93.com/maven/net/fabricmc/fabric-installer"
                    fabric_instance._game_url = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/game"
                    fabric_instance._loader_url = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader"
                    logger.info("已替换 Fabric 实例 URL (maven/game/loader)")
                except Exception as e:
                    logger.debug(f"替换 Fabric 实例 URL 失败: {e}")

                # download_file 已全局 patch，无需再局部 patch Fabric.install
            except (ImportError, AttributeError) as e:
                logger.debug(f"修补 Fabric 模块跳过: {e}")

            # ── NeoForge ──
            try:
                from minecraft_launcher_lib.mod_loader._neoforge import Neoforge

                # 注意: _API_URL 不替换，BMCLAPI 不镜像此 API 格式
                # 版本列表仍然从官方 maven.neoforged.net 获取

                # Monkey-patch get_installer_url 方法
                # BMCLAPI NeoForge 下载路径: /neoforge/version/{version}/download/installer.jar
                _original_neoforge_get_installer_url = Neoforge.get_installer_url

                def _patched_neoforge_get_installer_url(self_neoforge, minecraft_version, loader_version):
                    # 使用 BMCLAPI 专用 NeoForge 下载接口
                    return f"https://bmclapi2.bangbang93.com/neoforge/version/{loader_version}/download/installer.jar"

                Neoforge.get_installer_url = _patched_neoforge_get_installer_url
                logger.info("已修补 NeoForge.get_installer_url (BMCLAPI 专用下载接口)")

                # download_file 已全局 patch，无需再局部 patch NeoForge.install
            except (ImportError, AttributeError) as e:
                logger.debug(f"修补 NeoForge 模块跳过: {e}")

        except ImportError:
            pass

    def _patch_helper_module(self):
        """修补 _helper 模块中的请求缓存

        注意: 不再重写 get_requests_response_cache 的 URL。
        该函数被 Forge/Fabric/NeoForge 用于版本查询 API（如 maven-metadata.xml），
        这些查询应该走官方源以确保数据及时性。BMCLAPI 镜像的元数据可能滞后，
        导致新版本（如 1.21）查询失败。
        文件下载已通过 _patch_install_module 中的 download_file 补丁处理。
        """
        pass

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
