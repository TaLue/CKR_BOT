"""Macro data model + JSON persistence (Phase 5.5 — DOWN/UP stream).

The game is driven via a keymap where W (Jump) and S (Slide) are STATIONARY touch
points that can be tapped rapidly (W) or held for seconds (S). A tap-atomic model
loses hold duration, so a macro is a stream of DOWN/UP InputEvents (no MOVE — the
game has no swipe). dt_ms is the delay from the previous event (first event = 0,
or the anchor gap when recorded — see recorder).

Coordinates are pixels (identity with touch space, spec §3.1) — no scaling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Model schema version. v1 was tap-atomic (Macro.taps); v2 is the DOWN/UP stream
# (Macro.events). v1 files are intentionally not loadable — re-record for v2.
MACRO_VERSION = 2

Action = Literal["DOWN", "UP"]


class InputEvent(BaseModel):
    """A single touch transition. DOWN presses at (x, y); UP releases it."""

    dt_ms: int = Field(ge=0)  # delay since previous event (first = 0 or anchor gap)
    action: Action
    x: int
    y: int


class Screen(BaseModel):
    w: int
    h: int


class TouchMax(BaseModel):
    x: int
    y: int


class Macro(BaseModel):
    """A recorded DOWN/UP input stream for one level, with replay metadata."""

    name: str
    created_at: str  # ISO 8601
    version: int = MACRO_VERSION
    screen: Screen
    touch_max: TouchMax
    pressure_max: int
    events: list[InputEvent]

    def save(self, path: str | Path) -> None:
        """Write the macro as pretty JSON."""
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Macro":
        """Load and validate a macro from JSON (v2 DOWN/UP schema)."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
