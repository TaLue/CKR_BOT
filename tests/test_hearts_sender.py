"""HeartSender loop: sends via both dialogs on real fixtures, then scrolls and
stops when the list no longer moves."""
from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from ckrbot.config.models import HeartsConfig
from ckrbot.hearts.sender import HeartSender, region_mad
from ckrbot.vision.template import TemplateStore

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "screens"
_store = TemplateStore(str(_ROOT / "game" / "assets"))


def _frame(name: str):
    return cv2.imread(str(_FIX / name), cv2.IMREAD_COLOR)


class _FakeCapture:
    def __init__(self, frames) -> None:
        self._frames = list(frames)
        self.i = 0

    def grab(self, force: bool = False):
        f = self._frames[min(self.i, len(self._frames) - 1)]
        self.i += 1
        return f


class _FakeController:
    def __init__(self) -> None:
        self.actions = []

    def tap_point(self, x: int, y: int, settle_ms=None) -> None:
        self.actions.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration_ms=300) -> None:
        self.actions.append(("swipe",))


def _in(region, x, y) -> bool:
    x1, y1, x2, y2 = region
    return x1 <= x <= x2 and y1 <= y <= y2


def test_region_mad_zero_for_identical_and_positive_for_shifted() -> None:
    a = _frame("friend_list.png")[265:625, 150:720]
    assert region_mad(a, a.copy()) == 0.0
    shifted = np.zeros_like(a)
    shifted[8:, 8:] = a[:-8, :-8]
    assert region_mad(a, shifted) > 1.0


def test_sends_one_heart_then_scrolls_to_bottom() -> None:
    black = np.zeros((720, 1280, 3), dtype=np.uint8)
    # grabs: list -> dialog1 -> dialog2 -> black(before) -> black(after)
    frames = [_frame("friend_list.png"), _frame("click_sent.png"),
              _frame("confirm_sent.png"), black, black]
    cap = _FakeCapture(frames)
    ctrl = _FakeController()
    cfg = HeartsConfig()
    sender = HeartSender(cap, ctrl, _store, cfg, sleep=lambda s: None)

    sent = sender.run(threading.Event())

    assert sent == 1
    taps = [a for a in ctrl.actions if a[0] == "tap"]
    assert len(taps) == 3
    assert _in(cfg.send_region, taps[0][1], taps[0][2])          # tapped a send button
    assert _in(cfg.ask_confirm_region, taps[1][1], taps[1][2])   # confirmed "free Life?"
    assert _in(cfg.sent_confirm_region, taps[2][1], taps[2][2])  # acked "Message sent!"
    assert ("swipe",) in ctrl.actions                            # scrolled when none left
    assert ctrl.actions[-1] == ("swipe",)                        # stopped right after the bottom scroll
