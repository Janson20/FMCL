"""应用装配层 —— 把服务、任务调度、UI 端口拼成一个可运行的整体。

与 ``services/`` 的区别：

- ``services/`` 放**业务逻辑**，每块逻辑各管一摊，互不知道界面。
- ``app/`` 放**胶水**：服务定位器（``context``）、后台任务（``tasks``）、
  UI 能力端口（``ports``）、事件总线（``events``）、启动装配（``bootstrap``，阶段 2）。

``app/`` 可以 import 具体服务，服务反过来只能通过 ``AppContext`` 拿依赖。
"""

from app.context import AppContext
from app.events import EventBus
from app.ports import Choice, NullUIPort, ProgressReport, RecordingUIPort, UIPort
from app.tasks import TaskContext, TaskHandle, TaskRunner

__all__ = [
    "AppContext",
    "EventBus",
    "UIPort",
    "NullUIPort",
    "RecordingUIPort",
    "ProgressReport",
    "Choice",
    "TaskRunner",
    "TaskContext",
    "TaskHandle",
]
