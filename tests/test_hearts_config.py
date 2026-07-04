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
