"""业务服务层 —— 与界面无关的全部业务逻辑。

**硬性约束（阶段 1 验收标准第 1 条，CI 固化在 ``scripts/check_services_purity.py``）**

本包及其子包内**不得**出现：

- ``import tkinter`` / ``customtkinter`` / ``tkinterdnd2`` / ``tkinterweb`` / ``tkhtmlview``
- ``import PySide6``（任何子模块）
- ``import ui.*``（任何 UI 模块）
- 任何 ``ui/`` 目录下的静态资源路径假设（locales、themes 等一律由调用方注入）

需要与用户交互时，使用 ``self.ui``（``app.ports.UIPort``）。
需要后台线程时，使用 ``self.tasks``（``app.tasks.TaskRunner``）。
需要通知界面时，使用 ``self.publish("事件名", ...)``。

包结构约定（随阶段 1 推进逐步补齐）::

    services/
        errors.py            统一异常体系
        base.py              Service 基类
        i18n_service.py      多语言（1.16）
        theme_service.py     主题引擎（1.16）
        palette.py           12 色调色板（1.16）
        ...                  其余见 docs/refactor/03-phases.md 阶段 1 任务表
"""

from services.base import Service
from services.errors import (
    AlreadyExists,
    ConflictError,
    ExternalToolError,
    InvalidArgument,
    NetworkError,
    NotFound,
    OperationCancelled,
    PermissionDenied,
    PlatformNotSupported,
    ServiceError,
    ServiceNotAvailable,
)

__all__ = [
    "Service",
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
]
