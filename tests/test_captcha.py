"""Phase 6: CAPTCHA odd-one-out solver against the real captcha fixtures."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from ckrbot.game.captcha import (
    CARD_REGIONS,
    card_center,
    card_scores,
    find_odd_cards,
    read_tries,
    solve_captcha,
)
from ckrbot.vision.template import TemplateStore

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screens"
_ASSETS = Path(__file__).resolve().parent.parent / "game" / "assets"


def _frame(name: str):
    img = cv2.imread(str(_FIXTURES / name), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


# Confirmed visually (montage): the 2 odd (minority-pose) cards per fixture.
ODD = {
    "capcha_1.png": [3, 5],  # sit/crouch vs standing
    "capcha_2.png": [1, 4],  # crouch on board vs run
    "capcha_3.png": [1, 2],  # lunge vs upright
    # Real "Find the sliding card" round (captured live, user-confirmed answer):
    # card 5 is the sliding cookie and card 2 is its now-empty origin slot — the
    # solver must still resolve both from ONE frame. Regression for single-frame.
    "capcha_slide.png": [2, 5],
}


@pytest.mark.parametrize("fixture,expected", list(ODD.items()))
def test_find_odd_cards(fixture: str, expected: list[int]) -> None:
    assert find_odd_cards(_frame(fixture)) == expected


@pytest.mark.parametrize("fixture,expected", list(ODD.items()))
def test_card_scores_lowest_two_are_the_odd_cards(fixture: str, expected: list[int]) -> None:
    """The diagnostic scores (mean similarity) must rank the 2 odd cards lowest."""
    scores = card_scores(_frame(fixture))
    assert len(scores) == 6
    lowest_two = sorted(sorted(range(6), key=lambda i: scores[i])[:2])
    assert lowest_two == expected


@pytest.mark.parametrize("fixture,expected", list(ODD.items()))
def test_solve_returns_centers_of_odd_cards(fixture: str, expected: list[int]) -> None:
    points = solve_captcha(_frame(fixture))
    assert points == [card_center(CARD_REGIONS[i]) for i in expected]
    assert len(points) == 2


def test_read_tries_reads_remaining_count() -> None:
    store = TemplateStore(_ASSETS)
    tpls = {n: store.load(f"tpl_tries_{n}").image for n in (3, 2, 1)}
    assert read_tries(_frame("capcha_1.png"), tpls) == 3  # "Tries left 3/3"
    assert read_tries(_frame("capcha_2.png"), tpls) == 2  # 2/3
    assert read_tries(_frame("capcha_3.png"), tpls) == 1  # 1/3
    assert read_tries(_frame("capcha_slide.png"), tpls) == 1  # 1/3 (live sliding round)


def test_read_tries_none_when_not_captcha() -> None:
    store = TemplateStore(_ASSETS)
    tpls = {n: store.load(f"tpl_tries_{n}").image for n in (3, 2, 1)}
    assert read_tries(_frame("main_menu.png"), tpls) is None
    assert read_tries(_frame("end_round.png"), tpls) is None
