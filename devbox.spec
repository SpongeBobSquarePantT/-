# -*- mode: python ; coding: utf-8 -*-
"""DevBox 打包配置。

构建：python -m PyInstaller --noconfirm --clean devbox.spec
产物：dist/DevBox.exe（单文件，运行时解包到临时目录）
"""

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("frontend", "frontend"),
        ("backend/config/tools.json", "backend/config"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DevBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
