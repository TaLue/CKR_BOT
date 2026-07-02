"""Path resolution that works both in development and as a frozen EXE.

Writable data (macros/, logs/) and shipped resources (game/assets/, vendor/,
config.yaml) are resolved relative to the app BASE directory:

  * frozen (PyInstaller): the folder containing the .exe — everything lives next
    to it and stays writable (use a onedir build, or ship these folders beside
    the onefile exe).
  * dev: the current working directory (the repo root when run with `python -m`).

Relative paths in config are resolved against this base so add/delete/rename of
macros write to the right place regardless of how the app is launched.
"""

from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    """The directory the app treats as its root for data + resources."""
    if getattr(sys, "frozen", False):  # PyInstaller / cx_Freeze
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def resolve_path(path: str | Path) -> Path:
    """Absolute path: returned as-is if already absolute, else joined to base_dir()."""
    p = Path(path)
    return p if p.is_absolute() else (base_dir() / p)
