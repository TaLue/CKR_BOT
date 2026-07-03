"""Macro replay (Phase 5.5) — MacroPlayer drives a DOWN/UP input stream through
minitouch on a drift-free schedule, synced to the GAMEPLAY anchor (t=0).

INVARIANTS:
  * Anchor sync (CLAUDE.md #4): t=0 is when the GAMEPLAY anchor is first detected,
    cancelling out level loading time — no fixed startup delay.
  * Coordinates are pixels sent as-is (identity, spec §3.1).

The scheduler computes each event's target time as a CUMULATIVE offset from t0
(target_i = t0 + sum(dt[0..i])) and re-derives the remaining wait from the clock
each iteration, so per-step oversleep/jitter does NOT accumulate into drift. This
matters for held inputs (S/Slide): DOWN and its UP are scheduled independently, so
the hold duration is reproduced accurately.

If stopped/paused mid-hold, the open contact is released (up) first so no finger
is left pressed on screen.

``clock`` and ``sleep`` are injectable so the scheduler can be unit-tested with a
fake clock (no real wall-clock waiting).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from loguru import logger

from ckrbot.capture.screen import ScreenCapture
from ckrbot.input.minitouch import MinitouchClient
from ckrbot.macro.model import Macro
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import find_template


class MacroPlayer:
    """Replays a recorded Macro with anchor sync and a drift-free scheduler."""

    def __init__(
        self,
        mt: MinitouchClient,
        capture: ScreenCapture,
        templates: TemplateStore,
        *,
        anchor_template: str,
        threshold: float,
        poll_interval_ms: int,
        anchor_poll_ms: int = 20,
        end_templates: tuple[str, ...] = (),
        boost_templates: tuple[str, ...] = (),
        boost_threshold: float = 0.7,
        end_poll_ms: int = 200,
        start_delay_ms: int = 0,
        anchor_timeout_s: float = 30.0,
        busy_wait_ms: float = 1.5,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._mt = mt
        self._capture = capture
        self._templates = templates
        self._anchor_template = anchor_template
        self._threshold = threshold
        self._poll_s = poll_interval_ms / 1000.0
        # Poll fast while waiting for the anchor so t=0 lines up with the recorder's
        # (both use the same small interval) — see MacroRecorder.
        self._anchor_poll_s = anchor_poll_ms / 1000.0
        # Shift the whole macro relative to the anchor (compensates a small record
        # vs replay anchor-timing offset). Positive = macro starts later.
        self._start_delay_s = start_delay_ms / 1000.0
        # If set, a watcher thread aborts replay as soon as ANY of these templates
        # appears — the real round can end before the macro does (goal reached, or
        # death → CAPTCHA → Result). Replaying further would tap the wrong screen.
        self._end_templates = end_templates
        # In-gameplay icons to tap during replay (e.g. "Cookie Relay Boost"). Tapped
        # on a SECOND contact so the macro's own touches are undisturbed.
        self._boost_templates = boost_templates
        self._boost_threshold = boost_threshold
        self._end_poll_s = end_poll_ms / 1000.0
        self._anchor_timeout_s = anchor_timeout_s
        self._busy_s = busy_wait_ms / 1000.0
        self._chunk_s = 0.05  # max single sleep, so stop_evt is honored promptly
        self._clock = clock
        self._sleep = sleep

    def play(self, macro: Macro, stop_evt, pause_evt) -> bool:
        """Wait for the anchor, then replay the event stream. True if completed.

        TODO(Phase 6): the GAMEPLAY anchor relies on tpl_pause alone, which can
        false-positive on the Continue/Quit overlay. Harden with a gameplay-only
        marker when the state machine is built.
        """
        if not self._wait_for_anchor(stop_evt):
            return False
        return self._replay(macro.events, stop_evt, pause_evt)

    def _wait_for_anchor(self, stop_evt) -> bool:
        tpl = self._templates.load(self._anchor_template)
        deadline = self._clock() + self._anchor_timeout_s
        while not stop_evt.is_set():
            frame = self._capture.grab()
            if find_template(frame, tpl.image, tpl.region).confidence >= self._threshold:
                logger.info("GAMEPLAY anchor detected — macro t=0")
                return True
            if self._clock() >= deadline:
                logger.warning(
                    "anchor not detected within {:.0f}s — aborting replay",
                    self._anchor_timeout_s,
                )
                return False
            self._sleep(self._anchor_poll_s)
        return False

    def _replay(self, events, stop_evt, pause_evt, end_evt=None) -> bool:
        # end_evt fires when the round has ended early (Result screen). A watcher
        # thread sets it by polling for _end_templates; tests may pass their own.
        watch_stop = threading.Event()
        if end_evt is None:
            end_evt = threading.Event()
            if (self._end_templates or self._boost_templates) and self._capture is not None:
                threading.Thread(
                    target=self._watch_end, args=(end_evt, stop_evt, watch_stop), daemon=True
                ).start()

        t0 = self._clock() + self._start_delay_s  # shift whole macro by the start offset
        elapsed = 0.0  # cumulative dt from the macro (seconds)
        contact_open = False

        def release() -> None:
            nonlocal contact_open
            if contact_open:
                self._mt.up()
                contact_open = False

        try:
            for ev in events:
                elapsed += ev.dt_ms / 1000.0

                # Pause shifts the whole schedule forward so events stay spaced.
                paused = self._await_unpaused(pause_evt, stop_evt, release)
                if stop_evt.is_set():
                    release()
                    return False
                if end_evt.is_set():
                    release()
                    logger.info("round ended (Result screen) — stopping replay early")
                    return True
                t0 += paused

                if not self._wait_until(t0 + elapsed, stop_evt, end_evt):
                    release()  # interrupted mid-schedule — don't leave a finger down
                    if end_evt.is_set() and not stop_evt.is_set():
                        logger.info("round ended (Result screen) — stopping replay early")
                        return True
                    return False
                if ev.action == "DOWN":
                    self._mt.down(ev.x, ev.y)
                    contact_open = True
                else:
                    self._mt.up()
                    contact_open = False
            release()  # safety: never leave a contact pressed
            logger.info("macro replay complete: {} events", len(events))
            return True
        finally:
            watch_stop.set()

    def _watch_end(self, end_evt, stop_evt, watch_stop) -> None:
        """Watcher thread (live only): end the round on Result/CAPTCHA, and tap any
        in-gameplay boost icon that appears — without disturbing the replay schedule.

        Boost taps go on contact 1 (a second finger), so the macro's contact-0
        touches keep running. Uses real time (not the injected clock).
        """
        end_tpls = [self._templates.load(name) for name in self._end_templates]
        boost_tpls = [self._templates.load(name) for name in self._boost_templates]
        while not (watch_stop.is_set() or stop_evt.is_set() or end_evt.is_set()):
            try:
                frame = self._capture.grab()
                for tpl in end_tpls:
                    if find_template(frame, tpl.image, tpl.region).confidence >= self._threshold:
                        logger.info("replay watcher: {} detected → end round", tpl.name)
                        end_evt.set()
                        return
                # Tap the boost EVERY poll it is visible: a successful tap makes the
                # icon vanish (self-limiting); if a tap didn't register we retry.
                for tpl in boost_tpls:
                    result = find_template(frame, tpl.image, tpl.region)
                    if result.confidence >= self._boost_threshold:
                        logger.info("boost icon '{}' → tap {}", tpl.name, result.center)
                        self._mt.tap_raw(result.center[0], result.center[1], contact=1)
                        break
            except Exception as err:  # noqa: BLE001 - watcher must not crash replay
                logger.debug("replay watcher grab failed: {}", err)
            watch_stop.wait(self._end_poll_s)

    def _await_unpaused(self, pause_evt, stop_evt, release) -> float:
        """Block while paused; return seconds spent paused (0 if not paused).

        Releases any open contact before pausing so no finger is held while paused.
        """
        if not pause_evt.is_set():
            return 0.0
        release()
        start = self._clock()
        logger.info("replay paused")
        while pause_evt.is_set() and not stop_evt.is_set():
            self._sleep(self._poll_s)
        logger.info("replay resumed")
        return self._clock() - start

    def _wait_until(self, target: float, stop_evt, end_evt=None) -> bool:
        """Sleep (in chunks) until near target, then busy-wait the tail.

        Returns False if interrupted by stop_evt or end_evt (caller distinguishes).
        Because ``target`` is absolute and remaining is recomputed from the clock
        each loop, oversleep does not accumulate.
        """
        def interrupted() -> bool:
            return stop_evt.is_set() or (end_evt is not None and end_evt.is_set())

        while True:
            if interrupted():
                return False
            remaining = target - self._clock()
            if remaining <= self._busy_s:
                break
            self._sleep(min(remaining - self._busy_s, self._chunk_s))
        # Busy-wait the final <~2ms for precision.
        while self._clock() < target:
            if interrupted():
                return False
            self._sleep(0)
        return True
