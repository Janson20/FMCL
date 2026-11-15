"""启动器自动更新服务。

把根模块 updater.py 的「检查 / 下载 / 安装」包装成服务，供 Update 桥调用。
服务层不拼界面文案：失败只返回机器码（no_asset / download_failed /
install_failed / platform_unsupported），由桥翻成 i18n 键。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from services.base import Service


class UpdateService(Service):
    name = "update"
    label = "更新"

    def current_version(self) -> str:
        try:
            from updater import get_current_version

            return str(get_current_version() or "")
        except Exception:
            return ""

    def check_release(self) -> Optional[Dict[str, Any]]:
        """返回 updater.check_for_update() 的原始结果（None = 已是最新）。"""
        from updater import check_for_update

        info = check_for_update()
        return info if isinstance(info, dict) else None

    def download(
        self,
        release: Dict[str, Any],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Optional[str], str]:
        """按当前平台选安装包并下载。返回 (路径, 错误码)。"""
        from updater import download_update, find_suitable_asset

        asset = find_suitable_asset(list(release.get("assets") or []))
        if not asset:
            return None, "no_asset"
        path = download_update(
            asset,
            tag_name=str(release.get("tag_name") or ""),
            progress_callback=progress_callback,
        )
        if not path:
            return None, "download_failed"
        return str(path), ""

    def install(self, path: str) -> Tuple[bool, str]:
        from updater import install_update

        return bool(install_update(str(path))), "install_failed"

    def mark_updated(self) -> bool:
        """更新安装程序已启动：解锁 advanced_updater，再让界面退出。"""
        try:
            from services.achievement_engine import get_achievement_engine

            engine = get_achievement_engine()
            if engine is None:
                return False
            engine.check_and_unlock("advanced_updater", True)
            return True
        except Exception:
            return False


__all__ = ["UpdateService"]
