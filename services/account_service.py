"""账号与皮肤服务（阶段 3 任务 3.1 的读侧；3.5 会在此之上补登录/导入导出）。

## 范围（本轮到哪为止）

3.1 的首页与侧边栏要的是**只读**的账号信息与皮肤操作，所以本轮只落：

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| B-16 | 当前账号（名称 / 类型）+ 管理入口的存在性判断 | `ui/app_base.py:925-947` |
| B-17 | 皮肤尺寸校验 → 复制到 `.minecraft/skins/` → 写 `skin_path` → 移除 | `ui/app_base.py:972-1026` |
| A-25 | 启动时批量刷新微软账号 Token | `main.py:103-113, 429` |

**登录 / 切换 / 删除 / 导入导出（M-26 ~ M-29）刻意不在这里**：它们属于 3.5，
本轮写进来就会变成"没人调用的一半实现"（红线 1：功能只增不减，但也不留半成品）。
`set_current_account()` 是个例外 —— 它只有一行委托，且 3.5 的账号卡片要用它。

## 为什么账号系统要单独包一层

`launcher/account.py` 的 ``GlobalAccountSystem`` 是个全局单例
（``init_account_system`` / ``get_account_system``），而且**界面侧还有一处历史坑**：
旧代码在四个窗口里各写了一遍 `type_labels` 字典来把 ``AccountType`` 翻成 i18n 键
（`ui/app_base.py:934-938` 是其中一份）。这里把"类型 → i18n 键"的映射**收口到一处**
（``ACCOUNT_TYPE_KEYS``），界面只拿键、不自己拼。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from services.base import Service

#: 账号类型 → i18n 键。收口点见模块文档（旧实现散在四个窗口里）。
ACCOUNT_TYPE_KEYS: Dict[str, str] = {
    "microsoft": "account_type_microsoft",
    "offline": "account_type_offline",
    "yggdrasil": "account_type_yggdrasil",
}

#: 允许的皮肤尺寸（旧 `ui/app_base.py:987`；**注意旧提示文案只提了两种**，
#: 文案键 ``skin_size_invalid`` 逐字保留不动 —— 改文案是行为变更，要单独裁决）。
SKIN_SIZES: Tuple[Tuple[int, int], ...] = ((64, 64), (64, 32), (128, 128), (128, 64))


class AccountService(Service):
    """账号读侧 + 皮肤 + Token 刷新。"""

    name = "account"
    label = "账号与皮肤"

    def __init__(self, context: Any = None, *, system: Any = None, launcher: Any = None) -> None:
        """
        Args:
            context: ``AppContext``。
            system: 显式注入的 ``GlobalAccountSystem``。**测试用**；生产路径为
                None，届时懒调 ``launcher.account.get_account_system()``
                （旧界面的取法完全一致，见 `ui/app_base.py:927-931`）。
            launcher: 显式注入的 ``MinecraftLauncher``（皮肤路径存在它的 config 上）。
        """
        super().__init__(context)
        self._system = system
        self._launcher = launcher

    # ─── 装配 ───────────────────────────────────────────────

    def use_system(self, system: Any) -> None:
        self._system = system

    def use_launcher(self, launcher: Any) -> None:
        self._launcher = launcher

    @property
    def system(self) -> Any:
        """账号系统单例；不可用时返回 None（界面据此显示"未选择账号"）。"""
        if self._system is None:
            try:
                from launcher.account import get_account_system

                self._system = get_account_system()
            except Exception as e:  # noqa: BLE001 - 账号模块坏掉不该让首页打不开
                self.log.warning("取账号系统失败: %s", e)
                return None
        return self._system

    # ─── 装配：把核心接到账号系统上（旧 main.py:337-455 的接线段）──

    def attach_launcher(self, launcher: Any, ui_port: Any = None) -> bool:
        """把启动器核心与账号系统接起来。返回是否接上了账号系统。

        这一步**不能省**：``MinecraftLauncher.launch_game()`` 里
        ``if self._account_system:`` 才是登录凭据的唯一来源，没注入就静默退化成
        ``config.player_name`` 的离线身份（`launcher/core.py:1025-1045`）。
        旧入口把这段写在 ``main.py`` 的 ``_on_launcher_ready()`` 里，本方法是它的
        一比一对应物；顺序也一致（端口 → 旧配置迁移 → 取系统 → 注入）。

        Args:
            launcher: ``MinecraftLauncher`` 实例。
            ui_port: UI 能力端口（账号模块的登录流程要弹窗/问密码）；
                None 时跳过注入（旧实现同样允许核心侧缺端口降级）。

        Raises:
            不抛异常：任一子步骤失败只记日志 —— 账号接不上时界面仍要能用（离线身份）。
        """
        if launcher is None:
            return False
        self._launcher = launcher

        if ui_port is not None:
            try:
                from launcher.account import set_ui_port as set_account_ui_port

                set_account_ui_port(ui_port)
            except Exception as e:  # noqa: BLE001
                self.log.warning("UI 端口注入账号模块失败: %s", e)

        # 旧配置迁移（`player_name` → 离线账号；一个账号都没有时建默认 Steve）。
        # 必须在取账号系统**之前**：`migrate_accounts()` 内部会 `init_account_system()`。
        try:
            migrate = getattr(self.config, "migrate_accounts", None)
            if callable(migrate):
                migrate()
        except Exception as e:  # noqa: BLE001 - 迁移失败不影响启动（旧实现同）
            self.log.warning("账号迁移失败（不影响启动）: %s", e)

        system = self.system
        if system is None:
            return False
        setter = getattr(launcher, "set_account_system", None)
        if callable(setter):
            try:
                setter(system)
            except Exception as e:  # noqa: BLE001
                self.log.warning("账号系统注入启动器失败: %s", e)
                return False
        return True

    # ─── B-16 当前账号 ──────────────────────────────────────

    def current_account(self) -> Optional[Dict[str, Any]]:
        """当前账号的只读快照；未选账号返回 None。"""
        system = self.system
        if system is None:
            return None
        try:
            account = system.current_account
        except Exception as e:  # noqa: BLE001
            self.log.warning("读当前账号失败: %s", e)
            return None
        if account is None:
            return None
        return self.account_snapshot(account)

    @staticmethod
    def account_snapshot(account: Any) -> Dict[str, Any]:
        """把一个 ``Account`` 归一成界面要的字典（**不碰令牌**）。"""
        type_value = getattr(getattr(account, "account_type", None), "value", "") or ""
        return {
            "id": str(getattr(account, "id", "") or ""),
            "name": str(getattr(account, "name", "") or ""),
            "type": type_value,
            "type_key": ACCOUNT_TYPE_KEYS.get(type_value, ""),
            "uuid": str(getattr(account, "uuid", "") or ""),
            "display_name": str(getattr(account, "display_name", "") or getattr(account, "name", "") or ""),
        }

    def account_summary(self) -> Dict[str, Any]:
        """首页/侧边栏用的账号摘要（未选账号时 ``has_account=False``）。"""
        snapshot = self.current_account()
        if snapshot is None:
            return {"has_account": False, "id": "", "name": "", "type": "", "type_key": "", "uuid": ""}
        snapshot["has_account"] = True
        return snapshot

    def accounts(self) -> list:
        """全部账号快照（3.5 的账号卡片列表要用；首页只用来判断"有没有账号"）。"""
        system = self.system
        if system is None:
            return []
        try:
            return [self.account_snapshot(a) for a in system.accounts]
        except Exception as e:  # noqa: BLE001
            self.log.warning("读账号列表失败: %s", e)
            return []

    def set_current_account(self, account_id: str) -> bool:
        """切换当前账号（旧 `GlobalAccountSystem.set_current_account` 的一行委托）。"""
        system = self.system
        if system is None:
            return False
        try:
            return bool(system.set_current_account(account_id))
        except Exception as e:  # noqa: BLE001
            self.log.error("切换当前账号失败: %s", e)
            return False

    # ─── A-25 启动时批量刷新 Token ──────────────────────────

    def auto_refresh_tokens(self) -> int:
        """批量刷新过期 Token，返回刷新成功的账号数（失败返回 0，不抛异常）。"""
        system = self.system
        if system is None:
            return 0
        try:
            return int(system.auto_refresh_all_tokens() or 0)
        except Exception as e:  # noqa: BLE001 - 旧实现同样只记日志
            self.log.warning("启动时自动刷新 Token 失败（不影响正常使用）: %s", e)
            return 0

    # ─── B-17 皮肤 ──────────────────────────────────────────

    def skin_path(self) -> str:
        """当前自定义皮肤路径（空串表示没有）。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return ""
        try:
            return str(launcher.get_skin_path() or "")
        except Exception as e:  # noqa: BLE001
            self.log.warning("读皮肤路径失败: %s", e)
            return ""

    def select_skin(self, file_path: str) -> Tuple[bool, str, Dict[str, Any]]:
        """选择皮肤：校验尺寸 → 写配置 → 复制到 ``.minecraft/skins/``。

        Returns:
            ``(是否成功, i18n 键, 参数)``。失败时键是 ``skin_size_invalid`` /
            ``skin_file_error``，成功时是 ``skin_installed``（带 ``filename``）。
            **不在这里做翻译** —— 界面拿键去翻（QML 走 ``Tr.map``，旧界面走 ``_``）。
        """
        path = Path(str(file_path or ""))
        if not path.is_file():
            return False, "skin_file_error", {}

        ok, key, params = self._validate_skin(path)
        if not ok:
            return False, key, params

        launcher = self._resolve_launcher()
        if launcher is None:
            return False, "skin_file_error", {}

        # 顺序与旧实现一致：先写配置、再复制（复制失败也已经"选中"了）
        try:
            launcher.set_skin_path(str(path))
        except Exception as e:  # noqa: BLE001
            self.log.error("写入皮肤路径失败: %s", e)
            return False, "skin_file_error", {}

        try:
            skin_dir = Path(launcher.get_minecraft_dir()) / "skins"
            skin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(skin_dir / path.name))
        except Exception as e:  # noqa: BLE001 - 旧实现这里没兜异常，但不该让首页崩
            self.log.error("复制皮肤到 .minecraft/skins 失败: %s", e)
            return False, "skin_file_error", {}

        self._trigger_ach("personalize_skin")
        return True, "skin_installed", {"filename": path.name}

    def remove_skin(self) -> None:
        """移除自定义皮肤（清空 ``skin_path``，不删已复制到 skins/ 的文件 —— 旧实现同）。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return
        try:
            launcher.set_skin_path(None)
        except Exception as e:  # noqa: BLE001
            self.log.error("清除皮肤路径失败: %s", e)

    def _validate_skin(self, path: Path) -> Tuple[bool, str, Dict[str, Any]]:
        """尺寸校验。PIL 缺席时**跳过校验**（旧实现同：`ImportError: pass`）。"""
        try:
            from PIL import Image
        except ImportError:
            return True, "", {}
        try:
            with Image.open(path) as img:
                width, height = img.size
        except Exception as e:  # noqa: BLE001 - 打不开的图片 = 文件错误
            self.log.warning("读取皮肤文件失败: %s", e)
            return False, "skin_file_error", {}
        if (width, height) not in SKIN_SIZES:
            return False, "skin_size_invalid", {"width": width, "height": height}
        return True, "", {}

    # ─── 内部 ───────────────────────────────────────────────

    def _resolve_launcher(self) -> Any:
        if self._launcher is None and self.attached:
            self._launcher = self.try_get("launcher")
        return self._launcher

    def _trigger_ach(self, achievement_id: str, value: int = 1, trigger_type: Optional[str] = None) -> None:
        """触发成就进度（与 GameService 同一套语义，见那边的说明）。"""
        try:
            from achievement_engine import get_achievement_engine

            engine = get_achievement_engine()
            if engine:
                engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
        except Exception as e:  # noqa: BLE001
            self.log.debug("触发成就 %s 失败: %s", achievement_id, e)


__all__ = ["ACCOUNT_TYPE_KEYS", "SKIN_SIZES", "AccountService"]
