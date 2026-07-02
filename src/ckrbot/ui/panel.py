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
        name = data.get("macro_name")
        if name:  # stored as a bare filename so it survives the app moving folders
            candidate = Path(self._cfg.paths.macros_dir) / name
            if candidate.exists():
                self._cfg.farm.macro_file = str(candidate)

    def _save_settings(self) -> None:
        data = {
            "macro_name": Path(self._macro_var.get()).name if self._macro_var.get() else "",
            "max_rounds": self._safe_int(self._rounds_var.get(), 0),
            "start_delay_ms": self._safe_int(self._delay_var.get(), 0),
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
        for i, btn in enumerate((self._start_btn, self._pause_btn, self._stop_btn,
                                 self._reset_btn, self._record_btn)):
            btn.grid(row=0, column=i, padx=3)

        opts = ttk.Frame(self._root, padding=(8, 0))
        opts.pack(fill="x")
        ttk.Label(opts, text="Macro:").grid(row=0, column=0, sticky="w")
        self._macro_var = tk.StringVar()
        self._macro_combo = ttk.Combobox(opts, textvariable=self._macro_var, width=32,
                                          state="readonly")
        self._macro_combo.grid(row=0, column=1, padx=(4, 4))
        self._rename_btn = ttk.Button(opts, text="Rename", width=8, command=self._on_rename)
        self._rename_btn.grid(row=0, column=2, padx=2)
        self._delete_btn = ttk.Button(opts, text="Delete", width=8, command=self._on_delete)
        self._delete_btn.grid(row=0, column=3, padx=(2, 16))
        ttk.Label(opts, text="Rounds (0=∞):").grid(row=0, column=4, sticky="w")
        self._rounds_var = tk.StringVar(value=str(self._cfg.farm.max_rounds))
        ttk.Spinbox(opts, from_=0, to=99999, width=7, textvariable=self._rounds_var).grid(
            row=0, column=5, padx=4)
        ttk.Label(opts, text="Start delay (ms):").grid(row=0, column=6, sticky="w", padx=(12, 0))
        self._delay_var = tk.StringVar(value=str(self._cfg.timing.replay_start_delay_ms))
        ttk.Spinbox(opts, from_=-2000, to=5000, increment=50, width=7,
                    textvariable=self._delay_var).grid(row=0, column=7, padx=4)

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

    def _refresh_macros(self) -> None:
        macros = sorted(str(p) for p in Path(self._cfg.paths.macros_dir).glob("*.json"))
        self._macro_combo["values"] = macros
        current = self._macro_var.get()
        if current not in macros:  # stale (deleted/renamed) or unset -> pick a default
            default = self._cfg.farm.macro_file
            self._macro_var.set(default if default in macros else (macros[0] if macros else ""))

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
        macro_file = self._macro_var.get() or self._cfg.farm.macro_file
        self._cfg.farm.max_rounds = max_rounds
        self._cfg.farm.macro_file = macro_file
        self._cfg.timing.replay_start_delay_ms = start_delay
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
        path = self._macro_var.get()
        if not path:
            return
        name = Path(path).name
        if not messagebox.askyesno("Delete macro", f"Delete this macro?\n\n{name}"):
            return
        try:
            Path(path).unlink()
            logger.info("deleted macro: {}", name)
        except OSError as err:
            messagebox.showerror("Delete failed", str(err))
        self._macro_var.set("")
        self._refresh_macros()

    def _on_rename(self) -> None:
        if self._running() or self._recording:
            return
        path = self._macro_var.get()
        if not path:
            return
        old = Path(path)
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
            self._macro_var.set(str(new_path))
        except Exception as err:  # noqa: BLE001
            messagebox.showerror("Rename failed", str(err))
        self._refresh_macros()

    def _on_close(self) -> None:
        self._save_settings()  # remember current macro/rounds/delay
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
            macro = Macro.load(cfg.farm.macro_file)
            adb = self._ensure_adb()
            capture = ScreenCapture(adb, cfg.device.width, cfg.device.height)
            templates = TemplateStore(cfg.paths.assets_dir)
            identifier = ScreenIdentifier(templates, CKR_SCREENS,
                                          threshold=cfg.vision.default_threshold)
            self._mt = MinitouchClient(adb, _minitouch_binary(cfg))
            self._mt.start()
            controller = Controller(self._mt, templates,
                                    threshold=cfg.vision.default_threshold,
                                    tap_delay_ms=cfg.timing.tap_delay_ms,
                                    tap_delay_spread_ms=cfg.timing.tap_delay_spread_ms)
            player = MacroPlayer(self._mt, capture, templates, anchor_template="tpl_pause",
                                 end_templates=("tpl_result_ok", "tpl_captcha_header"),
                                 boost_templates=("tpl_relay_boost",),
                                 threshold=cfg.vision.default_threshold,
                                 poll_interval_ms=cfg.timing.poll_interval_ms,
                                 start_delay_ms=cfg.timing.replay_start_delay_ms,
                                 end_poll_ms=cfg.timing.replay_watch_poll_ms)
            self._engine = Engine(
                capture=capture, identifier=identifier, controller=controller,
                macro_player=player, macro=macro, config=cfg, templates=templates,
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
                                     poll_interval_ms=cfg.timing.poll_interval_ms)
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
                    self._rename_btn, self._delete_btn):
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
