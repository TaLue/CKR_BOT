"""Vision primitives (Phase 2) — pure functions over a BGR Frame.

INVARIANT (CLAUDE.md): these functions are PURE — they take a frame (and
templates/params) and return results, with no ADB calls or side effects, so they
are unit-testable from fixtures. All coordinates are in pixel space, which equals
touch space (spec §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ckrbot.capture.screen import Frame

# Region of interest as (x1, y1, x2, y2) in full-frame pixel coordinates.
Region = tuple[int, int, int, int]


@dataclass(frozen=True)
class MatchResult:
    """Result of a template match, in full-frame pixel coordinates."""

    confidence: float
    center: tuple[int, int]  # (x, y) tap point = center of the matched bbox
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)

    @property
    def found(self) -> bool:
        """Convenience: did this clear a typical threshold? (caller decides threshold)."""
        return self.confidence > 0.0


def _clamp_region(region: Region | None, width: int, height: int) -> Region:
    if region is None:
        return (0, 0, width, height)
    x1, y1, x2, y2 = region
    return (max(0, x1), max(0, y1), min(width, x2), min(height, y2))


def find_template(
    frame: Frame,
    template: np.ndarray,
    region: Region | None = None,
) -> MatchResult:
    """Locate ``template`` inside ``frame`` (optionally within ``region``).

    Uses ``TM_CCOEFF_NORMED`` on the 3-channel (color) images, so channel
    differences count — a gray Cancel button won't match a green button of the
    same shape. Returns the best match translated back to full-frame coordinates.

    A region smaller than the template yields ``confidence == 0.0`` rather than
    raising, so callers can treat "region too small" as "not found".
    """
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = _clamp_region(region, width, height)
    search = frame[y1:y2, x1:x2]

    th, tw = template.shape[:2]
    sh, sw = search.shape[:2]
    if sh < th or sw < tw:
        return MatchResult(0.0, (x1, y1), (x1, y1, x1, y1))

    res = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    mx, my = max_loc  # top-left of best match within the search window

    bx1, by1 = x1 + mx, y1 + my
    bx2, by2 = bx1 + tw, by1 + th
    center = (bx1 + tw // 2, by1 + th // 2)
    return MatchResult(float(max_val), center, (bx1, by1, bx2, by2))


def color_matches(
    frame: Frame,
    x: int,
    y: int,
    target_bgr: tuple[int, int, int],
    tol: int = 10,
) -> bool:
    """True if the pixel at (x, y) is within ``tol`` (per channel) of ``target_bgr``."""
    b, g, r = (int(c) for c in frame[y, x])
    tb, tg, tr = target_bgr
    return abs(b - tb) <= tol and abs(g - tg) <= tol and abs(r - tr) <= tol


def color_ratio_in_region(
    frame: Frame,
    region: Region,
    lower_bgr: tuple[int, int, int],
    upper_bgr: tuple[int, int, int],
) -> float:
    """Fraction of pixels in ``region`` whose BGR falls within [lower, upper].

    Useful for color-blob confirmation (e.g. "is there a green button here?")
    independent of exact shape.
    """
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = _clamp_region(region, width, height)
    sub = frame[y1:y2, x1:x2]
    if sub.size == 0:
        return 0.0
    mask = cv2.inRange(
        sub,
        np.array(lower_bgr, dtype=np.uint8),
        np.array(upper_bgr, dtype=np.uint8),
    )
    return float(np.count_nonzero(mask)) / float(mask.size)
