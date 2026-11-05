#!/usr/bin/env python3
"""Freeze the engine into dist/engine/orc-engine/ with PyInstaller (onedir) for this OS.

    python -m venv .venv && .venv/bin/pip install -e ".[dev]" pyinstaller
    .venv/bin/python scripts/build-engine.py

PyInstaller cannot cross-compile: run this on each target OS (CI does).
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "engine"


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(OUT), "--workpath", str(ROOT / "build" / "pyinstaller"),
            str(ROOT / "packaging" / "orc-engine.spec"),
        ],
        check=True,
        cwd=ROOT,
    )
    exe = OUT / "orc-engine" / ("orc-engine.exe" if sys.platform == "win32" else "orc-engine")
    if not exe.exists():
        sys.exit(f"build finished but {exe} is missing")
    print(f"engine built: {exe}")


if __name__ == "__main__":
    main()
