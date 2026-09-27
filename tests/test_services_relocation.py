"""阶段 1「形态 1：整体搬家」的永久回归守卫。

已有一批"本来就不依赖界面"的模块从仓库根 / `ui/` 搬进了 `services/`，
并在原位置留下**真别名**：``ui.x is services.x``。

## 为什么 shim 必须是真别名，而不是 `globals().update` 复制

第一版 shim 用 ``globals().update(vars(_impl))`` 复制引用。它让"读"是等的
（``from 旧路径 import X is 新路径.X``），但**"写"传不过去**：
``import ui.x as m; m.FOO = fake`` 只改到 shim 的 ``__dict__``。

后果是实测踩到的：``ui/music_source/`` 搬走后，
``tests/test_music_fallback.py`` 里的
``monkeypatch.setattr(ms, "MUSIC_SOURCES", fakes)`` **静默失效** ——
假音源没生效，测试**真去请求了 QQ 音乐接口**并拿到真实结果，12 个用例失败。

现在 shim 把模块名指向实现模块（``sys.modules[__name__] = _impl``），
读 / 写 / 打补丁 / ``is`` 判定全部与搬家前一致。
**这个缺陷本身被 ``test_patching_through_old_path_reaches_implementation`` 钉住。**
"""

from __future__ import annotations

import importlib
import io
import subprocess
import sys
import types
from pathlib import Path

import pytest

import _baseline

REPO_ROOT = Path(__file__).resolve().parent.parent

#: (旧模块名, 新模块名, 旧文件相对路径或 None, 新文件相对路径)
#: 旧路径为 None 表示该处已无 shim 文件（整包搬家的子模块由包的别名统一登记）。
MOVED = [
    ("achievement_defs", "services.achievement_defs", "achievement_defs.py", "services/achievement_defs.py"),
    ("achievement_engine", "services.achievement_engine", "achievement_engine.py", "services/achievement_engine.py"),
    ("achievement_sync", "services.achievement_sync", "achievement_sync.py", "services/achievement_sync.py"),
    ("ui.server_config_schema", "services.server_config_schema",
     "ui/server_config_schema.py", "services/server_config_schema.py"),
    ("ui.music_lyrics", "services.music_lyrics", "ui/music_lyrics.py", "services/music_lyrics.py"),
    ("ui.music_effects", "services.music_effects", "ui/music_effects.py", "services/music_effects.py"),
    ("backup_manager", "services.backup_manager", "backup_manager.py", "services/backup_manager.py"),
    # 任务 1.4 的音乐叶子模块（实测 UI 触点 = 0）
    ("ui.music_playlist", "services.music_playlist", "ui/music_playlist.py", "services/music_playlist.py"),
    ("ui.music_risk_captcha", "services.music_risk_captcha",
     "ui/music_risk_captcha.py", "services/music_risk_captcha.py"),
    # 整包搬家：包里只剩 __init__.py 做别名登记，子模块 shim 文件已删除
    ("ui.music_source", "services.music_source", "ui/music_source/__init__.py",
     "services/music_source/__init__.py"),
    ("ui.agent.voice", "services.voice", "ui/agent/voice/__init__.py", "services/voice/__init__.py"),
    ("ui.agent.voice.models", "services.voice.models", None, "services/voice/models.py"),
    ("ui.agent.voice.sensevoice", "services.voice.sensevoice", None, "services/voice/sensevoice.py"),
]

#: 整包搬家里需要被父包统一别名的子模块
PACKAGE_SUBMODULES = {
    "ui.music_source": ("base", "bili", "kg", "kw", "mg", "tx", "utils", "wy"),
    "ui.agent.voice": ("models", "sensevoice"),
}

#: 新文件 → 它在 git 里的原文路径。
#:
#: 显式写死而不是用 `new_path.replace("services/", "ui/")` 去猜：语音那条映射是
#: `services/voice/models.py` ← `ui/agent/voice/models.py`，字符串替换猜不出来，
#: 猜错的表现是"git 里找不到原文"这种含混失败。
GIT_SOURCE = {
    "services/achievement_defs.py": "achievement_defs.py",
    "services/achievement_engine.py": "achievement_engine.py",
    "services/achievement_sync.py": "achievement_sync.py",
    "services/server_config_schema.py": "ui/server_config_schema.py",
    "services/music_lyrics.py": "ui/music_lyrics.py",
    "services/music_effects.py": "ui/music_effects.py",
    "services/backup_manager.py": "backup_manager.py",
    "services/music_playlist.py": "ui/music_playlist.py",
    "services/music_risk_captcha.py": "ui/music_risk_captcha.py",
    "services/music_source/__init__.py": "ui/music_source/__init__.py",
    "services/voice/__init__.py": "ui/agent/voice/__init__.py",
    "services/voice/models.py": "ui/agent/voice/models.py",
    "services/voice/sensevoice.py": "ui/agent/voice/sensevoice.py",
}

#: 真实调用点用到的符号（取自全仓库 grep，不是猜的）
CALL_SITE_SYMBOLS = [
    ("achievement_engine", "get_achievement_engine"),
    ("achievement_engine", "init_achievement_engine"),
    ("achievement_engine", "AchievementEngine"),
    ("achievement_engine", "ACHIEVEMENTS"),
    ("achievement_engine", "_do_merge_db"),  # 私有名
    ("achievement_sync", "run_sync"),
    ("achievement_sync", "reset_cloud_db"),
    ("ui.server_config_schema", "DEFAULT_SERVER_PROPERTIES"),
    ("ui.music_lyrics", "LyricParser"),
    ("ui.music_lyrics", "LyricLine"),
    ("ui.music_source", "MUSIC_SOURCES"),
    ("ui.music_source", "SOURCE_META"),
    ("ui.music_source", "search_all"),
    ("ui.music_source", "resolve_track"),
    ("ui.music_source.base", "MusicInfo"),
    ("ui.music_source.bili", "BiliBiliMusicSource"),
    ("ui.music_source.wy", "NetEaseMusicSource"),
    ("backup_manager", "BackupManager"),
    ("ui.agent.voice.models", "MODEL_MANUAL_HINT_URL"),
    ("ui.agent.voice.models", "model_dir"),
    ("ui.agent.voice.models", "is_model_ready"),
    ("ui.agent.voice.models", "model_version"),
    ("ui.agent.voice.sensevoice", "SenseVoice"),
    ("ui.agent.voice.sensevoice", "pick_providers"),
    ("ui.agent.voice_input", "VoiceMicButton"),
    ("ui.agent.voice_input", "VoiceInputManager"),
    ("ui.agent.voice_input", "_run_session_in_thread"),  # 私有名
    ("ui.agent.voice_input", "_get_voice_service"),  # 私有名
    ("ui.music_playlist", "Playlist"),
    ("ui.music_playlist", "PlaylistManager"),
    ("ui.music_playlist", "PlaylistSong"),
    ("ui.music_risk_captcha", "run_captcha_flow"),
    ("ui.music_risk_captcha", "build_captcha_page"),
]


#: 搬家完成之后**已登记**的行数变化：``{新路径: (非空行数增量, 理由)}``。
#:
#: 这些不是"内容被删改"，而是搬家之后带任务号的缺陷修复。判据没有放松 ——
#: 不在表里的文件仍然必须与原文行数完全一致；每条例外都要写清归属与原因，
#: 防止这张表变成"哪里红了往哪里加"的垃圾场。
REGISTERED_LINE_DELTAS: dict[str, tuple[int, str]] = {
    "services/backup_manager.py": (
        10,
        "D-112（阶段 1 第 11 轮）：同一秒内的两次备份会因文件名只有秒级精度而互相覆盖"
        "（索引留下多条指向同一文件的记录 → 删一条连带删另一条、自动清理会删掉仅存的文件）。"
        "修法是撞名时追加 6 位短后缀并写明理由注释：非空行 +10（总行 +11）。",
    ),
    "services/agent/engine.py": (
        1,
        "第 12 轮：给读子进程文本输出的调用补 `errors=\"replace\"` —— `subprocess.run(..., "
        "text=True)` 不指定 encoding 时用 `locale.getpreferredencoding(False)`，而项目自己的"
        "文档命令是 `python -X utf8 -m pytest`；UTF-8 模式下子进程按 OEM 码页输出的中文会在"
        "reader 线程里抛 UnicodeDecodeError（实测 byte 0xd0）。补 kwarg 多占一行。",
    ),
    "services/music_playlist.py": (
        34,
        "D-20 + D-22（阶段 2 E 组 WP2，`poc/review/e_group/wp2_music_playlist.md`）："
        "①D-20 —— `get_music_data_dir()` 的 `mkdir` 是裸调（父目录建不出来 / 只读盘 / 路径被同名"
        "文件占住就直接抛），而两个可达调用方（`ui/app_music.py` 的 `_load_music_state` 与"
        "`_music_periodic_save_tick`）都把异常吞掉 → 用户可见症状是「歌单静默不落盘、重启就没了」，"
        "而 config.py 那边 cwd 不可写会回退到用户数据目录、`config.json` 照常落盘。修法是**同构**"
        "复用 config.py 的 `_is_writable_dir` / `_get_user_data_dir`：先试 primary、不可写就回退"
        "用户数据目录并打 warning，两者都不可写时打 error 且 `load` / `save` 不再把异常抛给 Tk 回调"
        "（落盘失败保留脏标记等下次重试）。"
        "②D-22 —— `add_song` / `is_song_in_playlist` / `is_song_in_any_playlist` / "
        "`get_playlist_names_for_song` / `record_to_history` 的去重判定逐首重算 `os.path.normpath`，"
        "改为 `_song_key` / `_query_keys` 把候选歌（查询路径）的键算一次（这一步是减行的）。"
        "非空行 +34（总行 +53，多出的 19 行是新增 docstring 分段带来的空行）。"
        "**这条偏差还必须同步到 `scripts/relocate_module.py` 的 `REGISTERED_LINE_DELTAS`"
        "（那里按总行数算 → +53），并把 `ui/music_playlist.py → services/music_playlist.py` "
        "补进该脚本的 `MOVES`（否则闸门会报「登记项没被命中」）—— 补齐之前"
        "`test_registered_line_deltas_agree_with_the_relocate_gate` 会红（它要求两张登记表的键集合一致，"
        "而 `services/music_playlist.py` 不在 `relocate.MOVES` 里，所以本条只能登记在测试侧等待同步）。**",
    ),
    "services/music_effects.py": (
        82,
        "D-147 + D-25（阶段 2 E 组 WP3，`poc/review/e_group/wp3_music_effects.md`）："
        "①D-147 —— 音效链真正干活的是**外部 ffmpeg**（pydub 只是容器），而它既没写进 "
        "pyproject/README、也没进打包，失败还被吞成「返回原文件」；更糟的是原来的守卫 "
        "`if not has_any_enabled or not _pydub_available:` 连一条日志都没有（实测本机 "
        "`_pydub_available` 恒为 False，见 D-148），用户看到的就是「设了没反应」。修法是新增 "
        "`_find_ffmpeg()` / `FFMPEG_PATH` / `ffmpeg_available()` / `unavailable_reason()`，"
        "把整体跳过与每个单效果的失败都记成可定位的 WARNING，并让 `available` 同时反映 ffmpeg。"
        "②D-25 —— `_apply_speed` 的 `original_duration` 原来传进来没人读（现已用于长度校验，"
        "这正是 D-19 要的进度基准），`_apply_reverb` 的 `dry_audio = audio` 赋值后无人读（已删）。"
        "新增的两个纯函数 `effective_duration()` / `playback_duration()` 是 D-19 修法的入口。"
        "非空行 +82（总行 +99，多出的 17 行是函数之间的空行与 docstring 分段）—— 全是新增的"
        "探测/日志/折算代码与「为什么」注释，唯一的删除是 D-25 明令删掉的那一行。",
    ),
}


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED)
def test_old_path_is_the_same_module_object(old_mod, new_mod, old_path, new_path):
    """核心不变式：``import 旧路径`` 与 ``import 新路径`` 必须是**同一个模块对象**。

    这是比"同一批对象"更强的保证：它同时覆盖读、写、打补丁与 ``is`` 判定。
    """
    old = importlib.import_module(old_mod)
    new = importlib.import_module(new_mod)
    assert old is new, f"{old_mod} 与 {new_mod} 不是同一个模块对象（shim 退化成了复制？）"
    assert Path(new.__file__).name == Path(new_path).name


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED)
def test_implementation_kept_the_original_line_count(old_mod, new_mod, old_path, new_path):
    """新文件行数必须与 git 原文一致 —— 内容整体搬走、没有删改。

    搬家**完成之后**的缺陷修复会合法地改变行数，这类改动逐条登记在
    ``REGISTERED_LINE_DELTAS`` 里（带任务号与理由）。判据没有放松：
    不在登记表里的文件仍然必须与原文行数**完全一致**。
    """
    git_src = GIT_SOURCE.get(new_path)
    assert git_src, f"GIT_SOURCE 里没有登记 {new_path} 的原文路径"
    original = _baseline.git_show(git_src)
    assert original.strip(), f"基线提交里找不到 {git_src} 的原文"

    moved = (REPO_ROOT / new_path).read_text(encoding="utf-8")
    original_lines = [ln.strip() for ln in original.splitlines() if ln.strip()]
    moved_lines = [ln.strip() for ln in moved.splitlines() if ln.strip()]
    delta, why = REGISTERED_LINE_DELTAS.get(new_path, (0, ""))
    assert len(moved_lines) == len(original_lines) + delta, (
        f"{new_path} 非空行数 {len(moved_lines)} != 原文 {len(original_lines)}"
        + (f" + 已登记 {delta}（{why}）" if delta else "（内容被删改了？）")
    )


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED)
def test_own_definitions_are_reachable_through_the_old_path(old_mod, new_mod, old_path, new_path):
    """实现模块**自己定义**的函数/类/常量，旧路径必须全都能取到同一个对象。

    只看"自己定义的"，不看它 import 进来的第三方模块对象（`json` / `np` 之类）——
    要求命名空间完全一致等于强迫 shim 把 `np`、`ort` 也再导出，既无意义也
    把"写法选择"当成了正确性要求。
    """
    impl_src = (REPO_ROOT / new_path).read_text(encoding="utf-8")
    imported = set()
    for node in __import__("ast").parse(impl_src).body:
        if isinstance(node, __import__("ast").Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, __import__("ast").ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)

    old = importlib.import_module(old_mod)
    new = importlib.import_module(new_mod)
    own = [
        n for n in vars(new)
        if not n.startswith("__") and n not in imported and not isinstance(vars(new)[n], types.ModuleType)
    ]
    assert own, f"{new_mod} 看起来什么都没定义"
    missing = [n for n in own if not hasattr(old, n)]
    assert missing == [], f"{old_mod} 取不到实现自己定义的名字: {missing}"


@pytest.mark.parametrize("mod_name,symbol", CALL_SITE_SYMBOLS)
def test_real_call_site_symbols_exist(mod_name, symbol):
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, symbol), f"仓库里有 `from {mod_name} import {symbol}`，但该名字不存在"


@pytest.mark.parametrize("pkg_mod,subs", sorted(PACKAGE_SUBMODULES.items()))
def test_package_submodules_are_aliased(pkg_mod, subs):
    """整包搬家时子模块也必须别名，否则会产生两份模块对象。"""
    for sub in subs:
        new_mod = pkg_mod.replace("ui.music_source", "services.music_source").replace(
            "ui.agent.voice", "services.voice"
        )
        old = importlib.import_module(f"{pkg_mod}.{sub}")
        new = importlib.import_module(f"{new_mod}.{sub}")
        assert old is new, f"{pkg_mod}.{sub} 与 {new_mod}.{sub} 不是同一个模块对象"


def test_patching_through_old_path_reaches_implementation():
    """**这条守卫对应一个实测踩到的缺陷**，不能删。

    第一版 shim 用 `globals().update` 复制引用，于是
    `import ui.music_source as ms; monkeypatch.setattr(ms, "MUSIC_SOURCES", fakes)`
    只改到 shim 的命名空间 —— 假音源不生效，测试真去请求了 QQ 音乐接口。

    别名方案下这个补丁必须落到实现模块上。
    """
    old = importlib.import_module("ui.music_source")
    new = importlib.import_module("services.music_source")

    original = new.MUSIC_SOURCES
    sentinel = {"__probe__": None}
    try:
        old.MUSIC_SOURCES = sentinel  # 通过**旧路径**写入
        assert new.MUSIC_SOURCES is sentinel, "通过旧路径的赋值没有传到实现模块（shim 退化成了复制）"
    finally:
        new.MUSIC_SOURCES = original


def test_private_names_are_reachable_through_the_old_path():
    """私有名也必须能通过旧路径取到（`achievement_sync` 真的用了 `_do_merge_db`）。"""
    old = importlib.import_module("achievement_engine")
    new = importlib.import_module("services.achievement_engine")
    privates = [n for n in dir(new) if n.startswith("_") and not n.startswith("__")]
    for name in privates:
        assert hasattr(old, name), f"旧路径缺私有名 {name}"
        assert getattr(old, name) is getattr(new, name)


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED)
def test_moved_implementations_are_ui_free(old_mod, new_mod, old_path, new_path):
    """搬进 services/ 的实现必须零 UI 依赖（用分层校验器同一套 AST 规则判定）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_fmcl_purity_for_test", REPO_ROOT / "scripts" / "check_services_purity.py"
    )
    purity = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = purity
    spec.loader.exec_module(purity)

    rule = next(r for r in purity.RULES if r.group == "services")
    report = purity.check_file(REPO_ROOT / new_path, rule)
    assert report.ok, "搬进 services/ 的实现出现 UI 依赖:\n" + "\n".join(
        v.render() for v in report.violations
    )


def test_services_package_covers_the_moved_modules():
    """分层校验器必须真的会扫到这些新文件。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_fmcl_purity_for_test2", REPO_ROOT / "scripts" / "check_services_purity.py"
    )
    purity = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = purity
    spec.loader.exec_module(purity)

    rule = next(r for r in purity.RULES if r.group == "services")
    scanned = {p.resolve() for p in purity.iter_python_files(rule.roots)}
    for _o, _n, _op, new_path in MOVED:
        assert (REPO_ROOT / new_path).resolve() in scanned, f"{new_path} 不在扫描范围内"


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED)
def test_shim_files_declare_the_alias(old_mod, new_mod, old_path, new_path):
    """仍然存在的 shim 文件必须显式声明 `sys.modules[__name__] = _impl`。

    这保证"别名"不是靠 import 副作用侥幸成立，而是文件里写着。
    """
    if old_path is None:
        pytest.skip(f"{old_mod} 的 shim 文件已删除（由父包统一别名）")
    text = io.open(REPO_ROOT / old_path, encoding="utf-8").read()
    assert "sys.modules[__name__] = _impl" in text, f"{old_path} 没有声明别名"


# ═══════════════════════════════════════════════════════════════════
# 两张"已登记行数偏差"表必须互相对得上（第 12 轮新增）
#
# 同一个事实被两处登记：本文件的 `REGISTERED_LINE_DELTAS` 按**非空行**算
# （跑 pytest 时用），`scripts/relocate_module.py` 的 `REGISTERED_LINE_DELTAS`
# 按**总行数**算（跑闸门时用）。两处独立演进迟早会打架 —— 一边登记了、另一边
# 没有，于是"pytest 绿、闸门红"或者反过来，而且没人知道谁对。
#
# 更糟的一种：登记项**过期**（代码后来改回去了）时，两张表都变成永久豁免名单。
# 所以这里不只对账，还**从 git 原文现算一遍**，把"登记值 == 现实的差值"钉死。
# ═══════════════════════════════════════════════════════════════════


def _load_relocate_script():
    """按文件路径加载 `scripts/relocate_module.py`（它是脚本，不是包）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_fmcl_relocate_for_test", REPO_ROOT / "scripts" / "relocate_module.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pristine_baseline_pin_is_valid():
    """**基线提交本身**必须是可用的 —— 这是"闸门自己也要被测试"的具体一条。

    本文件与 `scripts/relocate_module.py` 里所有"与原文一致"的比对都依赖
    ``PRISTINE_COMMIT``。阶段 1 收尾时踩过的坑：那批断言原本用 ``git show HEAD:``
    取"搬家前原文"，搬家提交一进历史，``HEAD`` 就变成搬家**之后**的代码 ——
    轻则 22 个用例整片报红，重则两边都是重构后的代码、比对**假绿**。

    所以这里把"基线钉在哪"变成一条会红的断言：
    提交必须可达、必须**不是**当前 HEAD、必须确实处于重构前形态，
    并且本文件的副本与闸门里的权威值一致。
    """
    relocate = _load_relocate_script()

    assert _baseline.PRISTINE_COMMIT == relocate.BASELINE_COMMIT, (
        "基线提交两处不一致 —— tests/_baseline.py 与 scripts/relocate_module.py "
        "必须钉在同一个提交上，否则测试与闸门会各比各的"
    )
    assert _baseline.git_has(f"{_baseline.PRISTINE_COMMIT}^{{commit}}"), (
        f"基线提交 {_baseline.PRISTINE_COMMIT} 取不到（历史被重写？浅克隆？）"
    )
    assert _baseline.git_rev_parse("HEAD") != _baseline.PRISTINE_COMMIT, (
        "当前 HEAD 就是基线提交 —— 此时「原文」与「现状」是同一棵树，比对照样相等，"
        "所有比对都会假绿"
    )
    for path, should_exist in _baseline.PRISTINE_PROBES:
        exists = _baseline.git_has(f"{_baseline.PRISTINE_COMMIT}:{path}")
        assert exists == should_exist, (
            f"基线提交里 {path} {'存在' if exists else '不存在'}，"
            f"但重构前应当{'存在' if should_exist else '不存在'} —— 基线钉错提交了？"
        )


def _old_to_new_paths(relocate) -> dict[str, str]:
    """用工具自己的 `_module_units()` 建「新文件相对路径 → 旧文件相对路径」映射。

    自己再抄一份映射就等于给闸门开了第二个真相来源；直接用工具的实现更可信。
    """
    mapping: dict[str, str] = {}
    for move in relocate.MOVES:
        for old_file, new_file, _om, _nm in relocate._module_units(move):
            mapping[new_file.relative_to(REPO_ROOT).as_posix()] = old_file.relative_to(
                REPO_ROOT
            ).as_posix()
    return mapping


def test_registered_line_deltas_agree_with_the_relocate_gate():
    """两张登记表的**键集合**必须一致（少一边就是闸门/测试不同步）。"""
    relocate = _load_relocate_script()
    gate_keys = set(relocate.REGISTERED_LINE_DELTAS)
    test_keys = set(REGISTERED_LINE_DELTAS)
    assert gate_keys == test_keys, (
        f"登记表不同步 —— 只在闸门里: {sorted(gate_keys - test_keys)}；"
        f"只在测试里: {sorted(test_keys - gate_keys)}"
    )


def test_registered_line_deltas_match_reality():
    """每个登记项都要**现算一遍**：总行数增量与两条登记值都必须是现实的值。

    这条是给"登记过期"兜底的：代码后来改回去了、或者当初随手写了个数，
    都会在这里红，而不是安静地变成永久豁免。
    """
    relocate = _load_relocate_script()
    old_to_new = _old_to_new_paths(relocate)

    for new_path, (total_delta, why) in sorted(relocate.REGISTERED_LINE_DELTAS.items()):
        assert why.strip(), f"{new_path}: 登记项没有理由"
        old_path = old_to_new.get(new_path)
        assert old_path, f"{new_path}: 它不是任何一次搬家的目标文件（登记的键写错了？）"

        original = _baseline.git_show(old_path)
        assert original.strip(), f"基线提交里找不到 {old_path} 的原文"

        moved = io.open(REPO_ROOT / new_path, encoding="utf-8").read()
        orig_lines = original.splitlines()
        moved_lines = moved.splitlines()
        real_total = len(moved_lines) - len(orig_lines)
        real_nonblank = len([ln for ln in moved_lines if ln.strip()]) - len(
            [ln for ln in orig_lines if ln.strip()]
        )
        assert real_total == total_delta, (
            f"{new_path}: 闸门登记的总行数增量 {total_delta} != 现实 {real_total}"
            "（登记过期了？还是又改了代码却没更新登记表？）"
        )
        assert new_path in REGISTERED_LINE_DELTAS, f"{new_path} 没有在本文件的登记表里"
        test_delta, test_why = REGISTERED_LINE_DELTAS[new_path]
        # 两张表的理由都要非空 —— 只查一边等于留了个"写空话"的口子
        assert test_why.strip(), f"{new_path}: 本文件的登记项没有理由"
        assert real_nonblank == test_delta, (
            f"{new_path}: 本文件登记的非空行增量 {test_delta} != 现实 {real_nonblank}"
        )
        assert real_total >= real_nonblank, "总行数增量不可能小于非空行增量"
