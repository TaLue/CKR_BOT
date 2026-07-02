"""Phase 2: color primitive unit tests on synthetic frames."""

from __future__ import annotations

import numpy as np

from ckrbot.vision.vision import color_matches, color_ratio_in_region, find_template


def _bgr_frame(width: int, height: int, bgr: tuple[int, int, int]) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = bgr
    return frame


def test_color_matches_within_and_outside_tolerance() -> None:
    frame = _bgr_frame(10, 10, (100, 150, 200))
    assert color_matches(frame, 5, 5, (105, 145, 200), tol=10) is True
    assert color_matches(frame, 5, 5, (100, 150, 180), tol=10) is False  # blue off by 20


def test_color_ratio_counts_pixels_in_range() -> None:
    frame = _bgr_frame(10, 10, (0, 0, 0))
    frame[0:5, :] = (0, 200, 0)  # top half green
    ratio = color_ratio_in_region(frame, (0, 0, 10, 10), (0, 150, 0), (50, 255, 50))
    assert abs(ratio - 0.5) < 1e-6


def _textured_patch(h: int, w: int) -> np.ndarray:
    """A patch with per-channel variance so TM_CCOEFF_NORMED is well-defined."""
    yy, xx = np.mgrid[0:h, 0:w]
    patch = np.zeros((h, w, 3), dtype=np.uint8)
    patch[:, :, 0] = (xx * 12) % 256  # B varies with x
    patch[:, :, 1] = (yy * 12) % 256  # G varies with y
    patch[:, :, 2] = ((xx + yy) * 7) % 256  # R varies diagonally
    return patch


def test_find_template_locates_patch_and_reports_center() -> None:
    frame = _bgr_frame(100, 100, (0, 0, 0))
    patch = _textured_patch(20, 20)
    frame[40:60, 30:50] = patch  # unique textured patch at (30,40)
    result = find_template(frame, patch)
    assert result.confidence > 0.99
    assert result.bbox == (30, 40, 50, 60)
    assert result.center == (40, 50)


def test_find_template_region_smaller_than_template_returns_zero() -> None:
    frame = _bgr_frame(100, 100, (10, 20, 30))
    template = _bgr_frame(40, 40, (10, 20, 30))
    result = find_template(frame, template, region=(0, 0, 20, 20))
    assert result.confidence == 0.0
