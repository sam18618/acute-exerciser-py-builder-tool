# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for PX Py Builder.

Produces a single windowed .exe (dist/PX_Py_Builder.exe) that needs no Python
install on the target machine. The builder is stdlib + tkinter only, so every
other package that happens to live in the dev venv is excluded to keep the
binary small.

    .venv\Scripts\pyinstaller.exe PX_Py_Builder.spec --noconfirm
"""

a = Analysis(
    ["run_builder.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "aqpxlib",
        "betterproto",
        "grpc",
        "grpc_tools",
        "grpclib",
        "google.protobuf",
        "jinja2",
        "zmq",
        "pytest",
        "black",
        "isort",
        "test",
        "unittest",
        "pydoc",
        "email",
        "http",
        "xml",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PX_Py_Builder",
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
)
