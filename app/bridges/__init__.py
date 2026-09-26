"""QML 桥接层 —— QML 侧访问 Python 的**唯一**入口（阶段 2 契约见 `docs/refactor/11`）。

## 边界

- 本包的模块**可以** import PySide6（它们就是 Qt 对象），这是与 `services/` 的分界：
  `services/` 禁止任何 UI 依赖，`app/bridges/` 专门用来把服务的能力翻译成 QML 能用的东西。
- 本包**不得**被 `app/__init__.py` 或任何旧 Tk 路径 import —— 旧界面不装 PySide6，
  一旦被拖进来，`main.py` 会在导入期直接崩。这条由 `scripts/check_qml_rules.py` 的静态检查兜底。
- 桥上只做三件事：**参数转换、调用服务、发信号**。写业务规则（版本比较、路径拼接、
  网络重试策略）即违规 —— 那些属于 `services/`（迁移红线 2）。

## 为什么 `__init__.py` 里一个 import 都没有

桥模块各自 import PySide6，而本包在**没有 Qt 的环境**（例如只跑 `services` 单元测试的进程、
或 CI 的静态检查步骤）里也可能被枚举。顶层保持零 import，避免"import 一个包名就要求装 Qt"。
"""

__all__: list = []
