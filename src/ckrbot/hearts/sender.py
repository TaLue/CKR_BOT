"""Send-Hearts mode — send the daily free Life to friends in the Friends list.

Assumes the Friends list is already on screen (the mode does not navigate menus).
A small vision loop: finish any open confirm dialog, else tap an un-sent friend's
green heart-letter button, else scroll; stop when a scroll leaves the list
unchanged (bottom reached). Pure helpers are unit-testable from fixtures.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from ckrbot.capture.screen import Frame
from ckrbot.config.models import HeartsConfig
from ckrbot.vision.vision import Region, find_template


def region_mad(a: Frame, b: Frame) -> float:
    """Mean absolute per-pixel difference of two crops (255.0 if shapes differ)."""
    if a.shape != b.shape:
        return 255.0
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _crop(frame: Frame, region: Region) -> Frame:
    x1, y1, x2, y2 = region
    return frame[y1:y2, x1:x2]


class HeartSender:
    """Sends a free Life to every un-sent friend in the list, scrolling as needed."""

    def __init__(self, capture, controller, templates, cfg: HeartsConfig,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._capture = capture
        self._controller = controller
        self._cfg = cfg
        self._sleep = sleep
        self._send_img = templates.load("tpl_send_life").image
        self._ask_img = templates.load("tpl_life_confirm").image
        self._sent_img = templates.load("tpl_sent_confirm").image

    def run(self, stop_evt) -> int:
        """Loop until the bottom of the list (or stop_evt). Returns hearts sent."""
        cfg = self._cfg
        thr = cfg.threshold
        sent = 0
        scrolls = 0
        logger.info("Send Hearts: started")
        while not stop_evt.is_set():
            frame = self._capture.grab()

            # 1. Finish an in-progress send before starting a new one.
            r = find_template(frame, self._sent_img, tuple(cfg.sent_confirm_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                sent += 1
                logger.info("Send Hearts: sent #{}", sent)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue
            r = find_template(frame, self._ask_img, tuple(cfg.ask_confirm_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue

            # 2. Send to any un-sent friend currently in view (order irrelevant).
            r = find_template(frame, self._send_img, tuple(cfg.send_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue

            # 3. Nothing to send in view — scroll the list down.
            if scrolls >= cfg.max_scrolls:
                logger.warning("Send Hearts: hit max_scrolls={} — stopping", cfg.max_scrolls)
                break
            before = _crop(frame, tuple(cfg.list_region)).copy()
            self._controller.swipe(cfg.swipe_from[0], cfg.swipe_from[1],
                                   cfg.swipe_to[0], cfg.swipe_to[1], cfg.swipe_ms)
            scrolls += 1
            self._sleep(cfg.scroll_settle_ms / 1000.0)
            after = _crop(self._capture.grab(), tuple(cfg.list_region))
            if region_mad(before, after) < cfg.unchanged_mad:
                logger.info("Send Hearts: reached the bottom of the list")
                break

        logger.info("Send Hearts: done — sent {} hearts ({} scrolls)", sent, scrolls)
        return sent
