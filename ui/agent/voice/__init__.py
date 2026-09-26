"""语音识别引擎包（SenseVoice-Small 离线模型）（转发 shim）

实现已搬到 ``services.voice``。

**本包名与各子模块名都直接指向实现模块**（``sys.modules[__name__] = _impl``），
而不是用 ``globals().update(...)`` 复制引用 —— 后者只能保证"读"到同一批对象，
**"写"传不过去**：``import ui.agent.voice as m; m.FOO = fake`` 只会改到 shim 的命名空间，
monkeypatch 会**静默失效**（实测：搬走本包后 test_music_fallback 的
``monkeypatch.setattr(ms, "MUSIC_SOURCES", ...)`` 失效，真去请求了外部接口）。

子模块也必须一并别名：否则 ``import ui.agent.voice.sub`` 会以旧包名把实现**再加载
一次**，产生两份模块对象（两个类 → ``isinstance`` 跨不过去）。

阶段 3 完成后本文件可删除。
"""

import importlib
import sys

import services.voice as _impl

#: 实现包里的子模块（由生成脚本按当时的文件清单写死，搬家后不会变）
_SUBMODULES: tuple = ('models', 'sensevoice')

for _sub in _SUBMODULES:
    sys.modules[f"{__name__}.{_sub}"] = importlib.import_module(f"services.voice.{_sub}")

sys.modules[__name__] = _impl
