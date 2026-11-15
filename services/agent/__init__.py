"""Agent 子包 —— AGENT（AI 助手）的全部纯逻辑，与界面无关。

**为什么是子包而不是一堆 `services/agent_xxx.py`**

`services/agent/` 与 `ui/agent/` 保持同名同层次：models / provider / providers /
stream / session / permission / config / skill / system_prompt / tool_registry /
tools。包内 import 全部指向本包，互不牵连。

**留在 `ui/agent/` 的三样东西**（它们真的依赖 Tk）：

- `agent_chat.py`  —— 会话列表 / Markdown 渲染 / 工具卡片 / 流式控件（1897 行）
- `agent_mixin.py` —— AGENT 标签页 Mixin（150 行）
- `voice_input.py` —— 语音按钮（其逻辑早已在 `services/voice_service.py`）

`ui/agent/tools.py`（453 行）**不在本包内**：它与 `ui/agent/tools/` 包同名，
Python 导入时目录优先，因此该模块**不可达**；旧路径因此不提供 shim。

本包 `__init__.py` 刻意**不做任何 re-export**：保持空壳可以让
`import services.agent.permission` 这类叶子导入不牵动 provider / tools 的
整条依赖链，也避免与 `ui/agent/__init__.py`（它要导入 Tk 视图）互相牵连。

对外统一入口是 ``services.agent_service.AgentService``；本子包承载与界面无关的
全部 Agent 逻辑。
"""

__all__: list = []
