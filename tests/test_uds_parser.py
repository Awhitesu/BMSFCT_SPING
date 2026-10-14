"""
单元测试：UDS 响应数据提取、ASCII 转换与公式计算
"""

import unittest
from core.uds_parser import (
    parse_uds_rule_string,
    format_uds_rule_string,
    parse_uds_response_data,
)


class TestUDSParser(unittest.TestCase):

    def test_parse_and_format_rule_string(self):
        # 1. ASCII
        rule_str = "ASCII(offset=3,len=17,var=ECU_VIN)"
        rule = parse_uds_rule_string(rule_str)
        self.assertIsNotNone(rule)
        self.assertEqual(rule["mode"], "ASCII")
        self.assertEqual(rule["offset"], 3)
        self.assertEqual(rule["length"], 17)
        self.assertEqual(rule["var_name"], "ECU_VIN")
        formatted = format_uds_rule_string(rule)
        self.assertEqual(formatted, "ASCII(offset=3,len=17,var=ECU_VIN)")

        # 2. CALC
        calc_str = 'CALC(offset=3,len=2,endian=little,signed=1,scale=0.1,offset_val=-40.0,unit="A",var=BMS_CURR)'
        rule_calc = parse_uds_rule_string(calc_str)
        self.assertIsNotNone(rule_calc)
        self.assertEqual(rule_calc["mode"], "CALC")
        self.assertEqual(rule_calc["offset"], 3)
        self.assertEqual(rule_calc["length"], 2)
        self.assertEqual(rule_calc["endian"], "little")
        self.assertTrue(rule_calc["signed"])
        self.assertAlmostEqual(rule_calc["scale"], 0.1)
        self.assertAlmostEqual(rule_calc["offset_val"], -40.0)
        self.assertEqual(rule_calc["unit"], "A")
        self.assertEqual(rule_calc["var_name"], "BMS_CURR")

        # 3. FORMULA
        form_str = 'FORMULA(offset=3,len=2,expr="(val * 0.05) + 12",unit="kPa",var=PRESS)'
        rule_form = parse_uds_rule_string(form_str)
        self.assertIsNotNone(rule_form)
        self.assertEqual(rule_form["mode"], "FORMULA")
        self.assertEqual(rule_form["formula"], "(val * 0.05) + 12")
        self.assertEqual(rule_form["unit"], "kPa")
        self.assertEqual(rule_form["var_name"], "PRESS")

    def test_ascii_extraction(self):
        # 62 F1 90 + 'LFVBA21A1N0000001'
        raw = bytes.fromhex("62 F1 90 4C 46 56 42 41 32 31 41 31 4E 30 30 30 30 30 30 31")
        rule = {"mode": "ASCII", "offset": 3, "length": 17, "var_name": "ECU_VIN"}
        res = parse_uds_response_data(raw, rule)
        self.assertTrue(res["success"])
        self.assertEqual(res["value"], "LFVBA21A1N0000001")
        self.assertEqual(res["display"], "LFVBA21A1N0000001")

    def test_calc_big_endian_linear(self):
        # 62 01 01 + 0E 10 (3600 * 0.1 = 360.0 V)
        raw = bytes.fromhex("62 01 01 0E 10")
        rule = {
            "mode": "CALC",
            "offset": 3,
            "length": 2,
            "endian": "big",
            "scale": 0.1,
            "offset_val": 0.0,
            "unit": "V",
            "var_name": "PACK_VOLT",
        }
        res = parse_uds_response_data(raw, rule)
        self.assertTrue(res["success"])
        self.assertEqual(res["raw_int"], 3600)
        self.assertAlmostEqual(res["value"], 360.0)
        self.assertEqual(res["display"], "360.0 V")

    def test_calc_with_negative_offset(self):
        # 62 01 02 + 03 E8 (1000 * 0.1 - 40 = 60.0 A)
        raw = bytes.fromhex("62 01 02 03 E8")
        rule = {
            "mode": "CALC",
            "offset": 3,
            "length": 2,
            "scale": 0.1,
            "offset_val": -40.0,
            "unit": "A",
            "var_name": "PACK_CURR",
        }
        res = parse_uds_response_data(raw, rule)
        self.assertTrue(res["success"])
        self.assertEqual(res["raw_int"], 1000)
        self.assertAlmostEqual(res["value"], 60.0)
        self.assertEqual(res["display"], "60.0 A")

    def test_formula_custom_math(self):
        # 62 01 04 + 02 58 (600, (600 * 0.05) + 12 = 42.0 kPa)
        raw = bytes.fromhex("62 01 04 02 58")
        rule = {
            "mode": "FORMULA",
            "offset": 3,
            "length": 2,
            "formula": "(val * 0.05) + 12",
            "unit": "kPa",
            "var_name": "PRESS",
        }
        res = parse_uds_response_data(raw, rule)
        self.assertTrue(res["success"])
        self.assertEqual(res["raw_int"], 600)
        self.assertAlmostEqual(res["value"], 42.0)
        self.assertEqual(res["display"], "42.0 kPa")

    def test_boundary_and_error_handling(self):
        raw = bytes.fromhex("62 01")
        # 超出长度
        rule = {"mode": "ASCII", "offset": 5, "length": 2}
        res = parse_uds_response_data(raw, rule)
        self.assertFalse(res["success"])
        self.assertIn("超出响应长度", res["error"])

        # 截取字节不足
        rule_short = {"mode": "CALC", "offset": 1, "length": 5}
        res_short = parse_uds_response_data(raw, rule_short)
        self.assertFalse(res_short["success"])
        self.assertIn("截取字节不足", res_short["error"])


if __name__ == "__main__":
    unittest.main()
