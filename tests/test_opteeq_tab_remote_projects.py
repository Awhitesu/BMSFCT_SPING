import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 设置 Qt 无头模式
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

# 确保 QApplication 存在
app = QApplication.instance() or QApplication(sys.argv)

from ui.tabs.opteeq_tab import OpteeQTab


class TestOpteeQTabUnifiedProjects(unittest.TestCase):
    def setUp(self):
        self.mock_programmer = MagicMock()
        self.mock_programmer.ip = "192.168.0.100"
        self.mock_programmer.port = 10023
        self.mock_programmer.is_connected = True
        self.mock_programmer.last_error = ""

        self.mock_dm = MagicMock()
        self.mock_dm.opteeq = self.mock_programmer

        self.tab = OpteeQTab(self.mock_dm)

    def tearDown(self):
        self.tab.status_timer.stop()
        self.tab.deleteLater()

    def test_initial_ui_state(self):
        self.assertTrue(self.tab.btn_read_remote.isEnabled())
        self.assertTrue(self.tab.btn_browse.isEnabled())
        self.assertEqual(self.tab.combo_project.count(), 1)
        self.assertIn("点击右侧读取", self.tab.combo_project.currentText())
        # 初始提示项，导出应禁用
        self.assertFalse(self.tab.btn_export_remote.isEnabled())

    def test_remote_projects_loaded_success(self):
        mock_files = ["proj_app_v1.opj", "proj_boot_v2.opj"]
        self.tab._on_remote_projects_loaded(mock_files)

        self.assertEqual(self.tab.combo_project.count(), 2)
        self.assertEqual(self.tab.combo_project.currentText(), "proj_app_v1.opj")
        self.assertTrue(self.tab.btn_export_remote.isEnabled())

    def test_remote_projects_loaded_empty(self):
        self.tab._on_remote_projects_loaded([])

        self.assertEqual(self.tab.combo_project.count(), 1)
        self.assertIn("无脱机工程", self.tab.combo_project.currentText())
        self.assertFalse(self.tab.btn_export_remote.isEnabled())

    def test_select_local_project_file(self):
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName") as mock_dialog:
            mock_dialog.return_value = ("C:/test_folder/local_proj.opj", "")
            self.tab.select_project()
            self.assertIn("local_proj.opj", self.tab.combo_project.currentText())

    @patch("PySide6.QtWidgets.QFileDialog.getSaveFileName")
    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_export_remote_project(self, mock_info, mock_save_dialog):
        mock_files = ["bms_aging_test.opj"]
        self.tab._on_remote_projects_loaded(mock_files)

        local_target = os.path.abspath("C:/temp/bms_aging_test.opj")
        mock_save_dialog.return_value = (local_target, "")
        self.mock_programmer.download_project_file.return_value = True

        with patch("threading.Thread") as mock_thread:
            self.tab.export_remote_project()
            target_func = mock_thread.call_args[1]["target"]
            target_func()

        self.mock_programmer.download_project_file.assert_called_once_with(
            "bms_aging_test.opj", local_target
        )

    def test_direct_run_remote_opj(self):
        # 选中下载器工程后，直接点击执行 CH1 烧录
        mock_files = ["Geely_VE21_DisableHSM_V2.opj"]
        self.tab._on_remote_projects_loaded(mock_files)
        self.assertEqual(self.tab.combo_project.currentText(), "Geely_VE21_DisableHSM_V2.opj")

        self.mock_programmer.run_project.return_value = True

        with patch("threading.Thread") as mock_thread:
            self.tab.run_project()
            self.assertTrue(self.tab._running)
            self.assertTrue(mock_thread.called)


if __name__ == "__main__":
    unittest.main()
