# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建脚本 —— 两个界面后端（由环境变量 `UI_BACKEND` 选择）。

真正的构建计划在 `scripts/build_plan.py`（普通模块，测试可以 import）；本文件只负责
平台相关的 EXE / BUNDLE / COLLECT 组装，因为这部分本来就与平台绑死。

用法（迁移前的用法保持不变）：

    pyinstaller build.spec                              # tk 后端（默认）→ dist/FMCL.exe
    $env:UI_BACKEND='qml'; pyinstaller build.spec       # qml 后端 → dist/FMCL-QML/（目录产物）
    $env:PLATFORM='win'; $env:ARCH='x86'                # 平台/架构覆盖（与迁移前一致）

产物形态（为什么 QML 是目录而不是单文件）：

* tk：**单文件 EXE**，与今天发布的产物逐条一致（同一个 `main.py`、同一套 `excludes`）；
* qml：**onedir**（`COLLECT`）。onefile 每次启动都要把 Qt 运行时（约 200 MB）解压到临时目录，
  启动器的冷启动体验不可接受。`updater.py` 只认 `FMCL-Setup-*.exe` 安装包，
  **装目录不影响自动更新链路**（已核对 `updater.py::find_suitable_asset`）。

阶段 3 期间 QML 产物是**验收/预览产物**，发布链仍然只出 tk 产物；单文件双后端（一个 exe 里
同时含 Tk 与 Qt、用 `config.ui_backend` 现场切换）是阶段 4.3 的事。
"""

import os
import sys
from pathlib import Path

# PyInstaller 会把 SPECPATH 注入 spec 的全局命名空间；测试/其它调用方可能直接 exec 本文件。
_SPEC_DIR = globals().get("SPECPATH") or os.getcwd()
if _SPEC_DIR not in sys.path:
    sys.path.insert(0, _SPEC_DIR)

from scripts.build_plan import (  # noqa: E402  （必须在 sys.path 处理之后）
    detect_platform,
    missing_qml_requirements,
    normalize_backend,
    plan,
    should_drop_collected_binary,
    should_drop_qml_module_dir,
)

block_cipher = None

_root = os.getcwd()
_backend = normalize_backend(os.environ.get("UI_BACKEND"))
# 自动检测平台，支持环境变量覆盖
_platform = os.environ.get("PLATFORM") or detect_platform(sys.platform)
arch = os.environ.get("ARCH", "amd64")

print(f"Building backend={_backend} platform={_platform}, arch={arch}")

# ── QML 后端的构建前置检查 ──
# `third_party/*` 不入库（体积原因），新克隆的机器上没有自编译的 FluentUI 模块。
# 不检查的话 PyInstaller 会产出一个"看着正常、一启动就找不到界面"的产物 —— 那种故障在
# 源码态永远复现不出来。这里宁可响亮地失败，并把补救命令打出来。
if _backend == "qml":
    _missing = missing_qml_requirements(Path(_root))
    if _missing:
        raise SystemExit(
            "[build.spec] QML 后端缺少构建前置：\n  - "
            + "\n  - ".join(_missing)
            + "\n\n先补齐第三方源码与自编译模块，再重试：\n"
            + "  powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1\n"
            + "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1\n"
        )

_P = plan(_backend, _root, _platform)

icon_path = _P["icon"]
datas = _P["datas"]
hidden_imports = list(_P["hidden_imports"])
excludes = list(_P["excludes"])
entry = _P["entry"]
product_name = _P["product_name"]
onefile = bool(_P["onefile"])

# ── 收集 tkinter/TCL 库文件（Windows 必需）──
# tkinter 需要访问 tcl86t.dll, tk86t.dll 等文件
binaries = []
if _platform == "win":
    for _lib_dir in (Path(sys.prefix) / "tcl" / "tk8.6", Path(sys.prefix) / "tcl" / "tcl8.6"):
        if _lib_dir.exists():
            binaries.append((str(_lib_dir), "."))

    # ── 语音输入 (sounddevice + onnxruntime + sentencepiece) 动态库收集 ──
    from PyInstaller.utils.hooks import collect_dynamic_libs

    for _package in ("_sounddevice_data", "onnxruntime", "sentencepiece"):
        try:
            binaries += collect_dynamic_libs(_package)
        except Exception:
            pass

a = Analysis(
    [entry],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── QML 后端：二次过滤收集结果 ──
# PyInstaller 的 PySide6 hook 会把整个 Qt 运行时收进来，其中未使用的部分体积很大
# （Qt6WebEngineCore.dll 单个 142 MB）。裁剪依据见 scripts/build_plan.py 的
# QML_DROP_BINARY_PATTERNS / QML_KEEP_MODULE_DIRS。
if _P["filter_collected"]:
    _binaries_before = len(a.binaries)
    a.binaries = [b for b in a.binaries if not (should_drop_collected_binary(b[0]) or should_drop_collected_binary(b[1]))]
    _datas_before = len(a.datas)
    a.datas = [d for d in a.datas if not (should_drop_qml_module_dir(d[0]) or should_drop_qml_module_dir(d[1]))]
    print(
        f"[build.spec] 裁剪：二进制 {_binaries_before} → {len(a.binaries)}，"
        f"数据 {_datas_before} → {len(a.datas)}"
    )

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

#: onedir（QML 后端）走 exclude_binaries=True + COLLECT；onefile 直接把 binaries/datas 塞进 EXE。
_payload = [a.binaries, a.zipfiles, a.datas] if onefile else []

if _platform == "win":
    if onefile:
        exe = EXE(
            pyz,
            a.scripts,
            a.binaries,
            a.zipfiles,
            a.datas,
            [],
            name=product_name,
            debug=False,
            bootloader_ignore_signals=False,
            strip=False,
            # Qt 运行时不压缩：UPX 压缩过的 Qt DLL 有加载失败风险，解压也拖慢冷启动。
            upx=not _P["with_qt"],
            upx_exclude=[],
            runtime_tmpdir=product_name,
            console=False,
            disable_windowed_traceback=False,
            argv_emulation=False,
            target_arch=None,
            codesign_identity=None,
            entitlements_file=None,
            icon=icon_path,
        )
    else:
        exe = EXE(
            pyz,
            a.scripts,
            [],
            exclude_binaries=True,
            name=product_name,
            debug=False,
            bootloader_ignore_signals=False,
            strip=False,
            upx=False,
            upx_exclude=[],
            runtime_tmpdir=None,
            console=False,
            disable_windowed_traceback=False,
            argv_emulation=False,
            target_arch=None,
            codesign_identity=None,
            entitlements_file=None,
            icon=icon_path,
        )
        coll = COLLECT(
            exe,
            a.binaries,
            a.zipfiles,
            a.datas,
            strip=False,
            upx=False,
            upx_exclude=[],
            name=product_name,
        )

    if onefile:
        # Windows Agent CLI: 控制台子系统的独立入口（仅 tk 产物需要；QML 产物是验收产物）
        agent_a = Analysis(
            ['agent_cli.py'],
            pathex=[],
            binaries=[],
            datas=datas,
            hiddenimports=hidden_imports,
            hookspath=[],
            hooksconfig={},
            runtime_hooks=[],
            excludes=[*excludes, 'numpy'],
            win_no_prefer_redirects=False,
            win_private_assemblies=False,
            cipher=block_cipher,
            noarchive=False,
        )

        agent_pyz = PYZ(agent_a.pure, agent_a.zipped_data, cipher=block_cipher)

        agent_exe = EXE(
            agent_pyz,
            agent_a.scripts,
            agent_a.binaries,
            agent_a.zipfiles,
            agent_a.datas,
            [],
            name='FMCL-Agent',
            debug=False,
            bootloader_ignore_signals=False,
            strip=False,
            upx=True,
            upx_exclude=[],
            runtime_tmpdir=None,
            console=True,
            disable_windowed_traceback=False,
            argv_emulation=False,
            target_arch=None,
            codesign_identity=None,
            entitlements_file=None,
            icon=icon_path,
        )

elif _platform == "mac":
    # macOS：单文件 EXE + BUNDLE 为 .app
    # 注意: target_arch 必须与实际 Python 安装架构一致，不能用 universal2
    # 因为 pip 安装的 .so 文件不是 fat binary
    mac_target_arch = 'arm64' if arch == 'arm64' else 'x86_64'
    exe = EXE(
        pyz,
        a.scripts,
        *_payload,
        [],
        exclude_binaries=not onefile,
        name=product_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=not _P["with_qt"],
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=mac_target_arch,
        codesign_identity=None,
        entitlements_file=None,
    )

    # QML 后端的 macOS 目录产物**未实测**（阶段 0 的出门条件是 Windows x64 一个目标）。
    if not onefile:
        coll = COLLECT(
            exe,
            a.binaries,
            a.zipfiles,
            a.datas,
            strip=False,
            upx=False,
            upx_exclude=[],
            name=product_name,
        )

    app = BUNDLE(
        exe,
        name=f'{product_name}.app',
        icon=icon_path.replace('.ico', '.icns') if os.path.exists(icon_path.replace('.ico', '.icns')) else None,
        bundle_identifier='com.fmcl.launcher',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSAppleScriptEnabled': False,
            'CFBundleShortVersionString': '2.0.2',
            'CFBundleVersion': '2.0.2',
        },
    )

else:
    # Linux：单文件可执行文件（QML 后端为目录产物）
    exe = EXE(
        pyz,
        a.scripts,
        *_payload,
        [],
        exclude_binaries=not onefile,
        name=product_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=onefile,
        upx=not _P["with_qt"],
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    if not onefile:
        coll = COLLECT(
            exe,
            a.binaries,
            a.zipfiles,
            a.datas,
            strip=False,
            upx=False,
            upx_exclude=[],
            name=product_name,
        )

print(f"Build configuration complete for backend={_backend} platform={_platform}-{arch}")
