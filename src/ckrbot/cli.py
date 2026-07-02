"""Entry point for CKR Farm Bot.

Subcommands:
  * ``info``    — load config + logging and print scaffold/device health (Phase 0).
  * ``capture`` — connect to the device and save one frame (Phase 1 verify;
                  works while LDPlayer is minimized).

The Tkinter Control Panel is Phase 7; ``python -m ckrbot`` currently runs ``info``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
from loguru import logger

from ckrbot import __version__
from ckrbot.config import AppConfig, load_config
from ckrbot.logging_setup import setup_logging


def _cmd_info(config: AppConfig) -> int:
    logger.info("CKR Farm Bot v{} — scaffold OK", __version__)
    logger.info(
        "device: {} {}x{} abi={} touch={} pressure_max={} (pixel-identity={})",
        config.device.serial,
        config.device.width,
        config.device.height,
        config.device.abi,
        config.device.touch_device,
        config.device.pressure_max,
        config.device.is_pixel_identity,
    )
    logger.info("macro_file={} max_rounds={}", config.farm.macro_file, config.farm.max_rounds)
    logger.info("Run 'python -m ckrbot' for the Control Panel, or 'farm' to run headless.")
    return 0


def _cmd_capture(config: AppConfig, out_path: str) -> int:
    # Imported lazily so `info` works even before ADB deps are installed.
    from ckrbot.adb.client import AdbClient
    from ckrbot.capture.screen import ScreenCapture

    adb = AdbClient(config.device.serial)
    adb.connect()
    cap = ScreenCapture(adb, config.device.width, config.device.height)
    frame = cap.grab()
    h, w = frame.shape[:2]
    if not cv2.imwrite(out_path, frame):
        logger.error("Failed to write capture to {}", out_path)
        return 1
    logger.info("Captured {}x{} frame -> {}", w, h, out_path)
    logger.info("If LDPlayer was minimized and this succeeded, minimize-capture works.")
    return 0


def _cmd_record(config: AppConfig, name: str) -> int:
    from ckrbot.adb.client import AdbClient
    from ckrbot.capture.screen import ScreenCapture
    from ckrbot.macro.recorder import MacroRecorder
    from ckrbot.vision.template import TemplateStore

    adb = AdbClient(config.device.serial)
    adb.connect()
    capture = ScreenCapture(adb, config.device.width, config.device.height)
    templates = TemplateStore(config.paths.assets_dir)
    recorder = MacroRecorder(
        adb, capture, templates,
        device=config.device,
        # Anchor/end template names are supplied here (app layer), keeping the
        # recorder game-agnostic. TODO(Phase 6): harden the pause-only anchor.
        anchor_template="tpl_pause",
        end_template="tpl_result_ok",
        threshold=config.vision.default_threshold,
        poll_interval_ms=config.timing.poll_interval_ms,
    )
    logger.info("Play ONE clean round now (no Continue/Quit). Recording until END_ROUND...")
    macro = recorder.record(name)
    out_path = Path(config.paths.macros_dir) / f"{name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    macro.save(out_path)
    logger.info("saved macro -> {} ({} events)", out_path, len(macro.events))
    return 0


def _minitouch_binary(config: AppConfig) -> str:
    """Path to the minitouch binary for the configured ABI (vendor layout)."""
    from ckrbot.paths import resolve_path
    return str(resolve_path(Path("vendor") / "minitouch" / config.device.abi / "minitouch"))


def _cmd_play(config: AppConfig, macro_file: str) -> int:
    import threading

    from ckrbot.adb.client import AdbClient
    from ckrbot.capture.screen import ScreenCapture
    from ckrbot.input.minitouch import MinitouchClient
    from ckrbot.macro.model import Macro
    from ckrbot.macro.player import MacroPlayer
    from ckrbot.vision.template import TemplateStore

    macro = Macro.load(macro_file)
    adb = AdbClient(config.device.serial)
    adb.connect()
    capture = ScreenCapture(adb, config.device.width, config.device.height)
    templates = TemplateStore(config.paths.assets_dir)
    mt = MinitouchClient(adb, _minitouch_binary(config))
    mt.start()
    player = MacroPlayer(
        mt, capture, templates,
        anchor_template="tpl_pause",  # TODO(Phase 6): harden anchor
        threshold=config.vision.default_threshold,
        poll_interval_ms=config.timing.poll_interval_ms,
    )
    stop, pause = threading.Event(), threading.Event()
    try:
        logger.info("replaying '{}' ({} events) — waiting for GAMEPLAY anchor...",
                    macro.name, len(macro.events))
        completed = player.play(macro, stop, pause)
    finally:
        mt.close()
    logger.info("replay {}", "completed" if completed else "aborted (no anchor / stopped)")
    return 0 if completed else 1


def _cmd_farm(config: AppConfig) -> int:
    import threading

    from ckrbot.adb.client import AdbClient
    from ckrbot.capture.screen import ScreenCapture
    from ckrbot.engine.engine import Engine
    from ckrbot.engine.screen import ScreenIdentifier
    from ckrbot.game.states import CKR_SCREENS
    from ckrbot.input.controller import Controller
    from ckrbot.input.minitouch import MinitouchClient
    from ckrbot.macro.model import Macro
    from ckrbot.macro.player import MacroPlayer
    from ckrbot.vision.template import TemplateStore

    macro = Macro.load(config.farm.macro_file)
    adb = AdbClient(config.device.serial)
    adb.connect()
    capture = ScreenCapture(adb, config.device.width, config.device.height)
    templates = TemplateStore(config.paths.assets_dir)
    identifier = ScreenIdentifier(templates, CKR_SCREENS, threshold=config.vision.default_threshold)
    mt = MinitouchClient(adb, _minitouch_binary(config))
    mt.start()
    controller = Controller(
        mt, templates,
        threshold=config.vision.default_threshold,
        tap_delay_ms=config.timing.tap_delay_ms,
        tap_delay_spread_ms=config.timing.tap_delay_spread_ms,
    )
    player = MacroPlayer(
        mt, capture, templates, anchor_template="tpl_pause",
        # End replay early when the round is over: Result screen, or a CAPTCHA
        # (death mid-run → Surprise! → auto-continues to Result after solving).
        end_templates=("tpl_result_ok", "tpl_captcha_header"),
        boost_templates=("tpl_relay_boost",),  # tap the Cookie Relay Boost mid-run
        threshold=config.vision.default_threshold,
        poll_interval_ms=config.timing.poll_interval_ms,
        start_delay_ms=config.timing.replay_start_delay_ms,
        end_poll_ms=config.timing.replay_watch_poll_ms,
    )
    engine = Engine(
        capture=capture, identifier=identifier, controller=controller,
        macro_player=player, macro=macro, config=config, templates=templates,
        back_fn=lambda: adb.shell("input keyevent 4"),
        debug_dir=config.paths.log_dir,  # dump annotated captcha frames for review
    )
    stop, pause = threading.Event(), threading.Event()
    logger.info("farming '{}' (max_rounds={}) — Ctrl+C to stop",
                macro.name, config.farm.max_rounds or "∞")
    try:
        engine.run(stop, pause)
    except KeyboardInterrupt:
        logger.info("interrupted → stopping")
        stop.set()
    finally:
        mt.close()
    return 0


def _cmd_gui(config: AppConfig) -> int:
    import queue

    from ckrbot.ui.panel import run_panel

    # Route logs into a queue the GUI polls (also keep the file sink).
    log_queue = setup_logging(config.paths.log_dir, gui_queue=queue.Queue())
    logger.info("CKR Farm Bot — Control Panel")
    run_panel(config, log_queue)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse args, load config, set up logging. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="ckrbot", description="CKR Farm Bot")
    parser.add_argument("-c", "--config", default=None, help="path to config.yaml")
    parser.add_argument("--log-level", default="INFO", help="log level (default: INFO)")
    parser.add_argument("--version", action="version", version=f"ckrbot {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="open the Tkinter Control Panel (default)")
    sub.add_parser("info", help="print config/device health")
    cap = sub.add_parser("capture", help="save one screen capture from the device")
    cap.add_argument("--out", default="logs/capture.png", help="output PNG path")
    rec = sub.add_parser("record", help="record one clean round into a macro JSON")
    rec.add_argument("--name", default="escape_from_the_oven_v1", help="macro name / filename")
    play = sub.add_parser("play", help="replay a macro JSON (waits for GAMEPLAY anchor)")
    play.add_argument("--macro", default=None, help="macro JSON path (default: config farm.macro_file)")
    sub.add_parser("farm", help="run the full state-machine farm loop (Ctrl+C to stop)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(config.paths.log_dir, level=args.log_level)

    if args.command == "capture":
        return _cmd_capture(config, args.out)
    if args.command == "record":
        return _cmd_record(config, args.name)
    if args.command == "play":
        return _cmd_play(config, args.macro or config.farm.macro_file)
    if args.command == "farm":
        return _cmd_farm(config)
    if args.command == "info":
        return _cmd_info(config)
    return _cmd_gui(config)  # default: open the Control Panel


if __name__ == "__main__":
    sys.exit(main())
