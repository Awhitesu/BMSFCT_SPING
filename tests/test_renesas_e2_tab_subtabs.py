import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from ui.tabs.renesas_e2_tab import RenesasE2Tab


class TestRenesasE2TabSubtabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.mock_dm = MagicMock()
        self.mock_programmer = MagicMock()
        self.mock_programmer.rfp_cli_path = "C:/rfp/rfp-cli.exe"
        self.mock_programmer.device_family = "RH850"
        self.mock_programmer.interface = "FINE"
        self.mock_programmer.speed = "1000000"
        self.mock_programmer.is_connected = True
        self.mock_programmer.query_state.return_value = "Ready"
        self.mock_programmer.get_recent_log.return_value = ""
        self.mock_dm.e2_rfp = self.mock_programmer
        self.tab = RenesasE2Tab(self.mock_dm)

    def tearDown(self):
        self.tab.status_timer.stop()
        self.tab.deleteLater()

    def test_subtabs_structure(self):
        """验证二级子 Tab 容器及两页的存在与名称"""
        self.assertIsNotNone(self.tab.sub_tabs)
        self.assertEqual(self.tab.sub_tabs.count(), 2)
        self.assertIn("烧录控制与防呆比对", self.tab.sub_tabs.tabText(0))
        self.assertIn("调试日志", self.tab.sub_tabs.tabText(1))

    def test_compact_header_controls(self):
        """验证顶部紧凑公用状态栏控件保持完备"""
        self.assertEqual(self.tab.edit_cli_path.text(), "C:/rfp/rfp-cli.exe")
        self.assertTrue(self.tab.edit_cli_path.isReadOnly())
        self.assertEqual(self.tab.combo_family.currentText(), "RH850")
        for family in ("RA", "RX", "RL78", "RH850", "RE"):
            self.assertGreaterEqual(self.tab.combo_family.findText(family), 0)
        self.assertEqual(self.tab.edit_interface.text(), "FINE")
        self.assertEqual(self.tab.edit_speed.text(), "1000000")
        self.assertIsNotNone(self.tab.lbl_device_state)
        self.assertIsNotNone(self.tab.btn_query)
        self.assertIsNotNone(self.tab.lbl_connection)

    def test_burn_tab_controls(self):
        """验证子 Tab 1 内部控件与防呆比对面板（E2 无设备内工程，无读取/导出按钮）"""
        self.assertIsNotNone(self.tab.combo_project)
        self.assertIsNotNone(self.tab.btn_browse)
        self.assertFalse(hasattr(self.tab, "btn_read_remote"))
        self.assertFalse(hasattr(self.tab, "btn_export_remote"))
        self.assertIsNotNone(self.tab.spin_timeout)
        self.assertIsNotNone(self.tab.btn_run)
        self.assertIsNotNone(self.tab.lbl_audit_source_file)
        self.assertIsNotNone(self.tab.lbl_audit_chip)
        self.assertIsNotNone(self.tab.lbl_audit_exp_crc)
        self.assertIsNotNone(self.tab.lbl_audit_act_crc)
        self.assertIsNotNone(self.tab.lbl_audit_status)
        self.assertIsNotNone(self.tab.lbl_audit_sectors)
        self.assertIsNotNone(self.tab.chk_deep_diff)
        self.assertIsNotNone(self.tab.btn_open_records)
        self.assertIsNotNone(self.tab.txt_audit_summary)

    def test_log_tab_controls(self):
        """验证子 Tab 2 内部控件及拉取底层日志按钮"""
        self.assertIsNotNone(self.tab.log_output)
        self.assertIsNotNone(self.tab.btn_fetch_hw_log)

    def test_audit_record_update_summary(self):
        """验证烧录完成事件触发时，全息摘要与比对结论同步更新"""
        self.tab._current_meta = {"opd_checksum": "0x12345678"}
        fake_res = {
            "match": True,
            "match_str": "MATCH (100% 吻合)",
            "act_crc": "0x12345678",
            "diagnosis": "测试通过",
            "report_path": "logs/burn_records/reports/test.log",
        }
        self.tab._on_burn_record_updated(fake_res)
        self.assertEqual(self.tab.lbl_audit_act_crc.text(), "0x12345678")
        self.assertEqual(self.tab.lbl_audit_status.text(), "MATCH (100% 吻合)")
        summary_text = self.tab.txt_audit_summary.toPlainText()
        self.assertIn("MATCH (100% 吻合)", summary_text)
        self.assertIn("logs/burn_records/reports/test.log", summary_text)

    def test_conn_params_saved_to_sys_config(self):
        """验证连接参数修改后写回 sys_config 并同步驱动实例"""
        self.mock_dm.db_manager.reset_mock()
        self.tab.combo_family.setCurrentText("RX")
        self.tab.edit_interface.setText("CSI")
        self.tab.edit_interface.editingFinished.emit()
        self.assertEqual(self.mock_programmer.device_family, "RX")
        self.assertEqual(self.mock_programmer.interface, "CSI")
        self.assertTrue(self.mock_dm.db_manager.save_sys_config.called)
        saved = self.mock_dm.db_manager.save_sys_config.call_args[0][0]
        self.assertEqual(saved["e2_device_family"], "RX")
        self.assertEqual(saved["e2_interface"], "CSI")


if __name__ == "__main__":
    unittest.main()
