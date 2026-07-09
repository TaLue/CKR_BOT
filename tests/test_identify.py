"""Phase 6a: priority identify() returns the correct state for every fixture,
and the look-alike pairs are never confused (spec §4/§13, INVARIANT #5)."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from ckrbot.engine.screen import ScreenIdentifier
from ckrbot.game.states import CKR_SCREENS, State
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import find_template

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures" / "screens"
_ASSETS = _ROOT / "game" / "assets"

_store = TemplateStore(_ASSETS)
_ident = ScreenIdentifier(_store, CKR_SCREENS, threshold=0.85)


def _frame(name: str):
    img = cv2.imread(str(_FIXTURES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    return img


# fixture -> expected identified state (spec mapping)
CASES = [
    ("main_menu.png", State.MAIN_MENU),
    ("main_menu_reward.png", State.MENU_REWARD),
    ("main_menu_reward_2.png", State.MENU_REWARD),
    ("start_step_1.png", State.START_1),
    ("start_step_2.png", State.START_2),
    ("start_step_3.png", State.START_3),
    ("start.png", State.GAMEPLAY),
    ("money.png", State.MONEY_POPUP),
    ("capcha_1.png", State.CAPTCHA),
    ("capcha_2.png", State.CAPTCHA),
    ("capcha_3.png", State.CAPTCHA),
    ("end_round.png", State.END_ROUND),
    ("end_round_box.png", State.END_BOX),
    ("end_round_box_open.png", State.END_BOX_OPEN),
    ("end_round_level_up.png", State.LEVEL_UP),
    ("conn_lost.png", State.CONN_LOST),
    ("friend_info.png", State.FRIEND_INFO),
    ("daily_checkin.png", State.DAILY_CHECKIN),
    ("title.png", State.TITLE),
]


@pytest.mark.parametrize("fixture,expected", CASES, ids=[f"{c[1].value}:{c[0]}" for c in CASES])
def test_identify_returns_expected_state(fixture: str, expected: State) -> None:
    assert _ident.identify(_frame(fixture)) == expected


def test_start1_and_start3_not_confused() -> None:
    assert _ident.identify(_frame("start_step_1.png")) == State.START_1
    assert _ident.identify(_frame("start_step_3.png")) == State.START_3


def test_endbox_and_endboxopen_not_confused() -> None:
    assert _ident.identify(_frame("end_round_box.png")) == State.END_BOX
    assert _ident.identify(_frame("end_round_box_open.png")) == State.END_BOX_OPEN


def test_start2_and_money_not_confused() -> None:
    assert _ident.identify(_frame("start_step_2.png")) == State.START_2
    assert _ident.identify(_frame("money.png")) == State.MONEY_POPUP


def test_daily_ok_locatable_on_daily_checkin() -> None:
    """tpl_daily_ok is the DAILY_CHECKIN tap target — verify it is locatable there."""
    tpl = _store.load("tpl_daily_ok")
    result = find_template(_frame("daily_checkin.png"), tpl.image, tpl.region)
    assert result.confidence >= 0.85
    assert result.center == (641, 659)  # green OK button center


def test_play_main_locatable_on_main_menu() -> None:
    """tpl_play_main is the MAIN_MENU tap target — verify it is locatable there."""
    tpl = _store.load("tpl_play_main")
    result = find_template(_frame("main_menu.png"), tpl.image, tpl.region)
    assert result.confidence >= 0.85


def test_play_main_does_not_match_start3() -> None:
    """FINDING (6b): START_3's Play button differs from the main-menu Play, so
    tpl_play_main does NOT match it — START_3 needs its own Play tap target
    (new crop or coordinate). Documented here so 6b's tap plan is correct."""
    tpl = _store.load("tpl_play_main")
    result = find_template(_frame("start_step_3.png"), tpl.image, tpl.region)
    assert result.confidence < 0.85
