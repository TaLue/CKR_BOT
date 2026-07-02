"""Device-profile detection (spec §2.6) — read the touch geometry from the device
so the bot adapts when moved to another machine/LDPlayer.

``parse_getevent_pl`` is pure (unit-tested); ``detect_profile`` runs it live.
"""

from __future__ import annotations

import re

from ckrbot.adb.client import AdbClient

_ADD_DEVICE = re.compile(r"^\s*add device \d+:\s*(\S+)")
_MAX = re.compile(r"max\s+(\d+)")


def parse_getevent_pl(text: str) -> dict | None:
    """Parse ``getevent -pl`` output → the touchscreen's geometry.

    Returns {path, max_x, max_y, max_pressure} for the first device that reports
    ABS_MT_POSITION_X (the touch device), or None if none is found.
    """
    devices: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = _ADD_DEVICE.match(line)
        if m:
            if cur is not None and cur["max_x"] is not None:
                devices.append(cur)
            cur = {"path": m.group(1), "max_x": None, "max_y": None, "max_pressure": None}
            continue
        if cur is None:
            continue
        for axis, key in (("ABS_MT_POSITION_X", "max_x"),
                          ("ABS_MT_POSITION_Y", "max_y"),
                          ("ABS_MT_PRESSURE", "max_pressure")):
            if axis in line:
                mm = _MAX.search(line)
                if mm:
                    cur[key] = int(mm.group(1))
    if cur is not None and cur["max_x"] is not None:
        devices.append(cur)
    return devices[0] if devices else None


def detect_profile(adb: AdbClient) -> dict:
    """Read the device profile (abi + touch geometry) from a connected device."""
    abi = (adb.shell("getprop ro.product.cpu.abi") or "").strip()
    touch = parse_getevent_pl(adb.shell("getevent -pl"))
    if touch is None:
        raise RuntimeError("no ABS_MT touchscreen found via 'getevent -pl'")
    return {
        "serial": adb.serial,
        "abi": abi or None,
        "touch_device": touch["path"],
        "touch_max_x": touch["max_x"],
        "touch_max_y": touch["max_y"],
        "pressure_max": touch["max_pressure"],
    }
