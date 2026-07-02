"""ADB client wrapper (Phase 1) — thin, game-agnostic layer over adbutils.

Provides connect / shell / screenshot and the socket-forward + push primitives
that minitouch (Phase 3) will need. Contains no game-specific knowledge.
"""

from __future__ import annotations

from pathlib import Path

import adbutils
from adbutils import AdbDevice
from loguru import logger


class AdbClient:
    """Connection to a single LDPlayer instance via the local adb server."""

    def __init__(self, serial: str, *, host: str = "127.0.0.1", port: int = 5037) -> None:
        self._serial = serial
        self._client = adbutils.AdbClient(host=host, port=port)
        self._device: AdbDevice | None = None

    def connect(self, timeout: float = 5.0) -> None:
        """Attach to the device, running ``adb connect`` first for network serials."""
        if ":" in self._serial:  # network serial (e.g. 127.0.0.1:5555) needs connect
            result = self._client.connect(self._serial, timeout=timeout)
            logger.debug("adb connect {} -> {}", self._serial, result)
        self._device = self._client.device(self._serial)
        # Touch the device once so a bad serial fails now, not mid-run.
        state = self._device.get_state()
        logger.info("ADB connected: {} (state={})", self._serial, state)

    @property
    def device(self) -> AdbDevice:
        """The underlying adbutils device (raises if not connected yet)."""
        if self._device is None:
            raise RuntimeError("AdbClient.connect() must be called before use")
        return self._device

    @property
    def serial(self) -> str:
        return self._serial

    def shell(self, cmd: str | list[str], *, timeout: float | None = None) -> str:
        """Run a shell command and return stdout as text."""
        return self.device.shell(cmd, timeout=timeout)

    def screenshot(self):
        """Capture the framebuffer as a PIL image.

        Works while LDPlayer is minimized: adb reads the device framebuffer, not
        the host desktop window.
        """
        return self.device.screenshot()

    def push(self, local: str | Path, remote: str) -> None:
        """Push a local file to the device (used for the minitouch binary)."""
        self.device.sync.push(str(local), remote)

    def forward(self, local: str, remote: str) -> str:
        """Set up an adb forward (used for the minitouch localabstract socket)."""
        return self.device.forward(local, remote)

    def forward_remove(self, local: str) -> None:
        """Remove an adb forward (clean teardown, avoids leaking ports)."""
        self.device.forward_remove(local)

    def shell_stream(self, cmd: str):
        """Run a shell command and keep the connection open (returns AdbConnection).

        Used to launch a long-lived daemon (minitouch): while the returned
        connection stays open the process runs; closing it terminates the process.
        """
        return self.device.shell(cmd, stream=True)
