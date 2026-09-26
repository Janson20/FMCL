"""模型供应商适配（净读 AI / OpenAI / Anthropic / 自定义）（转发 shim）

实现已搬到 ``services/agent/providers``。

**本包名与各子模块名都直接指向实现模块**（``sys.modules[__name__] = _impl``），
而不是用 ``globals().update(...)`` 复制引用 —— 后者只能保证"读"到同一批对象，
**"写"传不过去**（monkeypatch 会静默失效，实测踩过）。

子模块也必须一并别名：否则 ``import ui.agent.providers.sub`` 会以旧包名把实现**再加载一次**，
产生两份模块对象（两个类 → ``isinstance`` 跨不过去）。

别名之外仍把名字复制一份到本模块 ``__dict__``，兼容"先取模块对象、再 exec"的
加载方式（理由见单模块模板里的说明）。

阶段 3 完成后本文件可删除。
"""

import importlib
import sys

import services.agent.providers as _impl

#: 实现包里的子模块（生成时按当时的文件清单写死）
_SUBMODULES: tuple = ('anthropic', 'custom', 'jingdu', 'openai')

for _sub in _SUBMODULES:
    sys.modules[f"{__name__}.{_sub}"] = importlib.import_module(f"services.agent.providers.{_sub}")

sys.modules[__name__] = _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
