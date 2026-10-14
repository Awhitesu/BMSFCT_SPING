import unittest

from core.uds_dictionary import build_simple_uds_config


class TestUDSSimpleConfig(unittest.TestCase):
    def test_numeric_template_uses_stable_prefix(self):
        config = build_simple_uds_config({
            "name": "动力电池总压",
            "request": "22 01 01",
            "expected": "62 01 01 0E 10",
            "mode": "CALC",
            "offset": 3,
            "length": 2,
            "scale": 0.1,
            "unit": "V",
            "var_name": "BMS_PACK_VOLT",
        })
        self.assertEqual(config["expected"], "62 01 01")
        self.assertEqual(config["request"], "22 01 01")
        self.assertEqual(
            config["rule"],
            'CALC(offset=3,len=2,scale=0.1,unit="V",var=BMS_PACK_VOLT)',
        )

    def test_subfunction_response_clears_suppress_bit(self):
        config = build_simple_uds_config({
            "name": "扩展会话",
            "request": "10 83",
            "mode": "CALC",
            "offset": 2,
            "length": 1,
        })
        self.assertEqual(config["expected"], "50 03")


if __name__ == "__main__":
    unittest.main()
