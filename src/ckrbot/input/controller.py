"""Menu tap controller (Phase 6) — tap by locating a template on a frame.

Menu navigation taps a matched button's center via minitouch (pixel identity —
no scaling). A small humanized delay/jitter follows each tap. Distinct from macro
replay, which drives its own DOWN/UP schedule.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from loguru import logger

from ckrbot.capture.screen import Frame
from ckrbot.input.minitouch import MinitouchClient
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import find_template


class Controller:
    """Locates buttons on a frame and taps them through minitouch."""

    def __init__(
        self,
        mt: MinitouchClient,
        templates: TemplateStore,
        *,
        threshold: float,
        tap_delay_ms: int,
        tap_delay_spread_ms: int,
        region_pad: int = 10,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._mt = mt
        self._templates = templates
        self._threshold = threshold
        self._tap_delay_ms = tap_delay_ms
        self._spread_ms = tap_delay_spread_ms
        self._pad = region_pad
        self._sleep = sleep

    def _settle(self) -> None:
        jitter = random.uniform(0, self._spread_ms) if self._spread_ms else 0.0
        self._sleep((self._tap_delay_ms + jitter) / 1000.0)

    def tap_point(self, x: int, y: int, settle_ms: int | None = None) -> None:
        """Tap a pixel coordinate directly.

        ``settle_ms`` overrides the post-tap delay (e.g. captcha cards want fast
        taps, not the humanized menu delay). None uses the configured delay.
        """
        self._mt.tap_raw(x, y)
        if settle_ms is None:
            self._settle()
        else:
            self._sleep(settle_ms / 1000.0)

    def tap_template(self, frame: Frame, template_name: str) -> bool:
        """Tap the center of ``template_name`` if it matches on ``frame``.

        Returns False (without tapping) if the button isn't found — the caller
        can re-loop rather than tap blindly.
        """
        tpl = self._templates.load(template_name)
        h, w = frame.shape[:2]
        region = tpl.region
        if region is not None:
            x1, y1, x2, y2 = region
            region = (max(0, x1 - self._pad), max(0, y1 - self._pad),
                      min(w, x2 + self._pad), min(h, y2 + self._pad))
        result = find_template(frame, tpl.image, region)
        if result.confidence < self._threshold:
            # Not found is often transient (screen mid-transition); the caller
            # re-loops, so log at DEBUG rather than spamming warnings.
            logger.debug("tap_template: {} not found ({:.3f})", template_name, result.confidence)
            return False
        self._mt.tap_raw(*result.center)
        logger.debug("tapped {} @ {}", template_name, result.center)
        self._settle()
        return True
