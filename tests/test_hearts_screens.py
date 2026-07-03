# tests/test_hearts_screens.py
"""Send-Hearts templates match on their own screen/region and do not cross-match."""
from __future__ import annotations

from pathlib import Path

import cv2

from ckrbot.config.models import HeartsConfig
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import find_template

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "screens"
_store = TemplateStore(str(_ROOT / "game" / "assets"))
_hc = HeartsConfig()


def _frame(name: str):
    img = cv2.imread(str(_FIX / name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    return img


def _conf(fixture: str, tpl: str, region) -> float:
    t = _store.load(tpl)
    return find_template(_frame(fixture), t.image, tuple(region)).confidence


def test_send_button_found_on_friend_list() -> None:
    assert _conf("friend_list.png", "tpl_send_life", _hc.send_region) >= _hc.threshold


def test_ask_confirm_found_on_dialog1() -> None:
    assert _conf("click_sent.png", "tpl_life_confirm", _hc.ask_confirm_region) >= _hc.threshold


def test_sent_confirm_found_on_dialog2() -> None:
    assert _conf("confirm_sent.png", "tpl_sent_confirm", _hc.sent_confirm_region) >= _hc.threshold


def test_no_cross_matches() -> None:
    # send button must not appear in either dialog's confirm region
    assert _conf("click_sent.png", "tpl_send_life", _hc.send_region) < _hc.threshold
    # the two confirms must not match on the WRONG dialog within their own region
    assert _conf("confirm_sent.png", "tpl_life_confirm", _hc.ask_confirm_region) < _hc.threshold
    assert _conf("click_sent.png", "tpl_sent_confirm", _hc.sent_confirm_region) < _hc.threshold
