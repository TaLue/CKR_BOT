"""Phase 3 unit tests: banner parsing + pressure clamp (no device needed)."""

from __future__ import annotations

import pytest

from ckrbot.input.minitouch import (
    MinitouchBanner,
    MinitouchClient,
    MinitouchError,
    clamp_pressure,
    parse_banner,
)

DEVICE_BANNER = "v 1\n^ 10 1279 719 2\n$ 12345\n"


def test_parse_banner_reads_device_maxes() -> None:
    banner = parse_banner(DEVICE_BANNER)
    assert banner.version == 1
    assert banner.max_contacts == 10
    assert banner.max_x == 1279
    assert banner.max_y == 719
    assert banner.max_pressure == 2  # confirmed device value (spec §2.6)


def test_parse_banner_without_caps_line_raises() -> None:
    with pytest.raises(MinitouchError):
        parse_banner("v 1\n$ 999\n")


@pytest.mark.parametrize(
    "requested,expected",
    [
        (None, 2),  # default -> device max
        (100, 2),   # the classic copy-paste mistake, clamped down
        (1000, 2),
        (2, 2),
        (1, 1),
        (0, 1),     # floor at 1
    ],
)
def test_clamp_pressure_never_exceeds_max(requested, expected) -> None:
    assert clamp_pressure(requested, max_pressure=2) == expected


def test_tap_commands_use_contact_and_clamped_pressure() -> None:
    """down/up/tap_raw emit the right minitouch commands, incl. a 2nd contact."""
    class FakeSock:
        def __init__(self) -> None:
            self.data = b""

        def sendall(self, b: bytes) -> None:
            self.data += b

    client = MinitouchClient(adb=None, binary_path="x")  # type: ignore[arg-type]
    client._banner = MinitouchBanner(1, 10, 1279, 719, 2)  # noqa: SLF001 - test seam
    client._sock = FakeSock()  # type: ignore[assignment]  # noqa: SLF001

    client.tap_raw(100, 200, contact=1)
    assert client._sock.data.decode() == "d 1 100 200 2\nc\nu 1\nc\n"
    client._sock.data = b""
    client.down(50, 60)  # default contact 0
    client.up()
    assert client._sock.data.decode() == "d 0 50 60 2\nc\nu 0\nc\n"


def test_client_pressure_property_is_clamped() -> None:
    """The pressure tap_raw will actually send is clamped to the banner max."""
    client = MinitouchClient(adb=None, binary_path="unused", requested_pressure=100)  # type: ignore[arg-type]
    # Inject a banner as if start() had run, without touching a device.
    client._banner = MinitouchBanner(1, 10, 1279, 719, 2)  # noqa: SLF001 - test seam
    assert client.pressure == 2
