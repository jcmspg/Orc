# PyInstaller spec for the orc engine (onedir). Build with scripts/build-engine.py.
# Output: dist/engine/orc-engine/orc-engine[.exe]
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent

datas = [(str(root / "src" / "orc" / "docker" / "Dockerfile"), "orc/docker")]
binaries = []
hiddenimports = []
# uvicorn picks loops/protocols by string name at runtime; websockets/anthropic have lazy imports.
for pkg in ("uvicorn", "websockets", "anthropic"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("orc")

a = Analysis(
    [str(root / "packaging" / "engine_entry.py")],
    pathex=[str(root / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="orc-engine", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="orc-engine", upx=False)
