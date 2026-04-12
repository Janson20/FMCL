# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller构建脚本 - 多平台支持"""

import os
import sys
from pathlib import Path

# 获取平台和架构
platform = os.environ.get('PLATFORM', 'win')
arch = os.environ.get('ARCH', 'amd64')

print(f"Building for platform: {platform}, arch: {arch}")

block_cipher = None

# 收集所有隐式导入
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

# 根据平台添加特定导入
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

# 收集数据文件（如果有）
datas = []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
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

# 根据平台生成不同的可执行文件配置
if platform == 'win':
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='MCL',
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
    )
elif platform == 'mac':
    # macOS: 创建单个可执行文件
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='MCL',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,  # macOS上UPX可能导致问题
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,  # GUI应用
        disable_windowed_traceback=False,
        argv_emulation=True,  # macOS需要
        target_arch='universal2' if arch == 'arm64' else 'x86_64',
        codesign_identity=None,
        entitlements_file=None,
    )
    
    # 创建app bundle
    app = BUNDLE(
        exe,
        name='MCL.app',
        icon=None,
        bundle_identifier='com.mcl.launcher',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSAppleScriptEnabled': False,
            'CFBundleShortVersionString': '2.0.0',
            'CFBundleVersion': '2.0.0',
        },
    )
else:
    # Linux
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='MCL',
        debug=False,
        bootloader_ignore_signals=False,
        strip=True,  # Linux上可以strip
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
