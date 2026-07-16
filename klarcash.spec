# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec fuer die eigenstaendige Windows-.exe.
# Bauen mit: pyinstaller klarcash.spec --workpath desktop_build/build --distpath desktop_build/dist
# Ergebnis (One-File-Build) liegt danach unter desktop_build/dist/Klarcash.exe.

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Klarcash",
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
