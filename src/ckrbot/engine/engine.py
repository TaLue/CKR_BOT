"""Engine (Phase 6) — the farm loop.

Each iteration: capture → identify (priority) → dispatch the state's action. No
transitions are hardcoded (INVARIANT #3): branches like END_ROUND → box/levelup/
menu resolve from whatever screen actually appears next.

Round lifecycle: a round completes when the bot returns to a CLEAN MAIN_MENU
(no reward popup) after a replay. MENU_REWARD popups are collected first.

Special states: MONEY_POPUP → Cancel + STOP; CAPTCHA → solve (odd-one-out) with
retries; START_3 → tap Play then hand off to MacroPlayer; UNKNOWN → watchdog.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from pathlib import Path

import cv2
from loguru import logger

from ckrbot.config.models import AppConfig
from ckrbot.engine.watchdog import Watchdog
from ckrbot.game.captcha import card_scores, read_tries, solve_captcha
from ckrbot.game.states import TAP_PLAN, State

# Templates whose match gives the tap point for special-cased states.
_CANCEL = "tpl_cancel"
_PLAY_START = "tpl_play_start"
_PLAY_MAIN = "tpl_play_main"
_CAPTCHA_TAP_SETTLE_MS = 80  # captcha cards tapped fast (no humanized menu delay)


class Engine:
    """Runs the state-machine farm loop on a worker thread."""

    def __init__(
        self,
        *,
        capture,
        identifier,
        controller,
        macro_player,
        macros,
        config: AppConfig,
        templates=None,
        rng: random.Random | None = None,
        back_fn: Callable[[], None] | None = None,
        debug_dir: str | Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capture = capture
        self._identifier = identifier
        self._controller = controller
        self._macro_player = macro_player
        # Pool of macros; a random one is played each round so the game does not
        # see the exact same run repeated (spec §0 layout must stay fixed per macro).
        self._macros = list(macros)
        self._rng = rng or random.Random()
        self._cfg = config
        self._back_fn = back_fn
        self._debug_dir = Path(debug_dir) if debug_dir else None
        self._sleep = sleep
        self._wd = self._new_watchdog()
        self.round_count = 0
        self._captcha_dump_i = 0
        self._unknown_dump_i = 0
        # "Tries left" digit templates (3/2/1) for reading captcha progress.
        self._tries_templates = (
            {n: templates.load(f"tpl_tries_{n}").image for n in (3, 2, 1)}
            if templates is not None else {}
        )

    def _new_watchdog(self) -> Watchdog:
        return Watchdog(self._cfg.watchdog.unknown_limit, self._cfg.watchdog.max_recovery_attempts)

    def _pick_macro(self):
        """Randomly choose a macro from the active pool (1 macro -> always it)."""
        if len(self._macros) == 1:
            return self._macros[0]
        return self._rng.choice(self._macros)

    def reset(self) -> None:
        """Reset the round counter and watchdog (Control Panel Reset button)."""
        self.round_count = 0
        self._wd = self._new_watchdog()
        logger.info("engine reset: round_count=0")

    def run(self, stop_evt, pause_evt) -> None:
        """Main loop; runs until stop_evt is set (or a stop condition triggers)."""
        poll = self._cfg.timing.poll_interval_ms / 1000.0
        settle = self._cfg.timing.settle_ms / 1000.0
        randomize = self._cfg.farm.randomize_double_coins
        in_round = False
        # After Multi-Buy is tapped, the game auto-rolls: the screen reverts to a
        # START_1-looking panel (no Multi-Buy button, no Double Coins yet) until it
        # lands on START_3. While waiting we must NOT tap START_1 or START_2.
        awaiting_double_coins = False
        logger.info("engine started")

        while not stop_evt.is_set():
            if pause_evt.is_set():
                stop_evt.wait(poll)
                continue

            frame = self._capture.grab()
            state = self._identifier.identify(frame)
            if state != State.UNKNOWN:
                self._wd.on_known()
            # Once we leave the START_1/START_2 phase (e.g. START_3, MAIN_MENU,
            # GAMEPLAY, END_*), the multi-buy episode is over — allow fresh taps.
            if state not in (State.START_1, State.START_2):
                awaiting_double_coins = False

            if state == State.MONEY_POPUP:
                # Not enough coins for an (optional) buy — dismiss and KEEP FARMING
                # instead of stopping. Cancel never spends anything, so worst case is
                # a harmless retry; the flow returns to START and taps Play as usual.
                self._controller.tap_template(frame, _CANCEL)
                logger.info("MONEY_POPUP (เงินไม่พอ) → Cancel + skip (keep farming)")
                stop_evt.wait(poll)
                continue

            if state == State.CAPTCHA:
                self._handle_captcha(stop_evt)
                continue

            if state == State.TITLE:
                # Game relaunched (e.g. a dropped connection re-downloads + shows the
                # title). Tap to pass "touch to start" and wait for a known screen —
                # being identified, this does NOT trip the UNKNOWN watchdog. The
                # interrupted round is void, so clear in_round.
                self._controller.tap_point(640, 650)
                in_round = False
                stop_evt.wait(poll)
                continue

            if not randomize and state in (State.START_1, State.START_2, State.START_3):
                # Double Coins randomization OFF: skip Multi-Buy — just Play the level
                # (Play button is on START_1/START_3; START_2 won't occur without Multi).
                t0 = self._controller.tap_template_at(frame, _PLAY_START)  # tap instant = macro t=0
                if t0 is not None:
                    macro = self._pick_macro()
                    logger.info("START (no Double Coins) → Play → replay '{}'",
                                getattr(macro, "name", "?"))
                    self._macro_player.play(macro, stop_evt, pause_evt, t0=t0)
                    in_round = True
                stop_evt.wait(poll)
                continue

            if state == State.START_1:
                # Fresh START_1: select the pink box, then tap Multi to open
                # Multi-Buy. The Multi icon may not be present/settled the instant
                # START_1 is identified, so wait and RE-CAPTURE a fresh frame before
                # looking for it (searching the stale pre-tap frame misses it).
                # If we're already auto-rolling (awaiting Double Coins), the
                # START_1-looking screen is transient — WAIT, don't re-tap.
                if not awaiting_double_coins:
                    box_tpl, multi_tpl = TAP_PLAN[State.START_1]
                    self._controller.tap_template(frame, box_tpl)
                    self._sleep(self._cfg.timing.tap_delay_ms / 1000.0)  # let it settle
                    self._controller.tap_template(self._capture.grab(True), multi_tpl)
                stop_evt.wait(poll)
                continue

            if state == State.START_2:
                # Tap Multi-Buy ONCE, then WAIT: the game auto-rolls random buys
                # itself until Double Coins (START_3). Re-tapping (here or via the
                # transient START_1) would queue extra buys / spend money (spec §5).
                if not awaiting_double_coins:
                    self._controller.tap_template(frame, TAP_PLAN[State.START_2][0])
                    awaiting_double_coins = True
                    logger.info("START_2 → Multi-Buy tapped; waiting for auto-roll → START_3")
                stop_evt.wait(poll)
                continue

            if state == State.START_3:
                t0 = self._controller.tap_template_at(frame, _PLAY_START)  # tap instant = macro t=0
                if t0 is not None:
                    macro = self._pick_macro()
                    logger.info("START_3 → Play → macro replay '{}'",
                                getattr(macro, "name", "?"))
                    self._macro_player.play(macro, stop_evt, pause_evt, t0=t0)
                    in_round = True
                continue

            if state == State.MAIN_MENU:
                if in_round:
                    self._sleep(settle)  # let a reward popup appear
                    if self._identifier.identify(self._capture.grab(True)) == State.MENU_REWARD:
                        continue  # collect reward(s) first
                    self.round_count += 1
                    in_round = False
                    logger.info("round complete {}/{}", self.round_count,
                                self._cfg.farm.max_rounds or "∞")
                    if self._cfg.farm.max_rounds and self.round_count >= self._cfg.farm.max_rounds:
                        logger.info("reached max rounds → STOP")
                        stop_evt.set()
                        break
                    continue
                self._controller.tap_template(frame, _PLAY_MAIN)  # start a new round
                stop_evt.wait(poll)
                continue

            if state == State.GAMEPLAY:
                # Standalone gameplay (not entered via START_3 this loop) — the
                # MacroPlayer owns replay; just wait for the round to progress.
                stop_evt.wait(poll)
                continue

            if state in TAP_PLAN:  # START_1, START_2, END_*, LEVEL_UP, MENU_REWARD
                for template in TAP_PLAN[state]:
                    self._controller.tap_template(frame, template)
                stop_evt.wait(poll)
                continue

            # UNKNOWN → watchdog
            action = self._wd.on_unknown()
            if action == "recover":
                logger.warning("UNKNOWN screen → recovery (BACK)")
                self._dump_frame(frame, "unknown")  # save what we couldn't identify
                if self._back_fn is not None:
                    self._back_fn()
            elif action == "giveup":
                logger.error("UNKNOWN persists past recovery limit → STOP")
                self._dump_frame(frame, "unknown")
                stop_evt.set()
                break
            stop_evt.wait(poll)

        logger.info("engine stopped (rounds={})", self.round_count)

    def _handle_captcha(self, stop_evt, max_wrong=None, max_rounds=None,
                        round_timeout_ms=None) -> None:
        """Solve the CAPTCHA — 3 correct rounds IN A ROW (Tries left 3/3 → 2/3 →
        1/3 → cleared). Each round shows 6 ANIMATED cards; tap the 2 that differ,
        sampling frames and majority-voting to beat the animation.

        We READ "Tries left" to classify each round after tapping:
          * Tries DECREASED  -> CORRECT (advance).
          * Tries INCREASED   -> WRONG guess (streak reset); counts toward max_wrong.
          * No change (timeout) -> the tap didn't register (round just changed, cards
            still animating in). NOT a wrong guess — just retry (re-read + re-solve).
        Stops the bot after ``max_wrong`` real wrong guesses, or ``max_rounds`` total
        attempts (safety against looping). Counters are per encounter.
        """
        max_wrong = self._cfg.captcha.max_wrong if max_wrong is None else max_wrong
        max_rounds = self._cfg.captcha.max_rounds if max_rounds is None else max_rounds
        timeout_s = (self._cfg.captcha.round_timeout_ms if round_timeout_ms is None
                     else round_timeout_ms) / 1000.0
        poll = self._cfg.timing.poll_interval_ms / 1000.0

        wrong = 0
        rounds = 0
        while not stop_evt.is_set():
            frame = self._capture.grab(True)
            tries = read_tries(frame, self._tries_templates)
            if tries is None:  # no Tries digit -> cleared, or a transient frame
                if self._identifier.identify(frame) != State.CAPTCHA:
                    logger.info("CAPTCHA cleared ({} wrong round(s))", wrong)
                    return
                stop_evt.wait(poll)
                continue

            rounds += 1
            if rounds > max_rounds:
                logger.error("CAPTCHA not solved within {} total rounds → STOP bot", max_rounds)
                stop_evt.set()
                return

            # One frame captures all 6 cards in the SAME instant, so the 4-alike vs
            # 2-alike split is clean (no cross-phase animation noise from voting).
            points = solve_captcha(frame)
            # Log all 6 card scores (lowest 2 = the picks) so a wrong pick is
            # diagnosable from the log alone; the annotated frame is dumped too.
            scores = [round(float(s), 3) for s in card_scores(frame)]
            logger.info("CAPTCHA tries {}/3: tap {} | card scores {}", tries, points, scores)
            self._dump_captcha(frame, points, tries)
            gap_s = self._cfg.captcha.tap_gap_ms / 1000.0
            for i, (x, y) in enumerate(points):
                self._controller.tap_point(x, y, settle_ms=_CAPTCHA_TAP_SETTLE_MS)
                if i == 0 and gap_s > 0:  # let the game register the 1st pick before the 2nd
                    self._sleep(gap_s)

            outcome = self._await_tries_change(tries, timeout_s, stop_evt)
            if outcome == "cleared":
                logger.info("CAPTCHA cleared ({} wrong round(s))", wrong)
                return
            if outcome == "stop":
                return
            if outcome == "correct":
                continue  # progressed to the next round
            if outcome == "wrong":  # Tries reset upward -> a real wrong guess
                wrong += 1
                logger.warning("CAPTCHA wrong round {}/{}", wrong, max_wrong)
                if wrong >= max_wrong:
                    logger.error("CAPTCHA {} wrong rounds → STOP bot", wrong)
                    stop_evt.set()
                    return
            # outcome == "timeout": tap likely didn't register — retry (not counted)

    def _await_tries_change(self, prev_tries: int, timeout_s: float, stop_evt) -> str:
        """Poll until Tries left changes / captcha clears / timeout.

        Returns 'correct' (tries decreased), 'wrong' (increased/reset), 'cleared'
        (captcha gone), 'timeout' (unchanged), or 'stop'.
        """
        poll = self._cfg.captcha.poll_ms / 1000.0
        max_iters = round(timeout_s / poll) if poll > 0 else 50
        for _ in range(max(1, max_iters)):
            if stop_evt.is_set():
                return "stop"
            frame = self._capture.grab(True)
            tries = read_tries(frame, self._tries_templates)
            if tries is None:
                if self._identifier.identify(frame) != State.CAPTCHA:
                    return "cleared"
            elif tries < prev_tries:
                return "correct"
            elif tries > prev_tries:
                return "wrong"
            self._sleep(poll)
        return "timeout"

    def _dump_frame(self, frame, tag: str) -> None:
        """Save a raw frame to the debug dir (e.g. an unidentified UNKNOWN screen)."""
        if self._debug_dir is None:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            self._unknown_dump_i += 1
            path = self._debug_dir / f"{tag}_{self._unknown_dump_i:03d}.png"
            cv2.imwrite(str(path), frame)
            logger.info("saved {} frame -> {}", tag, path)
        except Exception as err:  # noqa: BLE001 - debug dump must never break the loop
            logger.warning("debug frame dump failed: {}", err)

    def _dump_captcha(self, frame, points, attempt: int) -> None:
        """Save an annotated captcha frame (marks the 2 tapped cards) for review."""
        if self._debug_dir is None:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            annotated = frame.copy()
            for (x, y) in points:
                cv2.circle(annotated, (x, y), 40, (0, 0, 255), 4)
            self._captcha_dump_i += 1
            path = self._debug_dir / f"captcha_{self._captcha_dump_i:03d}_try{attempt}.png"
            cv2.imwrite(str(path), annotated)
            logger.info("saved captcha debug frame -> {}", path)
        except Exception as err:  # noqa: BLE001 - debug dump must never break the loop
            logger.warning("captcha debug dump failed: {}", err)
