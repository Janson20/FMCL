# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建脚本 - 多平台支持"""

import os
import sys
import tkinter as tk
from pathlib import Path

# 自动检测平台
_platform = sys.platform
if _platform == 'win32':
    platform = 'win'
elif _platform == 'darwin':
    platform = 'mac'
else:
    platform = 'linux'

# 支持环境变量覆盖
platform = os.environ.get('PLATFORM', platform)
arch = os.environ.get('ARCH', 'amd64')

print(f"Building for platform: {platform}, arch: {arch}")

block_cipher = None

# 图标路径
icon_path = os.path.join(os.getcwd(), 'icon.ico')

# locales 目录路径（多语言支持）
locales_src = os.path.join(os.getcwd(), 'ui', 'locales')

# pyproject.toml 路径
pyproject_path = os.path.join(os.getcwd(), 'pyproject.toml')

# 数据文件列表：图标 + locales + pyproject.toml
datas = [(icon_path, '.'), (pyproject_path, '.')]
if os.path.exists(locales_src):
    datas.append((locales_src, 'ui' + os.sep + 'locales'))

# 通用隐式导入
hidden_imports = [
    'minecraft_launcher_lib',
    'minecraft_launcher_lib._helper',
    'minecraft_launcher_lib._internal_types',
    'minecraft_launcher_lib.command',
    'minecraft_launcher_lib.exceptions',
    'minecraft_launcher_lib.fabric',
    'minecraft_launcher_lib.forge',
    'minecraft_launcher_lib.install',
    'minecraft_launcher_lib.java_utils',
    'minecraft_launcher_lib.microsoft_account',
    'minecraft_launcher_lib.mod_loader',
    'minecraft_launcher_lib.mod_loader._forge',
    'minecraft_launcher_lib.mod_loader._fabric',
    'minecraft_launcher_lib.mod_loader._neoforge',
    'minecraft_launcher_lib.mrpack',
    'minecraft_launcher_lib.natives',
    'minecraft_launcher_lib.news',
    'minecraft_launcher_lib.quilt',
    'minecraft_launcher_lib.runtime',
    'minecraft_launcher_lib.types',
    'minecraft_launcher_lib.utils',
    'minecraft_launcher_lib.vanilla_launcher',
    'forgepy',
    'requests',
    'logzero',
    'pyautogui',
    'tqdm',
    'keyboard',
    'tkinter',
    'tkinter.ttk',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'tkinter.colorchooser',
    'tkinter.commondialog',
    'tkinter.constants',
    'PIL',
    'PIL.Image',
    'PIL.ImageTk',
    'customtkinter',
    'orjson',
    'urllib3',
    'rarfile',
]

# 平台特定导入
if platform == 'win':
    hidden_imports.extend([
        'pywintypes',
        'win32com',
        'win32com.client',
    ])
elif platform == 'mac':
    hidden_imports.extend([
        'AppKit',
        'Foundation',
    ])

# ── 收集 tkinter/TCL 库文件（Windows 必需）──
# tkinter 需要访问 tcl86t.dll, tk86t.dll 等文件
binaries_tkinter = []
if platform == 'win':
    # 获取 Python 的 tcl 库目录
    tk_lib_dir = Path(sys.prefix) / 'tcl' / 'tk8.6'
    if tk_lib_dir.exists():
        binaries_tkinter.append((str(tk_lib_dir), '.'))
    tcl_lib_dir = Path(sys.prefix) / 'tcl' / 'tcl8.6'
    if tcl_lib_dir.exists():
        binaries_tkinter.append((str(tcl_lib_dir), '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries_tkinter if platform == 'win' else [],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'IPython', 'jupyter', 'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if platform == 'win':
    # Windows: 单文件可执行文件 (NSIS 安装包会打包它)
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='FMCL',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir='FMCL',  # 为 tkinter/TCL 库创建独立临时目录
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_path,
    )

elif platform == 'mac':
    # macOS: 单文件 EXE + BUNDLE 为 .app
    # 注意: target_arch 必须与实际 Python 安装架构一致，不能用 universal2
    # 因为 pip 安装的 .so 文件不是 fat binary
    mac_target_arch = 'arm64' if arch == 'arm64' else 'x86_64'
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='FMCL',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=mac_target_arch,
        codesign_identity=None,
        entitlements_file=None,
    )

    app = BUNDLE(
        exe,
        name='FMCL.app',
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
    # Linux: 单文件可执行文件
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='FMCL',
        debug=False,
        bootloader_ignore_signals=False,
        strip=True,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

print(f"Build configuration complete for {platform}-{arch}")
