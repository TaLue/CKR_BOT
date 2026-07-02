"""Device-profile parser tests (spec §2.6)."""

from __future__ import annotations

from ckrbot.adb.probe import parse_getevent_pl

_SAMPLE = """add device 1: /dev/input/event3
  name:     "some_keyboard"
  events:
    KEY (0001): KEY_A KEY_B
add device 2: /dev/input/event4
  name:     "input"
  events:
    ABS (0003): ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X      : value 0, min 0, max 1279, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y      : value 0, min 0, max 719, fuzz 0, flat 0, resolution 0
                ABS_MT_PRESSURE        : value 0, min 0, max 2, fuzz 0, flat 0, resolution 0
"""


def test_parses_touch_device_geometry() -> None:
    prof = parse_getevent_pl(_SAMPLE)
    assert prof == {
        "path": "/dev/input/event4",
        "max_x": 1279,
        "max_y": 719,
        "max_pressure": 2,
    }


def test_none_when_no_touch_device() -> None:
    text = "add device 1: /dev/input/event3\n  events:\n    KEY (0001): KEY_A\n"
    assert parse_getevent_pl(text) is None


def test_picks_device_with_position_axis_not_keyboard() -> None:
    prof = parse_getevent_pl(_SAMPLE)
    assert prof is not None and prof["path"] == "/dev/input/event4"  # not event3
