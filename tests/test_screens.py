"""Phase 2 vision tests (spec §13): each fixture's state signal matches, and the
easily-confused pairs stay separable with a clear margin.

Measured confidences (see numbers inline) come from the real 1280x720 fixtures;
thresholds are set well inside those margins so the suite is not brittle.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import Region, find_template

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures" / "screens"
_ASSETS = _ROOT / "game" / "assets"

# config default_threshold (spec §9). Every true signal measured >= 0.994.
THRESHOLD = 0.85

_store = TemplateStore(_ASSETS)


def _frame(fixture: str):
    img = cv2.imread(str(_FIXTURES / fixture), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {fixture}"
    return img


def _pad(region: Region, p: int = 10, w: int = 1280, h: int = 720) -> Region:
    """Grow a region so template match has slide room (mirrors live detection)."""
    x1, y1, x2, y2 = region
    return (max(0, x1 - p), max(0, y1 - p), min(w, x2 + p), min(h, y2 + p))


def _confidence(fixture: str, tpl_name: str) -> float:
    tpl = _store.load(tpl_name)
    region = _pad(tpl.region) if tpl.region is not None else None
    return find_template(_frame(fixture), tpl.image, region).confidence


# fixture -> (state, signal template). One row per required fixture (spec mapping).
SIGNALS = [
    ("main_menu.png", "MAIN_MENU", "tpl_main_marker"),
    ("main_menu_reward.png", "MENU_REWARD", "tpl_congrats_confirm"),
    ("main_menu_reward_2.png", "MENU_REWARD", "tpl_congrats_confirm"),
    ("start_step_1.png", "START_1", "tpl_buy_upgrades"),
    ("start_step_2.png", "START_2", "tpl_multibuy"),
    ("start_step_3.png", "START_3", "tpl_double_coins"),
    ("start.png", "GAMEPLAY", "tpl_pause"),
    ("money.png", "MONEY_POPUP", "tpl_cancel"),
    ("capcha_1.png", "CAPTCHA", "tpl_captcha_header"),
    ("capcha_2.png", "CAPTCHA", "tpl_captcha_header"),
    ("capcha_3.png", "CAPTCHA", "tpl_captcha_header"),
    ("end_round.png", "END_ROUND", "tpl_result_ok"),
    ("end_round_box.png", "END_BOX", "tpl_open_all"),
    ("end_round_box_open.png", "END_BOX_OPEN", "tpl_box_confirm"),
    ("end_round_level_up.png", "LEVEL_UP", "tpl_levelup_confirm"),
]


@pytest.mark.parametrize("fixture,state,tpl", SIGNALS, ids=[f"{s[1]}:{s[0]}" for s in SIGNALS])
def test_state_signal_matches_in_region(fixture: str, state: str, tpl: str) -> None:
    """The identifying template for each state clears the threshold on its fixture."""
    conf = _confidence(fixture, tpl)
    assert conf >= THRESHOLD, f"{state}: {tpl} on {fixture} = {conf:.3f} < {THRESHOLD}"


def test_relay_boost_icon_detected_only_on_boost_screen() -> None:
    """The in-gameplay Cookie Relay Boost icon is tappable on its screen and does
    not false-trigger on ordinary gameplay."""
    tpl = _store.load("tpl_relay_boost")

    def conf(fixture: str) -> float:
        return find_template(_frame(fixture), tpl.image, _pad(tpl.region)).confidence

    assert conf("relay_boost.png") >= 0.7
    assert conf("start.png") < 0.7  # gameplay without the boost prompt


def test_start1_vs_start3_separated_by_double_coins() -> None:
    """START_3 has the Double Coins banner; START_1 does not (1.000 vs 0.233)."""
    on_start3 = _confidence("start_step_3.png", "tpl_double_coins")
    on_start1 = _confidence("start_step_1.png", "tpl_double_coins")
    assert on_start3 >= THRESHOLD
    assert on_start1 < THRESHOLD
    assert on_start3 - on_start1 > 0.3


def test_start2_vs_money_do_not_cross_match() -> None:
    """START_2 (Multi-Buy) and MONEY_POPUP (gray Cancel) must not match each other."""
    # Cancel identifies MONEY_POPUP, not START_2 (1.000 vs 0.096).
    assert _confidence("money.png", "tpl_cancel") >= THRESHOLD
    assert _confidence("start_step_2.png", "tpl_cancel") < THRESHOLD
    # Multi-Buy identifies START_2, not MONEY_POPUP (1.000 vs 0.242).
    assert _confidence("start_step_2.png", "tpl_multibuy") >= THRESHOLD
    assert _confidence("money.png", "tpl_multibuy") < THRESHOLD


def test_endbox_vs_endboxopen_separated_by_margin() -> None:
    """Hardest pair: identical teal buttons, differ only by inner text.

    Assert each button template scores higher on its own screen than on the
    confusable one, with gap > 0.05. Measured gaps ~0.24. If this ever narrows,
    re-crop the template to the text-only zone (spec/CLAUDE.md guidance).
    """
    # tpl_open_all belongs to END_BOX.
    open_own = _confidence("end_round_box.png", "tpl_open_all")
    open_other = _confidence("end_round_box_open.png", "tpl_open_all")
    assert open_own >= THRESHOLD
    assert open_other < THRESHOLD
    assert open_own - open_other > 0.05, f"END_BOX margin too narrow: {open_own - open_other:.3f}"

    # tpl_box_confirm belongs to END_BOX_OPEN (symmetric check).
    confirm_own = _confidence("end_round_box_open.png", "tpl_box_confirm")
    confirm_other = _confidence("end_round_box.png", "tpl_box_confirm")
    assert confirm_own >= THRESHOLD
    assert confirm_other < THRESHOLD
    assert confirm_own - confirm_other > 0.05, (
        f"END_BOX_OPEN margin too narrow: {confirm_own - confirm_other:.3f}"
    )
