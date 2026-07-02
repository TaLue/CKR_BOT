# CKR Farm Bot — Implementation Spec (v1)

> Spec สำหรับให้ Claude Code implement ทีละ phase
> **สรุปงาน:** บอทฟาร์มเกม CKR บน LDPlayer (1 instance, 1280×720) — นำทางเมนูด้วย **state machine** + เล่นด่านด้วย **recorded macro (tap-only)** + เจอ CAPTCHA ให้แก้อัตโนมัติ
> **Input ในเกม:** tap ล้วน | **Dependency เพิ่มได้:** ได้

---

## 0. หลักการหลัก 3 ข้อ

1. **แยกการนำทางเมนู (state machine) ออกจากการเล่นด่าน (macro replay)** เด็ดขาด
2. **State machine ไม่ hardcode transition** — ทุก loop จับภาพ → ระบุหน้าจอปัจจุบัน → ทำ action ของหน้านั้น การแตกแขนง (เช่น end_round → box/levelup/menu) จะ resolve เองตามหน้าจอที่โผล่จริง
3. **Macro เก็บเป็น raw touch coordinate** (getevent ↔ minitouch ใช้ space เดียวกัน) — ไม่แปลงไปกลับ pixel ให้ error สะสม

### ข้อสมมติที่ต้องจริงเพื่อให้ replay ตรง
- **Layout ด่านคงที่ทุกรอบ** (เหตุผลที่ต้องล็อก boost เดิม = Double Coins) — ถ้าด่านสุ่ม replay จะ desync
- Resolution ล็อกที่ **1280×720** ทั้งตอน record และ run

---

## 1. Architecture

```
┌──────────────────── Control Panel (Tkinter, main thread) ────────────────────┐
│  [Start] [Stop] [Reset] [Record]   Macro: [dropdown ▼]   Rounds: [ N ]        │
│  Round count: 12 / 50        Status: RUNNING                                   │
│  ┌── Log ─────────────────────────────────────────────────────────────────┐  │
│  │ 12:03:01 START_3 detected → tap Play                                     │  │
│  │ 12:03:04 gameplay anchor → macro replay start                           │  │
│  │ 12:04:10 CAPTCHA detected → แก้Auto                                      │  │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │  control: threading.Event (stop/pause)
                                │  log: queue.Queue  (thread-safe)
┌───────────────────────────────▼─────────────────────── Engine (worker thread) ─┐
│  loop: capture → identify state → dispatch → (tap | replay macro | pause)       │
│  + round lifecycle / round counter / max_rounds                                 │
└───┬───────────┬────────────┬──────────────┬───────────────┬────────────────────┘
    │           │            │              │               │
┌───▼──┐  ┌─────▼────┐  ┌────▼───┐   ┌──────▼──────┐  ┌──────▼──────┐
│ ADB  │  │ Capture  │  │ Vision │   │ MacroRecord │  │ MacroPlayer │
│      │  │ →numpy   │  │ tpl/px │   │ (getevent)  │  │ (minitouch) │
└──────┘  └──────────┘  └────────┘   └─────────────┘  └─────────────┘
```

Core (adb/capture/vision) ยึดตามดีไซน์เดิมใน `ldplayer_farm_bot_spec.md` — ไฟล์นี้เพิ่มส่วน **Macro** + **Control Panel** + **state machine เฉพาะเกม CKR**

---

## 2. Dependencies

| ด้าน | ใช้ | หมายเหตุ |
|------|-----|---------|
| ADB | `adbutils` | connect/shell/screencap |
| Vision | `opencv-python`, `numpy` | template matching + color |
| OCR (optional) | `pytesseract` | ใช้เท่าที่จำเป็น (เช่น ยืนยัน text) — v1 พยายามใช้ template ก่อน |
| Replay inject | **minitouch** (native binary, push เข้า device) | low-latency, raw-coord — จำเป็นสำหรับ timing เกม runner |
| Record | `adb shell getevent` | ไม่มี dep เพิ่ม |
| GUI | **tkinter** (stdlib) | control panel, ไม่ต้องลงเพิ่ม, worker-thread pattern |
| Config | `pydantic` v2 + `PyYAML` | |
| Log | `loguru` | log ไปทั้งไฟล์และ queue เข้า GUI |

> **ทำไม minitouch:** `adb shell input tap` ช้า ~100–200ms/ครั้ง และ timing ไม่แม่น — เกม runner รับไม่ได้ minitouch เปิด process ค้างไว้ ยิง event ผ่าน socket ระดับ ms และรับ **raw touch coordinate** ตรงกับที่ getevent อ่านมา
> ทางเลือก: maatouch (Android ใหม่กว่า) — เลือกอันใดอันหนึ่ง แต่ v1 spec นี้อ้าง minitouch เป็นหลัก (ทำตาม README ของ minitouch เรื่อง push binary ตาม abi + `adb forward localabstract:minitouch`)

---

## 2.6 Device Profile (ยืนยันจากเครื่องจริงแล้ว)

ค่าจาก `getprop` + `getevent -pl` ของ instance นี้ — hardcode/ใส่ config ได้เลย ไม่ต้อง detect เดา:

| ค่า | ผล |
|-----|-----|
| ABI = **x86_64** | ใช้ minitouch binary ตัว **x86_64** |
| touch device = **`/dev/input/event4`** (name "input") | stream getevent จาก event4 |
| ABS_MT_POSITION_X max = **1279**, Y max = **719** | = **1:1 กับ pixel 1280×720** |
| ABS_MT_PRESSURE max = **2** | ⚠️ pressure ตอน inject ต้อง **≤ 2** (อย่าใส่ 100/1000) |
| up sentinel = `ABS_MT_TRACKING_ID ffffffff` + `BTN_TOUCH UP` | pattern จบ 1 tap |
| ค่า position ใน getevent = **hex** | parser ต้อง `int(v, 16)` |
| timestamp = `[ วินาที.ไมโคร ]` | ใช้คิด dt |

> **⚠️ device อื่น/LDPlayer เครื่องอื่นอาจต่าง** (event path, max ranges, pressure) — โค้ดควรอ่าน `getevent -pl` + minitouch banner ตอน start มายืนยัน ไม่ fix ตายตัวสนิท แต่ค่า default ใส่ตามตารางนี้

## 3. Macro Subsystem (หัวใจ)

### 3.1 Coordinate model — **identity (ยืนยันแล้ว)**
- **touch raw coord = pixel coord** เพราะ device รายงาน max 1279×719 = จอ 1280×720 พอดี → **ไม่มีการแปลง scale ที่ไหนเลย**
- macro tap เก็บเป็น `(dt_ms, x, y)` ใน pixel space; replay ยิงเข้า minitouch ด้วย x,y เดิม
- menu tap (จาก template match) เป็น pixel อยู่แล้ว → ยิง minitouch ตรง ๆ เช่นกัน
- **pressure:** อ่าน max จาก minitouch banner (จะได้ 2) แล้วส่ง `min(1, max_pressure)` หรือ `max_pressure` — **ห้าม hardcode 100**
- metadata เก็บ `screen_w/h`, `touch_max_x/y`, `pressure_max` ไว้ (ถ้าวันหน้า device max ≠ screen ค่อยเปิดโหมด scale `x_px = raw * w / (max_x+1)`)
- ค่า position จาก getevent เป็น **hex** → `int(hexval, 16)`

### 3.2 MacroRecorder — บันทึกจาก getevent

```python
@dataclass
class TapEvent:
    dt_ms: int        # เวลาห่างจาก tap ก่อนหน้า (tap แรก dt=0)
    raw_x: int
    raw_y: int

class MacroRecorder:
    def __init__(self, adb: AdbClient, gameplay_anchor: "Screen", vision, capture): ...
    def record(self) -> "Macro":
        """
        1. เปิด stream `adb shell getevent -lt /dev/input/event4` อ่านทีละบรรทัด (thread)
        2. parse (ค่า hex): TRACKING_ID != ffffffff = down; อ่าน ABS_MT_POSITION_X/Y (int base16);
           PRESSURE อาจมี/ไม่มี (optional); TRACKING_ID ffffffff / BTN_TOUCH UP = up → 1 tap = (down x,y, ts วินาที)
        3. พร้อมกันนั้น capture loop เฝ้า gameplay_anchor
        4. t=0 = tap แรกที่เกิด "หลัง" anchor ถูก detect ครั้งแรก
           - tap ก่อน anchor (นำทางเมนู) → ทิ้ง
        5. dt ระหว่าง tap ใช้ timestamp ของ getevent (แม่นกว่า fps ของ screencap)
        6. หยุดบันทึกเมื่อ detect END_ROUND (จบด่าน)
        """
```

**ข้อกำหนด:**
- Record ควรเป็นรอบที่ "สะอาด" 
- parser ต้องทน device ที่รายงาน tracking id / ไม่มี BTN_TOUCH — รองรับ pattern `ABS_MT_TRACKING_ID = ffffffff` เป็น up

### 3.3 MacroPlayer — replay ผ่าน minitouch

```python
class MinitouchClient:
    def __init__(self, adb: AdbClient) -> None: ...   # push binary, forward socket, อ่าน banner (max x/y/pressure)
    def tap_raw(self, x: int, y: int) -> None: ...     # d 0 x y p / c / u 0 / c
    def close(self) -> None: ...

class MacroPlayer:
    def __init__(self, mt: MinitouchClient, gameplay_anchor, vision, capture) -> None: ...
    def play(self, macro: "Macro", stop_evt, pause_evt) -> None:
        """
        1. รอ gameplay_anchor detect (t=0 sync) — หักล้าง loading time
        2. เดินตาม TapEvent: sleep(dt) ด้วย monotonic + busy-wait ช่วงท้าย (<5ms) → mt.tap_raw()
        3. เคารพ stop_evt/pause_evt ทุก tap
        """
```

**Timing:** ใช้ `time.perf_counter()` คิด target time สะสมจาก t=0 (ไม่ใช่ sleep(dt) สะสม drift) → `sleep(remaining - 2ms)` แล้ว busy-wait ที่เหลือ

### 3.4 Macro file format (JSON)

```json
{
  "name": "escape_from_the_oven_v1",
  "created_at": "2026-07-02T12:00:00+07:00",
  "screen": { "w": 1280, "h": 720 },
  "minitouch_max": { "x": 32767, "y": 32767 },
  "taps": [
    { "dt_ms": 0,   "raw_x": 16000, "raw_y": 12000 },
    { "dt_ms": 850, "raw_x": 3000,  "raw_y": 20000 }
  ]
}
```
เก็บใน `macros/` — Control Panel dropdown เลือกได้

---

## 4. Detection Signals & Priority

ทุกหน้าจอ detect ด้วย **template match ใน region ที่จำกัด** (เร็ว + กัน false positive) — tap โดย match ปุ่มแล้วกดกลาง bbox

**ลำดับ identify (สำคัญ! specific → general):**

```
1. MONEY_POPUP     — มีปุ่ม Cancel เทา (tpl_cancel)                → tap Cancel + STOP
2. START_2         — "Pick desired Boosts!" + Multi-Buy (tpl_multibuy)
3. CAPTCHA         — header "Surprise!" (tpl_captcha_) → แก้Capcha Auto
4. MENU_REWARD     — "Congratulations!" + Confirm เขียว (tpl_congrats)
5. LEVEL_UP        — "Level Up" + Confirm เขียว (tpl_levelup)
6. END_BOX_OPEN    — "Mystery Box" + Confirm ฟ้า (tpl_box_confirm)
7. END_BOX         — "Mystery Box" + Open all ฟ้า (tpl_open_all)
8. END_ROUND       — "Result" + OK เขียว (tpl_result_ok)
9. GAMEPLAY        — pause icon ขวาบน (tpl_pause) / Jump-Slide  → sync anchor
10. START_3        — START marker + "Double Coins" banner (tpl_double_coins)
11. START_1        — START marker (tpl_buy_upgrades) + ไม่เจอ Double Coins
12. MAIN_MENU      — main marker (tpl_pet_cookie_treasure / Party Run) + Play เขียว
(ไม่เข้าเงื่อนไขไหน → UNKNOWN → watchdog)
```

> **หมายเหตุ START vs MAIN_MENU:** ทั้งคู่มีปุ่ม Play เขียวขวาล่างคล้ายกัน จึงต้องแยกด้วย marker เฉพาะหน้า (START = "Buy Upgrades!" panel / MAIN = "Pet Cookie Treasure" bar) **ห้ามใช้ปุ่ม Play เป็นตัวระบุหน้าเดี่ยว ๆ**

---

## 5. State Machine (ครบทุก state)

| State | ตรวจจับ | Action | หมายเหตุ |
|---|---|---|---|
| **MAIN_MENU** | main marker + Play เขียว | tap Play → รอ START_1 | เริ่มรอบใหม่ / ปลายรอบ |
| **START_1** | Buy-Upgrades panel, ไม่มี Double Coins | tap กล่องชมพู `?` (tpl_pink_box) 1 ครั้ง → tap Multi (tpl_multi_icon) | ตามข้อ 2 ของ flow |
| **START_2** | "Pick desired Boosts!" + Multi-Buy | tap Multi-Buy (tpl_multibuy) → รอ | ระบบจะ multi-buy จนได้ Double Coins |
| **START_3** | Double Coins banner เหนือ Play | tap Play → เข้าโหมด replay | จุดส่งต่อให้ MacroPlayer |
| **GAMEPLAY** *(anchor)* | pause icon ขวาบน | — (ใช้เป็น t=0 ของ macro) | ไม่ tap เอง; MacroPlayer คุมต่อ |
| **CAPTCHA** | "Surprise! Find the jumping card!" | **แก้อัตโนมัติ** เมื่อแก้เสร็จ detect END_ROUND/หน้าอื่นที่รู้จัก |
| **END_ROUND** | "Result" + OK เขียว | tap OK | แตกเป็น box/levelup/menu (resolve เอง loop ถัดไป) |
| **END_BOX** | "Mystery Box" + Open all | tap Open all → รอ END_BOX_OPEN | มี 1–2 กล่องก็กด Open all ครั้งเดียว |
| **END_BOX_OPEN** | "Mystery Box" + Confirm ฟ้า | tap Confirm (ครั้งเดียว) | ไป MAIN_MENU หรือ LEVEL_UP |
| **LEVEL_UP** | "Level Up" + Confirm เขียว | tap Confirm → MAIN_MENU | |
| **MENU_REWARD** | "Congratulations!" + Confirm เขียว | tap Confirm | **ซ้อนกันได้** — loop ถัดไปเจ ออีกก็กดอีก |
| **MONEY_POPUP** | ปุ่ม Cancel เทา + Buy ฟ้า | tap Cancel → **STOP bot** + log "เงินไม่พอ" | |
| **UNKNOWN** | ไม่ match อะไรเลย | watchdog: กด BACK / รอ / retry | |

### Flow diagram

```
                    ┌──────────────┐
        ┌──────────▶│  MAIN_MENU   │◀────────────┐
        │           └──────┬───────┘             │
        │              tap Play                   │ (รอ 2-3s เผื่อ reward)
        │           ┌──────▼───────┐              │
        │           │   START_1    │        ┌─────┴──────┐
        │           └──────┬───────┘        │MENU_REWARD │─┐ (ซ้อนได้)
        │      tap box? → tap Multi          └────────────┘ │
        │           ┌──────▼───────┐              ▲         │
        │           │   START_2    │              └─────────┘
        │           └──────┬───────┘
        │          tap Multi-Buy
        │           ┌──────▼───────┐      [Cancel + STOP]
        │           │   START_3    │◀··· MONEY_POPUP (เงินไม่พอ)
        │           └──────┬───────┘
        │             tap Play
        │           ┌──────▼───────┐   (sync t=0)
        │           │  GAMEPLAY    │───▶ MacroPlayer.play()
        │           └──────┬───────┘
        │                  │ จบ macro
        │        ┌─────────┴─────────┐
        │        ▼                   ▼
        │   ┌─────────┐         ┌──────────┐
        │   │ CAPTCHA │  ────▶   END_ROUND 
        │   └─────────┘         └─────┬────┘
        │                             │    
        │                    ┌────────┼──────────┐
        │                    │        │          │
        │                  END_BOX  LEVEL_UP  (→MAIN_MENU)
        │                    │        │
        │              tap Open all  tap Confirm
        │                     ▼       │
        │              END_BOX_OPEN   │
        │              tap Confirm    │
        └─────────────────┴───────────┘
```

---

## 6. Engine Loop & Round Lifecycle

```python
def run(self, stop_evt, pause_evt):
    in_round = False
    while not stop_evt.is_set():
        if pause_evt.is_set(): sleep; continue
        frame = capture.grab()
        screen = identify(frame)          # ตามลำดับ priority ข้อ 4

        if screen is MONEY_POPUP:
            controller.tap_match(cancel); log("เงินไม่พอ → หยุด"); stop_evt.set(); break
        if screen is CAPTCHA:
            log("CAPTCHA!"); continue  
        if screen is START_3:
            controller.tap_match(play); macro_player.play(macro, stop_evt, pause_evt); in_round = True; continue
        if screen is MAIN_MENU and in_round:
            settle()                        # รอ 2-3s เผื่อ MENU_REWARD (ข้อ 7)
            if identify(capture.grab(True)) is MENU_REWARD: continue   # เก็บ reward ก่อน
            round_count += 1; in_round = False
            log(f"จบรอบ {round_count}/{max_rounds}")
            if max_rounds and round_count >= max_rounds:
                log("ครบจำนวนรอบ → หยุด"); stop_evt.set(); break
            continue

        screen.handle(ctx)                  # state อื่น ๆ ทำ action ปกติ
        sleep(poll_interval)
```

**Round counter:** +1 เมื่อกลับเข้า MAIN_MENU สะอาด (ไม่มี reward popup ค้าง) หลังจบด่าน
**max_rounds:** 0 = ไม่จำกัด; >0 = หยุดเมื่อครบ

---

## 7. CAPTCHA Handling 

- detect CAPTCHA → log → auto แก้ capcha

---

## 8. Control Panel UI (Tkinter)

**Widgets:**
- ปุ่ม **Start / Resume** — clear pause_evt / start worker thread
- ปุ่ม **Stop** — set stop_evt (จบ loop, ปิด minitouch)
- ปุ่ม **Reset** — reset round_count = 0
- ปุ่ม **Record** — เข้าโหมด MacroRecorder (ระหว่าง record ปุ่มอื่น disable), จบแล้ว save เป็นไฟล์ใหม่ใน `macros/`
- **Dropdown** เลือก macro (list จาก `macros/*.json`)
- **Spinbox / Entry** `Rounds (N)` — 0 = infinite
- **Label** `Round: x / N` + `Status: IDLE/RUNNING/PAUSED/STOPPED`
- **Text (readonly)** Log — อ่านจาก `queue.Queue` ที่ engine ส่งมา (poll ด้วย `root.after(100, ...)`)

**Threading model:**
- GUI = main thread; Engine = worker thread (`threading.Thread`)
- control ผ่าน `threading.Event`: `stop_evt`, `pause_evt`
- log ผ่าน `queue.Queue` (loguru sink เขียนเข้า queue) → GUI poll แสดงผล
- **ห้าม** แตะ widget จาก worker thread ตรง ๆ (ใช้ queue เท่านั้น)

---

## 9. Config (`config.yaml` + pydantic)

```yaml
device:
  serial: "127.0.0.1:5555"
  width: 1280
  height: 720
  abi: "x86_64"                # minitouch binary ที่ใช้
  touch_device: "/dev/input/event4"
  touch_max_x: 1279            # = width-1 → coord เป็น pixel ตรง ๆ
  touch_max_y: 719             # = height-1
  pressure_max: 2              # ⚠️ minitouch pressure ต้อง ≤ ค่านี้
timing:
  poll_interval_ms: 400
  settle_ms: 2500          # รอ reward popup หลังกลับ main_menu (ข้อ 7 ของ flow)
  tap_delay_ms: 300
  tap_delay_spread_ms: 120
farm:
  max_rounds: 0            # 0 = infinite
  macro_file: "macros/escape_from_the_oven_v1.json"
watchdog:
  unknown_limit: 6
  max_recovery_attempts: 3
paths:
  assets_dir: "game/assets"
  macros_dir: "macros"
  log_dir: "logs"
vision:
  default_threshold: 0.85
```

---

## 10. Assets ที่ต้อง crop (จาก screenshot จริง 1280×720)

| ไฟล์ template | crop จาก | ใช้ระบุ/กด |
|---|---|---|
| `tpl_play_main.png` | main_menu | ปุ่ม Play เขียวขวาล่าง (ใช้เป็นจุด "กด" ของ MAIN_MENU) |
| `tpl_main_marker.png` | main_menu | bar "Pet · Cookie · Treasure" หรือปุ่ม Party Run (ระบุ MAIN_MENU) |
| `tpl_buy_upgrades.png` | start_step_1 | header "Buy Upgrades!" (ระบุว่าเป็นหน้า START) |
| `tpl_double_coins.png` | start_step_3 | banner แดง "Double Coins" (แยก START_3) |
| `tpl_pink_box.png` | start_step_1 | กล่องชมพู `?` ขอบเหลืองในกริดซ้ายล่าง (tap #1 ของ START_1) |
| `tpl_multi_icon.png` | start_step_1 | icon "Multi" ชมพูฝั่งขวา (tap #2 ของ START_1) |
| `tpl_multibuy.png` | start_step_2 | ปุ่ม Multi-Buy เขียว |
| `tpl_cancel.png` | money.png | ปุ่ม Cancel เทา (ระบุ MONEY_POPUP) |
| `tpl_pause.png` | start.png | ปุ่ม pause วงกลมขวาบน (anchor GAMEPLAY) |
| `tpl_captcha_header.png` | capcha_1 | แถบ header ฟ้า "Surprise!" |
| `tpl_result_ok.png` | end_round | ปุ่ม OK เขียว (+ title "Result") |
| `tpl_open_all.png` | end_round_box | ปุ่ม Open all ฟ้า |
| `tpl_box_confirm.png` | end_round_box_open | ปุ่ม Confirm ฟ้า |
| `tpl_levelup_confirm.png` | end_round_level_up | ปุ่ม Confirm เขียว (+ title "Level Up") |
| `tpl_congrats_confirm.png` | main_menu_reward | ปุ่ม Confirm เขียว (+ title "Congratulations!") |

> crop ให้แน่นเฉพาะ element (อย่าติด background เยอะ) และมาจาก screencap res เดียวกับตอนรัน

---

## 11. Project Structure

```
ckrbot/
├── config.yaml
├── macros/                       # macro .json (record มา)
├── src/ckrbot/
│   ├── cli.py                    # หรือ main.py เปิด GUI
│   ├── adb/client.py
│   ├── capture/screen.py
│   ├── vision/{vision.py, template.py}
│   ├── input/{controller.py, humanizer.py, minitouch.py}
│   ├── macro/{recorder.py, player.py, model.py}
│   ├── engine/{engine.py, screen.py, watchdog.py}
│   ├── ui/panel.py               # Tkinter control panel
│   ├── config/models.py
│   └── logging_setup.py
├── game/
│   ├── screens/                  # 1 ไฟล์/1 state (main_menu, start1, start2, start3, gameplay, captcha, end_round, end_box, end_box_open, level_up, menu_reward, money_popup)
│   └── assets/                   # tpl_*.png ตามข้อ 10
└── tests/
    ├── fixtures/                 # screenshot จริงทุกหน้า
    └── test_screens.py
```

---

## 12. Phased Task Breakdown (สำหรับ Claude Code)

- **Phase 0 — Scaffold:** structure, deps, config models, logging (ไฟล์ + queue sink)
- **Phase 1 — ADB + Capture:** `AdbClient`, `ScreenCapture`; verify capture ได้ตอน minimize
- **Phase 2 — Vision:** template match + region + color; `test_screens.py` ด้วย fixtures (assert แต่ละ state match ถูก + ไม่ false positive ข้ามกัน)
- **Phase 3 — minitouch:** push binary ตาม abi, forward socket, `MinitouchClient.tap_raw`; ทดสอบ tap เข้าเกม
- **Phase 4 — Macro record:** `getevent` parser + coordinate map + anchor trim → save JSON; ทดสอบ record 1 รอบ
- **Phase 5 — Macro replay:** `MacroPlayer` + timing scheduler + anchor sync; ทดสอบ replay รอบเดิมให้ตรง
- **Phase 6 — State machine + Engine:** ทุก `Screen` ใน game/screens, `identify` (priority), engine loop, round lifecycle, watchdog, CAPTCHA pause, MONEY_POPUP stop
- **Phase 7 — Control Panel:** Tkinter UI + threading (Event/Queue) + Record flow + macro dropdown + N rounds + log
- **Phase 8 — Tests & polish:** integration test (mock ADB ป้อน fixture sequence) เดินครบ flow, frame dump on UNKNOWN

**ลำดับพึ่งพา:** 4 ต้องมาก่อน 5; 3 ต้องมาก่อน 5 (replay ใช้ minitouch); 6 ใช้ 2

---

## 13. Testing

- **Vision/Screen (unit):** fixtures = 13 รูปที่มี + `start.png` + `money.png` → assert `identify()` คืน state ถูกต้องทุกใบ และ **ไม่สับสน START_1↔START_3, END_BOX↔END_BOX_OPEN, START_2↔MONEY_POPUP**
- **Macro (unit):** ป้อน getevent log ตัวอย่าง → assert parse เป็น TapEvent ถูก (dt, raw x/y); round-trip save/load JSON
- **Engine (integration, mock ADB):** ป้อน sequence ของ frame จำลอง flow เต็ม (รวม branch box/levelup/menu + captcha) → assert action + round_count ถูก
- **Timing (manual):** เทียบ macro replay กับ record จริงในเกมว่าไม่ desync

---

## 14. Acceptance Criteria

- [ ] Record 1 รอบแล้วได้ macro .json ที่ replay ตรง (ตัวละครวิ่งเหมือนตอน record จนจบด่าน)
- [ ] state machine เดินครบ flow ข้อ 1–7 ได้เอง วน ≥ 20 รอบไม่หลุด
- [ ] เจอ CAPTCHA → แก้auto จนกว่าจะไปต่อได้
- [ ] เจอ money.png → Cancel + หยุด + log "เงินไม่พอ"
- [ ] MENU_REWARD ซ้อนหลายอัน → กด Confirm ครบทุกอันก่อนจบรอบ
- [ ] ตั้ง N rounds แล้วหยุดเองเมื่อครบ; Reset counter ได้
- [ ] capture/replay ทำงานตอน LDPlayer minimize
- [ ] START_1 กับ START_3 แยกถูกด้วย "Double Coins"

---

## 15. Risks / Open items

- **minitouch:** abi = **x86_64** (ยืนยันแล้ว) → ใช้ binary x86_64; ⚠️ **pressure max = 2** ต้องส่ง pressure ≤ 2 (อย่าลอกตัวอย่างที่ใส่ 100) — อ่าน banner ยืนยันตอน start
- **`%date%/%time%` locale** ของ batch เดิม — ไม่เกี่ยวกับบอท แต่ตอนตั้งชื่อไฟล์ระวังไว้
- Level layout ต้องคงที่ (ข้อสมมติหลัก) — ถ้าเกม random ต้องเปลี่ยนไป reactive control แทน macro
