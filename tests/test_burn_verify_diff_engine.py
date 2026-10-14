import os
import sys
import unittest
import zipfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.burn_recorder import extract_source_data_bytes, compare_and_diff_data


class TestBurnVerifyDiffEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath("tests/scratch_burn")
        os.makedirs(self.test_dir, exist_ok=True)
        self.mock_opj = os.path.join(self.test_dir, "test_proj.opj")

        # Create a mock .opj zip with .sct and .opd
        sct_text = (
            "--source_file:D:/FW/test_firmware.hex;0x12345678,\n"
            "--opd_checksum:0x12345678\n"
            "--chip_name:TC387(DAP)\n"
            "--build_time:2026-09-28 16:00:00\n"
            "--device_model:SG400\n"
            "channel:setvcc(3300)\n"
            "dev.Param.freq = 5000000\n"
            "flashinfo = { {0x80000000, 1, 0x1000} }\n"
        )
        self.expected_bytes = b"\x55\xAA\x00\xFF" * 1024 # 4KB

        with zipfile.ZipFile(self.mock_opj, "w") as z:
            z.writestr("test.sct", sct_text)
            z.writestr("test.opd", self.expected_bytes)

    def tearDown(self):
        if os.path.isfile(self.mock_opj):
            try: os.remove(self.mock_opj)
            except: pass
        if os.path.isdir(self.test_dir):
            try: os.rmdir(self.test_dir)
            except: pass

    def test_extract_source_data_bytes(self):
        data = extract_source_data_bytes(self.mock_opj)
        self.assertEqual(data, self.expected_bytes)
        sliced = extract_source_data_bytes(self.mock_opj, max_size=16)
        self.assertEqual(sliced, self.expected_bytes[:16])

    def test_compare_and_diff_data_match(self):
        read_bytes = bytes(self.expected_bytes)
        res = compare_and_diff_data(self.expected_bytes, read_bytes, 0x80000000)
        self.assertTrue(res["is_match"])
        self.assertEqual(res["mismatch_count"], 0)
        self.assertEqual(res["match_rate"], 100.0)

    def test_compare_and_diff_data_mismatch(self):
        corrupted = bytearray(self.expected_bytes)
        corrupted[10] ^= 0x01 # bit flip
        res = compare_and_diff_data(self.expected_bytes, bytes(corrupted), 0x80000000)
        self.assertFalse(res["is_match"])
        self.assertEqual(res["mismatch_count"], 1)
        self.assertLess(res["match_rate"], 100.0)
        self.assertEqual(len(res["samples"]), 1)
        self.assertEqual(res["samples"][0]["address"], "0x8000000A")
        self.assertEqual(res["samples"][0]["flipped_bits"], [0])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(testRunner=runner)
