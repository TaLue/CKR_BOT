"""Pydantic config models for CKR Farm Bot.

Single source of truth for all runtime values (spec §9). Defaults match the
confirmed device profile (spec §2.6) so the app is usable without a config file,
but every value is overridable via ``config.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from ckrbot.paths import resolve_path

# Default config path relative to the repository root.
DEFAULT_CONFIG_PATH = Path("config.yaml")


class DeviceConfig(BaseModel):
    """LDPlayer instance / touch device geometry (spec §2.6).

    INVARIANT: ``touch_max_x/y`` report ``width-1``/``height-1`` on this device,
    so touch raw coordinates equal pixel coordinates (identity — no scaling).
    """

    serial: str = "127.0.0.1:5555"
    width: int = 1280
    height: int = 720
    abi: str = "x86_64"
    touch_device: str = "/dev/input/event4"
    touch_max_x: int = 1279
    touch_max_y: int = 719
    pressure_max: int = Field(default=2, ge=1)

    @property
    def is_pixel_identity(self) -> bool:
        """True when touch raw space maps 1:1 to pixel space (no scaling needed)."""
        return self.touch_max_x == self.width - 1 and self.touch_max_y == self.height - 1


class TimingConfig(BaseModel):
    """Loop / tap timing (milliseconds)."""

    poll_interval_ms: int = Field(default=400, ge=0)
    # How fast to poll while WAITING for the GAMEPLAY anchor (pause icon), for both
    # record and replay. Small = tighter, more consistent t=0 (capture time is the
    # real floor ~50-100ms). Keep record & replay on the same value.
    anchor_poll_ms: int = Field(default=20, ge=0)
    settle_ms: int = Field(default=2500, ge=0)
    tap_delay_ms: int = Field(default=300, ge=0)
    tap_delay_spread_ms: int = Field(default=120, ge=0)
    # Offset added to macro start after the GAMEPLAY anchor (ms). Positive = start
    # later (macro was firing too early); negative = start earlier. Tunable per run.
    replay_start_delay_ms: int = Field(default=0)
    # How often the replay watcher re-checks for end-of-round / boost icons (ms).
    # Lower = catches short-lived icons (e.g. the 1-2s boost prompt) more reliably.
    replay_watch_poll_ms: int = Field(default=200, ge=50)


class FarmConfig(BaseModel):
    """Farming run parameters."""

    max_rounds: int = Field(default=0, ge=0)  # 0 = infinite
    macro_file: str = "macros/escape_from_the_oven_v1.json"
    tap_boost: bool = True  # tap the Cookie Relay Boost icon mid-run when it appears
    # True: Multi-Buy roll until Double Coins (spec flow). False: skip it — just tap
    # Play on the START screen (no Double Coins buff, no money spent rolling).
    randomize_double_coins: bool = True


class WatchdogConfig(BaseModel):
    """UNKNOWN-state recovery limits (spec §5 watchdog)."""

    unknown_limit: int = Field(default=6, ge=1)
    max_recovery_attempts: int = Field(default=3, ge=1)


class PathsConfig(BaseModel):
    """Filesystem locations (relative to repo root unless absolute)."""

    assets_dir: str = "game/assets"
    macros_dir: str = "macros"
    log_dir: str = "logs"


class VisionConfig(BaseModel):
    """Template-matching defaults."""

    default_threshold: float = Field(default=0.85, ge=0.0, le=1.0)


class CaptchaConfig(BaseModel):
    """CAPTCHA solving limits (spec §7). Each captcha needs 3 correct rounds in a
    row (Tries left 3/3 → 2/3 → 1/3 → cleared). We read Tries left to count only
    WRONG rounds; after ``max_wrong`` wrong guesses the bot stops."""

    max_wrong: int = Field(default=5, ge=1)          # stop after this many WRONG (reset) rounds
    max_rounds: int = Field(default=15, ge=1)        # total-attempts safety cap (avoid looping)
    vote_frames: int = Field(default=3, ge=1)        # frames sampled per round (beat animation)
    round_timeout_ms: int = Field(default=2500, ge=300)  # wait for Tries left to change (retry after)
    poll_ms: int = Field(default=200, ge=20)         # how often to re-check Tries left (faster = snappier)


class AppConfig(BaseModel):
    """Root config aggregating every section (spec §9)."""

    device: DeviceConfig = Field(default_factory=DeviceConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    farm: FarmConfig = Field(default_factory=FarmConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    captcha: CaptchaConfig = Field(default_factory=CaptchaConfig)

    @model_validator(mode="after")
    def _warn_if_not_pixel_identity(self) -> "AppConfig":
        """Guard the pixel-identity invariant.

        The whole coordinate model assumes touch raw == pixel (spec §3.1). If a
        different device breaks that, fail loudly here rather than silently
        producing off-target taps — enabling a scale mode is a deliberate change.
        """
        if not self.device.is_pixel_identity:
            raise ValueError(
                "Device is not pixel-identity: "
                f"touch_max=({self.device.touch_max_x},{self.device.touch_max_y}) "
                f"but screen=({self.device.width},{self.device.height}). "
                "Coordinate scaling is intentionally unsupported in v1 (spec §3.1)."
            )
        return self


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate config from a YAML file.

    Falls back to model defaults (the confirmed device profile) when the file
    is missing, so the scaffold runs out of the box.
    """
    cfg_path = resolve_path(path if path is not None else DEFAULT_CONFIG_PATH)
    if not cfg_path.exists():
        return AppConfig()
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = AppConfig.model_validate(data)
    # Resolve data/resource paths to absolute (relative to the app base) so the
    # app works the same in dev and as a frozen EXE — see ckrbot.paths.
    cfg.paths.assets_dir = str(resolve_path(cfg.paths.assets_dir))
    cfg.paths.macros_dir = str(resolve_path(cfg.paths.macros_dir))
    cfg.paths.log_dir = str(resolve_path(cfg.paths.log_dir))
    cfg.farm.macro_file = str(resolve_path(cfg.farm.macro_file))
    return cfg
