"""MinitouchClient (Phase 3) — low-latency raw-coordinate touch injection.

Lifecycle: push binary -> chmod -> run daemon (kept alive over a streaming shell)
-> adb forward tcp:port -> connect TCP -> read banner -> send tap commands.

INVARIANTS (CLAUDE.md #1, #2):
  * Coordinates are pixels (1280x720) sent as-is — NO scaling (device max 1279x719).
  * Inject pressure is clamped to the banner's max_pressure (=2 on this device);
    never hardcode 100/1000.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ckrbot.adb.client import AdbClient

DEVICE_BINARY_PATH = "/data/local/tmp/minitouch"
ABSTRACT_SOCKET = "localabstract:minitouch"
DEFAULT_FORWARD_PORT = 1111


class MinitouchError(RuntimeError):
    """Raised when minitouch cannot be started or has died."""


@dataclass(frozen=True)
class MinitouchBanner:
    """Parsed capabilities line from the minitouch banner."""

    version: int
    max_contacts: int
    max_x: int
    max_y: int
    max_pressure: int


def parse_banner(text: str) -> MinitouchBanner:
    """Parse the minitouch banner (pure — unit-testable without a device).

    Expected lines::

        v <version>
        ^ <max_contacts> <max_x> <max_y> <max_pressure>
        $ <pid>
    """
    version: int | None = None
    caps: tuple[int, int, int, int] | None = None
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 2:
            version = int(parts[1])
        elif parts[0] == "^" and len(parts) >= 5:
            caps = (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
    if caps is None:
        raise MinitouchError(f"minitouch banner missing '^' capabilities line: {text!r}")
    return MinitouchBanner(version=version or 0, max_contacts=caps[0], max_x=caps[1],
                           max_y=caps[2], max_pressure=caps[3])


def clamp_pressure(requested: int | None, max_pressure: int) -> int:
    """Pressure to inject: clamped to [1, max_pressure] (INVARIANT #2).

    ``requested is None`` means "use the device max" (safe default). Any explicit
    value is capped at ``max_pressure`` so a copy-pasted 100 can never be sent.
    """
    if requested is None:
        return max_pressure
    return max(1, min(requested, max_pressure))


class MinitouchClient:
    """Manages the minitouch daemon + socket for a single device."""

    def __init__(
        self,
        adb: AdbClient,
        binary_path: str | Path,
        *,
        forward_port: int = DEFAULT_FORWARD_PORT,
        requested_pressure: int | None = None,
    ) -> None:
        self._adb = adb
        self._binary = Path(binary_path)
        self._port = forward_port
        self._forward_local = f"tcp:{forward_port}"
        self._requested_pressure = requested_pressure
        self._daemon = None  # AdbConnection keeping the daemon alive
        self._sock: socket.socket | None = None
        self._banner: MinitouchBanner | None = None
        self._send_lock = threading.Lock()  # serialize socket writes across threads

    # --- properties ---------------------------------------------------------
    @property
    def banner(self) -> MinitouchBanner:
        if self._banner is None:
            raise MinitouchError("MinitouchClient.start() has not completed")
        return self._banner

    @property
    def pressure(self) -> int:
        """The clamped pressure that tap_raw will actually send."""
        return clamp_pressure(self._requested_pressure, self.banner.max_pressure)

    # --- lifecycle ----------------------------------------------------------
    def start(self, retries: int = 3) -> MinitouchBanner:
        """Push, launch, forward, connect and read the banner. Retries on failure."""
        if not self._binary.exists():
            raise MinitouchError(f"minitouch binary not found: {self._binary}")

        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._push_and_launch()
                self._banner = self._connect_and_read_banner()
                logger.info(
                    "minitouch up: v{} max_contacts={} max=({},{}) max_pressure={} -> tap pressure={}",
                    self._banner.version, self._banner.max_contacts, self._banner.max_x,
                    self._banner.max_y, self._banner.max_pressure, self.pressure,
                )
                return self._banner
            except (OSError, MinitouchError) as err:
                last_err = err
                logger.warning("minitouch start attempt {}/{} failed: {}", attempt, retries, err)
                self._teardown()  # clean partial state before retrying
                time.sleep(0.5)
        raise MinitouchError(f"minitouch failed to start after {retries} attempts") from last_err

    def _push_and_launch(self) -> None:
        self._adb.push(self._binary, DEVICE_BINARY_PATH)
        self._adb.shell(f"chmod 755 {DEVICE_BINARY_PATH}")
        # Kill any stale daemon so the abstract socket name is free.
        self._adb.shell(f"pkill -f {DEVICE_BINARY_PATH}")
        self._daemon = self._adb.shell_stream(DEVICE_BINARY_PATH)

    def _connect_and_read_banner(self) -> MinitouchBanner:
        # Give the daemon a moment to create the abstract socket, then forward+connect.
        last_err: Exception | None = None
        for _ in range(10):
            if self._daemon is not None and self._daemon.closed:
                raise MinitouchError("minitouch daemon exited during startup")
            try:
                self._adb.forward(self._forward_local, ABSTRACT_SOCKET)
                sock = socket.create_connection(("127.0.0.1", self._port), timeout=2.0)
                banner = parse_banner(self._read_banner_text(sock))
                self._sock = sock
                return banner
            except (OSError, MinitouchError) as err:
                last_err = err
                time.sleep(0.2)
        raise MinitouchError(f"could not connect to minitouch socket: {last_err}")

    @staticmethod
    def _read_banner_text(sock: socket.socket) -> str:
        """Read banner bytes until the '$' (pid) line arrives."""
        sock.settimeout(2.0)
        buf = b""
        while b"\n" not in buf or not any(
            line.startswith(b"$") for line in buf.split(b"\n")
        ):
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", errors="replace")

    # --- injection ----------------------------------------------------------
    def _send(self, command: str) -> None:
        if self._sock is None:
            raise MinitouchError("MinitouchClient not started")
        with self._send_lock:  # atomic per command — safe for concurrent callers
            try:
                self._sock.sendall(command.encode("ascii"))
            except OSError as err:
                raise MinitouchError(
                    f"minitouch socket write failed (daemon dead?): {err}") from err

    def down(self, x: int, y: int, contact: int = 0) -> None:
        """Press a contact at pixel (x, y) and hold it (identity coords)."""
        self._send(f"d {contact} {x} {y} {self.pressure}\nc\n")

    def up(self, contact: int = 0) -> None:
        """Release the contact. Safe to call even if nothing is pressed."""
        self._send(f"u {contact}\nc\n")

    def tap_raw(self, x: int, y: int, contact: int = 0) -> None:
        """Down + up in one shot. ``contact`` allows a second finger (e.g. a boost
        tap during macro replay) without disturbing the macro's contact 0."""
        self._send(f"d {contact} {x} {y} {self.pressure}\nc\nu {contact}\nc\n")

    # --- teardown -----------------------------------------------------------
    def _teardown(self) -> None:
        """Best-effort cleanup of socket, daemon and forward (never raises)."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._daemon is not None:
            try:
                self._daemon.close()  # closes the shell stream
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass
            self._daemon = None
        # Closing the shell stream does not reliably kill the daemon, so kill it
        # explicitly (otherwise the abstract socket stays held for the next run).
        try:
            self._adb.shell(f"pkill -f {DEVICE_BINARY_PATH}")
        except Exception:  # noqa: BLE001 - process may already be gone
            pass
        try:
            self._adb.forward_remove(self._forward_local)
        except Exception:  # noqa: BLE001 - forward may not exist
            pass

    def close(self) -> None:
        """Close the socket, kill the daemon and remove the forward."""
        self._teardown()
        self._banner = None
        logger.info("minitouch closed (socket/daemon/forward released)")

    def __enter__(self) -> "MinitouchClient":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
