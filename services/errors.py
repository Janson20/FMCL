"""服务层统一异常体系。

阶段 1 约束（见 ``docs/refactor/03-phases.md``）：

- ``services/`` 包内任何模块不得导入 ``tkinter`` / ``customtkinter`` /
  ``PySide6`` / 任何 ``ui.*`` 模块。UI 差异只能通过 ``app.ports.UIPort``
  注入进来。
- 服务层对外只使用两种失败表达方式：**返回正常值** 或 **抛出 ServiceError
  的子类**。不允许再新增"返回 (False, "错误信息") 元组"的新接口；
  历史遗留的元组返回值在阶段 1 原样保留（只搬家不改逻辑），
  由调用方继续按老方式处理。

UI 层可以统一写::

    try:
        service.do_something()
    except PermissionDenied as e:
        ui.show_error(e.title, e.message)
    except ServiceError as e:
        ui.show_error("操作失败", e.message)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """服务层异常基类。

    Attributes:
        code: 稳定的机器可读错误码（便于 i18n 与测试断言，不要用它做展示）。
        message: 面向用户的简短说明（已是本地化后的文本或 i18n 键）。
        detail: 面向排查的细节（路径、URL、原始异常文本），可含敏感信息，
            只写日志、不进 UI。
        cause: 原始异常对象，便于 ``raise ... from`` 链路完整。
    """

    code: str = "service_error"
    default_message: str = "操作失败"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        detail: Optional[str] = None,
        cause: Optional[BaseException] = None,
        **extra: Any,
    ) -> None:
        self.message = message or self.default_message
        self.detail = detail
        self.cause = cause
        self.extra: Dict[str, Any] = extra
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - 仅影响日志可读性
        if self.detail:
            return f"{self.message} ({self.detail})"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """序列化，供 QML bridge / 崩溃报告使用。"""
        data: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            data["detail"] = self.detail
        if self.extra:
            data.update(self.extra)
        return data

    def log(self, log: Optional[logging.Logger] = None) -> "ServiceError":
        """记录到日志并返回自身，便于 ``raise e.log()`` 一行写法。"""
        (log or logger).error(
            "%s: %s%s",
            type(self).__name__,
            self.message,
            f" | detail={self.detail}" if self.detail else "",
            exc_info=self.cause if isinstance(self.cause, BaseException) else None,
        )
        return self


class ServiceNotAvailable(ServiceError):
    """请求的服务未在 AppContext 中注册。属编程错误，不应被用户触发。"""

    code = "service_not_available"
    default_message = "所需服务不可用"


class InvalidArgument(ServiceError):
    """调用方传入的参数不合法（含路径不存在、格式错误等）。"""

    code = "invalid_argument"
    default_message = "参数不合法"


class NotFound(ServiceError):
    """请求的资源不存在（版本、账号、文件、记录……）。"""

    code = "not_found"
    default_message = "未找到指定内容"


class AlreadyExists(ServiceError):
    """目标已存在，重复创建被拒绝。"""

    code = "already_exists"
    default_message = "目标已存在"


class PermissionDenied(ServiceError):
    """权限/授权不足（文件系统权限、未登录、Token 失效）。"""

    code = "permission_denied"
    default_message = "权限不足或尚未登录"


class OperationCancelled(ServiceError):
    """用户主动取消，或任务被上层撤销。属正常控制流，通常不弹错误框。"""

    code = "cancelled"
    default_message = "操作已取消"


class ConflictError(ServiceError):
    """资源被占用（文件被游戏锁定、端口被占用、服务已在运行）。"""

    code = "conflict"
    default_message = "资源被占用"


class ExternalToolError(ServiceError):
    """外部程序执行失败（java、EasyTier、7z、.NET SDK……）。"""

    code = "external_tool_error"
    default_message = "外部程序执行失败"


class PlatformNotSupported(ServiceError):
    """当前平台不支持该功能（例如非 Windows 的联机功能）。"""

    code = "platform_not_supported"
    default_message = "当前平台不支持此功能"


class NetworkError(ServiceError):
    """网络请求失败（超时、DNS、HTTP 状态异常、被风控）。"""

    code = "network_error"
    default_message = "网络请求失败"


@contextmanager
def wrap(
    message: str,
    *,
    exc: type[ServiceError] = ServiceError,
    detail: Optional[str] = None,
    passthrough: tuple[type[BaseException], ...] = (ServiceError, KeyboardInterrupt, SystemExit),
    log: Optional[logging.Logger] = None,
) -> Iterator[None]:
    """把任意底层异常收敛成 ServiceError 子类。

    已属于 ``passthrough`` 的异常原样抛出，避免把 ``OperationCancelled``
    这类控制流信号错误地包装成失败。

    Usage::

        with wrap("安装版本失败", exc=ExternalToolError, log=logger):
            subprocess.run(cmd, check=True)
    """
    try:
        yield
    except passthrough:
        raise
    except Exception as e:  # noqa: BLE001 - 此处就是要兜住一切
        err = exc(detail=detail or f"{type(e).__name__}: {e}", cause=e)
        err.log(log)
        raise err from e


__all__ = [
    "ServiceError",
    "ServiceNotAvailable",
    "InvalidArgument",
    "NotFound",
    "AlreadyExists",
    "PermissionDenied",
    "OperationCancelled",
    "ConflictError",
    "ExternalToolError",
    "PlatformNotSupported",
    "NetworkError",
    "wrap",
]
