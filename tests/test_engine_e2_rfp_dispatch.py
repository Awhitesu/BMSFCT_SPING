"""
集成测试：执行引擎对瑞萨 E2 (rfp-cli) 下载器工步的分派与留痕
"""

import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QCoreApplication

from core.engine import ChannelWorker, SubStep, SubStepType

app = QCoreApplication.instance() or QCoreApplication([])

DEVICE = "瑞萨 E2 仿真器 (rfp-cli)"


class TestEngineE2RfpDispatch(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        self.programmer = MagicMock()
        self.programmer.is_connected = True
        self.programmer.tool_ready = True
        self.programmer.last_error = ""
        self.programmer.query_state.return_value = "Ready"
        self.programmer.connect.return_value = True
        self.programmer.run_project.return_value = True
        self.programmer.get_checksum.return_value = "0x00ABCDEF"
        self.mgr.e2_rfp = self.programmer
        self.worker = ChannelWorker(0, [], device_manager=self.mgr)

    def _run(self, action, params_str=""):
        sub_step = SubStep(
            type=SubStepType.SET_INSTRUMENT,
            params={"device": DEVICE, "action": action, "params": params_str},
        )
        return self.worker._execute_sub_step_logic(sub_step)

    def test_connect_action(self):
        success, result = self._run("连接检测")
        self.assertTrue(success)
        self.assertEqual(result, "Ready")
        self.programmer.connect.assert_called_once()

    def test_query_state_action(self):
        success, result = self._run("查询状态")
        self.assertTrue(success)
        self.assertEqual(result, "Ready")
        self.programmer.query_state.assert_called()

    def test_query_state_lost_fails(self):
        self.programmer.query_state.return_value = "Lost"
        success, result = self._run("查询状态")
        self.assertFalse(success)
        self.assertEqual(result, "Lost")

    def test_driver_missing(self):
        self.mgr.e2_rfp = None
        success, result = self._run("连接检测")
        self.assertFalse(success)

    def test_burn_success_with_verify_diff_and_archive(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name) / 'app.bin'
        source.write_bytes(b"\x55\xAA" * 128)
        def fake_read_chip(start, size, out_file):
            with open(out_file, "wb") as f:
                f.write(b"\x55\xAA" * 128)
            return True
        self.programmer.read_chip.side_effect = fake_read_chip

        with patch("core.burn_recorder.record_burn_event") as mock_record:
            mock_record.return_value = {"match": True}
            success, result = self._run(
                "执行烧录工程",
                f"FILE:{source} / DEVICE_FAMILY:RX / INTERFACE:CSI / SPEED:2000000 / TIMEOUT:120 / VERIFY_DIFF:1 / VERIFY_ADDRESS:0",
            )

        self.assertTrue(success)
        self.assertEqual("PASS", result)
        self.programmer.run_project.assert_called_once()
        call = self.programmer.run_project.call_args
        self.assertEqual(call[0][0], str(source))
        self.assertEqual(call[1]["timeout_s"], 120)
        # 连接参数覆盖到驱动实例
        self.assertEqual(self.programmer.device_family, "RX")
        self.assertEqual(self.programmer.interface, "CSI")
        self.assertEqual(self.programmer.speed, "2000000")
        # 回读 + 留痕
        self.programmer.read_chip.assert_called_once()
        self.programmer.get_checksum.assert_called_once()
        mock_record.assert_called_once()
        record = mock_record.call_args[0][0]
        self.assertEqual(record["result"], "PASS")
        self.assertEqual(record["project_name"], "app.bin")
        self.assertEqual(record["actual_checksum"], "0x00ABCDEF")

    def test_burn_success_without_verify_diff_no_archive(self):
        with patch("core.burn_recorder.record_burn_event") as mock_record:
            success, result = self._run("执行烧录工程", "FILE:D:/fw/app.hex / TIMEOUT:60")
        self.assertTrue(success)
        self.assertEqual(result, "PASS")
        self.programmer.read_chip.assert_not_called()
        mock_record.assert_not_called()

    def test_burn_failure(self):
        self.programmer.run_project.return_value = False
        self.programmer.last_error = "rfp-cli 烧录失败 (returncode=1)"
        with patch("core.burn_recorder.record_burn_event") as mock_record:
            success, result = self._run("执行烧录工程", "FILE:D:/fw/app.hex / VERIFY_DIFF:1")
        self.assertFalse(success)
        self.assertEqual(result, "rfp-cli 烧录失败 (returncode=1)")
        mock_record.assert_not_called()

    def test_burn_forward_slash_path_not_split(self):
        """FILE 值含 / 的 Windows 路径不得被参数分隔符截断"""
        success, result = self._run("执行烧录工程", "FILE:D:/fw dir/app_v2.mot / TIMEOUT:60")
        self.assertTrue(success)
        call = self.programmer.run_project.call_args
        self.assertEqual(call[0][0], "D:/fw dir/app_v2.mot")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(testRunner=runner)
