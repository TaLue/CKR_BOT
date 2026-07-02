"""Phase 5.5: getevent DOWN/UP parser + segmentation (anchor gap, END trim,
balance) + Macro JSON round-trip. All pure — no device."""

from __future__ import annotations

from pathlib import Path

from ckrbot.macro.model import InputEvent, Macro, Screen, TouchMax
from ckrbot.macro.recorder import (
    RawEvent,
    build_macro_events,
    parse_getevent_events,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _lines(name: str) -> list[str]:
    return (_FIXTURES / name).read_text(encoding="utf-8").splitlines()


# ---- parser: fixture of 6 taps -> 12 DOWN/UP events ------------------------

# (dt_ms, action, x, y) expected from getevent_sample.txt (device-ts dts, first=0).
EXPECTED = [
    (0, "DOWN", 939, 633),
    (90, "UP", 939, 633),
    (1748, "DOWN", 94, 655),
    (127, "UP", 94, 655),
    (6052, "DOWN", 176, 625),
    (119, "UP", 176, 625),
    (1174, "DOWN", 1103, 624),
    (89, "UP", 1103, 624),
    (1339, "DOWN", 174, 625),
    (118, "UP", 174, 625),
    (1147, "DOWN", 1103, 622),
    (101, "UP", 1103, 622),
]


def test_parser_emits_down_up_pairs_with_coords_and_dt() -> None:
    events = parse_getevent_events(_lines("getevent_sample.txt"))
    got = [(e.dt_ms, e.action, e.x, e.y) for e in events]
    assert got == EXPECTED


def test_parser_alternates_down_up_never_merges() -> None:
    events = parse_getevent_events(_lines("getevent_sample.txt"))
    assert [e.action for e in events] == ["DOWN", "UP"] * 6  # 6 taps, none merged


def test_parser_ignores_comment_and_blank_lines() -> None:
    assert len(parse_getevent_events(_lines("getevent_sample.txt"))) == 12


# ---- S-hold: long press keeps its duration in the UP dt --------------------

def test_hold_duration_preserved_as_up_dt() -> None:
    events = parse_getevent_events(_lines("getevent_hold_sample.txt"))
    assert [(e.action, e.x, e.y) for e in events] == [
        ("DOWN", 1103, 622),
        ("UP", 1103, 622),
    ]
    assert events[0].dt_ms == 0
    assert events[1].dt_ms == 5000  # 105.0 - 100.0 s held


# ---- W-rapid: fast repeated taps all captured, none merged -----------------

def test_rapid_taps_all_captured() -> None:
    lines = []
    ts = 1.000
    for i in range(4):  # 4 quick taps at W=(175,625), 60ms apart, 20ms press
        lines += [
            f"[   {ts:.6f}] EV_ABS  ABS_MT_TRACKING_ID  {0x20 + i:08x}",
            "[   %.6f] EV_ABS  ABS_MT_POSITION_X  000000af" % ts,
            "[   %.6f] EV_ABS  ABS_MT_POSITION_Y  00000271" % ts,
            f"[   {ts:.6f}] EV_SYN  SYN_REPORT  00000000",
            f"[   {ts + 0.020:.6f}] EV_ABS  ABS_MT_TRACKING_ID  ffffffff",
            f"[   {ts + 0.020:.6f}] EV_SYN  SYN_REPORT  00000000",
        ]
        ts += 0.060
    events = parse_getevent_events(lines)
    assert [e.action for e in events] == ["DOWN", "UP"] * 4  # 8 events, none merged
    assert all((e.x, e.y) == (175, 625) for e in events)  # 0xaf=175, 0x271=625


def test_parser_carries_unchanged_axis_forward() -> None:
    """evdev reports a changed axis only; a DOWN reusing Y must inherit it."""
    lines = [
        "[   1.000000] EV_ABS  ABS_MT_TRACKING_ID  00000001",
        "[   1.000000] EV_ABS  ABS_MT_POSITION_X   00000096",
        "[   1.000000] EV_ABS  ABS_MT_POSITION_Y   0000012c",
        "[   1.000000] EV_SYN  SYN_REPORT          00000000",
        "[   1.050000] EV_ABS  ABS_MT_TRACKING_ID  ffffffff",
        "[   1.050000] EV_SYN  SYN_REPORT          00000000",
        # second tap: only X changes; Y (300) not re-reported
        "[   2.000000] EV_ABS  ABS_MT_TRACKING_ID  00000002",
        "[   2.000000] EV_ABS  ABS_MT_POSITION_X   00000384",
        "[   2.000000] EV_SYN  SYN_REPORT          00000000",
        "[   2.050000] EV_ABS  ABS_MT_TRACKING_ID  ffffffff",
        "[   2.050000] EV_SYN  SYN_REPORT          00000000",
    ]
    events = parse_getevent_events(lines)
    assert [(e.action, e.x, e.y) for e in events] == [
        ("DOWN", 150, 300), ("UP", 150, 300),
        ("DOWN", 900, 300), ("UP", 900, 300),  # Y carried forward
    ]


# ---- segmentation: anchor gap, END trim, balance ---------------------------

def _raw(device_ts, host_ts, action, x, y):
    return RawEvent(device_ts=device_ts, host_ts=host_ts, action=action, x=x, y=y)


def test_anchor_gap_sets_first_event_dt() -> None:
    """First gameplay event dt = host gap from anchor detection (not 0)."""
    raw = [
        _raw(10.0, 100.30, "DOWN", 175, 625),  # arrives 0.30s after anchor@100.0
        _raw(10.05, 100.35, "UP", 175, 625),
    ]
    events = build_macro_events(raw, anchor_host=100.0, end_host=None)
    assert events[0].dt_ms == 300  # anchor gap, not 0
    assert events[1].dt_ms == 50   # device-ts delta 10.05-10.00


def test_events_before_anchor_are_discarded() -> None:
    raw = [
        _raw(9.0, 99.0, "DOWN", 500, 500),   # menu tap before anchor@100
        _raw(9.05, 99.05, "UP", 500, 500),
        _raw(10.0, 100.5, "DOWN", 175, 625),
        _raw(10.05, 100.55, "UP", 175, 625),
    ]
    events = build_macro_events(raw, anchor_host=100.0, end_host=None)
    assert [(e.action, e.x, e.y) for e in events] == [
        ("DOWN", 175, 625), ("UP", 175, 625)
    ]


def test_events_after_end_are_trimmed() -> None:
    """Post-END popup taps (after END detection) are removed."""
    raw = [
        _raw(10.0, 100.5, "DOWN", 175, 625),
        _raw(10.05, 100.55, "UP", 175, 625),
        _raw(20.0, 130.0, "DOWN", 1197, 45),   # pause tap after END@120
        _raw(20.05, 130.05, "UP", 1197, 45),
        _raw(21.0, 131.0, "DOWN", 617, 456),   # popup center tap
        _raw(21.05, 131.05, "UP", 617, 456),
    ]
    events = build_macro_events(raw, anchor_host=100.0, end_host=120.0)
    assert [(e.x, e.y) for e in events] == [(175, 625), (175, 625)]  # tail trimmed


def test_unclosed_down_gets_auto_up() -> None:
    """If recording ends with the contact still DOWN (S held across END), a
    closing UP is appended so no finger is left pressed."""
    raw = [
        _raw(10.0, 100.5, "DOWN", 1103, 622),  # S pressed, never released in-window
    ]
    events = build_macro_events(raw, anchor_host=100.0, end_host=None)
    assert [(e.action, e.x, e.y) for e in events] == [
        ("DOWN", 1103, 622), ("UP", 1103, 622)
    ]


def test_dangling_up_dropped_and_dt_folded() -> None:
    """A leading UP with no matching DOWN is dropped; its dt folds into the next
    event so the timeline is preserved."""
    raw = [
        _raw(10.0, 100.5, "UP", 500, 500),     # contact began before window
        _raw(10.2, 100.7, "DOWN", 175, 625),
        _raw(10.25, 100.75, "UP", 175, 625),
    ]
    events = build_macro_events(raw, anchor_host=100.0, end_host=None)
    assert [(e.action, e.x, e.y) for e in events] == [
        ("DOWN", 175, 625), ("UP", 175, 625)
    ]
    # first event dt = anchor gap(500ms) + dropped-UP's device gap(200ms) = 700ms
    assert events[0].dt_ms == 700


# ---- Macro JSON round-trip -------------------------------------------------

def test_macro_json_round_trip(tmp_path) -> None:
    macro = Macro(
        name="escape_from_the_oven_v2",
        created_at="2026-07-02T12:00:00+07:00",
        screen=Screen(w=1280, h=720),
        touch_max=TouchMax(x=1279, y=719),
        pressure_max=2,
        events=[
            InputEvent(dt_ms=0, action="DOWN", x=175, y=625),
            InputEvent(dt_ms=5000, action="UP", x=175, y=625),
        ],
    )
    path = tmp_path / "m.json"
    macro.save(path)
    loaded = Macro.load(path)
    assert loaded == macro
    assert loaded.version == 2
    assert loaded.events[1].action == "UP"
    assert loaded.events[1].dt_ms == 5000
