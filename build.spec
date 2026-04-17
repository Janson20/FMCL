# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建脚本 - 多平台支持"""

import os
import sys
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
icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')

# 通用隐式导入
hidden_imports = [
    'minecraft_launcher_lib',
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
    'PIL',
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

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(icon_path, '.')],
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
        runtime_tmpdir=None,
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
