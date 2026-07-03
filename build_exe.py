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
# User data lives next to the exe (dist/ckrbot/) but the --clean build wipes that
# whole folder. Stash it OUTSIDE dist so a rebuild never destroys recorded macros
# or tuned GUI settings, then put it back afterwards.
USERDATA_BACKUP = ROOT / "build" / "_userdata"
USERDATA = ("macros", "ui_settings.json")


def _stash_userdata() -> list[str]:
    """Copy existing user data out of dist before the destructive build. Returns
    the names actually stashed."""
    stashed: list[str] = []
    if not DIST.exists():
        return stashed
    USERDATA_BACKUP.mkdir(parents=True, exist_ok=True)
    for rel in USERDATA:
        src = DIST / rel
        if not src.exists():
            continue
        dst = USERDATA_BACKUP / rel
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        stashed.append(rel)
    if stashed:
        print(f">> stashed user data ({', '.join(stashed)}) -> {USERDATA_BACKUP}")
    return stashed


def _restore_userdata(stashed: list[str]) -> None:
    """Put stashed user data back next to the freshly built exe (merging macros so
    nothing recorded between builds is lost)."""
    for rel in stashed:
        src = USERDATA_BACKUP / rel
        dst = DIST / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():  # merge into the (empty) fresh dir
                target = dst / item.name
                shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
        else:
            shutil.copy2(src, dst)
    if stashed:
        print(f">> restored user data ({', '.join(stashed)})")


def main() -> None:
    stashed = _stash_userdata()  # protect macros/settings from --clean

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

    _restore_userdata(stashed)  # bring recorded macros / tuned settings back

    print(f"\nBuilt: {DIST}\nRun {DIST / 'ckrbot.exe'} — config/macros/assets are editable beside it.")


if __name__ == "__main__":
    main()
