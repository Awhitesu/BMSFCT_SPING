"""
集成测试：执行引擎 UDS 响应数据提取、计算与变量写回闭环
"""

import unittest
import base64
from unittest.mock import MagicMock
from PySide6.QtCore import QCoreApplication
from core.engine import ChannelWorker, SubStep, SubStepType
from devices.uds_client import UDSResponse

app = QCoreApplication.instance() or QCoreApplication([])


class TestUDSEngineIntegration(unittest.TestCase):

    def setUp(self):
        self.mgr = MagicMock()
        self.pcan = MagicMock()
        self.pcan.is_connected = True
        self.mgr.pcan = self.pcan
        self.engine = ChannelWorker(0, [], device_manager=self.mgr)

    def test_uds_ascii_extraction_and_variable_writeback(self):
        # 模拟 UDS 返回 17位 VIN
        uds_client = MagicMock()
        # 62 F1 90 + 17 字节 VIN
        vin_bytes = b"\x62\xF1\x90LFVBA21A1N0000001"
        uds_client.request.return_value = UDSResponse(
            request_sid=0x22,
            raw=vin_bytes,
            positive=True,
            service_id=0x62,
            data=vin_bytes[1:],
        )
        self.pcan.create_uds_client.return_value = uds_client

        # 脚本: 22 F1 90 => 62 F1 90 | ASCII(offset=3,len=17,var=ECU_VIN)
        script_text = "22 F1 90 => 62 F1 90 | ASCII(offset=3,len=17,var=ECU_VIN)"
        encoded = base64.urlsafe_b64encode(script_text.encode("utf-8")).decode("ascii")

        params = {
            "device": "PCAN-UDS",
            "params": f"TX_ID:0x7E0/RX_ID:0x7E8/TYPE:0/TIMEOUT:1000/CYCLES:1/INTERVAL:1/LIST64:{encoded}",
        }
        sub_step = SubStep(type=SubStepType.CAN_INTERACT, params=params)

        self.engine.device_manager = self.mgr
        success, result_val = self.engine._execute_sub_step_logic(sub_step)
        if not success:
            print("TEST_FAIL_DEBUG:", result_val, "LAST_HW_LOG:", self.engine.last_hw_log)
        self.assertTrue(success)
        self.assertEqual(result_val, "LFVBA21A1N0000001")
        self.assertEqual(self.engine.variables.get("ECU_VIN"), "LFVBA21A1N0000001")

    def test_uds_calc_numeric_and_read_var_step(self):
        # 模拟 UDS 返回总压 3600 (0x0E 10) -> 360.0V
        uds_client = MagicMock()
        volt_bytes = bytes.fromhex("62 01 01 0E 10")
        uds_client.request.return_value = UDSResponse(
            request_sid=0x22,
            raw=volt_bytes,
            positive=True,
            service_id=0x62,
            data=volt_bytes[1:],
        )
        self.pcan.create_uds_client.return_value = uds_client

        # 脚本: 22 01 01 => 62 01 01 | CALC(offset=3,len=2,scale=0.1,unit="V",var=BMS_PACK_VOLT)
        script_text = '22 01 01 => 62 01 01 | CALC(offset=3,len=2,scale=0.1,unit="V",var=BMS_PACK_VOLT)'
        encoded = base64.urlsafe_b64encode(script_text.encode("utf-8")).decode("ascii")

        params = {
            "device": "PCAN-UDS",
            "params": f"TX_ID:0x7E0/RX_ID:0x7E8/TYPE:0/TIMEOUT:1000/CYCLES:1/INTERVAL:1/LIST64:{encoded}",
        }
        sub_step = SubStep(type=SubStepType.CAN_INTERACT, params=params)

        self.engine.device_manager = self.mgr
        success, result_val = self.engine._execute_sub_step_logic(sub_step)
        self.assertTrue(success)
        self.assertEqual(result_val, "360.0")
        self.assertAlmostEqual(self.engine.variables.get("BMS_PACK_VOLT"), 360.0)

        # 验证后续 READ_VAR 工步读取该变量
        read_step = SubStep(
            type=SubStepType.READ_VAR,
            params={"params": "VAR:BMS_PACK_VOLT"},
        )
        r_success, r_val = self.engine._execute_sub_step_logic(read_step)
        self.assertTrue(r_success)
        self.assertEqual(r_val, "360.00")

    def test_uds_backward_compatibility_without_rule(self):
        # 兼容旧配方: 无 pipe 解析规则
        uds_client = MagicMock()
        raw_bytes = bytes.fromhex("62 01 01")
        uds_client.request.return_value = UDSResponse(
            request_sid=0x22,
            raw=raw_bytes,
            positive=True,
            service_id=0x62,
            data=raw_bytes[1:],
        )
        self.pcan.create_uds_client.return_value = uds_client

        script_text = "22 01 01 => 62 01 01"
        encoded = base64.urlsafe_b64encode(script_text.encode("utf-8")).decode("ascii")

        params = {
            "device": "PCAN-UDS",
            "params": f"TX_ID:0x7E0/RX_ID:0x7E8/TYPE:0/TIMEOUT:1000/CYCLES:1/INTERVAL:1/LIST64:{encoded}",
        }
        sub_step = SubStep(type=SubStepType.CAN_INTERACT, params=params)

        self.engine.device_manager = self.mgr
        success, result_val = self.engine._execute_sub_step_logic(sub_step)
        self.assertTrue(success)
        self.assertEqual(result_val, "62 01 01")


if __name__ == "__main__":
    unittest.main()
