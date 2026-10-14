import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from devices.opteeq_sg400 import OpteeQSG400


def _make_hex_record(addr: int, rectype: int, payload: bytes) -> str:
    body = bytes([len(payload), (addr >> 8) & 0xFF, addr & 0xFF, rectype]) + payload
    checksum = ((~(sum(body) & 0xFF) + 1) & 0xFF)
    return ":" + body.hex().upper() + f"{checksum:02X}"


class TestIntelHexDecode(unittest.TestCase):
    def test_decode_linear_image(self):
        payload = bytes(range(32))
        lines = [
            _make_hex_record(0, 0x04, b"\xA0\x00"),  # ELA: 0xA0000000
            _make_hex_record(0x0000, 0x00, payload[:16]),
            _make_hex_record(0x0010, 0x00, payload[16:]),
            _make_hex_record(0, 0x01, b""),
        ]
        data = ("\n".join(lines) + "\n").encode("ascii")
        out = OpteeQSG400._decode_intel_hex(data, 0xA0000000, 32)
        self.assertEqual(out, payload)

    def test_decode_offsets_relative_to_base(self):
        payload = b"\x11\x22\x33\x44"
        lines = [
            _make_hex_record(0, 0x04, b"\xA0\x00"),
            _make_hex_record(0x0100, 0x00, payload),
            _make_hex_record(0, 0x01, b""),
        ]
        data = "\n".join(lines).encode("ascii")
        out = OpteeQSG400._decode_intel_hex(data, 0xA0000000, 0x104)
        self.assertEqual(len(out), 0x104)
        self.assertEqual(out[0x100:0x104], payload)
        self.assertEqual(out[:0x100], b"\x00" * 0x100)

    def test_garbage_returns_empty(self):
        self.assertEqual(OpteeQSG400._decode_intel_hex(b"not hex at all", 0, 16), b"")


if __name__ == "__main__":
    unittest.main()
