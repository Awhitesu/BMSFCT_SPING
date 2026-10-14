"""
单元测试：UDSDictionaryManager 与增强解析能力
"""

import os
import sys
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.uds_dictionary import UDSDictionaryManager, DEFAULT_BMS_DIDS
from core.uds_parser import parse_uds_response_data, parse_uds_array_data


class TestUDSDictionaryAndParser(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.manager = UDSDictionaryManager(config_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_bms_dids_loaded(self):
        items = self.manager.get_all_items()
        self.assertGreaterEqual(len(items), 10)
        volt_item = next((x for x in items if "总压" in x["name"]), None)
        self.assertIsNotNone(volt_item)
        self.assertEqual(volt_item["did"], "01 01")
        self.assertEqual(volt_item["mode"], "CALC")
        self.assertEqual(volt_item["scale"], 0.1)

    def test_add_and_persistence(self):
        new_item = {
            "name": "⚡ 自定义绝缘阻抗",
            "did": "D0 01",
            "request": "22 D0 01",
            "expected": "62 D0 01 03 E8",
            "mode": "CALC",
            "offset": 3,
            "length": 2,
            "scale": 1.0,
            "unit": "kΩ",
            "var_name": "CUSTOM_ISO",
        }
        self.assertTrue(self.manager.add_or_update_item(new_item))

        # 重新创建 manager 加载持久化文件
        mgr2 = UDSDictionaryManager(config_dir=self.temp_dir)
        found = next((x for x in mgr2.get_all_items() if x["name"] == "⚡ 自定义绝缘阻抗"), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["did"], "D0 01")

    def test_csv_export_and_import(self):
        csv_file = os.path.join(self.temp_dir, "dids.csv")
        self.manager.export_to_csv(csv_file)
        self.assertTrue(os.path.isfile(csv_file))

        # 新开一个空目录的 manager 并从该 csv 导入
        empty_dir = os.path.join(self.temp_dir, "empty")
        mgr3 = UDSDictionaryManager(config_dir=empty_dir)
        imported = mgr3.import_from_csv(csv_file)
        self.assertGreaterEqual(imported, 10)

    def test_ascii_trim_null_and_padding(self):
        # 模拟带尾部 \x00 与 0xAA 填充字节的固件版本号
        raw_firmware = bytes([0x62, 0xF1, 0x89]) + b"V1.0.3\x00\xAA\xAA\xAA"
        rule = {"mode": "ASCII", "offset": 3, "length": 0, "var_name": "FW_VER"}
        res = parse_uds_response_data(raw_firmware, rule)
        self.assertTrue(res["success"])
        self.assertEqual(res["value"], "V1.0.3")

    def test_bms_cell_array_extraction(self):
        # 模拟 4 串电芯电压 (每串 2 字节, 大端, mV: 3200, 3205, 3210, 3215)
        raw_cells = bytes([0x62, 0x02, 0x01]) + bytes.fromhex("0C 80 0C 85 0C 8A 0C 8F")
        res = parse_uds_array_data(raw_cells, offset=3, item_size=2, count=4, scale=1.0, unit="mV", var_prefix="BMS_CELL_")
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 4)
        self.assertEqual(res["elements"], [3200, 3205, 3210, 3215])
        self.assertEqual(res["variables_map"]["BMS_CELL_0"], 3200)
        self.assertEqual(res["variables_map"]["BMS_CELL_3"], 3215)


if __name__ == "__main__":
    unittest.main()
