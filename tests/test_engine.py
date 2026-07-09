"""Phase 6 integration (spec §13): drive the engine with a scripted fixture
sequence (mock capture), assert dispatched actions + round lifecycle + stops.

Uses the REAL ScreenIdentifier + fixtures (so identify is exercised) and the REAL
captcha solver; the controller and macro player are faked to record actions."""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from ckrbot.config.models import AppConfig
from ckrbot.engine.engine import Engine
from ckrbot.engine.screen import ScreenIdentifier
from ckrbot.game.states import CKR_SCREENS, State
from ckrbot.vision.template import TemplateStore

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures" / "screens"
_ASSETS = _ROOT / "game" / "assets"


def _load(name: str):
    img = cv2.imread(str(_FIXTURES / name), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


class FakeCapture:
    """Returns scripted frames; sets stop_evt when the script is exhausted."""

    def __init__(self, frame_names, stop_evt) -> None:
        self._frames = [_load(n) for n in frame_names]
        self._i = 0
        self._stop = stop_evt

    def grab(self, force: bool = False):
        if self._i >= len(self._frames):
            self._stop.set()
            # Black frame identifies as UNKNOWN -> the trailing iteration is a
            # no-op (no stray tap) before the loop sees stop and exits.
            return np.zeros((720, 1280, 3), dtype=np.uint8)
        f = self._frames[self._i]
        self._i += 1
        return f


class FakeController:
    """Records tap_template(name) and tap_point(x,y); taps always 'succeed'."""

    def __init__(self) -> None:
        self.taps: list = []

    def tap_template(self, frame, template_name: str) -> bool:
        self.taps.append(template_name)
        return True

    def tap_template_at(self, frame, template_name: str) -> float | None:
        self.taps.append(template_name)
        return 0.0  # tap-anchor t=0 (fake perf_counter)

    def tap_point(self, x: int, y: int, settle_ms: int | None = None) -> None:
        self.taps.append(("point", x, y))


class FakeMacroPlayer:
    def __init__(self) -> None:
        self.played = 0
        self.macros_played: list = []
        self.t0s: list = []

    def play(self, macro, stop_evt, pause_evt, t0=None) -> bool:
        self.played += 1
        self.macros_played.append(macro)
        self.t0s.append(t0)
        return True


def _config(max_rounds: int = 0) -> AppConfig:
    cfg = AppConfig()
    # zero timings so the test doesn't actually sleep
    cfg.timing.poll_interval_ms = 0
    cfg.timing.settle_ms = 0
    cfg.timing.tap_delay_ms = 0
    cfg.timing.tap_delay_spread_ms = 0
    cfg.farm.max_rounds = max_rounds
    return cfg


def _engine(frame_names, stop_evt, *, max_rounds=0, controller=None, player=None,
            macros=None, rng=None):
    store = TemplateStore(_ASSETS)
    identifier = ScreenIdentifier(store, CKR_SCREENS, threshold=0.85)
    controller = controller or FakeController()
    player = player or FakeMacroPlayer()
    eng = Engine(
        capture=FakeCapture(frame_names, stop_evt),
        identifier=identifier,
        controller=controller,
        macro_player=player,
        macros=macros if macros is not None else [object()],
        config=_config(max_rounds),
        templates=store,
        rng=rng,
        sleep=lambda s: None,
    )
    return eng, controller, player


def test_full_round_flow_counts_one_round_and_stops() -> None:
    stop = threading.Event()
    frames = [
        "main_menu.png",        # not in_round -> tap Play (main)
        "start_step_1.png",     # tap pink_box, then re-grab (below) for multi_icon
        "start_step_1.png",     # START_1 re-capture -> tap multi_icon
        "start_step_2.png",     # tap multibuy
        "start_step_3.png",     # tap play_start + replay -> in_round
        "end_round.png",        # tap result_ok
        "end_round_box.png",    # tap open_all
        "end_round_box_open.png",  # tap box_confirm
        "main_menu.png",        # in_round: settle-grab -> main_menu (below)
        "main_menu.png",        # settle re-grab (not reward) -> round_count=1 -> stop (max=1)
    ]
    eng, ctrl, player = _engine(frames, stop, max_rounds=1)
    eng.run(stop, threading.Event())

    assert ctrl.taps == [
        "tpl_play_main",
        "tpl_pink_box", "tpl_multi_icon",
        "tpl_multibuy",
        "tpl_play_start",
        "tpl_result_ok",
        "tpl_open_all",
        "tpl_box_confirm",
    ]
    assert player.played == 1
    assert eng.round_count == 1
    assert stop.is_set()


def test_menu_reward_collected_before_counting_round() -> None:
    stop = threading.Event()
    # After replay, MAIN_MENU appears but a reward popup is up on the settle re-grab.
    frames = [
        "start_step_3.png",        # -> replay, in_round
        "main_menu.png",           # in_round -> settle re-grab:
        "main_menu_reward.png",    # reward present -> continue (NOT counted)
        "main_menu_reward.png",    # MENU_REWARD -> tap congrats_confirm
        "main_menu.png",           # in_round -> settle re-grab:
        "main_menu.png",           # clean -> round_count=1 -> stop
    ]
    eng, ctrl, player = _engine(frames, stop, max_rounds=1)
    eng.run(stop, threading.Event())

    assert "tpl_congrats_confirm" in ctrl.taps  # reward was collected
    assert eng.round_count == 1


def test_money_popup_dismissed_without_stopping() -> None:
    """Not enough Coins: tap Cancel and KEEP FARMING (no self-stop). Two popups in a
    row are both dismissed, proving the bot does not stop after the first."""
    stop = threading.Event()
    frames = ["money.png", "money.png"]
    eng, ctrl, player = _engine(frames, stop)
    eng.run(stop, threading.Event())

    assert ctrl.taps.count("tpl_cancel") == 2  # both dismissed; not stopped after the 1st
    assert eng.round_count == 0                 # no round completed, but bot kept running


def test_title_screen_taps_to_start_and_does_not_stop() -> None:
    """Game relaunch title/loading screen: tap 'touch to start' and KEEP RUNNING
    (identified, so it must not trip the UNKNOWN watchdog / stop the bot)."""
    stop = threading.Event()
    frames = ["title.png", "title.png"]
    eng, ctrl, player = _engine(frames, stop)
    eng.run(stop, threading.Event())

    taps = [t for t in ctrl.taps if isinstance(t, tuple) and t[0] == "point"]
    assert len(taps) == 2 and taps[0] == ("point", 640, 650)  # tapped to start both times
    assert eng.round_count == 0  # relaunch is not a completed round


def test_captcha_solves_three_correct_rounds_until_cleared() -> None:
    """Tries left 3/3 → 2/3 → 1/3 → cleared: 3 correct rounds. Screens persist
    across the await-detect and the next solve grab, so frames are duplicated."""
    stop = threading.Event()
    frames = [
        "capcha_1.png",  # round A: read 3/3, solve+tap
        "capcha_2.png",  # await: now 2/3 -> correct
        "capcha_2.png",  # round B: read 2/3, solve+tap
        "capcha_3.png",  # await: now 1/3 -> correct
        "capcha_3.png",  # round C: read 1/3, solve+tap
        "end_round.png",  # await: captcha gone -> cleared
    ]
    eng, ctrl, player = _engine(frames, stop)
    eng._handle_captcha(stop, max_wrong=5)

    points = [t for t in ctrl.taps if isinstance(t, tuple) and t[0] == "point"]
    assert len(points) == 3 * 2  # 3 rounds solved, 2 taps each
    assert not stop.is_set()      # cleared cleanly, bot keeps running


def test_captcha_counts_only_wrong_rounds_and_stops_after_max() -> None:
    """A wrong guess resets Tries left (2/3 -> back to 3/3). Only wrong rounds are
    counted; after max_wrong the bot stops."""
    stop = threading.Event()
    # Each round: read 2/3, tap, then await sees 3/3 (reset) -> wrong.
    frames = ["capcha_2.png", "capcha_1.png", "capcha_2.png", "capcha_1.png"]
    eng, ctrl, player = _engine(frames, stop)
    eng._handle_captcha(stop, max_wrong=2)

    points = [t for t in ctrl.taps if isinstance(t, tuple) and t[0] == "point"]
    assert len(points) == 2 * 2  # 2 wrong rounds attempted before stopping
    assert stop.is_set()          # stopped after max_wrong wrong rounds


def test_captcha_timeout_not_counted_as_wrong() -> None:
    """A round where Tries left doesn't change (tap didn't register / cards still
    animating) is a retry, NOT a wrong guess — it must not stop the bot."""
    stop = threading.Event()
    # Round 1: Tries stays 3/3 (await sees no change -> timeout). Then a non-captcha
    # frame clears it. No Tries reset ever occurs. round_timeout=100ms + poll=200ms
    # -> max 1 await check, so exactly one same-tries frame is consumed.
    frames = ["capcha_1.png", "capcha_1.png", "end_round.png"]
    eng, ctrl, player = _engine(frames, stop)
    eng._handle_captcha(stop, max_wrong=5, max_rounds=15, round_timeout_ms=100)
    assert not stop.is_set()  # timeout did not count as wrong / did not stop the bot


def test_start2_taps_multibuy_once_then_waits_for_start3() -> None:
    """START_2 must tap Multi-Buy ONCE and wait through the auto-roll, not re-tap
    every poll while the START_2 screen persists (spec §5)."""
    stop = threading.Event()
    frames = [
        "start_step_2.png",   # tap multibuy (first time)
        "start_step_2.png",   # still auto-rolling -> WAIT, no re-tap
        "start_step_2.png",   # still auto-rolling -> WAIT, no re-tap
        "start_step_3.png",   # Double Coins appeared -> tap play_start + replay
    ]
    eng, ctrl, player = _engine(frames, stop, max_rounds=1)
    eng.run(stop, threading.Event())

    assert ctrl.taps.count("tpl_multibuy") == 1  # exactly once despite 3 START_2 frames
    assert ctrl.taps == ["tpl_multibuy", "tpl_play_start"]
    assert player.played == 1


def test_randomize_off_skips_multibuy_and_plays_directly() -> None:
    """With Double Coins randomization OFF, START_1 taps Play (play_start) directly
    instead of the pink box / Multi-Buy flow."""
    stop = threading.Event()
    eng, ctrl, player = _engine(["start_step_1.png"], stop, max_rounds=1)
    eng._cfg.farm.randomize_double_coins = False
    eng.run(stop, threading.Event())
    assert "tpl_play_start" in ctrl.taps
    assert "tpl_pink_box" not in ctrl.taps and "tpl_multi_icon" not in ctrl.taps
    assert player.played == 1


def test_autoroll_start1_frames_after_multibuy_are_not_tapped() -> None:
    """The reported bug: after Multi-Buy, the game auto-rolls and the screen looks
    like START_1 (no Multi-Buy button, no Double Coins). The bot must WAIT, not
    spam pink_box/multi_icon, until Double Coins (START_3) appears."""
    stop = threading.Event()
    frames = [
        "start_step_2.png",   # tap Multi-Buy -> awaiting Double Coins
        "start_step_1.png",   # auto-roll (looks like START_1) -> WAIT
        "start_step_1.png",   # auto-roll -> WAIT
        "start_step_1.png",   # auto-roll -> WAIT
        "start_step_3.png",   # Double Coins! -> tap Play + replay
    ]
    eng, ctrl, player = _engine(frames, stop, max_rounds=1)
    eng.run(stop, threading.Event())

    assert "tpl_multi_icon" not in ctrl.taps  # never tapped during auto-roll
    assert "tpl_pink_box" not in ctrl.taps
    assert ctrl.taps == ["tpl_multibuy", "tpl_play_start"]
    assert player.played == 1


def test_pick_macro_always_from_active_pool() -> None:
    """Each round randomly picks a macro from the active pool (never outside it)."""
    import random
    pool = [object(), object(), object()]
    eng, _c, _p = _engine(["main_menu.png"], threading.Event(), macros=pool,
                          rng=random.Random(0))
    picks = [eng._pick_macro() for _ in range(60)]
    assert all(p in pool for p in picks)
    assert len(set(id(p) for p in picks)) >= 2  # actually varies across the pool


def test_single_macro_pool_returns_that_macro() -> None:
    m = object()
    eng, _c, _p = _engine(["main_menu.png"], threading.Event(), macros=[m])
    assert eng._pick_macro() is m


def test_conn_lost_taps_confirm_and_continues() -> None:
    """The 'Connection lost!' overlay → tap the green Confirm (retry), don't stop."""
    stop = threading.Event()
    eng, ctrl, player = _engine(["conn_lost.png"], stop)
    eng.run(stop, threading.Event())
    assert ctrl.taps == ["tpl_conn_confirm"]
    assert eng.round_count == 0


def test_friend_info_taps_close_and_continues() -> None:
    """The Friend's Info popup → tap the top-right X to close, don't stop."""
    stop = threading.Event()
    eng, ctrl, player = _engine(["friend_info.png"], stop)
    eng.run(stop, threading.Event())
    assert ctrl.taps == ["tpl_friend_close"]
    assert eng.round_count == 0


def test_end_round_branches_to_levelup() -> None:
    """END_ROUND can branch to LEVEL_UP (resolved from the next screen, not hardcoded)."""
    stop = threading.Event()
    frames = ["end_round.png", "end_round_level_up.png"]
    eng, ctrl, player = _engine(frames, stop)
    eng.run(stop, threading.Event())
    assert ctrl.taps == ["tpl_result_ok", "tpl_levelup_confirm"]
