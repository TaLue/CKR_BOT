"""Build a distributable CKR Farm Bot EXE (PyInstaller, onedir).

Produces  dist/ckrbot/  containing:
    ckrbot.exe
    _internal/            (Python runtime + deps, incl. adbutils' bundled adb.exe)
    config.yaml           (editable, next to the exe)
    game/assets/          (templates)
    vendor/minitouch/     (minitouch binaries)
    macros/  logs/        (writable — created on first run)

The app resolves data/resource paths relative to the exe folder (see
ckrbot.paths), so config/macros/assets sit NEXT TO the exe and stay editable —
add/delete/rename of macros work in the built app.

Usage:
    pip install -e ".[build]"
    python build_exe.py
Then zip and share  dist/ckrbot/ .  (Test the exe on a clean machine.)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "ckrbot"


def main() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "ckrbot",
        "--onedir",
        "--windowed",                       # GUI app (no console window)
        "--collect-data", "adbutils",       # CRITICAL: bundle adbutils' adb.exe
        "--collect-submodules", "ckrbot",
        str(ROOT / "src" / "ckrbot" / "__main__.py"),
    ]
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True)

    # Ship editable resources NEXT TO the exe (paths resolve to the exe folder).
    shutil.copy2(ROOT / "config.yaml", DIST / "config.yaml")
    for rel in ("game/assets", "vendor/minitouch"):
        dst = DIST / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ROOT / rel, dst)
    for rel in ("macros", "logs"):
        (DIST / rel).mkdir(parents=True, exist_ok=True)

    print(f"\nBuilt: {DIST}\nRun {DIST / 'ckrbot.exe'} — config/macros/assets are editable beside it.")


if __name__ == "__main__":
    main()
