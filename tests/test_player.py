"""Phase 5.5: MacroPlayer DOWN/UP scheduler unit tests with a fake clock.

The drift test injects per-sleep overshoot (jitter). A naive sleep(dt) scheduler
would accumulate N*overshoot of drift; the cumulative-target scheduler keeps every
event within ~one overshoot of its absolute target — which is what makes a held
input (S) reproduce its duration accurately.
"""

from __future__ import annotations

import threading

from ckrbot.macro.model import InputEvent
from ckrbot.macro.player import MacroPlayer


class FakeClock:
    """Deterministic clock backed by integer nanoseconds (models a real clock:
    any positive sleep advances time, so busy-waits always converge)."""

    def __init__(self, overshoot: float = 0.0, unpause: tuple | None = None) -> None:
        self._ns = 0
        self._overshoot_ns = round(overshoot * 1e9)
        self._unpause = unpause  # (event, at_time_seconds)

    def time(self) -> float:
        return self._ns / 1e9

    def sleep(self, dt: float) -> None:
        if dt <= 0:
            self._ns += 1_000  # 1µs yield
        else:
            self._ns += max(1, round(dt * 1e9)) + self._overshoot_ns
        if self._unpause is not None:
            evt, at = self._unpause
            if self._ns / 1e9 >= at:
                evt.clear()


class FakeMinitouch:
    """Records (time, action, x, y) for each down/up; can trip stop after N calls."""

    def __init__(self, clock: FakeClock, stop_after=None, stop_evt=None) -> None:
        self.clock = clock
        self.calls: list[tuple[float, str, int | None, int | None]] = []
        self._stop_after = stop_after
        self._stop_evt = stop_evt

    def _record(self, action, x, y) -> None:
        self.calls.append((self.clock.time(), action, x, y))
        if self._stop_after and len(self.calls) >= self._stop_after and self._stop_evt:
            self._stop_evt.set()

    def down(self, x: int, y: int) -> None:
        self._record("DOWN", x, y)

    def up(self) -> None:
        self._record("UP", None, None)


def _player(mt, clock, **kw) -> MacroPlayer:
    return MacroPlayer(
        mt, capture=None, templates=None,  # type: ignore[arg-type]
        anchor_template="", threshold=1.0, poll_interval_ms=10,
        clock=clock.time, sleep=clock.sleep, **kw,
    )


def _ev(dt, action, x=0, y=0) -> InputEvent:
    return InputEvent(dt_ms=dt, action=action, x=x, y=y)


def test_no_cumulative_drift_under_per_sleep_jitter() -> None:
    clock = FakeClock(overshoot=0.003)  # every sleep overshoots by 3ms
    mt = FakeMinitouch(clock)
    player = _player(mt, clock)
    # Alternating DOWN/UP, 100ms apart, ending on UP (balanced -> no extra release).
    events = [
        _ev(0 if i == 0 else 100, "DOWN" if i % 2 == 0 else "UP", i, i)
        for i in range(10)
    ]

    t0 = clock.time()
    assert player._replay(events, threading.Event(), threading.Event()) is True

    cum, targets = 0.0, []
    for e in events:
        cum += e.dt_ms / 1000.0
        targets.append(t0 + cum)
    fires = [c[0] for c in mt.calls]
    assert len(fires) == 10
    errors = [abs(f - tgt) for f, tgt in zip(fires, targets)]
    assert max(errors) < 0.005, errors  # every event within 5ms; no accumulation


def test_hold_duration_reproduced() -> None:
    """DOWN and its UP are scheduled independently -> the hold length is exact."""
    clock = FakeClock()
    mt = FakeMinitouch(clock)
    player = _player(mt, clock)
    events = [_ev(0, "DOWN", 1103, 622), _ev(5000, "UP", 1103, 622)]
    assert player._replay(events, threading.Event(), threading.Event()) is True
    assert [c[1] for c in mt.calls] == ["DOWN", "UP"]
    hold = mt.calls[1][0] - mt.calls[0][0]
    assert abs(hold - 5.0) < 0.005  # ~5000ms hold


def test_stop_event_halts_replay_midway() -> None:
    clock = FakeClock()
    stop = threading.Event()
    # Trip stop after 3 recorded calls (all DOWNs here).
    mt = FakeMinitouch(clock, stop_after=3, stop_evt=stop)
    player = _player(mt, clock)
    events = [_ev(0 if i == 0 else 50, "DOWN", i, i) for i in range(10)]

    assert player._replay(events, stop, threading.Event()) is False
    # 3 DOWNs fired; then stop -> release() sends a closing UP (contact was open).
    assert [c[1] for c in mt.calls] == ["DOWN", "DOWN", "DOWN", "UP"]


def test_stop_mid_hold_releases_contact() -> None:
    """Stopping while a DOWN is open must emit UP so no finger is left pressed."""
    clock = FakeClock()
    stop = threading.Event()
    mt = FakeMinitouch(clock, stop_after=1, stop_evt=stop)  # stop right after DOWN
    player = _player(mt, clock)
    events = [_ev(0, "DOWN", 175, 625), _ev(5000, "UP", 175, 625)]

    assert player._replay(events, stop, threading.Event()) is False
    assert [c[1] for c in mt.calls] == ["DOWN", "UP"]  # released on stop
    assert mt.calls[1][0] < 5.0  # the UP is the release, not the scheduled 5s one


def test_tap_anchor_schedules_from_passed_t0() -> None:
    """Tap-anchor mode: t=0 is the caller's Play-tap timestamp (anchor_t0), so the
    schedule is pinned to it regardless of when _replay actually starts."""
    clock = FakeClock()
    mt = FakeMinitouch(clock)
    player = _player(mt, clock)
    clock.sleep(3.0)  # simulate loading elapsing between the Play tap and _replay
    play_t0 = 0.0     # the Play tap happened at t=0 (before the 3s load)
    events = [_ev(4000, "DOWN", 1, 1), _ev(100, "UP", 1, 1)]  # first input 4s after t=0
    assert player._replay(events, threading.Event(), threading.Event(),
                          anchor_t0=play_t0) is True
    # first input fires at play_t0 + 4.0s (measured from the tap, not from _replay start)
    assert abs(mt.calls[0][0] - (play_t0 + 4.0)) < 0.005
    assert abs(mt.calls[1][0] - (play_t0 + 4.1)) < 0.005


def test_start_delay_shifts_macro_start() -> None:
    """start_delay_ms delays the whole macro after the anchor (compensates a
    record-vs-replay timing offset)."""
    clock = FakeClock()
    mt = FakeMinitouch(clock)
    player = _player(mt, clock, start_delay_ms=200)
    t0 = clock.time()
    events = [_ev(0, "DOWN", 1, 1), _ev(100, "UP", 1, 1)]
    assert player._replay(events, threading.Event(), threading.Event()) is True
    # first event (dt=0) fires at t0 + start_delay, not t0
    assert abs(mt.calls[0][0] - (t0 + 0.200)) < 0.005
    assert abs(mt.calls[1][0] - (t0 + 0.300)) < 0.005  # +100ms after, spacing intact


def test_end_event_stops_replay_early() -> None:
    """When the Result screen is detected (end_evt), replay stops early and
    reports completion (round ended normally, not a stop)."""
    clock = FakeClock()
    end = threading.Event()

    class Mt(FakeMinitouch):
        def _record(self, action, x, y):
            super()._record(action, x, y)
            if len(self.calls) >= 2:  # Result detected after 2 events
                end.set()

    mt = Mt(clock)
    player = _player(mt, clock)
    events = [_ev(0, "DOWN", 1, 1), _ev(50, "UP", 1, 1),
              _ev(50, "DOWN", 2, 2), _ev(5000, "UP", 2, 2)]
    assert player._replay(events, threading.Event(), threading.Event(), end_evt=end) is True
    assert [c[1] for c in mt.calls] == ["DOWN", "UP"]  # remaining events not played


def test_end_mid_hold_releases_contact() -> None:
    """If the round ends while a contact is held, replay releases it (no stuck finger)."""
    clock = FakeClock()
    end = threading.Event()

    class Mt(FakeMinitouch):
        def _record(self, action, x, y):
            super()._record(action, x, y)
            if action == "DOWN":  # Result detected right after a press
                end.set()

    mt = Mt(clock)
    player = _player(mt, clock)
    events = [_ev(0, "DOWN", 1103, 622), _ev(5000, "UP", 1103, 622), _ev(50, "DOWN", 1, 1)]
    assert player._replay(events, threading.Event(), threading.Event(), end_evt=end) is True
    assert [c[1] for c in mt.calls] == ["DOWN", "UP"]  # DOWN, then release UP
    assert mt.calls[1][0] < 5.0  # released immediately, not after the 5000ms hold


def test_pause_blocks_then_resume_shifts_schedule() -> None:
    pause = threading.Event()
    pause.set()  # start paused
    clock = FakeClock(unpause=(pause, 0.5))  # auto-resume once now >= 0.5s
    mt = FakeMinitouch(clock)
    player = _player(mt, clock)
    events = [_ev(0, "DOWN", 1, 1), _ev(100, "UP", 1, 1)]

    assert player._replay(events, threading.Event(), pause) is True
    assert [c[1] for c in mt.calls] == ["DOWN", "UP"]
    assert mt.calls[0][0] >= 0.5  # first event held until pause released
    spacing = mt.calls[1][0] - mt.calls[0][0]
    assert abs(spacing - 0.1) < 0.02  # 100ms spacing preserved after resume
