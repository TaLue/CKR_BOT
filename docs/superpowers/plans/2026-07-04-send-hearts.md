# Send Hearts (free Life) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Send-Hearts mode that scans the Friends list for un-sent friends, sends each a free Life through the two confirm dialogs, scrolls when none remain, and stops at the bottom — driven from its own Start/Stop section in the Control Panel.

**Architecture:** A new game-layer module `ckrbot/hearts/sender.py` (`HeartSender`) runs a small vision loop on a GUI worker thread, reusing the core `ScreenCapture`, `TemplateStore`, `find_template`, and `Controller`. A generic `MinitouchClient.swipe` (core) scrolls the list. Detection uses 3 new templates matched in config-driven regions.

**Tech Stack:** Python 3.12, opencv-python, numpy, pydantic v2, minitouch, tkinter, pytest, loguru.

## Global Constraints

- **Pixel identity (INVARIANT #1):** touch raw coord == pixel coord. Never scale coordinates.
- **minitouch pressure ≤ device max (INVARIANT #2):** use `self.pressure` (already clamped); never hardcode.
- **No magic numbers in code:** every tunable comes from `config.yaml` via pydantic (`HeartsConfig`).
- **Vision functions stay pure:** take a frame, return a result; no ADB/side effects.
- **Template search region must be larger than the template** (the Cookie-Relay-Boost bug: a region equal to template size has zero slide room and never matches a shifted icon).
- **Code/identifiers/comments in English**, full type hints on public functions, short docstrings.
- Branch: `feature/send-hearts` (already checked out).

---

### Task 1: `MinitouchClient.swipe` (core scroll gesture)

**Files:**
- Modify: `src/ckrbot/input/minitouch.py` (add `swipe` next to `tap_raw`, ~line 197)
- Test: `tests/test_minitouch_swipe.py` (create)

**Interfaces:**
- Consumes: existing `MinitouchClient._send`, `MinitouchClient.pressure`, `MinitouchBanner`.
- Produces: `MinitouchClient.swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300, steps: int = 12) -> None` — presses contact 0 at (x1,y1), moves in `steps` linear increments to (x2,y2), releases.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_minitouch_swipe.py
"""MinitouchClient.swipe emits a down -> moves -> up gesture on contact 0."""
from __future__ import annotations

from ckrbot.input.minitouch import MinitouchBanner, MinitouchClient


class _FakeSock:
    def __init__(self) -> None:
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data


def _client() -> MinitouchClient:
    mt = MinitouchClient(adb=None, binary_path="x")  # no device work in __init__
    mt._sock = _FakeSock()  # inject a fake socket
    mt._banner = MinitouchBanner(version=1, max_contacts=10, max_x=1279, max_y=719, max_pressure=2)
    return mt


def test_swipe_emits_down_moves_up_on_contact_0() -> None:
    mt = _client()
    mt.swipe(400, 560, 400, 320, duration_ms=0, steps=4)
    cmds = mt._sock.sent.decode()
    # starts with a press at the origin (pressure clamped to device max = 2)
    assert cmds.startswith("d 0 400 560 2\nc\n")
    # 4 interpolated moves, ending at the destination
    assert cmds.count("m 0 ") == 4
    assert "m 0 400 320 2\nc\n" in cmds  # final move lands on the target
    # ends by releasing contact 0
    assert cmds.rstrip().endswith("u 0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_minitouch_swipe.py -v`
Expected: FAIL — `AttributeError: 'MinitouchClient' object has no attribute 'swipe'`

- [ ] **Step 3: Write minimal implementation**

Add after `tap_raw` (around line 197) in `src/ckrbot/input/minitouch.py`:

```python
    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 300, steps: int = 12) -> None:
        """Drag contact 0 from (x1,y1) to (x2,y2) over ``duration_ms`` in ``steps``
        linear moves (a scroll gesture). Identity coords; pressure is clamped."""
        p = self.pressure
        self._send(f"d 0 {x1} {y1} {p}\nc\n")
        for i in range(1, steps + 1):
            xi = round(x1 + (x2 - x1) * i / steps)
            yi = round(y1 + (y2 - y1) * i / steps)
            self._send(f"m 0 {xi} {yi} {p}\nc\n")
            if duration_ms:
                time.sleep(duration_ms / 1000.0 / steps)
        self._send(f"u 0\nc\n")
```

(`time` is already imported at the top of the module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_minitouch_swipe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ckrbot/input/minitouch.py tests/test_minitouch_swipe.py
git commit -m "feat(minitouch): add swipe gesture for list scrolling"
```

---

### Task 2: `HeartsConfig` + wire into `AppConfig` + `config.yaml`

**Files:**
- Modify: `src/ckrbot/config/models.py` (add `HeartsConfig`, add field to `AppConfig`)
- Modify: `config.yaml` (add `hearts:` section)
- Test: `tests/test_hearts_config.py` (create)

**Interfaces:**
- Produces: `HeartsConfig` with fields `threshold: float`, `send_region/ask_confirm_region/sent_confirm_region/list_region: tuple[int,int,int,int]`, `swipe_from/swipe_to: tuple[int,int]`, `swipe_ms/action_delay_ms/scroll_settle_ms/poll_ms/max_scrolls: int`, `unchanged_mad: float`. Reachable as `AppConfig().hearts`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hearts_config.py
"""HeartsConfig has sane defaults and is reachable from AppConfig / YAML."""
from __future__ import annotations

from ckrbot.config.models import AppConfig, HeartsConfig


def test_defaults_present_on_appconfig() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.hearts, HeartsConfig)
    assert 0.0 <= cfg.hearts.threshold <= 1.0
    assert len(cfg.hearts.send_region) == 4
    assert len(cfg.hearts.swipe_from) == 2
    assert cfg.hearts.max_scrolls >= 1


def test_yaml_override(tmp_path) -> None:
    from ckrbot.config.models import load_config
    p = tmp_path / "c.yaml"
    p.write_text("hearts:\n  max_scrolls: 3\n  threshold: 0.9\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.hearts.max_scrolls == 3
    assert cfg.hearts.threshold == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_hearts_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'HeartsConfig'`

- [ ] **Step 3: Write minimal implementation**

In `src/ckrbot/config/models.py`, add before `class AppConfig`:

```python
class HeartsConfig(BaseModel):
    """Send-Hearts (free Life) mode. Regions are (x1,y1,x2,y2) in pixel space; the
    user is assumed to be on the Friends list when the mode starts."""

    threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    # Column where the green heart-letter send buttons appear (search all rows).
    send_region: tuple[int, int, int, int] = (585, 265, 725, 630)
    # Green Confirm on the "Send X a free Life?" dialog (right button).
    ask_confirm_region: tuple[int, int, int, int] = (660, 405, 910, 512)
    # Green Confirm on the "Message sent!" dialog (centered button).
    sent_confirm_region: tuple[int, int, int, int] = (480, 405, 800, 512)
    # Rows area, compared before/after a scroll to detect the bottom of the list.
    list_region: tuple[int, int, int, int] = (150, 265, 720, 625)
    swipe_from: tuple[int, int] = (430, 560)
    swipe_to: tuple[int, int] = (430, 320)
    swipe_ms: int = Field(default=400, ge=0)
    action_delay_ms: int = Field(default=600, ge=0)   # wait after a tap for the next screen
    scroll_settle_ms: int = Field(default=700, ge=0)  # wait after a swipe before compare/re-scan
    poll_ms: int = Field(default=400, ge=0)
    max_scrolls: int = Field(default=40, ge=1)        # safety cap on total scrolls
    unchanged_mad: float = Field(default=2.0, ge=0.0)  # list mean-abs-diff below this = at bottom
```

Then add the field to `AppConfig` (after `captcha`):

```python
    captcha: CaptchaConfig = Field(default_factory=CaptchaConfig)
    hearts: HeartsConfig = Field(default_factory=HeartsConfig)
```

Append to `config.yaml`:

```yaml
hearts:
  threshold: 0.85
  send_region: [585, 265, 725, 630]      # heart-letter button column (all rows)
  ask_confirm_region: [660, 405, 910, 512]   # dialog 1 Confirm (right)
  sent_confirm_region: [480, 405, 800, 512]  # dialog 2 Confirm (center)
  list_region: [150, 265, 720, 625]      # rows area, for scroll-end compare
  swipe_from: [430, 560]
  swipe_to: [430, 320]
  swipe_ms: 400
  action_delay_ms: 600
  scroll_settle_ms: 700
  poll_ms: 400
  max_scrolls: 40
  unchanged_mad: 2.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_hearts_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ckrbot/config/models.py config.yaml tests/test_hearts_config.py
git commit -m "feat(config): add HeartsConfig for the Send-Hearts mode"
```

---

### Task 3: Templates + fixtures + detection test

**Files:**
- Create: `game/assets/tpl_send_life.png`, `game/assets/tpl_life_confirm.png`, `game/assets/tpl_sent_confirm.png`
- Modify: `game/assets/crops_manifest.json` (3 entries)
- Create: `tests/fixtures/screens/friend_list.png`, `tests/fixtures/screens/click_sent.png`, `tests/fixtures/screens/confirm_sent.png`
- Test: `tests/test_hearts_screens.py` (create)

**Interfaces:**
- Produces: three loadable templates (`TemplateStore.load("tpl_send_life"|"tpl_life_confirm"|"tpl_sent_confirm")`) and three screen fixtures.

- [ ] **Step 1: Crop the templates and copy fixtures**

Run this one-off script (crop boxes are estimates from the captures; the test in Step 3 verifies separation — adjust boxes and re-run if a cross-match assertion fails):

```python
# scratch: crop hearts templates + copy fixtures
import shutil
from pathlib import Path
import cv2

ROOT = Path("E:/ckrbot")
SRC = ROOT / "source" / "Sent-Life"
ASSETS = ROOT / "game" / "assets"
FIX = ROOT / "tests" / "fixtures" / "screens"

# (source capture, crop box x1,y1,x2,y2, output template name)
CROPS = [
    ("friend_list.png", (600, 296, 710, 350), "tpl_send_life"),
    ("click_sent.png",  (702, 424, 884, 492), "tpl_life_confirm"),
    ("confirm_sent.png",(516, 424, 764, 492), "tpl_sent_confirm"),
]
for src, (x1, y1, x2, y2), name in CROPS:
    img = cv2.imread(str(SRC / src), cv2.IMREAD_COLOR)
    cv2.imwrite(str(ASSETS / f"{name}.png"), img[y1:y2, x1:x2])

for src in ("friend_list.png", "click_sent.png", "confirm_sent.png"):
    shutil.copy2(SRC / src, FIX / src)
print("done")
```

- [ ] **Step 2: Add manifest entries**

Add to `game/assets/crops_manifest.json` (records the source box; the runtime search region comes from `HeartsConfig`):

```json
  "tpl_send_life": { "source": "friend_list.png", "box": [600, 296, 710, 350], "method": "color-blob" },
  "tpl_life_confirm": { "source": "click_sent.png", "box": [702, 424, 884, 492], "method": "color-blob" },
  "tpl_sent_confirm": { "source": "confirm_sent.png", "box": [516, 424, 764, 492], "method": "color-blob" }
```

- [ ] **Step 3: Write the detection test**

```python
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
```

- [ ] **Step 4: Run the test**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_hearts_screens.py -v`
Expected: PASS. If a `test_no_cross_matches` assertion fails, tighten the offending region in `HeartsConfig`/`config.yaml` (and this test's defaults) so the wrong button falls outside it, or re-crop; re-run.

- [ ] **Step 5: Commit**

```bash
git add game/assets/tpl_send_life.png game/assets/tpl_life_confirm.png game/assets/tpl_sent_confirm.png game/assets/crops_manifest.json tests/fixtures/screens/friend_list.png tests/fixtures/screens/click_sent.png tests/fixtures/screens/confirm_sent.png tests/test_hearts_screens.py
git commit -m "feat(assets): add Send-Hearts templates + friend-list fixtures"
```

---

### Task 4: `HeartSender` module + `Controller.swipe`

**Files:**
- Create: `src/ckrbot/hearts/__init__.py` (empty)
- Create: `src/ckrbot/hearts/sender.py`
- Modify: `src/ckrbot/input/controller.py` (add `swipe`)
- Test: `tests/test_hearts_sender.py` (create)

**Interfaces:**
- Consumes: `MinitouchClient.swipe` (Task 1), `HeartsConfig` (Task 2), templates (Task 3), `ScreenCapture.grab`, `Controller.tap_point`, `find_template`.
- Produces:
  - `Controller.swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None`
  - `ckrbot.hearts.sender.region_mad(a, b) -> float`
  - `HeartSender(capture, controller, templates, cfg: HeartsConfig, sleep=time.sleep)` with `run(stop_evt) -> int` (returns hearts sent).

- [ ] **Step 1: Add `Controller.swipe`**

In `src/ckrbot/input/controller.py`, add a method to `Controller` (after `tap_template_at`):

```python
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """Scroll/drag from (x1,y1) to (x2,y2) via minitouch (e.g. scroll a list)."""
        self._mt.swipe(x1, y1, x2, y2, duration_ms)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_hearts_sender.py
"""HeartSender loop: sends via both dialogs on real fixtures, then scrolls and
stops when the list no longer moves."""
from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from ckrbot.config.models import HeartsConfig
from ckrbot.hearts.sender import HeartSender, region_mad
from ckrbot.vision.template import TemplateStore

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "screens"
_store = TemplateStore(str(_ROOT / "game" / "assets"))


def _frame(name: str):
    return cv2.imread(str(_FIX / name), cv2.IMREAD_COLOR)


class _FakeCapture:
    def __init__(self, frames) -> None:
        self._frames = list(frames)
        self.i = 0

    def grab(self, force: bool = False):
        f = self._frames[min(self.i, len(self._frames) - 1)]
        self.i += 1
        return f


class _FakeController:
    def __init__(self) -> None:
        self.actions = []

    def tap_point(self, x: int, y: int, settle_ms=None) -> None:
        self.actions.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration_ms=300) -> None:
        self.actions.append(("swipe",))


def _in(region, x, y) -> bool:
    x1, y1, x2, y2 = region
    return x1 <= x <= x2 and y1 <= y <= y2


def test_region_mad_zero_for_identical_and_positive_for_shifted() -> None:
    a = _frame("friend_list.png")[265:625, 150:720]
    assert region_mad(a, a.copy()) == 0.0
    shifted = np.zeros_like(a)
    shifted[8:, 8:] = a[:-8, :-8]
    assert region_mad(a, shifted) > 1.0


def test_sends_one_heart_then_scrolls_to_bottom() -> None:
    black = np.zeros((720, 1280, 3), dtype=np.uint8)
    # grabs: list -> dialog1 -> dialog2 -> black(before) -> black(after)
    frames = [_frame("friend_list.png"), _frame("click_sent.png"),
              _frame("confirm_sent.png"), black, black]
    cap = _FakeCapture(frames)
    ctrl = _FakeController()
    cfg = HeartsConfig()
    sender = HeartSender(cap, ctrl, _store, cfg, sleep=lambda s: None)

    sent = sender.run(threading.Event())

    assert sent == 1
    taps = [a for a in ctrl.actions if a[0] == "tap"]
    assert len(taps) == 3
    assert _in(cfg.send_region, taps[0][1], taps[0][2])          # tapped a send button
    assert _in(cfg.ask_confirm_region, taps[1][1], taps[1][2])   # confirmed "free Life?"
    assert _in(cfg.sent_confirm_region, taps[2][1], taps[2][2])  # acked "Message sent!"
    assert ("swipe",) in ctrl.actions                            # scrolled when none left
    assert ctrl.actions[-1] == ("swipe",)                        # stopped right after the bottom scroll
```

- [ ] **Step 3: Run test to verify it fails**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_hearts_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ckrbot.hearts'`

- [ ] **Step 4: Write the implementation**

Create `src/ckrbot/hearts/__init__.py` (empty file).

Create `src/ckrbot/hearts/sender.py`:

```python
"""Send-Hearts mode — send the daily free Life to friends in the Friends list.

Assumes the Friends list is already on screen (the mode does not navigate menus).
A small vision loop: finish any open confirm dialog, else tap an un-sent friend's
green heart-letter button, else scroll; stop when a scroll leaves the list
unchanged (bottom reached). Pure helpers are unit-testable from fixtures.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from ckrbot.capture.screen import Frame
from ckrbot.config.models import HeartsConfig
from ckrbot.vision.vision import Region, find_template


def region_mad(a: Frame, b: Frame) -> float:
    """Mean absolute per-pixel difference of two crops (255.0 if shapes differ)."""
    if a.shape != b.shape:
        return 255.0
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _crop(frame: Frame, region: Region) -> Frame:
    x1, y1, x2, y2 = region
    return frame[y1:y2, x1:x2]


class HeartSender:
    """Sends a free Life to every un-sent friend in the list, scrolling as needed."""

    def __init__(self, capture, controller, templates, cfg: HeartsConfig,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._capture = capture
        self._controller = controller
        self._cfg = cfg
        self._sleep = sleep
        self._send_img = templates.load("tpl_send_life").image
        self._ask_img = templates.load("tpl_life_confirm").image
        self._sent_img = templates.load("tpl_sent_confirm").image

    def run(self, stop_evt) -> int:
        """Loop until the bottom of the list (or stop_evt). Returns hearts sent."""
        cfg = self._cfg
        thr = cfg.threshold
        sent = 0
        scrolls = 0
        logger.info("Send Hearts: started")
        while not stop_evt.is_set():
            frame = self._capture.grab()

            # 1. Finish an in-progress send before starting a new one.
            r = find_template(frame, self._sent_img, tuple(cfg.sent_confirm_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                sent += 1
                logger.info("Send Hearts: sent #{}", sent)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue
            r = find_template(frame, self._ask_img, tuple(cfg.ask_confirm_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue

            # 2. Send to any un-sent friend currently in view (order irrelevant).
            r = find_template(frame, self._send_img, tuple(cfg.send_region))
            if r.confidence >= thr:
                self._controller.tap_point(*r.center)
                self._sleep(cfg.action_delay_ms / 1000.0)
                continue

            # 3. Nothing to send in view — scroll the list down.
            if scrolls >= cfg.max_scrolls:
                logger.warning("Send Hearts: hit max_scrolls={} — stopping", cfg.max_scrolls)
                break
            before = _crop(frame, tuple(cfg.list_region)).copy()
            self._controller.swipe(cfg.swipe_from[0], cfg.swipe_from[1],
                                   cfg.swipe_to[0], cfg.swipe_to[1], cfg.swipe_ms)
            scrolls += 1
            self._sleep(cfg.scroll_settle_ms / 1000.0)
            after = _crop(self._capture.grab(), tuple(cfg.list_region))
            if region_mad(before, after) < cfg.unchanged_mad:
                logger.info("Send Hearts: reached the bottom of the list")
                break

        logger.info("Send Hearts: done — sent {} hearts ({} scrolls)", sent, scrolls)
        return sent
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest tests/test_hearts_sender.py -v`
Expected: PASS (both tests). If `test_sends_one_heart_then_scrolls_to_bottom` fails on a tap-region assertion, the template regions need the same adjustment as Task 3 — reconcile and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/ckrbot/hearts/__init__.py src/ckrbot/hearts/sender.py src/ckrbot/input/controller.py tests/test_hearts_sender.py
git commit -m "feat(hearts): HeartSender loop + Controller.swipe"
```

---

### Task 5: Control Panel "Send Hearts" section

**Files:**
- Modify: `src/ckrbot/ui/panel.py` (add widgets, `_on_hearts_start`, `_on_hearts_stop`, `_run_hearts`; include the two buttons in `_set_buttons_recording`)
- Test: manual (GUI) — no unit test; verified against the live game in the final step.

**Interfaces:**
- Consumes: `HeartSender` (Task 4), `Controller.swipe`, shared `self._stop_evt` / `self._worker` / `self._ensure_adb` / `self._mt`.
- Produces: two buttons wired to start/stop a Send-Hearts worker, mutually exclusive with the farm (both reuse `self._worker` + `self._stop_evt`, and `_running()` already blocks a second run).

- [ ] **Step 1: Add the buttons**

In `_build_widgets`, after the toggles rows (after line ~167, before the `status` frame), add:

```python
        # Row 3: Send Hearts (free Life) — its own Start/Stop, mutually exclusive
        # with the farm (both share the worker; _running() blocks a second run).
        ttk.Label(opts, text="Send Hearts:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self._hearts_start_btn = ttk.Button(opts, text="Start", width=8,
                                            command=self._on_hearts_start)
        self._hearts_start_btn.grid(row=3, column=1, sticky="w", padx=4, pady=(6, 0))
        self._hearts_stop_btn = ttk.Button(opts, text="Stop", width=8,
                                           command=self._on_hearts_stop)
        self._hearts_stop_btn.grid(row=3, column=2, sticky="w", padx=2, pady=(6, 0))
```

- [ ] **Step 2: Add the handlers + worker**

Add these methods to `ControlPanel` (near `_on_start`/`_on_stop`):

```python
    def _on_hearts_start(self) -> None:
        if self._recording or self._running():
            logger.info("cannot start Send Hearts while another run is active")
            return
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._worker = threading.Thread(target=self._run_hearts, daemon=True)
        self._worker.start()
        logger.info("Send Hearts: start (be on the Friends list)")

    def _on_hearts_stop(self) -> None:
        if self._running():
            logger.info("Send Hearts: stopping...")
            self._stop_evt.set()

    def _run_hearts(self) -> None:
        from ckrbot.capture.screen import ScreenCapture
        from ckrbot.hearts.sender import HeartSender
        from ckrbot.input.controller import Controller
        from ckrbot.input.minitouch import MinitouchClient
        from ckrbot.vision.template import TemplateStore

        cfg = self._cfg
        try:
            adb = self._ensure_adb()
            capture = ScreenCapture(adb, cfg.device.width, cfg.device.height)
            templates = TemplateStore(cfg.paths.assets_dir)
            self._mt = MinitouchClient(adb, _minitouch_binary(cfg))
            self._mt.start()
            controller = Controller(self._mt, templates,
                                    threshold=cfg.vision.tap_threshold,
                                    tap_delay_ms=cfg.timing.tap_delay_ms,
                                    tap_delay_spread_ms=cfg.timing.tap_delay_spread_ms)
            sender = HeartSender(capture, controller, templates, cfg.hearts)
            sender.run(self._stop_evt)
        except Exception as err:  # noqa: BLE001 - surface any failure to the log pane
            logger.exception("send hearts error: {}", err)
        finally:
            if self._mt is not None:
                try:
                    self._mt.close()
                except Exception:  # noqa: BLE001
                    pass
                self._mt = None
            logger.info("send hearts stopped")
```

- [ ] **Step 3: Disable the buttons during recording**

In `_set_buttons_recording`, add the two hearts buttons to the disabled set:

```python
        for btn in (self._start_btn, self._pause_btn, self._stop_btn, self._reset_btn,
                    self._rename_btn, self._delete_btn, self._connect_btn, self._active_btn,
                    self._hearts_start_btn, self._hearts_stop_btn):
            btn.configure(state=state)
```

- [ ] **Step 4: Sanity-run the whole suite + import the GUI module**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -m pytest -q`
Expected: PASS (all tests).

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" -c "import ckrbot.ui.panel"`
Expected: no error (module imports cleanly).

- [ ] **Step 5: Commit**

```bash
git add src/ckrbot/ui/panel.py
git commit -m "feat(gui): add Send Hearts Start/Stop section"
```

---

### Task 6: Build EXE + live verification

**Files:** none (build + manual check).

- [ ] **Step 1: Build the EXE**

Run: `& "E:\ckrbot\.venv\Scripts\python.exe" E:\ckrbot\build_exe.py`
Expected: `Built: E:\ckrbot\dist\ckrbot` (macros preserved by the stash/restore step).

- [ ] **Step 2: Live check (user)**

Open `dist\ckrbot\ckrbot.exe`, go to the Friends list in the game, press **Send Hearts → Start**. Expect the log to show `Send Hearts: sent #N` lines and the mode to scroll and stop at the bottom (`reached the bottom of the list`). If a button is missed or mis-tapped, capture a frame and adjust the matching region / template (same approach as the boost fix), then rebuild.

- [ ] **Step 3: Merge + push (when the user approves)**

```bash
git checkout main && git merge --no-ff feature/send-hearts -m "Merge feature/send-hearts: Send Hearts (free Life) mode"
cmd /c "git push origin main"
```

---

## Self-review notes

- **Spec coverage:** module (T4), swipe (T1), 3 templates (T3), HeartsConfig (T2), GUI Start/Stop + mutual exclusion (T5), tests with the 3 captures (T3/T4), build (T6). All spec sections covered.
- **Type consistency:** `region_mad`, `HeartSender.run(stop_evt) -> int`, `Controller.swipe`, `MinitouchClient.swipe` signatures match across tasks; regions are `tuple[int,int,int,int]` everywhere (passed to `find_template` via `tuple(...)`).
- **Regions are estimates.** The Task 3 cross-match test and the Task 4 region asserts are the guard rails; the Task 6 live check is the final validation (regions/threshold may need one tuning pass against the real game, exactly like the Cookie-Relay-Boost fix).
