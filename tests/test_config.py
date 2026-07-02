"""Phase 0 scaffold checks: config models + loader + logging plumbing."""

from __future__ import annotations

import queue

import pytest
from pydantic import ValidationError

from ckrbot.config import AppConfig, load_config
from ckrbot.logging_setup import setup_logging


def test_defaults_match_device_profile() -> None:
    """Model defaults equal the confirmed device profile (spec §2.6)."""
    cfg = AppConfig()
    assert cfg.device.width == 1280
    assert cfg.device.height == 720
    assert cfg.device.abi == "x86_64"
    assert cfg.device.touch_device == "/dev/input/event4"
    assert cfg.device.touch_max_x == 1279
    assert cfg.device.touch_max_y == 719
    assert cfg.device.pressure_max == 2
    assert cfg.device.is_pixel_identity is True
    assert cfg.vision.default_threshold == 0.85


def test_load_repo_config_yaml() -> None:
    """The shipped config.yaml loads and validates."""
    cfg = load_config("config.yaml")
    assert cfg.device.pressure_max == 2
    assert cfg.farm.macro_file.endswith(".json")
    assert cfg.timing.poll_interval_ms == 400


def test_missing_config_falls_back_to_defaults(tmp_path) -> None:
    cfg = load_config(tmp_path / "does_not_exist.yaml")
    assert cfg == AppConfig()


def test_non_pixel_identity_device_rejected() -> None:
    """Coordinate scaling is unsupported in v1 — mismatch must fail loudly (spec §3.1)."""
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"device": {"width": 1280, "touch_max_x": 32767}})


def test_logging_queue_sink_receives_lines(tmp_path) -> None:
    """The GUI queue sink captures emitted log lines (spec §8)."""
    from loguru import logger

    q: "queue.Queue[str]" = setup_logging(tmp_path / "logs", to_stderr=False)
    logger.info("hello-scaffold")
    logger.complete()  # flush enqueued sinks
    lines = []
    while not q.empty():
        lines.append(q.get_nowait())
    assert any("hello-scaffold" in line for line in lines)
