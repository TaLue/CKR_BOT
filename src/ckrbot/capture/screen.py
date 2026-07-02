"""Screen capture (Phase 1) — ADB framebuffer -> numpy BGR frame.

INVARIANT (spec §3.1 / CLAUDE.md #1): the capture is pixel-identity with the
touch space. We therefore NEVER resize a captured frame — a wrong-sized capture
means the device/emulator is misconfigured and is a hard error, because any
rescale would silently break every macro tap and menu tap coordinate.
"""

from __future__ import annotations

import cv2
import numpy as np

from ckrbot.adb.client import AdbClient

# A frame is an (H, W, 3) BGR uint8 array (OpenCV convention). Vision functions
# (Phase 2) consume this type and must stay pure.
Frame = np.ndarray


class ScreenCapture:
    """Grabs frames from the device at the locked resolution."""

    def __init__(self, adb: AdbClient, width: int, height: int) -> None:
        self._adb = adb
        self._width = width
        self._height = height

    def grab(self, force: bool = False) -> Frame:
        """Capture a fresh frame as BGR uint8.

        Args:
            force: accepted for API symmetry with callers that want to signal a
                guaranteed-fresh read; capture is always fresh (no cache), so it
                is a no-op today.

        Raises:
            ValueError: if the capture is not exactly the configured resolution
                (resizing is forbidden — see module docstring).
        """
        img = self._adb.screenshot()  # PIL image, RGB(A)
        arr = np.asarray(img)

        if arr.ndim == 2:  # grayscale — unexpected, promote to 3-channel
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
        if arr.shape[2] == 4:  # drop alpha
            arr = arr[:, :, :3]

        height, width = arr.shape[:2]
        if (width, height) != (self._width, self._height):
            raise ValueError(
                f"Capture is {width}x{height} but config expects "
                f"{self._width}x{self._height}. Lock the emulator resolution — "
                "rescaling would break coordinate identity (spec §3.1)."
            )

        # PIL gives RGB; OpenCV/template matching works in BGR.
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
