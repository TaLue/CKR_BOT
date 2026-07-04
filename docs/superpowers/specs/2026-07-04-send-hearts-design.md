# Send Hearts (Free Life) mode — design

**Date:** 2026-07-04
**Status:** approved (design), pending implementation plan
**Branch:** to be created off `main`

## Goal

Add a second bot mode that sends the daily **free Life ("heart")** to friends in
the Cookie Run Friends list. It scans the visible list for un-sent friends (a green
heart-letter send button), sends to each through the two confirm dialogs, scrolls
the list when none remain in view, and stops at the bottom of the list.

This is a **separate module** from the farm Engine, reusing the game-agnostic core
(capture / template match / minitouch), with its **own Start/Stop** section in the
existing Control Panel. Farm and Send-Hearts never run at the same time.

## Assumptions / scope

- The user has already navigated to the **Friends tab of the Friends list** before
  pressing Start (the mode does NOT navigate menus). Confirmed with the user.
- 1280×720, pixel-identity coordinates (INVARIANT #1) — no scaling.
- No "already sent" (checkmark) template is needed: once a friend is sent, its button
  changes, so `tpl_send_life` simply stops matching that row and the loop moves on.
- Sending a free Life costs the user nothing (it is the game's daily social action).

## Screens (from `source/Sent-Life/`)

| Capture | Screen | Action |
|---|---|---|
| `friend_list.png` | Friends list; each un-sent friend row has a **green heart-letter button** on the right (~x 655, y varies per row). | tap a send button |
| `click_sent.png` | Dialog **"Send X a free Life?"** — gray **Cancel** (left ~485,458), green **Confirm** (right ~793,458). | tap green Confirm (right) |
| `confirm_sent.png` | Dialog **"Message sent!"** — single green **Confirm** (center ~640,458). | tap green Confirm (center) |

After the second Confirm, the list returns and that friend's button becomes a
checkmark (no longer matches `tpl_send_life`).

## Architecture

New game-layer module, mirroring the Engine pattern but much smaller:

```
src/ckrbot/hearts/
    __init__.py
    sender.py        # HeartSender + HeartState enum + pure helpers
```

- `HeartSender.run(stop_evt)` runs on a GUI worker thread (its own stop Event).
- Reuses: `ScreenCapture`, `TemplateStore`, `find_template`, `Controller`
  (humanized taps), and `MinitouchClient` (new `swipe`).
- Its own lightweight screen identify lives in the module (does NOT touch the farm
  `ScreenIdentifier` / `CKR_SCREENS`).
- Core addition (game-agnostic): `MinitouchClient.swipe(x1,y1,x2,y2,duration_ms)` and
  a thin `Controller.swipe(...)` delegating to it — used to scroll the list.

## Templates (crop from the 3 captures → `game/assets/tpl_*.png` + `crops_manifest.json`)

Search each with a **padded region** (do NOT use a region equal to the template size —
that was the Cookie-Relay-Boost bug: zero slide room ⇒ no match when the icon shifts).

| Template | Source | Role |
|---|---|---|
| `tpl_send_life` | friend_list.png (one green heart-letter button) | find un-sent friend in the list column |
| `tpl_life_confirm` | click_sent.png (green Confirm, right) | identify + tap dialog 1 |
| `tpl_sent_confirm` | confirm_sent.png (green Confirm, center) | identify + tap dialog 2 |

The two Confirm buttons share art but are separated by **region** (right vs center),
so region-constrained matching disambiguates the two dialogs without a text marker.

## Loop (one capture per iteration, priority order)

```
sent = 0; scrolls = 0
while not stop:
    frame = capture.grab()
    # 1. finish an in-progress send before starting a new one
    if match(frame, tpl_sent_confirm, sent_confirm_region) >= thr:
        tap(center); sent += 1; sleep(action_delay); continue      # "Message sent!" ack
    if match(frame, tpl_life_confirm, ask_confirm_region) >= thr:
        tap(center); sleep(action_delay); continue                 # "free Life?" confirm
    # 2. send to the best-matching un-sent friend in view (order irrelevant)
    btn = find(frame, tpl_send_life, send_region)
    if btn.confidence >= thr:
        tap(btn.center); sleep(action_delay); continue
    # 3. nothing to send in view -> scroll the list down
    if scrolls >= max_scrolls: log("max scrolls"); break
    before = crop(frame, list_region)
    swipe(swipe_from -> swipe_to, swipe_ms); scrolls += 1; sleep(scroll_settle)
    after = crop(capture.grab(), list_region)
    if mean_abs_diff(before, after) < unchanged_mad:               # list didn't move
        log("reached bottom"); break
log("done: sent {} hearts", sent)
```

- Steps 1–2 before 3 guarantee a started send always completes.
- **Stop** when a scroll leaves the list region visually unchanged (bottom reached),
  or `max_scrolls` is hit (safety).
- On any UNKNOWN frame (no match, list still scrollable) the loop keeps scrolling — it
  does not get stuck.

## GUI

- New **"Send Hearts"** section (label + Start / Stop) below the farm controls in
  `panel.py`, sharing the connected device / minitouch / capture / templates.
- **Mutual exclusion:** while the farm engine is running, Send-Hearts Start is
  disabled, and vice versa (a mode-busy guard on the shared worker state).
- Log lines routed to the existing GUI log panel; a final "sent N hearts" summary.

## Config (`config.yaml` → `HeartsConfig`, pydantic; no magic numbers in code)

```yaml
hearts:
  threshold: 0.85            # match confidence for buttons/confirms
  send_region: [590, 260, 720, 630]      # heart-letter button column
  ask_confirm_region: [660, 405, 910, 512]   # dialog 1 Confirm (right)
  sent_confirm_region: [480, 405, 800, 512]  # dialog 2 Confirm (center)
  list_region: [150, 265, 720, 625]      # rows area, for scroll-end compare
  swipe_from: [430, 560]
  swipe_to: [430, 320]
  swipe_ms: 400
  action_delay_ms: 600       # wait after a tap for the next screen
  scroll_settle_ms: 700      # wait after a swipe before compare/re-scan
  poll_ms: 400
  max_scrolls: 40            # safety cap
  unchanged_mad: 2.0         # mean abs pixel diff below this = list at bottom
```

Regions/coords are first estimates from the captures; refined when cropping templates.

## Testing

Pure/vision tests (no device), fixtures = the 3 captures copied to
`tests/fixtures/screens/`:

- **Detection:** `tpl_send_life` clears threshold in `send_region` on friend_list.png;
  each Confirm clears its region on its dialog; and none cross-match
  (send button not in dialog regions; the two Confirms not in each other's region).
- **Loop:** a fake capture feeds a scripted frame sequence
  (send → ask → sent → list → … → bottom); a fake controller records taps/swipes.
  Assert the tap order, the sent count, and that it stops at "bottom".
- **Scroll-end:** `mean_abs_diff` helper — identical crop ⇒ below `unchanged_mad`;
  shifted crop ⇒ above.

## Out of scope (YAGNI)

- Menu navigation to reach the Friends list (user starts there).
- Reading friend names / selective sending (send to everyone).
- An "already sent" checkmark template (not needed — see Assumptions).
- Receiving/collecting hearts from others.
