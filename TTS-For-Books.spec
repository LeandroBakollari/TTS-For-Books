# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules
import espeakng_loader
import en_core_web_sm

block_cipher = None

espeak_pkg_dir = Path(espeakng_loader.__file__).resolve().parent
spacy_model_dir = Path(en_core_web_sm.__file__).resolve().parent

datas = [
    ("assets", "assets"),
    (str(espeak_pkg_dir / "espeak-ng-data"), "espeakng_loader/espeak-ng-data"),
    (str(spacy_model_dir), "en_core_web_sm"),
]
binaries = []
hiddenimports = []

for pkg in [
    "PySide6",
    "kokoro",
    "espeakng_loader",
    "spacy",
    "en_core_web_sm",
]:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

for pkg in [
    "language_tags",
    "csvw",
    "segments",
    "phonemizer",
    "misaki",
]:
    datas += collect_data_files(pkg)
    hiddenimports += collect_submodules(pkg)

datas += collect_data_files("espeakng_loader")
hiddenimports += collect_submodules("espeakng_loader")
hiddenimports += collect_submodules("spacy")
hiddenimports += collect_submodules("en_core_web_sm")

datas = list(dict.fromkeys(datas))
binaries = list(dict.fromkeys(binaries))
hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    ["src\\abtts\\__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TTS-For-Books",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TTS-For-Books",
)