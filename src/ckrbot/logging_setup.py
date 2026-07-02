"""Logging setup for CKR Farm Bot (spec §8 threading model).

Two sinks:
  * file sink   — rotating log under ``log_dir`` (thread-safe via enqueue).
  * queue sink  — pushes formatted lines into a ``queue.Queue`` so the Tkinter
                  GUI (main thread) can poll and display them. The worker/engine
                  thread never touches widgets directly — it only logs.
"""

from __future__ import annotations

import queue
import sys
from pathlib import Path

from loguru import logger

# Compact line for the GUI log pane: "12:03:01 | INFO | message"
_GUI_FORMAT = "{time:HH:mm:ss} | {level: <7} | {message}"
_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}"


def _make_queue_sink(log_queue: "queue.Queue[str]"):
    """Build a loguru sink that enqueues each already-formatted record line."""

    def _sink(message) -> None:  # loguru passes a Message (str subclass)
        # str(message) is the fully formatted line (incl. trailing newline).
        log_queue.put(str(message).rstrip("\n"))

    return _sink


def setup_logging(
    log_dir: str | Path,
    *,
    level: str = "INFO",
    gui_queue: "queue.Queue[str] | None" = None,
    to_stderr: bool = True,
) -> "queue.Queue[str]":
    """Configure loguru sinks and return the GUI log queue.

    Args:
        log_dir: directory for rotating log files (created if missing).
        level: minimum level for all sinks.
        gui_queue: existing queue to reuse; a new one is created if None.
        to_stderr: also mirror logs to stderr (useful for headless/dev runs).

    Returns:
        The queue the GUI should poll for log lines.
    """
    log_queue: "queue.Queue[str]" = gui_queue if gui_queue is not None else queue.Queue()

    logger.remove()  # drop loguru's default stderr handler

    # A --windowed (GUI) PyInstaller exe has no console, so sys.stderr is None —
    # only add the stderr sink when a real stream exists.
    if to_stderr and sys.stderr is not None:
        logger.add(sys.stderr, level=level, format=_GUI_FORMAT, enqueue=True)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_path / "ckrbot_{time:YYYY-MM-DD}.log",
        level=level,
        format=_FILE_FORMAT,
        rotation="10 MB",
        retention="10 days",
        encoding="utf-8",
        enqueue=True,  # thread-safe: engine worker thread logs safely
    )

    logger.add(_make_queue_sink(log_queue), level=level, format=_GUI_FORMAT, enqueue=True)

    return log_queue
