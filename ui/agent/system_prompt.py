"""系统提示词组装（转发 shim）

实现已搬到 ``services/agent/system_prompt.py``。

**本模块名直接指向实现模块**（``sys.modules[__name__] = _impl``），而不是用
``globals().update(...)`` 复制引用。后者只能保证"读"到同一批对象，**"写"传不过去**：
``import ui.agent.system_prompt as m; m.FOO = fake`` 只会改到 shim 的命名空间，实现模块里的
``FOO`` 不变 —— monkeypatch 会**静默失效**。

这个坑是实测踩到的：``ui/music_source/`` 搬走后，``tests/test_music_fallback.py``
里的 ``monkeypatch.setattr(ms, "MUSIC_SOURCES", fakes)`` 失效，测试**真去请求了
QQ 音乐接口**。

别名方式让 ``ui.agent.system_prompt is services.agent.system_prompt`` 成立：读、写、打补丁、``is`` 判定全部与
搬家前一致。

**同时还把实现模块的名字复制一份到本模块的 ``__dict__``**，用于兼容另一种加载
方式：仓库里 ``tests/test_music_playlist.py`` 是按**文件路径**加载模块
（``spec_from_file_location`` + 自己注册进 ``sys.modules``）以绕开 ``ui/__init__.py``
的 GUI 导入，它会**保留 exec 之前的模块对象** —— 只做别名的话那个对象仍是空壳
（实测踩到过：``mp.PlaylistSong`` AttributeError）。两种机制都很便宜，一起用最稳。

阶段 3 完成后本文件可删除。
"""

import sys

import services.agent.system_prompt as _impl

sys.modules[__name__] = _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
