"""Tkinter Control Panel (Phase 7, spec §8).

GUI runs on the main thread; the engine runs on a worker thread. Control flows
through ``threading.Event`` (stop/pause); logs flow through a ``queue.Queue`` that
the GUI polls with ``root.after`` — the worker thread NEVER touches widgets.
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from tkinter import messagebox, simpledialog

from loguru import logger

from ckrbot.config.models import AppConfig
from ckrbot.paths import resolve_path


def _minitouch_binary(config: AppConfig) -> str:
    return str(resolve_path(Path("vendor") / "minitouch" / config.device.abi / "minitouch"))


class ControlPanel:
    """The Start/Pause/Stop/Reset/Record panel."""

    def __init__(self, root: tk.Tk, config: AppConfig, log_queue: "queue.Queue[str]") -> None:
        self._root = root
        self._cfg = config
        self._log_queue = log_queue

        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._worker: threading.Thread | None = None
        self._engine = None  # ckrbot.engine.engine.Engine
        self._adb = None  # shared AdbClient
        self._mt = None  # MinitouchClient (owned by a run)
        self._recording = False
        self._was_recording = False
        self._active_names: list[str] = []   # macro filenames in the random pool
        self._run_macro_files: list[str] = []  # macros for the current run (set on Start)

        # Remembered GUI settings (macro/rounds/start delay), stored next to the exe
        # so they survive restarts — see ckrbot.paths (works as a frozen EXE too).
        self._settings_path = resolve_path("ui_settings.json")
        self._load_settings()

        self._build_widgets()
        self._refresh_macros()
        self._poll()

    # --- persisted GUI settings --------------------------------------------
    def _load_settings(self) -> None:
        """Apply the last-used macro/rounds/delay onto config before widgets build."""
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data.get("max_rounds"), int):
            self._cfg.farm.max_rounds = data["max_rounds"]
        if isinstance(data.get("start_delay_ms"), int):
            self._cfg.timing.replay_start_delay_ms = data["start_delay_ms"]
        if isinstance(data.get("tap_boost"), bool):
            self._cfg.farm.tap_boost = data["tap_boost"]
        if isinstance(data.get("randomize_double_coins"), bool):
            self._cfg.farm.randomize_double_coins = data["randomize_double_coins"]
        name = data.get("macro_name")
        if name:  # stored as a bare filename so it survives the app moving folders
            candidate = Path(self._cfg.paths.macros_dir) / name
            if candidate.exists():
                self._cfg.farm.macro_file = str(candidate)
        dev = data.get("device")
        if isinstance(dev, dict):  # detected device profile from a previous Connect LD
            for key in ("serial", "abi", "touch_device", "touch_max_x", "touch_max_y",
                        "pressure_max"):
                if dev.get(key) is not None:
                    setattr(self._cfg.device, key, dev[key])
        active = data.get("active_macros")
        if isinstance(active, list):
            self._active_names = [str(n) for n in active]

    def _save_settings(self) -> None:
        d = self._cfg.device
        data = {
            "macro_name": Path(self._macro_var.get()).name if self._macro_var.get() else "",
            "max_rounds": self._safe_int(self._rounds_var.get(), 0),
            "start_delay_ms": self._safe_int(self._delay_var.get(), 0),
            "tap_boost": bool(self._boost_var.get()),
            "randomize_double_coins": bool(self._rdc_var.get()),
            "device": {
                "serial": d.serial, "abi": d.abi, "touch_device": d.touch_device,
                "touch_max_x": d.touch_max_x, "touch_max_y": d.touch_max_y,
                "pressure_max": d.pressure_max,
            },
            "active_macros": self._active_names,
        }
        try:
            self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as err:
            logger.warning("could not save settings: {}", err)

    @staticmethod
    def _safe_int(text: str, default: int) -> int:
        try:
            return int(text)
        except (TypeError, ValueError):
            return default

    # --- widgets ------------------------------------------------------------
    def _build_widgets(self) -> None:
        self._root.title("CKR Farm Bot")
        self._root.geometry("720x480")

        top = ttk.Frame(self._root, padding=8)
        top.pack(fill="x")

        self._start_btn = ttk.Button(top, text="Start", command=self._on_start)
        self._pause_btn = ttk.Button(top, text="Pause", command=self._on_pause)
        self._stop_btn = ttk.Button(top, text="Stop", command=self._on_stop)
        self._reset_btn = ttk.Button(top, text="Reset", command=self._on_reset)
        self._record_btn = ttk.Button(top, text="Record", command=self._on_record)
        self._connect_btn = ttk.Button(top, text="Connect LD", command=self._on_connect)
        for i, btn in enumerate((self._start_btn, self._pause_btn, self._stop_btn,
                                 self._reset_btn, self._record_btn, self._connect_btn)):
            btn.grid(row=0, column=i, padx=3)

        opts = ttk.Frame(self._root, padding=(8, 0))
        opts.pack(fill="x")
        # Row 0: macro selection + management + active pool
        ttk.Label(opts, text="Macro:").grid(row=0, column=0, sticky="w")
        self._macro_var = tk.StringVar()
        self._macro_combo = ttk.Combobox(opts, textvariable=self._macro_var, width=30,
                                          state="readonly")
        self._macro_combo.grid(row=0, column=1, padx=(4, 4))
        self._rename_btn = ttk.Button(opts, text="Rename", width=8, command=self._on_rename)
        self._rename_btn.grid(row=0, column=2, padx=2)
        self._delete_btn = ttk.Button(opts, text="Delete", width=8, command=self._on_delete)
        self._delete_btn.grid(row=0, column=3, padx=2)
        self._active_btn = ttk.Button(opts, text="Active...", width=8,
                                      command=self._open_active_dialog)
        self._active_btn.grid(row=0, column=4, padx=2)
        self._active_var = tk.StringVar(value="pool: 0")
        ttk.Label(opts, textvariable=self._active_var).grid(row=0, column=5, padx=6, sticky="w")

        # Row 1: rounds + start delay
        ttk.Label(opts, text="Rounds (0=∞):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._rounds_var = tk.StringVar(value=str(self._cfg.farm.max_rounds))
        ttk.Spinbox(opts, from_=0, to=99999, width=7, textvariable=self._rounds_var).grid(
            row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(opts, text="Start delay (ms):").grid(row=1, column=2, columnspan=2,
                                                       sticky="e", pady=(4, 0))
        self._delay_var = tk.StringVar(value=str(self._cfg.timing.replay_start_delay_ms))
        ttk.Spinbox(opts, from_=-2000, to=5000, increment=50, width=7,
                    textvariable=self._delay_var).grid(row=1, column=4, sticky="w", padx=4,
                                                       pady=(4, 0))
        # Row 2: behavior toggles
        self._boost_var = tk.BooleanVar(value=self._cfg.farm.tap_boost)
        ttk.Checkbutton(opts, text="Tap cookie relay", variable=self._boost_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._rdc_var = tk.BooleanVar(value=self._cfg.farm.randomize_double_coins)
        ttk.Checkbutton(opts, text="Randomize Double Coins", variable=self._rdc_var).grid(
            row=2, column=2, columnspan=4, sticky="w", pady=(4, 0))

        # Row 3: Send Hearts (free Life) — its own Start/Stop, mutually exclusive
        # with the farm (both share the worker; _running() blocks a second run).
        ttk.Label(opts, text="Send Hearts:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self._hearts_start_btn = ttk.Button(opts, text="Start", width=8,
                                            command=self._on_hearts_start)
        self._hearts_start_btn.grid(row=3, column=1, sticky="w", padx=4, pady=(6, 0))
        self._hearts_stop_btn = ttk.Button(opts, text="Stop", width=8,
                                           command=self._on_hearts_stop)
        self._hearts_stop_btn.grid(row=3, column=2, sticky="w", padx=2, pady=(6, 0))

        status = ttk.Frame(self._root, padding=8)
        status.pack(fill="x")
        self._round_var = tk.StringVar(value="Round: 0 / -")
        self._status_var = tk.StringVar(value="Status: IDLE")
        ttk.Label(status, textvariable=self._round_var).pack(side="left")
        ttk.Label(status, textvariable=self._status_var).pack(side="right")

        logframe = ttk.Frame(self._root, padding=(8, 0, 8, 8))
        logframe.pack(fill="both", expand=True)
        self._log = tk.Text(logframe, height=18, wrap="none", state="disabled",
                            bg="#111", fg="#ddd", font=("Consolas", 9))
        scroll = ttk.Scrollbar(logframe, command=self._log.yview)
        self._log.configure(yscrollcommand=scroll.set)
        self._log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _macros_dir(self) -> Path:
        return Path(self._cfg.paths.macros_dir)

    def _selected_path(self) -> Path | None:
        """Full path of the macro selected in the dropdown (shows only the name)."""
        name = self._macro_var.get()
        return (self._macros_dir() / name) if name else None

    def _refresh_macros(self) -> None:
        names = sorted(p.name for p in self._macros_dir().glob("*.json"))
        self._macro_combo["values"] = names  # show file names, not full paths
        if self._macro_var.get() not in names:  # stale (deleted/renamed) or unset
            default = Path(self._cfg.farm.macro_file).name
            self._macro_var.set(default if default in names else (names[0] if names else ""))
        # Drop any active-pool entries whose files no longer exist.
        self._active_names = [n for n in self._active_names if n in names]
        self._update_active_label()

    # --- button actions -----------------------------------------------------
    def _running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _on_start(self) -> None:
        if self._recording:
            return
        if self._running():
            if self._pause_evt.is_set():  # Resume
                self._pause_evt.clear()
                logger.info("resumed")
            return
        max_rounds = max(0, self._safe_int(self._rounds_var.get(), 0))
        start_delay = self._safe_int(self._delay_var.get(), 0)
        sel = self._selected_path()
        macro_file = str(sel) if sel is not None else self._cfg.farm.macro_file
        self._cfg.farm.max_rounds = max_rounds
        self._cfg.farm.macro_file = macro_file
        self._cfg.farm.tap_boost = bool(self._boost_var.get())
        self._cfg.farm.randomize_double_coins = bool(self._rdc_var.get())
        self._cfg.timing.replay_start_delay_ms = start_delay

        self._run_macro_files = self._active_macro_files()  # resolved on main thread
        if not self._run_macro_files:
            logger.error("no macro to play — pick one in the dropdown or set an Active pool")
            return
        self._save_settings()  # remember these for next launch

        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._worker = threading.Thread(target=self._run_engine, daemon=True)
        self._worker.start()
        logger.info("start: macro={} rounds={}", macro_file, max_rounds or "∞")

    def _on_pause(self) -> None:
        if self._running() and not self._pause_evt.is_set():
            self._pause_evt.set()
            logger.info("paused")

    def _on_stop(self) -> None:
        if self._running():
            logger.info("stopping...")
            self._stop_evt.set()

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

    def _on_reset(self) -> None:
        if self._engine is not None:
            self._engine.reset()
        else:
            logger.info("reset (round counter cleared)")

    def _on_record(self) -> None:
        if self._running() or self._recording:
            return
        self._recording = True
        self._set_buttons_recording(True)
        threading.Thread(target=self._run_record, daemon=True).start()

    def _on_delete(self) -> None:
        if self._running() or self._recording:
            return
        path = self._selected_path()
        if path is None:
            return
        name = path.name
        if not messagebox.askyesno("Delete macro", f"Delete this macro?\n\n{name}"):
            return
        try:
            path.unlink()
            logger.info("deleted macro: {}", name)
        except OSError as err:
            messagebox.showerror("Delete failed", str(err))
        self._macro_var.set("")
        self._refresh_macros()

    def _on_rename(self) -> None:
        if self._running() or self._recording:
            return
        old = self._selected_path()
        if old is None:
            return
        new_stem = simpledialog.askstring("Rename macro", "New name:", initialvalue=old.stem)
        if not new_stem or not new_stem.strip():
            return
        new_path = old.with_name(new_stem.strip() + ".json")
        if new_path == old:
            return
        if new_path.exists():
            messagebox.showerror("Rename", "A macro with that name already exists.")
            return
        try:
            from ckrbot.macro.model import Macro
            macro = Macro.load(old)          # keep the JSON's internal name in sync
            macro.name = new_path.stem
            macro.save(new_path)
            old.unlink()
            logger.info("renamed macro: {} -> {}", old.name, new_path.name)
            self._macro_var.set(new_path.name)
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("Rename failed", str(err))
        self._refresh_macros()

    def _update_active_label(self) -> None:
        self._active_var.set(f"pool: {len(self._active_names)}")

    def _active_macro_files(self) -> list[str]:
        """Files for the current run: the active pool, else the dropdown selection."""
        mdir = self._macros_dir()
        files = [str(mdir / n) for n in self._active_names if (mdir / n).exists()]
        if files:
            return files
        sel = self._selected_path()
        if sel is not None and sel.exists():
            return [str(sel)]
        if Path(self._cfg.farm.macro_file).exists():
            return [self._cfg.farm.macro_file]
        return []

    def _open_active_dialog(self) -> None:
        """Pick which macros go in the random pool (played randomly each round)."""
        if self._running() or self._recording:
            return
        macros = sorted(p.name for p in Path(self._cfg.paths.macros_dir).glob("*.json"))
        dlg = tk.Toplevel(self._root)
        dlg.title("Active macros — randomized each round")
        dlg.transient(self._root)
        dlg.grab_set()
        ttk.Label(dlg, justify="left", padding=10,
                  text="Select macros to randomize each round.\n"
                       "(none selected = just use the Macro dropdown)").pack(anchor="w")
        body = ttk.Frame(dlg, padding=(10, 0))
        body.pack(fill="both", expand=True)
        lb = tk.Listbox(body, selectmode=tk.MULTIPLE, width=46,
                        height=min(14, max(3, len(macros))), activestyle="none")
        scroll = ttk.Scrollbar(body, command=lb.yview)
        lb.configure(yscrollcommand=scroll.set)
        lb.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for name in macros:
            lb.insert(tk.END, name)
        for i, name in enumerate(macros):
            if name in self._active_names:
                lb.selection_set(i)

        btns = ttk.Frame(dlg, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Select all",
                   command=lambda: lb.selection_set(0, tk.END)).pack(side="left")
        ttk.Button(btns, text="Clear",
                   command=lambda: lb.selection_clear(0, tk.END)).pack(side="left", padx=4)

        def _ok() -> None:
            self._active_names = [macros[i] for i in lb.curselection()]
            self._save_settings()
            self._update_active_label()
            dlg.destroy()

        ttk.Button(btns, text="OK", command=_ok).pack(side="right")

    def _on_connect(self) -> None:
        """Detect the device profile (abi + touch geometry) and apply it — lets the
        bot adapt when moved to another machine/LDPlayer."""
        if self._running() or self._recording:
            return
        threading.Thread(target=self._run_connect, daemon=True).start()

    def _run_connect(self) -> None:
        import adbutils

        from ckrbot.adb.client import AdbClient
        from ckrbot.adb.probe import detect_profile

        try:
            client = adbutils.AdbClient()
            serial = self._cfg.device.serial
            if ":" in serial:  # network serial (LDPlayer) — connect so it lists
                try:
                    client.connect(serial, timeout=3.0)
                except Exception:  # noqa: BLE001
                    pass
            available = [d.serial for d in client.list() if d.state == "device"]
            if not available:
                logger.error("Connect LD: no device found — open LDPlayer and enable "
                             "ADB debugging (check the serial/port in config.yaml)")
                return
            use = serial if serial in available else available[0]
            adb = AdbClient(use)
            adb.connect()
            prof = detect_profile(adb)

            d = self._cfg.device
            if prof["abi"]:
                d.abi = prof["abi"]
            d.serial = prof["serial"]
            d.touch_device = prof["touch_device"]
            d.touch_max_x = prof["touch_max_x"]
            d.touch_max_y = prof["touch_max_y"]
            if prof["pressure_max"] is not None:
                d.pressure_max = prof["pressure_max"]
            self._adb = adb  # reuse this connection for the next run
            self._save_settings()  # persist the detected profile

            logger.info("Connect LD OK: serial={} abi={} touch={} max=({},{}) pressure={}",
                        d.serial, d.abi, d.touch_device, d.touch_max_x, d.touch_max_y,
                        d.pressure_max)
            if d.touch_max_x != d.width - 1 or d.touch_max_y != d.height - 1:
                logger.warning("touch max ({},{}) != screen-1 ({},{}) — coordinates are "
                               "NOT pixel-identity; replay may be off (spec §3.1)",
                               d.touch_max_x, d.touch_max_y, d.width - 1, d.height - 1)
            if d.abi != "x86_64":
                logger.warning("abi={} but only the x86_64 minitouch binary is shipped — "
                               "add vendor/minitouch/{}/minitouch", d.abi, d.abi)
        except Exception as err:  # noqa: BLE001
            logger.exception("Connect LD failed: {}", err)

    def _on_close(self) -> None:
        self._save_settings()  # remember current macro/rounds/delay + device
        self._stop_evt.set()
        self._root.after(200, self._root.destroy)

    # --- worker threads -----------------------------------------------------
    def _ensure_adb(self):
        from ckrbot.adb.client import AdbClient
        if self._adb is None:
            self._adb = AdbClient(self._cfg.device.serial)
            self._adb.connect()
        return self._adb

    def _run_engine(self) -> None:
        from ckrbot.capture.screen import ScreenCapture
        from ckrbot.engine.engine import Engine
        from ckrbot.engine.screen import ScreenIdentifier
        from ckrbot.game.states import CKR_SCREENS
        from ckrbot.input.controller import Controller
        from ckrbot.input.minitouch import MinitouchClient
        from ckrbot.macro.model import Macro
        from ckrbot.macro.player import MacroPlayer
        from ckrbot.vision.template import TemplateStore

        cfg = self._cfg
        try:
            macros = [Macro.load(f) for f in self._run_macro_files]
            logger.info("macro pool ({}): {}", len(macros),
                        ", ".join(Path(f).name for f in self._run_macro_files))
            adb = self._ensure_adb()
            capture = ScreenCapture(adb, cfg.device.width, cfg.device.height)
            templates = TemplateStore(cfg.paths.assets_dir)
            identifier = ScreenIdentifier(templates, CKR_SCREENS,
                                          threshold=cfg.vision.default_threshold)
            self._mt = MinitouchClient(adb, _minitouch_binary(cfg))
            self._mt.start()
            controller = Controller(self._mt, templates,
                                    threshold=cfg.vision.tap_threshold,
                                    tap_delay_ms=cfg.timing.tap_delay_ms,
                                    tap_delay_spread_ms=cfg.timing.tap_delay_spread_ms)
            boost_templates = ("tpl_relay_boost",) if cfg.farm.tap_boost else ()
            player = MacroPlayer(self._mt, capture, templates, anchor_template="tpl_pause",
                                 end_templates=("tpl_result_ok", "tpl_captcha_header"),
                                 boost_templates=boost_templates,
                                 threshold=cfg.vision.default_threshold,
                                 poll_interval_ms=cfg.timing.poll_interval_ms,
                                 anchor_poll_ms=cfg.timing.anchor_poll_ms,
                                 start_delay_ms=cfg.timing.replay_start_delay_ms,
                                 end_poll_ms=cfg.timing.replay_watch_poll_ms)
            self._engine = Engine(
                capture=capture, identifier=identifier, controller=controller,
                macro_player=player, macros=macros, config=cfg, templates=templates,
                back_fn=lambda: adb.shell("input keyevent 4"),
                debug_dir=cfg.paths.log_dir,
            )
            self._engine.run(self._stop_evt, self._pause_evt)
        except Exception as err:  # noqa: BLE001 - surface any failure to the log pane
            logger.exception("engine error: {}", err)
        finally:
            if self._mt is not None:
                try:
                    self._mt.close()
                except Exception:  # noqa: BLE001
                    pass
                self._mt = None
            self._engine = None
            logger.info("engine stopped")

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

    def _run_record(self) -> None:
        from datetime import datetime

        from ckrbot.capture.screen import ScreenCapture
        from ckrbot.macro.recorder import MacroRecorder
        from ckrbot.vision.template import TemplateStore

        cfg = self._cfg
        name = f"macro_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            adb = self._ensure_adb()
            capture = ScreenCapture(adb, cfg.device.width, cfg.device.height)
            templates = TemplateStore(cfg.paths.assets_dir)
            recorder = MacroRecorder(adb, capture, templates, device=cfg.device,
                                     anchor_template="tpl_pause", end_template="tpl_result_ok",
                                     threshold=cfg.vision.default_threshold,
                                     poll_interval_ms=cfg.timing.poll_interval_ms,
                                     anchor_poll_ms=cfg.timing.anchor_poll_ms,
                                     play_template="tpl_play_start")  # tap-anchor t=0
            logger.info("recording '{}': play ONE clean round (stops at END_ROUND)", name)
            macro = recorder.record(name, stop_evt=self._stop_evt)
            out = Path(cfg.paths.macros_dir) / f"{name}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            macro.save(out)
            logger.info("saved macro -> {} ({} events)", out, len(macro.events))
        except Exception as err:  # noqa: BLE001
            logger.exception("record error: {}", err)
        finally:
            self._recording = False

    # --- periodic UI update -------------------------------------------------
    def _set_buttons_recording(self, recording: bool) -> None:
        state = "disabled" if recording else "normal"
        for btn in (self._start_btn, self._pause_btn, self._stop_btn, self._reset_btn,
                    self._rename_btn, self._delete_btn, self._connect_btn, self._active_btn,
                    self._hearts_start_btn, self._hearts_stop_btn):
            btn.configure(state=state)

    def _append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        # cap to the last ~500 lines
        if int(self._log.index("end-1c").split(".")[0]) > 500:
            self._log.delete("1.0", "100.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass

        rounds = self._cfg.farm.max_rounds or "∞"
        rc = self._engine.round_count if self._engine is not None else 0
        self._round_var.set(f"Round: {rc} / {rounds}")

        if self._recording:
            status = "RECORDING"
        elif self._running():
            status = "PAUSED" if self._pause_evt.is_set() else "RUNNING"
        elif self._stop_evt.is_set():
            status = "STOPPED"
        else:
            status = "IDLE"
        self._status_var.set(f"Status: {status}")
        if not self._recording:
            self._set_buttons_recording(False)
        if self._was_recording and not self._recording:  # a recording just finished
            self._refresh_macros()
        self._was_recording = self._recording

        self._root.after(100, self._poll)


def run_panel(config: AppConfig, log_queue: "queue.Queue[str]") -> None:
    """Create the Tk root and run the Control Panel (blocks until closed)."""
    root = tk.Tk()
    ControlPanel(root, config, log_queue)
    root.mainloop()
