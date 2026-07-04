# CLAUDE.md — CKR Farm Bot

ไฟล์นี้เป็น context ให้ Claude Code อ่านทุก session อ่าน `docs/ckr_farm_bot_spec.md` เป็น source of truth เสมอ ไฟล์นี้สรุป **invariant + วิธีทำงาน** ที่ห้ามพลาด

## โปรเจกต์คืออะไร
บอทฟาร์มเกม CKR บน LDPlayer (1 instance) ผ่าน ADB — **นำทางเมนูด้วย state machine** (จับภาพหน้าจอ → template match → tap) + **เล่นด่านด้วย recorded macro (tap-only)** มี Control Panel (Tkinter) ให้ Start/Stop/Reset/Record + ตั้งจำนวนรอบ + log

## Stack
Python 3.11+ · adbutils · opencv-python + numpy · minitouch (replay) · getevent (record) · tkinter (GUI) · pydantic v2 + PyYAML · loguru · pytest

---

## ⚠️ INVARIANTS — ห้ามละเมิด

1. **Coordinate = pixel identity.** touch device max = 1279×719 = จอ 1280×720 → touch raw coord **เท่ากับ** pixel coord **ห้ามใส่การแปลง scale** ทั้ง macro และ menu tap อยู่ใน pixel space เดียวกัน
2. **minitouch pressure ≤ 2.** device นี้ `ABS_MT_PRESSURE max = 2` → อ่าน max จาก minitouch banner แล้ว clamp **ห้าม hardcode 100/1000** (ตัวอย่างทั่วไปมักผิดตรงนี้)
3. **State machine ไม่ hardcode transition.** ทุก loop จับภาพ → identify state → ทำ action การแตกแขนง (end_round → box/levelup/menu) resolve เองจากหน้าที่โผล่จริง
4. **Macro t=0 = จังหวะกดปุ่ม Play (tap-anchor).** เริ่มนับ macro t=0 ตอน **tap `tpl_play_start`** ทั้ง record (device_ts ของ Play tap ใน getevent stream) และ replay (perf_counter ตอนยิง minitouch) → ไม่มี capture latency ในเส้นทางจับเวลา (จอ ADB screencap ช้า ~350-400ms = jitter ±หลายร้อย ms ถ้า anchor ด้วย visual). ปุ่ม pause (GAMEPLAY) ยังใช้อยู่ แต่เป็น **safety gate เท่านั้น** (ยืนยันด่านโหลดจริงก่อน replay) ไม่ใช้จับเวลา. gap แรกจึงรวมเวลา loading — error ที่เหลือ = loading variance (นิ่งกว่า capture jitter). ห้ามกลับไปใช้ visual pause เป็น t=0 หรือ fixed delay. ถ้าหา Play tap ใน stream ไม่เจอ → fallback visual anchor เดิม
5. **identify ตามลำดับ priority.** popup/หน้าเฉพาะ ก่อน หน้าทั่วไป (ดู §4 ใน spec) — โดยเฉพาะ START_1↔START_3, END_BOX↔END_BOX_OPEN, START_2↔MONEY_POPUP ห้ามสับสน
6. **money.png (เงินไม่พอ) → tap Cancel + เล่นต่อ (skip)** ไม่หยุดบอท — Cancel ไม่เสียเงิน, flow กลับไป START แล้วกด Play ตามปกติ (เดิม stop; เปลี่ยนตามผู้ใช้เพื่อฟาร์มต่อเนื่อง)

---

## Device Profile (ยืนยันจากเครื่องจริง — ใส่ config default ได้)
- ABI: `x86_64` → binary ที่ `vendor/minitouch/x86_64/minitouch` (PIE)
- touch device: `/dev/input/event4`
- ABS_MT_POSITION_X max 1279 · Y max 719 · PRESSURE max 2
- getevent: ค่า position เป็น **hex** (`int(v,16)`), up = `ABS_MT_TRACKING_ID ffffffff`, timestamp = วินาที.ไมโคร

## Coding conventions
- **code/identifier/comment เป็นภาษาอังกฤษ** · type hints เต็มทุก public function · docstring สั้นๆ
- vision functions ต้อง **pure** (รับ Frame คืนผล ไม่ยิง ADB/side effect) เพื่อ unit test ด้วย fixture
- แยก **core (game-agnostic)** ออกจาก **game layer** เด็ดขาด — core ห้ามมีชื่อปุ่ม/หน้าจอของเกม hardcode
- ห้าม `adb shell input tap` สำหรับ macro (ช้า/timing เพี้ยน) — ใช้ minitouch
- config ทุกค่าจาก `config.yaml` (pydantic) ห้าม magic number ในโค้ด

## Paths
```
docs/ckr_farm_bot_spec.md          # SPEC — อ่านก่อนเสมอ
config.yaml
game/assets/tpl_*.png              # 15 templates (มี crops_manifest.json ระบุ source+box)
vendor/minitouch/x86_64/minitouch  # replay binary
tests/fixtures/getevent_sample.txt # parser fixture (มี expected output ในไฟล์)
tests/fixtures/screens/*.png       # 15 หน้าจอจริง (เทส vision/identify)
macros/                            # macro .json (record มา)
logs/
```

---

## Workflow (สำคัญ)
- **ทำทีละ phase ตาม §12 ของ spec แล้วหยุดให้รีวิว** ก่อนไป phase ถัดไป — อย่าทำรวดเดียวยาว
- ลำดับพึ่งพา: Phase 4 (record) + 3 (minitouch) มาก่อน 5 (replay); 6 (engine) ใช้ 2 (vision)
- **ยึด TDD กับส่วนเสี่ยง:** vision/identify และ getevent parser เขียน test (จาก fixtures) ให้ผ่านก่อนต่อ
- ส่วนที่เสี่ยงสุด = macro record/replay timing → ทำ + ทดสอบกับเกมจริงให้ชัวร์ก่อนขยาย
- ถ้า template match ไม่แม่น: ปรับ threshold / จำกัด region ก่อน อย่าเพิ่ง re-crop (asset verify แล้ว)

## Commands
```bash
# ตั้ง environment (เติมให้ตรงตอน Phase 0)
pip install -e ".[dev]"      # หรือ pip install -r requirements.txt
pytest                       # run tests
pytest tests/test_screens.py # เทส vision/identify
python -m ckrbot             # เปิด Control Panel
```

## Do NOT
- ❌ ใส่ coordinate scaling (มันคือ identity)
- ❌ hardcode minitouch pressure > 2
- ❌ hardcode transition ระหว่าง state
- ❌ ใช้ปุ่ม Play เดี่ยวๆ ระบุ MAIN_MENU (START ก็มี Play) — ต้องใช้ marker เฉพาะหน้า
- ❌ commit `macros/`, `logs/`, screenshot ลง git
