import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 设置 Qt 无头模式
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

# 确保 QApplication 存在
app = QApplication.instance() or QApplication(sys.argv)

from ui.tabs.renesas_e2_tab import RenesasE2Tab


def _make_fake_hex():
    fd, path = tempfile.mkstemp(suffix=".hex")
    with os.fdopen(fd, "w") as f:
        f.write(":10000000214601360121470136007EFE09D2190140\n:00000001FF\n")
    return path


class TestRenesasE2TabRun(unittest.TestCase):
    def setUp(self):
        self.mock_programmer = MagicMock()
        self.mock_programmer.rfp_cli_path = "C:/rfp/rfp-cli.exe"
        self.mock_programmer.device_family = "RH850"
        self.mock_programmer.interface = "FINE"
        self.mock_programmer.speed = "1000000"
        self.mock_programmer.is_connected = True
        self.mock_programmer.last_error = ""
        self.mock_programmer.query_state.return_value = "Ready"
        self.mock_programmer.run_project.return_value = True
        self.mock_programmer.get_checksum.return_value = "0x12345678"
        self.mock_programmer.get_recent_log.return_value = ""
        self.mock_programmer.read_chip.return_value = False

        self.mock_dm = MagicMock()
        self.mock_dm.e2_rfp = self.mock_programmer

        self.tab = RenesasE2Tab(self.mock_dm)
        self.hex_path = _make_fake_hex()

    def tearDown(self):
        self.tab.status_timer.stop()
        self.tab.deleteLater()
        try:
            os.remove(self.hex_path)
        except OSError:
            pass

    def _select_project_quietly(self, path):
        self.tab.combo_project.blockSignals(True)
        self.tab.combo_project.setEditText(path)
        self.tab.combo_project.blockSignals(False)

    def test_initial_ui_state(self):
        self.assertTrue(self.tab.btn_run.isEnabled())
        self.assertTrue(self.tab.btn_browse.isEnabled())
        self.assertEqual(self.tab.combo_project.count(), 1)
        self.assertIn("浏览本地工程文件", self.tab.combo_project.currentText())

    def test_buttons_disabled_when_disconnected(self):
        self.mock_programmer.is_connected = False
        self.tab.refresh_connection_state()
        self.assertFalse(self.tab.btn_run.isEnabled())
        self.assertFalse(self.tab.btn_query.isEnabled())
        # 浏览本地文件不依赖设备连接
        self.assertTrue(self.tab.btn_browse.isEnabled())

    def test_select_local_project_file(self):
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName") as mock_dialog:
            mock_dialog.return_value = ("C:/test_folder/local_proj.hex", "")
            self.tab.select_project()
            self.assertIn("local_proj.hex", self.tab.combo_project.currentText())

    def test_query_state(self):
        with patch("threading.Thread") as mock_thread:
            self.tab.query_state()
            self.assertTrue(self.tab._running)
            target_func = mock_thread.call_args[1]["target"]
            target_func()
        self.assertFalse(self.tab._running)
        self.assertEqual(self.tab.lbl_device_state.text(), "Ready")

    def test_run_project_success(self):
        self._select_project_quietly(self.hex_path)
        fake_record = {
            "match": True,
            "match_str": "MATCH (100% 吻合)",
            "diagnosis": "烧录成功完成",
            "report_path": "logs/burn_records/reports/x.log",
        }
        with patch("threading.Thread") as mock_thread, \
                patch("ui.tabs.renesas_e2_tab.record_burn_event", return_value=fake_record) as mock_record:
            self.tab.run_project()
            self.assertTrue(self.tab._running)
            target_func = mock_thread.call_args[1]["target"]
            target_func()

        self.mock_programmer.run_project.assert_called_once()
        call_kwargs = self.mock_programmer.run_project.call_args
        self.assertEqual(call_kwargs[0][0], self.hex_path)
        self.assertEqual(call_kwargs[1]["timeout_s"], 300)
        self.assertTrue(mock_record.called)
        self.assertFalse(self.tab._running)
        self.assertEqual(self.tab.lbl_device_state.text(), "Pass")
        self.assertEqual(self.tab.lbl_audit_act_crc.text(), "0x12345678")
        self.assertIn("PASS", self.tab.log_output.toPlainText())
        self.assertIn("MATCH", self.tab.txt_audit_summary.toPlainText())

    def test_run_project_failure(self):
        self._select_project_quietly(self.hex_path)
        self.mock_programmer.run_project.return_value = False
        self.mock_programmer.last_error = "rfp-cli 烧录失败 (returncode=1)"
        fake_record = {
            "match": False,
            "match_str": "MISMATCH",
            "diagnosis": "烧录异常",
            "report_path": "logs/burn_records/reports/y.log",
        }
        with patch("threading.Thread") as mock_thread, \
                patch("ui.tabs.renesas_e2_tab.record_burn_event", return_value=fake_record):
            self.tab.run_project()
            target_func = mock_thread.call_args[1]["target"]
            target_func()

        self.assertFalse(self.tab._running)
        self.assertIn("rfp-cli 烧录失败", self.tab.lbl_device_state.text())
        self.assertIn("FAIL", self.tab.log_output.toPlainText())
        # 失败时按钮锁必须释放
        self.assertTrue(self.tab.btn_run.isEnabled())

    def test_run_project_invalid_extension(self):
        self._select_project_quietly("C:/test/readme.txt")
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.tab.run_project()
            self.assertTrue(mock_warn.called)
        self.mock_programmer.run_project.assert_not_called()

    def test_fetch_hardware_log(self):
        self.mock_programmer.get_recent_log.return_value = "rfp-cli 已就绪"
        with patch("threading.Thread") as mock_thread:
            self.tab.fetch_hardware_log()
            target_func = mock_thread.call_args[1]["target"]
            target_func()
        self.assertIn("rfp-cli 已就绪", self.tab.log_output.toPlainText())


if __name__ == "__main__":
    unittest.main()
