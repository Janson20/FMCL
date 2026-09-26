"""Agent 子包 —— AGENT（AI 助手）的全部纯逻辑，与界面无关。

**为什么是子包而不是一堆 `services/agent_xxx.py`**

`ui/agent/` 本来就是一个自洽的小世界：models / provider / providers / stream /
session / permission / config / skill / system_prompt / tool_registry / tools。
搬进 `services/agent/` 保持同名同层次，包内 import 只需一条机械改写
（`from ui.agent.` → `from services.agent.`），逐节点 AST 完全一致 —— 这是
`scripts/relocate_module.py` 登记并逐个校验过的（任务 1.14）。

**留在 `ui/agent/` 的三样东西**（它们真的依赖 Tk）：

- `agent_chat.py`  —— 会话列表 / Markdown 渲染 / 工具卡片 / 流式控件（1897 行）
- `agent_mixin.py` —— AGENT 标签页 Mixin（150 行）
- `voice_input.py` —— 语音按钮（其逻辑早已在 `services/voice_service.py`）

`ui/agent/tools.py`（453 行）**没有搬**：它与 `ui/agent/tools/` 包同名，Python
导入时目录优先，因此该模块在搬家前后都是**不可达的死代码**；而搬家工具要求
旧路径留下可 import 的 shim，被遮蔽的路径无法满足该校验。详见任务 1.14 报告。

本包 `__init__.py` 刻意**不做任何 re-export**：保持空壳可以让
`import services.agent.permission` 这类叶子导入不牵动 provider / tools 的
整条依赖链，也避免与 `ui/agent/__init__.py`（它要导入 Tk 视图）互相牵连。

对外统一入口是 ``services.agent_service.AgentService``（形态 2：逻辑与界面
混在一起的那部分），本子包是形态 1（整体搬家）的产物。
"""

__all__: list = []
