# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建脚本 —— 阶段 4.3 起 QML-only、onedir-only。

真正的构建计划在 `scripts/build_plan.py`（普通模块，测试可以 import）；本文件只负责
平台相关的 EXE / BUNDLE / COLLECT 组装，因为这部分本来就与平台绑死。

用法：

    pyinstaller build.spec --noconfirm      # → dist/FMCL/（目录产物）

产物形态：QML 用 **onedir**（`COLLECT`）。onefile 每次启动都要把 Qt 运行时（约 200 MB）
解压到临时目录，启动器的冷启动体验不可接受。`updater.py` 只认 `FMCL-Setup-*.exe` 安装包，
**装目录不影响自动更新链路**（已核对 `updater.py::find_suitable_asset`）。回滚靠上一版安装包
（用户 2026-11-14 裁决：1-B / 2-A / 3-B）。

历史分支已清理：4.2 之前为 Tk 单文件产物保留的 onefile / Windows Agent 入口不再存在；
QML 产物永远走 COLLECT，因此 EXE 永远带 `exclude_binaries=True`。
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
    plan,
    should_drop_collected_binary,
    should_drop_qml_module_dir,
)

block_cipher = None

_root = os.getcwd()
# 自动检测平台，支持环境变量覆盖（macOS/Linux 的目录产物与 Windows 共用一套组装流程）
_platform = os.environ.get("PLATFORM") or detect_platform(sys.platform)
arch = os.environ.get("ARCH", "amd64")

print(f"Building QML platform={_platform}, arch={arch}")

# ── QML 构建前置检查 ──
# `third_party/*` 不入库（体积原因），新克隆的机器上没有自编译的 FluentUI 模块。
# 不检查的话 PyInstaller 会产出一个"看着正常、一启动就找不到界面"的产物 —— 那种故障在
# 源码态永远复现不出来。这里宁可响亮地失败，并把补救命令打出来。
_missing = missing_qml_requirements(Path(_root))
if _missing:
    raise SystemExit(
        "[build.spec] QML 后端缺少构建前置：\n  - "
        + "\n  - ".join(_missing)
        + "\n\n先补齐第三方源码与自编译模块，再重试：\n"
        + "  powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1\n"
        + "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1\n"
    )

_P = plan(_root, _platform)

icon_path = _P["icon"]
datas = _P["datas"]
hidden_imports = list(_P["hidden_imports"])
excludes = list(_P["excludes"])
entry = _P["entry"]
product_name = _P["product_name"]

# ── 语音输入 (sounddevice + onnxruntime + sentencepiece) 动态库收集 ──
binaries = []
if _platform == "win":
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

# ── 二次过滤收集结果 ──
# PyInstaller 的 PySide6 hook 会把整个 Qt 运行时收进来，其中未使用的部分体积很大
# （Qt6WebEngineCore.dll 单个 142 MB）。裁剪依据见 scripts/build_plan.py 的
# QML_DROP_BINARY_PATTERNS / QML_KEEP_MODULE_DIRS。
_binaries_before = len(a.binaries)
a.binaries = [
    b
    for b in a.binaries
    if not (
        should_drop_collected_binary(b[0])
        or should_drop_collected_binary(b[1])
        or should_drop_qml_module_dir(b[0])
        or should_drop_qml_module_dir(b[1])
    )
]
_datas_before = len(a.datas)
a.datas = [d for d in a.datas if not (should_drop_qml_module_dir(d[0]) or should_drop_qml_module_dir(d[1]))]
print(
    f"[build.spec] 裁剪：二进制 {_binaries_before} → {len(a.binaries)}，"
    f"数据 {_datas_before} → {len(a.datas)}"
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

#: onedir（唯一形态）走 exclude_binaries=True + COLLECT；EXE 本身只带引导器。
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

if _platform == "mac":
    # macOS 的目录产物**未实测**（阶段 0 的出门条件是 Windows x64 一个目标）。
    # BUNDLE 必须包 COLLECT（目录），不能像 onefile 那样包 EXE。
    app = BUNDLE(
        coll,
        name=f"{product_name}.app",
        icon=icon_path.replace(".ico", ".icns") if os.path.exists(icon_path.replace(".ico", ".icns")) else None,
        bundle_identifier="com.fmcl.launcher",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSAppleScriptEnabled": False,
            "CFBundleShortVersionString": "2.0.2",
            "CFBundleVersion": "2.0.2",
        },
    )

print(f"Build configuration complete for QML {product_name} platform={_platform}-{arch}")
