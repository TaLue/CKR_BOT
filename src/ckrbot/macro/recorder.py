"""Macro recorder (Phase 5.5) — getevent DOWN/UP parser + anchor/END trimming.

The parser is pure (``parse_getevent_events`` / ``build_macro_events``) so it is
tested against fixtures with no device. ``MacroRecorder.record()`` needs a live
device.

Model: a stream of DOWN/UP InputEvents (W tapped rapidly, S held for seconds).
Emission is keyed on SYN_REPORT so a DOWN carries the position reported in its
own frame; UP reuses the active contact's coordinates. Axis values are persistent
device state (evdev reports an axis only when it changes).

dt between events uses getevent timestamps (precise). The FIRST gameplay event's
dt is the ANCHOR GAP (time from GAMEPLAY anchor detection to the first input),
measured on the host clock — not 0 — so replay doesn't start early.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from ckrbot.adb.client import AdbClient
from ckrbot.capture.screen import ScreenCapture
from ckrbot.config.models import DeviceConfig
from ckrbot.macro.model import InputEvent, Macro, Screen, TouchMax
from ckrbot.vision.template import TemplateStore
from ckrbot.vision.vision import Region, find_template

# Optional "/dev/input/...:" device-path column (present only in multi-device dumps).
_LINE_RE = re.compile(
    r"^\[\s*(?P<ts>\d+\.\d+)\]\s+(?:\S+:\s+)?\S+\s+(?P<code>\S+)\s+(?P<value>\S+)\s*$"
)
_UP_ID = "ffffffff"


@dataclass(frozen=True)
class RawEvent:
    """A DOWN/UP transition with device (getevent) and host arrival timestamps."""

    device_ts: float
    host_ts: float
    action: str  # "DOWN" | "UP"
    x: int
    y: int


class _InputAssembler:
    """Incremental getevent-line state machine emitting DOWN/UP on each SYN frame.

    Single-touch model (the game uses one finger). Persistent axis state (X/Y) is
    carried across frames because evdev only reports an axis when it changes. A
    DOWN uses the position current at its frame; an UP reuses the active contact's
    down coordinates (touches are stationary — no MOVE).

    A SECONDARY concurrent contact (a new tracking id while one is already active,
    with no BTN_TOUCH DOWN — type-A multitouch) is IGNORED. This keeps a held
    input intact if a stray/overlapping touch occurs mid-hold. (A recording should
    still be made with no autofire/keymap noise — see MacroRecorder.record.)
    """

    def __init__(self) -> None:
        self._x: int | None = None  # persistent device axis state
        self._y: int | None = None
        self._active = False  # is a (primary) contact currently down?
        self._down_x: int | None = None  # coords of the active contact
        self._down_y: int | None = None
        self._f_btn_down = False
        self._f_track_down = False
        self._f_up = False

    def feed(self, line: str, host_ts: float) -> list[RawEvent]:
        """Feed one raw getevent line; returns events emitted at a SYN boundary."""
        m = _LINE_RE.match(line)
        if m is None:
            return []
        ts = float(m.group("ts"))
        code = m.group("code")
        value = m.group("value")

        if code == "ABS_MT_TRACKING_ID":
            if value == _UP_ID:
                self._f_up = True
            else:
                self._f_track_down = True
        elif code == "BTN_TOUCH":
            if value.upper() == "UP":
                self._f_up = True
            elif value.upper() == "DOWN":
                self._f_btn_down = True
        elif code == "ABS_MT_POSITION_X":
            self._x = int(value, 16)
        elif code == "ABS_MT_POSITION_Y":
            self._y = int(value, 16)
        elif code == "SYN_REPORT":
            return self._flush(ts, host_ts)
        return []

    def _flush(self, ts: float, host_ts: float) -> list[RawEvent]:
        out: list[RawEvent] = []
        # A genuine new press = explicit BTN_TOUCH DOWN, or a tracking-id-down while
        # no contact is active. A tracking-id-down WHILE active is a secondary
        # contact (multitouch) and is ignored to protect the held primary contact.
        new_press = self._f_btn_down or (self._f_track_down and not self._active)

        if new_press and not self._active and self._x is not None and self._y is not None:
            self._active = True
            self._down_x, self._down_y = self._x, self._y
            out.append(RawEvent(ts, host_ts, "DOWN", self._down_x, self._down_y))
        if self._f_up and self._active:
            out.append(RawEvent(ts, host_ts, "UP", self._down_x, self._down_y))
            self._active = False
            self._down_x = self._down_y = None

        self._f_btn_down = self._f_track_down = self._f_up = False
        return out


def _events_from_raw(raw: Sequence[RawEvent], first_gap_s: float | None) -> list[InputEvent]:
    """Convert raw events to InputEvents. Inter-event dt from device ts; the first
    event's dt is the anchor gap (host clock) when provided, else 0."""
    out: list[InputEvent] = []
    prev: RawEvent | None = None
    for i, e in enumerate(raw):
        if i == 0:
            dt_ms = max(0, round(first_gap_s * 1000)) if first_gap_s is not None else 0
        else:
            assert prev is not None
            dt_ms = max(0, round((e.device_ts - prev.device_ts) * 1000))
        out.append(InputEvent(dt_ms=dt_ms, action=e.action, x=e.x, y=e.y))
        prev = e
    return out


def _balance(events: list[InputEvent]) -> list[InputEvent]:
    """Ensure DOWN/UP are balanced (single contact).

    Drops a dangling UP (contact began before the recording window), folding its
    dt into the next event to preserve the timeline; if the stream ends with the
    contact still DOWN (e.g. S held across END_ROUND), appends a closing UP so no
    finger is left pressed.
    """
    out: list[InputEvent] = []
    active = 0
    last_down: InputEvent | None = None
    carry = 0
    for e in events:
        dt = e.dt_ms + carry
        carry = 0
        if e.action == "DOWN":
            active += 1
            ne = InputEvent(dt_ms=dt, action="DOWN", x=e.x, y=e.y)
            last_down = ne
            out.append(ne)
        else:  # UP
            if active > 0:
                active -= 1
                out.append(InputEvent(dt_ms=dt, action="UP", x=e.x, y=e.y))
            else:
                carry = dt  # dangling UP — drop, carry its dt forward
    if active > 0 and last_down is not None:
        out.append(InputEvent(dt_ms=0, action="UP", x=last_down.x, y=last_down.y))
    return out


def parse_getevent_events(lines: Iterable[str]) -> list[InputEvent]:
    """Parse a full getevent -lt stream into DOWN/UP InputEvents (pure).

    No anchor gap / trimming / balancing — this is the raw device stream (used by
    the parser fixture test). first event dt = 0.
    """
    assembler = _InputAssembler()
    raw: list[RawEvent] = []
    for line in lines:
        raw.extend(assembler.feed(line, host_ts=0.0))
    return _events_from_raw(raw, first_gap_s=None)


def build_macro_events(
    raw: Sequence[RawEvent],
    anchor_host: float | None,
    end_host: float | None,
) -> list[InputEvent]:
    """Segment raw events into the final macro (pure, testable).

    Keeps events in the (anchor, END] host-time window, sets the first event's dt
    to the anchor gap, then balances DOWN/UP.
    """
    gameplay = [
        e
        for e in raw
        if (anchor_host is None or e.host_ts >= anchor_host)
        and (end_host is None or e.host_ts < end_host)
    ]
    if not gameplay:
        return []
    gap_s = (gameplay[0].host_ts - anchor_host) if anchor_host is not None else None
    return _balance(_events_from_raw(gameplay, gap_s))


def _in_region(x: int, y: int, region: Region, pad: int = 20) -> bool:
    """True if (x, y) falls inside ``region`` (padded), used to spot the Play tap."""
    x1, y1, x2, y2 = region
    return (x1 - pad) <= x <= (x2 + pad) and (y1 - pad) <= y <= (y2 + pad)


def build_macro_events_tap(
    raw: Sequence[RawEvent],
    play_region: Region,
    anchor_host: float | None,
    end_host: float | None,
) -> list[InputEvent] | None:
    """Segment the macro anchored to the PLAY-button tap (tap-anchor mode).

    t=0 is the Play tap (the last DOWN inside ``play_region`` — the menu tap that
    starts the round), timed on the DEVICE clock so it carries no capture latency.
    Everything after the tap's release, up to END, is gameplay; the first event's
    dt is the device-clock gap from the Play tap to the first input (loading + lead
    time). Returns None if no Play tap is found so the caller can fall back to the
    visual-anchor build.

    ``anchor_host`` (pause detection) bounds the Play-tap search to BEFORE gameplay:
    a gameplay Slide tap can land near the Play button's x-edge, so only DOWNs before
    the round starts are Play-tap candidates.
    """
    play_down_idx = None
    for i, e in enumerate(raw):
        if anchor_host is not None and e.host_ts >= anchor_host:
            break  # gameplay has started — stop looking for the Play tap
        if e.action == "DOWN" and _in_region(e.x, e.y, play_region):
            play_down_idx = i  # keep the LAST such DOWN (the one that started this round)
    if play_down_idx is None:
        return None
    play_tap = raw[play_down_idx]
    # Skip past the Play tap's own release so it isn't taken as the first input.
    release_idx = next(
        (j for j in range(play_down_idx + 1, len(raw)) if raw[j].action == "UP"),
        play_down_idx,
    )
    gameplay = [
        e for e in raw[release_idx + 1:] if end_host is None or e.host_ts < end_host
    ]
    if not gameplay:
        return None
    gap_s = gameplay[0].device_ts - play_tap.device_ts
    return _balance(_events_from_raw(gameplay, gap_s))


class MacroRecorder:
    """Records a clean gameplay round into a Macro.

    Generic by design: anchor/end template names are injected by the caller (the
    game/app layer), so this module has no hardcoded screen names.
    """

    def __init__(
        self,
        adb: AdbClient,
        capture: ScreenCapture,
        templates: TemplateStore,
        *,
        device: DeviceConfig,
        anchor_template: str,
        end_template: str,
        threshold: float,
        poll_interval_ms: int,
        anchor_poll_ms: int = 20,
        play_template: str | None = None,
    ) -> None:
        self._adb = adb
        self._capture = capture
        self._templates = templates
        self._device = device
        self._anchor_template = anchor_template
        self._end_template = end_template
        self._threshold = threshold
        # Poll the anchor/END detection loop fast so t=0 is captured tightly (the
        # same fast rate must be used on replay — see MacroPlayer).
        self._poll_s = anchor_poll_ms / 1000.0
        # Tap-anchor mode: t=0 is the Play-button tap read from the getevent stream
        # (device clock, capture-latency-free). Falls back to the visual pause anchor
        # if this is None or no Play tap is found. See build_macro_events_tap.
        self._play_region: Region | None = (
            templates.region_of(play_template) if play_template else None
        )

    def _matches(self, frame, tpl_name: str) -> bool:
        tpl = self._templates.load(tpl_name)
        return find_template(frame, tpl.image, tpl.region).confidence >= self._threshold

    def _stream_reader(
        self,
        conn,
        raw: list[RawEvent],
        lock: threading.Lock,
        stop_flag: threading.Event,
    ) -> None:
        assembler = _InputAssembler()
        try:
            for line in conn.conn.makefile("r", encoding="utf-8", errors="replace"):
                if stop_flag.is_set():
                    break
                events = assembler.feed(line, host_ts=time.perf_counter())
                if events:
                    with lock:
                        raw.extend(events)
        except OSError:
            pass  # socket closed on stop — expected

    def record(self, name: str, stop_evt: threading.Event | None = None) -> Macro:
        """Record one round; blocks until END_ROUND (or stop_evt) is detected.

        TODO(Phase 6): the GAMEPLAY anchor relies on tpl_pause alone, which can
        false-positive on the Continue/Quit overlay. Requires a CLEAN round; also
        do not tap the END popup while recording (post-END taps after detection
        are trimmed, but tapping before detection can leak). Harden in Phase 6.

        PREREQUISITE: disable any LDPlayer keymap autofire / stuck keys before
        recording. A concurrent auto-tap (observed at Slide/Jump) injects phantom
        touches that pollute the macro; the assembler ignores brief secondary
        contacts but a persistent autofire will still corrupt a recording.
        """
        stop_evt = stop_evt or threading.Event()
        raw: list[RawEvent] = []
        lock = threading.Lock()
        reader_stop = threading.Event()

        getevent_cmd = f"getevent -lt {self._device.touch_device}"
        conn = self._adb.shell_stream(getevent_cmd)
        reader = threading.Thread(
            target=self._stream_reader, args=(conn, raw, lock, reader_stop), daemon=True
        )
        reader.start()
        logger.info("recording '{}': streaming {}", name, getevent_cmd)

        # anchor_host (pause detection) is no longer macro t=0 — it only marks that
        # gameplay loaded, and bounds the Play-tap search to before the round starts.
        anchor_host: float | None = None
        end_host: float | None = None
        try:
            while not stop_evt.is_set():
                frame = self._capture.grab()
                if anchor_host is None:
                    if self._matches(frame, self._anchor_template):
                        anchor_host = time.perf_counter()
                        logger.info("GAMEPLAY detected (loading done)")
                elif self._matches(frame, self._end_template):
                    end_host = time.perf_counter()
                    logger.info("END_ROUND detected — stopping record")
                    break
                stop_evt.wait(self._poll_s)
        finally:
            reader_stop.set()
            try:
                conn.close()  # unblocks the reader's makefile iteration
            except Exception:  # noqa: BLE001
                pass
            reader.join(timeout=2.0)

        with lock:
            raw_snapshot = list(raw)
        if anchor_host is None:
            logger.warning("anchor never detected — building macro without trimming")

        # Tap-anchor (preferred): t=0 = the Play-button tap, timed on the device clock
        # (no capture latency). Fall back to the visual pause anchor if unavailable.
        events = None
        anchored = "pause"
        if self._play_region is not None:
            events = build_macro_events_tap(raw_snapshot, self._play_region, anchor_host, end_host)
            if events is None:
                logger.warning("tap-anchor: no Play tap found in stream — "
                               "falling back to visual pause anchor")
            else:
                anchored = "Play tap"
        if events is None:
            events = build_macro_events(raw_snapshot, anchor_host, end_host)

        downs = sum(1 for e in events if e.action == "DOWN")
        gap = events[0].dt_ms if events else 0
        # First-event gap = t=0 → first input (spans level loading in tap-anchor mode);
        # replay waits exactly this long after its own t=0.
        logger.info("recorded {} events ({} DOWN / {} UP) for '{}' — t=0 @ {}, first-input gap {} ms",
                    len(events), downs, len(events) - downs, name, anchored, gap)
        return Macro(
            name=name,
            created_at=datetime.now().astimezone().isoformat(),
            screen=Screen(w=self._device.width, h=self._device.height),
            touch_max=TouchMax(x=self._device.touch_max_x, y=self._device.touch_max_y),
            pressure_max=self._device.pressure_max,
            events=events,
        )
