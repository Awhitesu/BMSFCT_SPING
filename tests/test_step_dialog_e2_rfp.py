import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QComboBox

app = QApplication.instance() or QApplication([])

from ui.dialogs.step_dialog import StepDialog


class TestStepDialogE2Rfp(unittest.TestCase):
    def setUp(self):
        self.mock_device_mgr = MagicMock()
        self.mock_device_mgr.db_manager.load_sys_config.return_value = {
            "e2_device_family": "RH850",
            "e2_interface": "FINE",
            "e2_speed": "1000000",
        }
        self.mock_programmer = MagicMock()
        self.mock_programmer.is_connected = True
        self.mock_programmer.last_error = ""
        self.mock_device_mgr.e2_rfp = self.mock_programmer

    def _make_burn_dialog(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("瑞萨 E2 仿真器 (rfp-cli)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()
        return dlg

    def test_device_in_downloader_combo(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        items = [dlg.device_combo.itemText(i) for i in range(dlg.device_combo.count())]
        self.assertIn("瑞萨 E2 仿真器 (rfp-cli)", items)

    def test_action_list(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("瑞萨 E2 仿真器 (rfp-cli)")
        dlg.on_device_changed()
        actions = [dlg.action_combo.itemText(i) for i in range(dlg.action_combo.count())]
        self.assertEqual(actions, ["连接检测", "查询状态", "执行烧录工程"])

    def test_burn_action_shows_e2_page(self):
        dlg = self._make_burn_dialog()
        self.assertEqual(dlg.param_stack.currentIndex(), 15)
        self.assertIsInstance(dlg.e2_file, QComboBox)
        self.assertTrue(dlg.e2_file.isEditable())
        self.assertTrue(hasattr(dlg, "btn_e2_file"))
        # E2 没有设备内工程存储，不提供"读取下载器工程"按钮
        self.assertFalse(hasattr(dlg, "btn_read_e2"))
        # 默认值取自 sys_config 的 e2_* 键
        self.assertEqual(dlg.combo_e2_family.currentText(), "RH850")
        self.assertEqual(dlg.e2_interface.text(), "FINE")
        self.assertEqual(dlg.e2_speed.text(), "1000000")

    def test_non_burn_action_shows_empty_page(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("瑞萨 E2 仿真器 (rfp-cli)")
        dlg.action_combo.setCurrentText("连接检测")
        dlg.on_action_changed()
        self.assertEqual(dlg.param_stack.currentIndex(), 14)
        data = dlg.get_data()
        self.assertEqual(data["type"], "连接检测")

    def test_params_serialization(self):
        dlg = self._make_burn_dialog()
        dlg.e2_file.setEditText("D:/fw/app_v1.hex")
        dlg.combo_e2_family.setCurrentText("RX")
        dlg.e2_interface.setText("CSI")
        dlg.e2_speed.setText("2000000")
        dlg.e2_timeout.setValue(180)
        dlg.cb_e2_verify_diff.setChecked(True)

        data = dlg.get_data()
        self.assertEqual(data["type"], "设置仪表")
        self.assertIn("FILE:D:/fw/app_v1.hex", data["params"])
        self.assertIn("DEVICE_FAMILY:RX", data["params"])
        self.assertIn("INTERFACE:CSI", data["params"])
        self.assertIn("SPEED:2000000", data["params"])
        self.assertIn("TIMEOUT:180", data["params"])
        self.assertIn("VERIFY_DIFF:1", data["params"])

    def test_load_existing_step_data_roundtrip(self):
        step_data = {
            "device": "瑞萨 E2 仿真器 (rfp-cli)",
            "action": "执行烧录工程",
            "params": "FILE:D:/fw/demo.mot / DEVICE_FAMILY:RL78 / INTERFACE:FINE / SPEED:500000 / TIMEOUT:250 / VERIFY_DIFF:1",
            "type": "设置仪表",
            "is_judgment": True,
            "fail_strategy": "重试3次",
        }
        dlg = StepDialog(step_data=step_data, device_manager=self.mock_device_mgr)
        self.assertEqual(dlg.device_combo.currentText(), "瑞萨 E2 仿真器 (rfp-cli)")
        self.assertEqual(dlg.action_combo.currentText(), "执行烧录工程")
        self.assertEqual(dlg.e2_file.currentText(), "D:/fw/demo.mot")
        self.assertEqual(dlg.combo_e2_family.currentText(), "RL78")
        self.assertEqual(dlg.e2_interface.text(), "FINE")
        self.assertEqual(dlg.e2_speed.text(), "500000")
        self.assertEqual(dlg.e2_timeout.value(), 250)
        self.assertTrue(dlg.cb_e2_verify_diff.isChecked())

        data = dlg.get_data()
        self.assertIn("FILE:D:/fw/demo.mot", data["params"])
        self.assertIn("TIMEOUT:250", data["params"])
        self.assertIn("VERIFY_DIFF:1", data["params"])

    def test_select_local_file_adds_to_combo(self):
        dlg = self._make_burn_dialog()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                   return_value=("C:/test/custom.hex", "All files")):
            dlg.select_e2_project()
        norm_expected = os.path.normpath("C:/test/custom.hex")
        self.assertEqual(dlg.e2_file.currentText(), norm_expected)
        data = dlg.get_data()
        self.assertIn(f"FILE:{norm_expected}", data["params"])


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(testRunner=runner)
