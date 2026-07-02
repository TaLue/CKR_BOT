# CKR Farm Bot

Farm bot for the CKR game on LDPlayer (1 instance, 1280×720) via ADB.
Menu navigation uses a **state machine** (screencap → template match → tap);
levels are played back with a **recorded macro (tap-only)**. A Tkinter Control
Panel provides Start/Stop/Reset/Record + round count + log.

- **Spec (source of truth):** `docs/ckr_farm_bot_spec.md`
- **Invariants (must not violate):** `CLAUDE.md`

## Setup

```bash
pip install -e ".[dev]"      # or: pip install -r requirements.txt
```

## Usage

```bash
pytest                       # run tests
python -m ckrbot             # Phase 0: config/logging smoke check (Control Panel = Phase 7)
```

## Status

Built phase-by-phase (spec §12). **Phase 0 (scaffold) complete:** package
structure, pydantic config models with real device-profile defaults, logging
(file + GUI queue sink), packaging.
