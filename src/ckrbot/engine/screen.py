"""Screen identification core (Phase 6a) — game-agnostic.

A ScreenSpec names a state and the template markers that identify it. The
identifier tries specs in PRIORITY ORDER (specific/popup screens before general
ones, spec §4) and returns the first whose markers all match (and whose `absent`
markers do not). No transitions are hardcoded here — the engine acts on whatever
state is identified each loop (INVARIANT #3).

This module has no CKR-specific names; the ordered spec list is injected by the
game layer (ckrbot.game.states).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ckrbot.capture.screen import Frame
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import Region, find_template

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScreenSpec:
    """One state: its name and the template markers that identify it.

    All ``markers`` must match AND none of ``absent`` may match. Priority is the
    position of this spec in the identifier's ordered list.
    """

    name: str
    markers: tuple[str, ...]
    absent: tuple[str, ...] = ()


def _pad(region: Region, pad: int, width: int, height: int) -> Region:
    x1, y1, x2, y2 = region
    return (max(0, x1 - pad), max(0, y1 - pad), min(width, x2 + pad), min(height, y2 + pad))


class ScreenIdentifier:
    """Identifies the current screen by matching markers in priority order."""

    def __init__(
        self,
        templates: TemplateStore,
        specs: list[ScreenSpec],
        *,
        threshold: float,
        region_pad: int = 10,
    ) -> None:
        self._templates = templates
        self._specs = specs
        self._threshold = threshold
        self._pad = region_pad

    def matches(self, frame: Frame, template_name: str) -> bool:
        """True if ``template_name`` matches in its region above threshold."""
        tpl = self._templates.load(template_name)
        h, w = frame.shape[:2]
        region = _pad(tpl.region, self._pad, w, h) if tpl.region is not None else None
        return find_template(frame, tpl.image, region).confidence >= self._threshold

    def identify(self, frame: Frame) -> str:
        """Return the first matching state name (priority order), else UNKNOWN."""
        for spec in self._specs:
            if all(self.matches(frame, m) for m in spec.markers) and not any(
                self.matches(frame, a) for a in spec.absent
            ):
                return spec.name
        return UNKNOWN
