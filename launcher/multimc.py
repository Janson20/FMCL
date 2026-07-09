"""MultiMC 整合包安装 Mixin 类

参考 HMCL (Hello Minecraft! Launcher) 的 MultiMC 格式实现，
实现 MultiMC / Prism Launcher / PolyMC 整合包的完整安装、更新和启动支持。
"""

import hashlib
import json
import os
import re
import shutil
import threading
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import requests as req
from logzero import logger

from launcher.multimc_types import (
    MultiMCManifest,
    MultiMCManifestComponent,
    MultiMCInstanceConfig,
    MultiMCInstancePatch,
    ModpackConfiguration,
    FileInfo,
    detect_multimc_format,
    find_root_entry,
    read_mmc_pack_json,
    read_instance_cfg,
    get_meta_url,
    get_component_loader,
    is_minecraft_component,
    compute_file_sha1,
)

DEFAULT_UA = "MCL-MultiMC-Installer/1.0 (compatible; Minecraft Launcher)"


class MultiMCMixin:
    """MultiMC 整合包安装 Mixin 类

    通过多重继承混入 MinecraftLauncher 类，提供：
    - get_multimc_pack_info(): 读取整合包元数据
    - install_multimc_pack(): 客户端安装
    - install_multimc_pack_server(): 服务端安装
    - update_multimc_pack(): 增量更新
    """

    PARALLEL_DOWNLOADS = 8

    # ─── 读取整合包信息 ──────────────────────────────────────

    def get_multimc_pack_info(self, zip_path: str) -> Dict[str, Any]:
        """读取 MultiMC 整合包的元数据信息。"""
        if not os.path.isfile(zip_path):
            raise ValueError(f"文件不存在: {zip_path}")

        is_mmc, _ = detect_multimc_format(zip_path)
        if not is_mmc:
            raise ValueError("不是有效的 MultiMC 整合包")

        root_entry = find_root_entry(zip_path)
        mmc_manifest = read_mmc_pack_json(zip_path, root_entry)
        instance_cfg = read_instance_cfg(zip_path, root_entry)

        mc_version = mmc_manifest.get_minecraft_version()
        loader_info = mmc_manifest.get_loader_info()

        components_info = []
        for comp in mmc_manifest.components:
            if not comp.dependency_only:
                components_info.append({
                    "uid": comp.uid,
                    "version": comp.version,
                    "name": comp.cached_name or comp.uid,
                    "important": comp.important,
                })

        return {
            "name": instance_cfg.name,
            "mc_version": mc_version or instance_cfg.game_version or "",
            "loader_type": loader_info[0] if loader_info else None,
            "loader_version": loader_info[1] if loader_info else None,
            "description": instance_cfg.notes or "",
            "icon_key": instance_cfg.icon_key,
            "components": components_info,
            "format": "multimc",
        }

    # ─── Patch 加载 ──────────────────────────────────────────

    def _load_component_patches(
        self,
        zip_path: str,
        components: List[MultiMCManifestComponent],
        mc_version: str,
        root_entry: str = "",
    ) -> List[MultiMCInstancePatch]:
        """加载所有组件的 JSON Patch（本地优先，远端兜底）。"""
        patches: List[MultiMCInstancePatch] = []
        zip_names: List[str] = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            zip_names = zf.namelist()

        for comp in components:
            component_uid = comp.uid
            patch_path = f"{root_entry}patches/{component_uid}.json"

            if patch_path in zip_names:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    text = zf.read(patch_path).decode("utf-8", errors="replace")
                try:
                    patch = MultiMCInstancePatch.from_json(text, component_uid)
                    logger.info(f"加载本地 patch: {component_uid}")
                    patches.append(patch)
                except Exception as e:
                    logger.warning(f"解析本地 patch 失败 {component_uid}: {e}")
                    patch = self._download_patch(component_uid, comp.version, mc_version)
                    if patch:
                        patches.append(patch)
            else:
                patch = self._download_patch(component_uid, comp.version, mc_version)
                if patch:
                    patches.append(patch)

        return patches

    def _download_patch(
        self, component_uid: str, version: Optional[str], mc_version: str
    ) -> Optional[MultiMCInstancePatch]:
        """从 meta.multimc.org 下载组件 Patch。"""
        try:
            url = get_meta_url(component_uid, version, mc_version)
            logger.info(f"下载远端 patch: {url}")
            resp = req.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=30)
            resp.raise_for_status()
            return MultiMCInstancePatch.from_json(resp.text, component_uid)
        except Exception as e:
            logger.warning(f"下载 patch 失败 {component_uid}: {e}")
            return None

    def _resolve_patch_dependencies(
        self, patches: List[MultiMCInstancePatch], mc_version: str
    ) -> List[MultiMCInstancePatch]:
        """递归解析 Patch 依赖。"""
        existed: Dict[str, MultiMCInstancePatch] = {p.uid: p for p in patches}

        while True:
            missing_found = False
            for patch in list(existed.values()):
                for require in patch.requires:
                    if require.uid not in existed:
                        logger.info(f"解析缺失依赖: {require.uid}")
                        version = require.equals_version or require.suggests
                        dep_patch = self._download_patch(require.uid, version, mc_version)
                        if dep_patch:
                            existed[dep_patch.uid] = dep_patch
                            missing_found = True
                        else:
                            logger.warning(f"无法解析依赖: {require.uid}")
            if not missing_found:
                break

        return list(existed.values())

    def _merge_patches_to_version_json(
        self, patches: List[MultiMCInstancePatch], version_id: str
    ) -> Dict[str, Any]:
        """将多个组件 Patches 合并为标准 version.json。

        参考 HMCL MultiMCInstancePatch.resolveArtifact() 的合并逻辑。
        """
        if not patches:
            raise ValueError("没有可用的组件 patches")

        for p in patches:
            if p.format_version != 1:
                raise ValueError(
                    f"不支持的 patch 格式版本: {p.uid} formatVersion={p.format_version}"
                )

        last = patches[-1]
        minecraft_args = last.minecraft_arguments or ""
        main_class = last.main_class or "net.minecraft.client.main.Main"
        asset_index = last.asset_index
        jvm_args: List[str] = list(last.jvm_args)
        traits: List[str] = list(last.traits)
        tweakers: List[str] = list(last.tweakers)
        libraries: List[Dict] = list(last.libraries)
        maven_files: List[Dict] = list(last.maven_files)
        jar_mods: List[Dict] = list(last.jar_mods)
        java_majors: List[int] = list(last.compatible_java_majors)
        main_jar = last.main_jar

        for patch in reversed(patches[:-1]):
            if not minecraft_args and patch.minecraft_arguments:
                minecraft_args = patch.minecraft_arguments
            for arg in patch.jvm_args:
                if arg not in jvm_args:
                    jvm_args.append(arg)
            if not main_class or main_class == "net.minecraft.client.main.Main":
                main_class = patch.main_class or main_class
            if not asset_index:
                asset_index = patch.asset_index
            if not java_majors:
                java_majors = list(patch.compatible_java_majors)
            if not main_jar:
                main_jar = patch.main_jar
            for t in patch.traits:
                if t not in traits:
                    traits.append(t)
            for t in patch.tweakers:
                if t not in tweakers:
                    tweakers.append(t)
            for lib in patch.libraries:
                if lib not in libraries:
                    libraries.append(lib)
            for mf in patch.maven_files:
                if mf not in maven_files:
                    maven_files.append(mf)
            for jm in patch.jar_mods:
                if jm not in jar_mods:
                    jar_mods.append(jm)

        traits = list(dict.fromkeys(traits))
        tweakers = list(dict.fromkeys(tweakers))

        tokens = minecraft_args.split()
        mc_args_list: List[str] = []
        i = 0
        while i < len(tokens):
            if tokens[i] == "--tweakClass" and i + 1 < len(tokens):
                tweakers.append(tokens[i + 1])
                i += 2
            else:
                mc_args_list.append(tokens[i])
                i += 1

        for tweaker in tweakers:
            mc_args_list.append("--tweakClass")
            mc_args_list.append(tweaker)

        for trait in traits:
            if trait == "FirstThreadOnMacOS":
                import platform
                if platform.system() == "Darwin":
                    jvm_args.append("-XstartOnFirstThread")
            elif trait in ("XR:Initial", "texturepacks", "no-texturepacks"):
                logger.debug(f"跳过暂不支持的 trait: {trait}")
            elif trait:
                logger.warning(f"未知 trait: {trait}")

        game_args: List[Any] = [a for a in mc_args_list if a]
        jvm_arg_objects: List[Any] = list(dict.fromkeys([a for a in jvm_args if a]))

        version_json: Dict[str, Any] = {
            "id": version_id,
            "type": "release",
            "mainClass": main_class,
            "minecraftArguments": " ".join(game_args),
            "arguments": {"game": game_args, "jvm": jvm_arg_objects},
            "libraries": libraries,
            "logging": {},
        }

        if asset_index:
            version_json["assetIndex"] = asset_index
        if main_jar:
            version_json["downloads"] = {
                "client": main_jar.get("downloads", {}).get("artifact", main_jar),
            }

        game_version = None
        for patch in patches:
            if is_minecraft_component(patch.uid):
                game_version = patch.version
                break

        return {
            "version_json": version_json,
            "game_version": game_version,
            "main_jar": main_jar,
            "jar_mod_file_names": [
                jm.get("name", "") for jm in jar_mods
            ],
            "maven_files": maven_files,
            "libraries": libraries,
        }

    # ─── 文件操作 ────────────────────────────────────────────

    def _extract_minecraft_overlay(
        self, zip_path: str, version_dir: str, root_entry: str = ""
    ) -> List[FileInfo]:
        """解压 .minecraft/ 覆盖文件并计算 SHA-1 哈希。"""
        overlay_prefix = f"{root_entry}.minecraft/"
        file_infos: List[FileInfo] = []
        os.makedirs(version_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if not name.startswith(overlay_prefix) or name.endswith("/"):
                    continue
                rel_path = name[len(overlay_prefix):]
                if not rel_path:
                    continue

                target_path = os.path.join(version_dir, rel_path)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(name) as src:
                    content = src.read()
                    with open(target_path, "wb") as dst:
                        dst.write(content)
                sha1 = hashlib.sha1(content).hexdigest()
                file_infos.append(FileInfo(path=rel_path, hash=sha1))

        logger.info(f"解压覆盖文件完成: {len(file_infos)} 个文件 -> {version_dir}")
        return file_infos

    def _copy_embedded_libraries(
        self, zip_path: str, version_dir: str, root_entry: str = ""
    ) -> None:
        """复制内嵌 libraries/ 目录。"""
        lib_prefix = f"{root_entry}libraries/"
        target_lib_dir = os.path.join(version_dir, "libraries")

        with zipfile.ZipFile(zip_path, "r") as zf:
            lib_names = [
                n for n in zf.namelist()
                if n.startswith(lib_prefix) and not n.endswith("/")
            ]
            if not lib_names:
                return

            os.makedirs(target_lib_dir, exist_ok=True)
            for name in lib_names:
                rel_path = name[len(lib_prefix):]
                target_path = os.path.join(target_lib_dir, rel_path)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())

        logger.info(f"复制内嵌库完成: {len(lib_names)} 个文件")

    def _copy_icon(
        self, zip_path: str, version_dir: str,
        icon_key: Optional[str], root_entry: str = "",
    ) -> None:
        """复制图标文件。"""
        if not icon_key:
            return
        icon_path = f"{root_entry}{icon_key}.png"
        with zipfile.ZipFile(zip_path, "r") as zf:
            if icon_path not in zf.namelist():
                return
            target = os.path.join(version_dir, "icon.png")
            with zf.open(icon_path) as src, open(target, "wb") as dst:
                dst.write(src.read())
        logger.info(f"图标已复制: {icon_key}")

    def _apply_jar_mods(
        self, zip_path: str, version_dir: str,
        jar_mod_names: List[str], root_entry: str = "",
    ) -> bool:
        """将 jarmods/ 合并到客户端主 JAR。"""
        if not jar_mod_names:
            return True

        jar_mod_prefix = f"{root_entry}jarmods/"
        version_jar = os.path.join(
            version_dir, f"{os.path.basename(version_dir)}.jar"
        )
        if not os.path.isfile(version_jar):
            logger.warning(f"主 JAR 不存在: {version_jar}")
            return False

        try:
            with zipfile.ZipFile(zip_path, "r") as src_zip, \
                 zipfile.ZipFile(version_jar, "a") as dst_zip:
                for jm_name in jar_mod_names:
                    jm_path = f"{jar_mod_prefix}{jm_name}"
                    if jm_path not in src_zip.namelist():
                        logger.warning(f"JAR mod 不存在: {jm_path}")
                        continue
                    jm_content = src_zip.read(jm_path)
                    try:
                        import io
                        with zipfile.ZipFile(io.BytesIO(jm_content), "r") as jm_zip:
                            dst_names = set(dst_zip.namelist())
                            for en in jm_zip.namelist():
                                if not en.endswith("/") and en not in dst_names:
                                    dst_zip.writestr(en, jm_zip.read(en))
                        logger.info(f"JAR mod 已合并: {jm_name}")
                    except zipfile.BadZipFile:
                        logger.warning(f"JAR mod 非 zip: {jm_name}")
            return True
        except Exception as e:
            logger.error(f"应用 JAR mods 失败: {e}")
            return False

    # ─── 配置应用 ────────────────────────────────────────────

    def _apply_instance_config_to_settings(
        self, instance_cfg: MultiMCInstanceConfig, version_id: str
    ) -> None:
        """记录 instance.cfg 配置供启动时使用。"""
        if not hasattr(self, "_mmc_instance_configs"):
            self._mmc_instance_configs = {}
        self._mmc_instance_configs[version_id] = instance_cfg
        logger.info(f"已应用 instance.cfg 设置: {instance_cfg.name}")

    def _get_instance_launch_overrides(self, version_id: str) -> Dict[str, Any]:
        """获取 MultiMC 实例的启动覆盖参数。"""
        if not hasattr(self, "_mmc_instance_configs"):
            return {}
        cfg = self._mmc_instance_configs.get(version_id)
        if not cfg:
            return {}

        overrides: Dict[str, Any] = {}
        if cfg.override_java_location and cfg.java_path:
            overrides["java_path"] = cfg.java_path
        if cfg.override_memory:
            if cfg.max_memory:
                overrides["max_memory"] = cfg.max_memory
            if cfg.min_memory:
                overrides["min_memory"] = cfg.min_memory
        if cfg.override_java_args and cfg.jvm_args:
            overrides["jvm_args"] = cfg.jvm_args
        if cfg.override_window:
            overrides["fullscreen"] = cfg.fullscreen
            if cfg.width:
                overrides["width"] = cfg.width
            if cfg.height:
                overrides["height"] = cfg.height
        if cfg.override_commands:
            if cfg.wrapper_command:
                overrides["wrapper_command"] = cfg.wrapper_command
            if cfg.pre_launch_command:
                overrides["pre_launch_command"] = cfg.pre_launch_command
            if cfg.post_exit_command:
                overrides["post_exit_command"] = cfg.post_exit_command
        return overrides

    # ─── ModpackConfiguration 持久化 ─────────────────────────

    def _save_modpack_configuration(
        self, version_id: str, instance_cfg: MultiMCInstanceConfig,
        file_infos: List[FileInfo],
    ) -> None:
        """保存 ModpackConfiguration 供增量更新。"""
        mc_dir = getattr(self, "minecraft_dir", "")
        config = ModpackConfiguration(
            manifest=instance_cfg.to_dict(),
            type="MultiMC", name=version_id,
            version=getattr(instance_cfg, 'game_version', '') or "",
            overrides=file_infos,
        )
        config_path = os.path.join(mc_dir, "versions", version_id, "mmc_config.json")
        config.save_to_file(config_path)
        logger.info(f"ModpackConfiguration 已保存: {config_path}")

    def _load_modpack_configuration(
        self, version_id: str
    ) -> Optional[ModpackConfiguration]:
        """加载 ModpackConfiguration。"""
        mc_dir = getattr(self, "minecraft_dir", "")
        config_path = os.path.join(mc_dir, "versions", version_id, "mmc_config.json")
        return ModpackConfiguration.load_from_file(config_path)

    # ================== 客户端安装 ==================

    def _install_mc_and_loader_from_patches(
        self, game_version: str, loader_type: Optional[str],
        loader_version: Optional[str], merged: Dict[str, Any],
    ) -> Tuple[bool, str]:
        import minecraft_launcher_lib as _mcllib
        mc_dir = getattr(self, "minecraft_dir", "")
        callback = self._get_callback() if hasattr(self, "_get_callback") else {}

        try:
            _mcllib.install.install_minecraft_version(game_version, mc_dir, callback=callback)
        except Exception as e:
            return False, f"Minecraft {game_version} install failed: {e}"

        version_json = merged.get("version_json", {})
        version_id = version_json.get("id", game_version)

        if loader_type and loader_version:
            try:
                loader_map = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}
                loader_name = loader_map.get(loader_type)
                if loader_name:
                    loader = _mcllib.mod_loader.get_mod_loader(loader_name)
                    loader.install(game_version, mc_dir, loader_version=loader_version, callback=callback)
                    if loader_type == "fabric":
                        version_id = f"fabric-loader-{loader_version}-{game_version}"
                    elif loader_type == "forge":
                        version_id = f"{game_version}-forge-{loader_version}"
                    elif loader_type == "quilt":
                        version_id = f"quilt-loader-{loader_version}-{game_version}"
                    elif loader_type == "neoforge":
                        version_id = f"{game_version}-neoforge-{loader_version}"
            except Exception as e:
                logger.warning(f"Loader install failed (continuing): {e}")

        return True, version_id

    def install_multimc_pack(
        self, zip_path: str, optional_files: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        if not os.path.isfile(zip_path):
            return False, f"file not found: {zip_path}"

        is_mmc, _ = detect_multimc_format(zip_path)
        if not is_mmc:
            return False, "not a valid MultiMC modpack"

        mc_dir = getattr(self, "minecraft_dir", "")
        version_dir = ""

        try:
            root_entry = find_root_entry(zip_path)
            mmc_manifest = read_mmc_pack_json(zip_path, root_entry)
            instance_cfg = read_instance_cfg(zip_path, root_entry,
                default_name=os.path.splitext(os.path.basename(zip_path))[0])

            mc_version = mmc_manifest.get_minecraft_version()
            if not mc_version:
                return False, "mmc-pack.json missing net.minecraft component"

            loader_info = mmc_manifest.get_loader_info()
            loader_type = loader_info[0] if loader_info else None
            loader_version = loader_info[1] if loader_info else None
            version_id = instance_cfg.name
            version_dir = os.path.join(mc_dir, "versions", version_id)

            self._set_status(f"parsing modpack: {instance_cfg.name}")

            patches = self._load_component_patches(zip_path, mmc_manifest.components, mc_version, root_entry)
            if not patches:
                return False, "no available component patches"

            self._set_status("resolving component dependencies...")
            patches = self._resolve_patch_dependencies(patches, mc_version)

            self._set_status("merging component configs...")
            merged = self._merge_patches_to_version_json(patches, version_id)

            self._set_status("parallel installing...")
            overlay_error: Optional[Exception] = None
            install_error: Optional[Exception] = None
            overlay_infos: List[FileInfo] = []
            final_version_id = version_id

            def _do_overlay():
                nonlocal overlay_error, overlay_infos
                try:
                    os.makedirs(version_dir, exist_ok=True)
                    overlay_infos = self._extract_minecraft_overlay(zip_path, version_dir, root_entry)
                except Exception as e:
                    overlay_error = e

            def _do_install():
                nonlocal install_error, final_version_id
                try:
                    ok, vid = self._install_mc_and_loader_from_patches(mc_version, loader_type, loader_version, merged)
                    if ok:
                        final_version_id = vid
                    else:
                        install_error = Exception(vid)
                except Exception as e:
                    install_error = e

            t_a = threading.Thread(target=_do_overlay, daemon=True)
            t_b = threading.Thread(target=_do_install, daemon=True)
            t_a.start(); t_b.start()
            t_a.join(); t_b.join()

            if overlay_error:
                self._cleanup_version_dir(version_dir)
                return False, f"overlay extraction failed: {overlay_error}"
            if install_error:
                self._cleanup_version_dir(version_dir)
                return False, f"game install failed: {install_error}"

            if final_version_id != version_id:
                old_dir = version_dir
                version_dir = os.path.join(mc_dir, "versions", final_version_id)
                if old_dir != version_dir and os.path.isdir(old_dir) and not os.path.isdir(version_dir):
                    shutil.move(old_dir, version_dir)
                version_id = final_version_id

            self._set_status("supplementing config...")
            self._supplement_version_json(version_id, merged)

            self._set_status("copying embedded files...")
            self._copy_embedded_libraries(zip_path, version_dir, root_entry)
            self._copy_icon(zip_path, version_dir, instance_cfg.icon_key, root_entry)

            jar_mod_names = merged.get("jar_mod_file_names", [])
            if jar_mod_names:
                self._set_status("applying JAR mods...")
                self._apply_jar_mods(zip_path, version_dir, jar_mod_names, root_entry)

            self._apply_instance_config_to_settings(instance_cfg, version_id)
            self._save_modpack_configuration(version_id, instance_cfg, overlay_infos)

            self._set_status(f"install complete: {instance_cfg.name}")
            return True, version_id

        except Exception as e:
            logger.error(f"MultiMC install failed: {e}")
            if version_dir:
                self._cleanup_version_dir(version_dir)
            return False, str(e)

    def _supplement_version_json(self, version_id: str, merged: Dict[str, Any]) -> None:
        mc_dir = getattr(self, "minecraft_dir", "")
        json_path = os.path.join(mc_dir, "versions", version_id, f"{version_id}.json")
        if not os.path.isfile(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.load(f)

            version_info = merged.get("version_json", {})
            existing = {lib.get("name", ""): lib for lib in vj.get("libraries", [])}
            for lib in version_info.get("libraries", []):
                name = lib.get("name", "")
                if name and name not in existing:
                    vj.setdefault("libraries", []).append(lib)

            extra_jvm = version_info.get("arguments", {}).get("jvm", [])
            if extra_jvm:
                ej = vj.get("arguments", {}).get("jvm", [])
                for arg in extra_jvm:
                    if arg not in ej:
                        vj.setdefault("arguments", {}).setdefault("jvm", []).append(arg)

            alt = version_info.get("mainClass")
            if alt and alt != "net.minecraft.client.main.Main":
                vj["mainClass"] = alt

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(vj, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"supplement version.json failed: {e}")

    # ================== server install ==================

    def install_multimc_pack_server(
        self, zip_path: str, server_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        if not os.path.isfile(zip_path):
            return False, f"file not found: {zip_path}"

        is_mmc, _ = detect_multimc_format(zip_path)
        if not is_mmc:
            return False, "not a valid MultiMC modpack"

        try:
            root_entry = find_root_entry(zip_path)
            mmc_manifest = read_mmc_pack_json(zip_path, root_entry)
            instance_cfg = read_instance_cfg(zip_path, root_entry,
                default_name=os.path.splitext(os.path.basename(zip_path))[0])

            mc_version = mmc_manifest.get_minecraft_version()
            if not mc_version:
                return False, "mmc-pack.json missing net.minecraft component"

            loader_info = mmc_manifest.get_loader_info()
            loader_type = loader_info[0] if loader_info else None
            loader_version = loader_info[1] if loader_info else None

            if not server_name:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', instance_cfg.name)
                server_name = f"{safe_name}-{mc_version}"

            mc_dir = getattr(self, "minecraft_dir", "")
            server_dir = self._resolve_server_dir(server_name)

            self._set_status(f"installing server: {server_name}")
            self._extract_minecraft_overlay(zip_path, server_dir, root_entry)

            import minecraft_launcher_lib as _mcllib
            callback = self._get_callback() if hasattr(self, "_get_callback") else {}
            try:
                _mcllib.install.install_minecraft_version(mc_version, mc_dir, callback=callback)
            except Exception as e:
                logger.warning(f"MC install failed (continuing): {e}")

            java_path = getattr(self, "_ensure_java_runtime", lambda v: "java")(mc_version)

            if loader_type:
                install_sl = getattr(self, "_install_server_mod_loader", None)
                if install_sl:
                    ok, _ = install_sl(loader_type, loader_version, mc_version, Path(server_dir), java_path)
                    if not ok:
                        logger.warning(f"server loader install failed")
            else:
                dl_vj = getattr(self, "_download_vanilla_server_jar", None)
                if dl_vj:
                    dl_vj(mc_version, Path(server_dir))

            with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")

            return True, server_name

        except Exception as e:
            logger.error(f"MultiMC server install failed: {e}")
            return False, str(e)

    def _resolve_server_dir(self, server_name: str) -> str:
        mc_dir = getattr(self, "minecraft_dir", "")
        gsvd = getattr(self, "_get_server_versions_dir", None)
        if gsvd:
            return str(gsvd() / server_name)
        d = os.path.join(mc_dir, "servers", server_name)
        os.makedirs(d, exist_ok=True)
        return d

    # ================== incremental update ==================

    def update_multimc_pack(self, zip_path: str, version_id: str) -> Tuple[bool, str]:
        prev = self._load_modpack_configuration(version_id)
        if not prev:
            return self.install_multimc_pack(zip_path)

        mc_dir = getattr(self, "minecraft_dir", "")
        version_dir = os.path.join(mc_dir, "versions", version_id)
        if not os.path.isdir(version_dir):
            return False, f"version dir not found: {version_dir}"

        try:
            root_entry = find_root_entry(zip_path)
            mmc_manifest = read_mmc_pack_json(zip_path, root_entry)
            instance_cfg = read_instance_cfg(zip_path, root_entry)
            mc_version = mmc_manifest.get_minecraft_version()
            if not mc_version:
                return False, "mmc-pack.json missing net.minecraft component"

            self._set_status(f"updating: {version_id}")
            old_hashes = {fi.path: fi.hash for fi in prev.overrides}
            overlay_prefix = f"{root_entry}.minecraft/"
            new_infos: List[FileInfo] = []
            updated = 0
            skipped = 0

            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if not name.startswith(overlay_prefix) or name.endswith("/"):
                        continue
                    rel = name[len(overlay_prefix):]
                    if not rel:
                        continue
                    target = os.path.join(version_dir, rel)
                    content = zf.read(name)
                    new_hash = hashlib.sha1(content).hexdigest()

                    if os.path.isfile(target):
                        cur = compute_file_sha1(target)
                        old = old_hashes.get(rel)
                        if old and cur == old:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with open(target, "wb") as f:
                                f.write(content)
                            updated += 1
                        elif old and cur != old:
                            skipped += 1
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with open(target, "wb") as f:
                                f.write(content)
                            updated += 1
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, "wb") as f:
                            f.write(content)
                        updated += 1

                    new_infos.append(FileInfo(path=rel, hash=new_hash))

            for fi in prev.overrides:
                if fi.path not in {n.path for n in new_infos}:
                    target = os.path.join(version_dir, fi.path)
                    if os.path.isfile(target) and fi.hash == compute_file_sha1(target):
                        os.remove(target)

            patches = self._load_component_patches(zip_path, mmc_manifest.components, mc_version, root_entry)
            patches = self._resolve_patch_dependencies(patches, mc_version)
            merged = self._merge_patches_to_version_json(patches, version_id)
            self._supplement_version_json(version_id, merged)

            loader_info = mmc_manifest.get_loader_info()
            if loader_info:
                self._install_mc_and_loader_from_patches(mc_version, loader_info[0], loader_info[1], merged)

            self._copy_icon(zip_path, version_dir, instance_cfg.icon_key, root_entry)
            self._save_modpack_configuration(version_id, instance_cfg, new_infos)

            self._set_status(f"update complete: {version_id}")
            logger.info(f"updated {updated} files, kept {skipped} user-modified files")
            return True, version_id

        except Exception as e:
            logger.error(f"update failed: {e}")
            return False, str(e)

    # ================== helpers ==================

    def _cleanup_version_dir(self, version_dir: str) -> None:
        try:
            if os.path.isdir(version_dir):
                shutil.rmtree(version_dir)
                logger.info(f"cleaned up: {version_dir}")
        except Exception as e:
            logger.warning(f"cleanup failed: {e}")

    def _set_status(self, msg: str) -> None:
        try:
            if hasattr(self, "set_status"):
                self.set_status(msg)
        except Exception:
            pass
