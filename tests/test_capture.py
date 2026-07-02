"""Phase 1: ScreenCapture decode + pixel-identity validation (mocked ADB)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from ckrbot.capture.screen import ScreenCapture


class _FakeAdb:
    """Stand-in for AdbClient that returns a preset PIL image from screenshot()."""

    def __init__(self, img: Image.Image) -> None:
        self._img = img

    def screenshot(self) -> Image.Image:
        return self._img


def _solid_rgb(width: int, height: int, rgb: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (width, height), rgb)


def test_grab_returns_bgr_frame_of_expected_shape() -> None:
    img = _solid_rgb(1280, 720, (10, 20, 30))  # RGB
    cap = ScreenCapture(_FakeAdb(img), 1280, 720)
    frame = cap.grab()
    assert frame.shape == (720, 1280, 3)
    assert frame.dtype == np.uint8
    # RGB (10,20,30) -> BGR (30,20,10)
    assert tuple(int(c) for c in frame[0, 0]) == (30, 20, 10)


def test_grab_drops_alpha_channel() -> None:
    img = Image.new("RGBA", (1280, 720), (1, 2, 3, 255))
    cap = ScreenCapture(_FakeAdb(img), 1280, 720)
    frame = cap.grab()
    assert frame.shape == (720, 1280, 3)


def test_grab_rejects_wrong_resolution() -> None:
    img = _solid_rgb(800, 600, (0, 0, 0))
    cap = ScreenCapture(_FakeAdb(img), 1280, 720)
    with pytest.raises(ValueError, match="identity"):
        cap.grab()
