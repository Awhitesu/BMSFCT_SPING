import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox

app = QApplication.instance() or QApplication([])

from ui.dialogs.step_dialog import StepDialog

class TestStepDialogDownloaderProject(unittest.TestCase):
    def setUp(self):
        self.mock_device_mgr = MagicMock()
        self.mock_programmer = MagicMock()
        self.mock_programmer.is_connected = True
        self.mock_programmer.get_project_list.return_value = [
            "UnlockHSMCode.opj",
            "Geely_VE21_DisableHSM_V2.opj",
            "EOLT_GMW_V05.hex.opj"
        ]
        self.mock_programmer.last_error = ""
        self.mock_device_mgr.opteeq = self.mock_programmer

    def test_opteeq_project_is_combobox(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        self.assertIsInstance(dlg.opteeq_project, QComboBox)
        self.assertTrue(dlg.opteeq_project.isEditable())
        self.assertTrue(hasattr(dlg, "btn_read_opteeq"))
        self.assertTrue(hasattr(dlg, "btn_opteeq_project"))

    def test_switch_to_burn_project_auto_preloads(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()

        self.assertEqual(dlg.param_stack.currentIndex(), 11)
        items = [dlg.opteeq_project.itemText(i) for i in range(dlg.opteeq_project.count())]
        self.assertIn("UnlockHSMCode.opj", items)
        self.assertIn("Geely_VE21_DisableHSM_V2.opj", items)

    def test_read_downloader_projects_action(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()

        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
            dlg.read_downloader_projects()
            self.assertTrue(mock_info.called)

        items = [dlg.opteeq_project.itemText(i) for i in range(dlg.opteeq_project.count())]
        self.assertEqual(items, [
            "UnlockHSMCode.opj",
            "Geely_VE21_DisableHSM_V2.opj",
            "EOLT_GMW_V05.hex.opj"
        ])

        dlg.opteeq_project.setCurrentText("Geely_VE21_DisableHSM_V2.opj")
        dlg.opteeq_timeout.setValue(180)
        data = dlg.get_data()
        self.assertEqual(data["type"], "设置仪表")
        self.assertIn("PROJECT:Geely_VE21_DisableHSM_V2.opj", data["params"])
        self.assertIn("TIMEOUT:180", data["params"])

    def test_load_existing_step_data(self):
        step_data = {
            "device": "力捷丰 SG400 下载器 (OpteeQ SG400)",
            "action": "执行烧录工程",
            "params": "PROJECT:UnlockHSMCode.opj / TIMEOUT:250",
            "type": "设置仪表",
            "is_judgment": True,
            "fail_strategy": "重试3次"
        }
        dlg = StepDialog(step_data=step_data, device_manager=self.mock_device_mgr)
        self.assertEqual(dlg.device_combo.currentText(), "力捷丰 SG400 下载器 (OpteeQ SG400)")
        self.assertEqual(dlg.action_combo.currentText(), "执行烧录工程")
        self.assertEqual(dlg.opteeq_project.currentText(), "UnlockHSMCode.opj")
        self.assertEqual(dlg.opteeq_timeout.value(), 250)

        data = dlg.get_data()
        self.assertIn("PROJECT:UnlockHSMCode.opj", data["params"])
        self.assertIn("TIMEOUT:250", data["params"])

    def test_select_local_file_adds_to_combo(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()

        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=("C:/test/custom.opj", "All files")):
            dlg.select_opteeq_project()

        norm_expected = os.path.normpath("C:/test/custom.opj")
        self.assertEqual(dlg.opteeq_project.currentText(), norm_expected)
        data = dlg.get_data()
        self.assertIn(f"PROJECT:{norm_expected}", data["params"])

    def test_dropdown_view_is_opaque(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        view = dlg.opteeq_project.view()
        self.assertIsNotNone(view)
        self.assertTrue(view.autoFillBackground())
        # Check that stylesheet sets solid background for both view and items
        qss = dlg.styleSheet()
        self.assertIn("QComboBox QAbstractItemView", qss)
        self.assertIn("QComboBox QAbstractItemView::item", qss)
        self.assertIn("background-color: #17172B", qss)

    def test_dropdown_full_text_popup_width_and_tooltips(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()

        # Add a very long engineering project name
        long_name = "EOL_81.01.02_EOL_12.00.01.3_V02_052716_DisableHSM_ExtraLong.hex.opj"
        dlg.opteeq_project.addItem(long_name)
        dlg._adjust_opteeq_project_popup_width()

        # Check tooltip is populated
        idx = dlg.opteeq_project.findText(long_name)
        self.assertEqual(dlg.opteeq_project.itemData(idx, Qt.ToolTipRole), long_name)

        # Trigger showPopup and check view minimum width is >= 560 and adapts to text
        dlg.opteeq_project.showPopup()
        view = dlg.opteeq_project.view()
        self.assertGreaterEqual(view.minimumWidth(), 560)
        fm = dlg.opteeq_project.fontMetrics()
        expected_min = fm.horizontalAdvance(long_name) + 60
        self.assertGreaterEqual(view.minimumWidth(), expected_min)

    def test_burn_verify_diff_ui_and_persistence(self):
        dlg = StepDialog(device_manager=self.mock_device_mgr)
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()

        self.assertTrue(hasattr(dlg, "group_burn_verify"))
        self.assertTrue(hasattr(dlg, "cb_opteeq_verify_diff"))
        self.assertFalse(dlg.cb_opteeq_verify_diff.isChecked())
        self.assertFalse(dlg.verify_sub_container.isEnabled())

        # Check enable verify diff
        dlg.cb_opteeq_verify_diff.setChecked(True)
        self.assertTrue(dlg.verify_sub_container.isEnabled())
        dlg.opteeq_project.setCurrentText("Geely_VE21_DisableHSM_V2.opj")

        data = dlg.get_data()
        self.assertIn("VERIFY_DIFF:1", data["params"])
        self.assertIn("VERIFY_SCOPE:", data["params"])
        self.assertIn("ARCHIVE_REPORT:1", data["params"])

        # Load back
        loaded_dlg = StepDialog(step_data=data, device_manager=self.mock_device_mgr)
        self.assertTrue(loaded_dlg.cb_opteeq_verify_diff.isChecked())
        self.assertTrue(loaded_dlg.verify_sub_container.isEnabled())

if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(testRunner=runner)
