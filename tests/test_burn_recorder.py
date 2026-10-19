import os
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.burn_recorder import (
    compare_and_diff_data,
    generate_source_address_data_image,
    parse_opj_metadata,
    record_burn_event,
)


class TestBurnRecorder(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix='burn-record-test-')
        self.test_dir = self._temporary.name
        self.sample_opj = os.path.abspath(r"C:\Users\95403\.gemini\antigravity-ide\brain\838e2ea1-fd49-4b24-a903-3934c490f566\scratch\geely.opj")

    def tearDown(self):
        self._temporary.cleanup()

    def test_parse_opj_metadata(self):
        if not os.path.exists(self.sample_opj):
            self.skipTest("Sample opj does not exist")
        meta = parse_opj_metadata(self.sample_opj)
        self.assertTrue(meta["valid"])
        self.assertEqual(meta["source_file"], "Geely_VE21_DisableHSM_V2.hex")
        self.assertEqual(meta["source_crc"], "0x1e4bbef0")
        self.assertEqual(meta["chip_name"], "TC387(DAP)")
        self.assertGreater(len(meta["sectors"]), 0)
        self.assertIn("0xA0000000", meta["summary_address_range"])
        # 扇区三元组语义: (起始, 逻辑扇区数, 区域总大小) -> 区域结束 = 起始 + 区域大小
        self.assertEqual(meta["sectors"][0]["end_hex"], "0xA0080000")
        self.assertEqual(meta["sectors"][0]["sector_size"], 0x4000)
        self.assertEqual(meta["sectors"][-1]["end_hex"], "0xAFC20000")

    def test_generate_source_address_data_image(self):
        if not os.path.exists(self.sample_opj):
            self.skipTest("Sample opj does not exist")
        out_txt = os.path.join(self.test_dir, "image.txt")
        content = generate_source_address_data_image(self.sample_opj, out_txt)
        self.assertTrue(os.path.isfile(out_txt))
        self.assertIn("0xA0000000", content)
        self.assertIn("TC387(DAP)", content)

    def test_compare_and_diff_data_match(self):
        data = b"\x00\x01\x02\x03\x04\x05"
        diff = compare_and_diff_data(data, data, base_address=0xA0000000)
        self.assertTrue(diff["is_match"])
        self.assertEqual(diff["mismatch_count"], 0)
        self.assertEqual(diff["match_rate"], 100.0)

    def test_compare_and_diff_data_bit_flip(self):
        src = b"\x5A"  # 01011010
        dst = b"\x58"  # 01011000 (Bit 1 is flipped from 1 to 0)
        diff = compare_and_diff_data(src, dst, base_address=0xA0001000)
        self.assertFalse(diff["is_match"])
        self.assertEqual(diff["mismatch_count"], 1)
        self.assertEqual(len(diff["samples"]), 1)
        self.assertEqual(diff["samples"][0]["address"], "0xA0001000")
        self.assertEqual(diff["samples"][0]["flipped_bits"], [1])

    def test_record_burn_event(self):
        record_data = {
            "sn": "TEST_SN_888",
            "project_name": "Geely_VE21_DisableHSM_V2.opj",
            "source_file": "Geely_VE21_DisableHSM_V2.hex",
            "chip_name": "TC387(DAP)",
            "address_range": "0xA0000000 ~ 0xA0880000",
            "expected_checksum": "0x1e4bbef0",
            "actual_checksum": "0x1e4bbef0",
            "checksum_match": True,
            "vcc_mv": 3300,
            "duration_sec": 3.85,
            "result": "PASS",
            "diagnosis": "烧录完成，物理校验一致",
            "hardware_log": "ch1 Initing ... Chip name: TC387\nch1 Run project file SUCCESS",
        }
        res = record_burn_event(record_data, base_dir=self.test_dir)
        self.assertTrue(os.path.isfile(res["csv_path"]))
        self.assertTrue(os.path.isfile(res["report_path"]))
        self.assertTrue(res["match"])

        with open(res["csv_path"], "r", encoding="utf-8-sig") as f:
            csv_content = f.read()
        self.assertIn("TEST_SN_888", csv_content)
        self.assertIn("0x1e4bbef0", csv_content)
        self.assertIn("MATCH", csv_content)


if __name__ == "__main__":
    unittest.main()


class TestFullImageArchive(unittest.TestCase):
    """完整数据镜像存档与 NG 标记"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.test_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _record(self, result="PASS", diff=None, src=b"", rb=b""):
        return {
            "sn": "SN_IMG_001", "project_name": "proj.opj", "source_file": "fw.hex",
            "chip_name": "TC387(DAP)", "address_range": "0xA0000000 ~ 0xA0000100",
            "expected_checksum": "0x12345678", "actual_checksum": "0x12345678",
            "vcc_mv": 3300, "duration_sec": 1.0, "result": result,
            "diagnosis": "测试", "diff_details": diff,
            "source_bytes": src, "readback_bytes": rb, "base_address": 0xA0000000,
        }

    def test_mismatch_count_full_not_capped(self):
        """超过 50 处不一致时 mismatch_count 必须全量统计"""
        from core.burn_recorder import compare_and_diff_data
        src = bytes(200)
        rb = bytearray(200)
        for k in range(200):
            rb[k] = 0xFF  # 200 处全不一致
        diff = compare_and_diff_data(src, bytes(rb), 0)
        self.assertEqual(diff["mismatch_count"], 200)
        self.assertEqual(len(diff["samples"]), 50)          # 样本仍只留 50 处
        self.assertEqual(len(diff["mismatch_offsets"]), 200)  # 偏移全量记录

    def test_image_ok_marks_all_rows(self):
        """OK 时每行都标 OK，镜像文件生成"""
        src = bytes(range(64))
        diff = compare_and_diff_data(src, src, 0xA0000000)
        res = record_burn_event(self._record("PASS", diff, src, src), base_dir=self.test_dir)
        self.assertTrue(os.path.isfile(res["image_path"]))
        with open(res["image_path"], encoding="utf-8") as f:
            content = f.read()
        self.assertIn("OK - 全部地址逐字节一致", content)
        self.assertIn("0xA0000000", content)
        self.assertEqual(content.count("|  OK"), 4)  # 64 字节 = 4 行
        self.assertNotIn("NG  <<<", content)
        # 报告里应包含镜像路径
        with open(res["report_path"], encoding="utf-8") as f:
            report = f.read()
        self.assertIn("完整数据镜像存档", report)
        self.assertIn("全部地址区间比对均为 OK", report)

    def test_image_ng_marks_address_and_bits(self):
        """NG 地址和数据必须标记出来"""
        from core.burn_recorder import compare_and_diff_data, summarize_mismatch_ranges
        src = bytes(64)
        rb = bytearray(src)
        rb[3] = 0x01   # 行内第 3 偏移翻转位 [0]
        rb[20] = 0xFF  # 第二行
        diff = compare_and_diff_data(src, bytes(rb), 0xA0000000)
        res = record_burn_event(self._record("FAIL", diff, src, bytes(rb)), base_dir=self.test_dir)
        with open(res["image_path"], encoding="utf-8") as f:
            content = f.read()
        self.assertIn("NG - 共 2 字节不一致", content)
        self.assertIn("NG  <<<", content)
        self.assertIn("+0x3: 00→01 翻转位[0]", content)
        self.assertIn("+0x4: 00→FF 翻转位[0, 1, 2, 3, 4, 5, 6, 7]", content)
        self.assertIn("|  OK", content)  # 无差异行仍标 OK
        # 报告含 NG 区间汇总
        with open(res["report_path"], encoding="utf-8") as f:
            report = f.read()
        self.assertIn("NG 不一致地址区间", report)
        self.assertIn("0xA0000003", report)
        self.assertIn("0xA0000014", report)
        # 区间聚合函数
        ranges = summarize_mismatch_ranges([3, 20, 21, 22], 0xA0000000)
        self.assertEqual(ranges[0], "0xA0000003 (1 字节)")
        self.assertEqual(ranges[1], "0xA0000014 ~ 0xA0000016 (3 字节)")

    def test_readback_only_image(self):
        """无源程序基准时（如 E2）只出回读单栏镜像"""
        res = record_burn_event(self._record("PASS", None, b"", b"\xAA" * 32), base_dir=self.test_dir)
        with open(res["image_path"], encoding="utf-8") as f:
            content = f.read()
        self.assertIn("仅回读存档", content)
        self.assertIn("AA AA", content)

    def test_no_readback_no_image(self):
        res = record_burn_event(self._record("PASS", None, b"", b""), base_dir=self.test_dir)
        self.assertEqual(res["image_path"], "")

    def test_crc_format_diff_does_not_override_byte_diff(self):
        """逐字节比对一致时，CRC 格式/算法不同(0x04ae8410 vs 594928688)不得误报 MISMATCH"""
        rec = self._record("PASS", None, b"", b"")
        rec["expected_checksum"] = "0x04ae8410"
        rec["actual_checksum"] = "594928688"  # 下载器返回十进制，算法不同
        src = bytes(range(64))
        diff = compare_and_diff_data(src, src, 0xA0000000)
        rec["diff_details"] = diff
        rec["source_bytes"] = src
        rec["readback_bytes"] = src
        res = record_burn_event(rec, base_dir=self.test_dir)
        self.assertTrue(res["match"])
        self.assertIn("MATCH", res["match_str"])
        with open(res["report_path"], encoding="utf-8") as f:
            report = f.read()
        self.assertIn("MATCH", report)
        self.assertNotIn("MISMATCH", report)

    def test_byte_diff_mismatch_overrides_crc_match(self):
        """CRC 相同但逐字节比对不一致时，必须报 MISMATCH"""
        rec = self._record("FAIL", None, b"", b"")
        src = bytes(64)
        rb = bytearray(src)
        rb[7] = 0xFF
        diff = compare_and_diff_data(src, bytes(rb), 0xA0000000)
        rec["diff_details"] = diff
        rec["source_bytes"] = src
        rec["readback_bytes"] = bytes(rb)
        res = record_burn_event(rec, base_dir=self.test_dir)
        self.assertFalse(res["match"])
        self.assertIn("MISMATCH", res["match_str"])


if __name__ == "__main__":
    unittest.main()
