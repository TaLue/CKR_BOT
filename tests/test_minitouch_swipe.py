"""MinitouchClient.swipe emits a down -> moves -> up gesture on contact 0."""
from __future__ import annotations

from ckrbot.input.minitouch import MinitouchBanner, MinitouchClient


class _FakeSock:
    def __init__(self) -> None:
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data


def _client() -> MinitouchClient:
    mt = MinitouchClient(adb=None, binary_path="x")  # no device work in __init__
    mt._sock = _FakeSock()  # inject a fake socket
    mt._banner = MinitouchBanner(version=1, max_contacts=10, max_x=1279, max_y=719, max_pressure=2)
    return mt


def test_swipe_emits_down_moves_up_on_contact_0() -> None:
    mt = _client()
    mt.swipe(400, 560, 400, 320, duration_ms=0, steps=4)
    cmds = mt._sock.sent.decode()
    # starts with a press at the origin (pressure clamped to device max = 2)
    assert cmds.startswith("d 0 400 560 2\nc\n")
    # 4 interpolated moves, ending at the destination
    assert cmds.count("m 0 ") == 4
    assert "m 0 400 320 2\nc\n" in cmds  # final move lands on the target
    # ends by releasing contact 0
    assert cmds.endswith("u 0\nc\n")  # release is committed (finger not left down)
