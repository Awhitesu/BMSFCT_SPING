import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.dialogs.step_dialog import StepDialog

class TestStepDialogEmptyParam(unittest.TestCase):
    def test_sg400_connect_detect_shows_page_empty(self):
        dlg = StepDialog()
        # 选择 设备操作 -> 下载器 -> 力捷丰 SG400
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("连接检测")
        dlg.on_action_changed()
        
        self.assertEqual(dlg.param_stack.currentIndex(), 14)
        self.assertEqual(dlg.param_stack.currentWidget(), dlg.page_empty)
        self.assertIn("无需配置额外参数", dlg.lbl_empty_param_tip.text())
        
        data = dlg.get_data()
        self.assertEqual(data["device"], "力捷丰 SG400 下载器 (OpteeQ SG400)")
        self.assertEqual(data["action"], "连接检测")
        self.assertEqual(data["type"], "连接检测")
        self.assertEqual(data["params"], "--")
        self.assertNotIn("V", data["params"])
        self.assertNotIn("A", data["params"])

    def test_sg400_query_state_shows_page_empty(self):
        dlg = StepDialog()
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("查询状态")
        dlg.on_action_changed()
        
        self.assertEqual(dlg.param_stack.currentIndex(), 14)
        self.assertEqual(dlg.param_stack.currentWidget(), dlg.page_empty)
        
        data = dlg.get_data()
        self.assertEqual(data["type"], "查询状态")
        self.assertEqual(data["params"], "--")

    def test_sg400_burn_project_shows_page_opteeq(self):
        dlg = StepDialog()
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("下载器")
        dlg.device_combo.setCurrentText("力捷丰 SG400 下载器 (OpteeQ SG400)")
        dlg.action_combo.setCurrentText("执行烧录工程")
        dlg.on_action_changed()
        
        self.assertEqual(dlg.param_stack.currentIndex(), 11)
        self.assertEqual(dlg.param_stack.currentWidget(), dlg.page_opteeq)
        
        dlg.opteeq_project.setCurrentText("C:/projects/demo.opj")
        dlg.opteeq_timeout.setValue(120)
        data = dlg.get_data()
        self.assertEqual(data["type"], "设置仪表")
        self.assertIn("PROJECT:C:/projects/demo.opj", data["params"])
        self.assertIn("TIMEOUT:120", data["params"])
        self.assertNotIn("V", data["params"])

    def test_load_existing_sg400_step(self):
        existing_step = {
            "device": "力捷丰 SG400 下载器 (OpteeQ SG400)",
            "action": "连接检测",
            "params": "--",
            "type": "连接检测",
            "is_judgment": False,
            "fail_strategy": "失败停止"
        }
        dlg = StepDialog(step_data=existing_step)
        self.assertEqual(dlg.device_combo.currentText(), "力捷丰 SG400 下载器 (OpteeQ SG400)")
        self.assertEqual(dlg.action_combo.currentText(), "连接检测")
        self.assertEqual(dlg.param_stack.currentIndex(), 14)
        self.assertEqual(dlg.param_stack.currentWidget(), dlg.page_empty)
        
        data = dlg.get_data()
        self.assertEqual(data["params"], "--")
        self.assertEqual(data["type"], "连接检测")

    def test_mes_and_barrier_show_page_empty(self):
        dlg = StepDialog()
        dlg.category_combo.setCurrentText("三方协议")
        dlg.device_combo.setCurrentText("MES过站校验")
        dlg.on_action_changed()
        self.assertEqual(dlg.param_stack.currentIndex(), 14)
        
        dlg.category_combo.setCurrentText("通用交互")
        # 继电器全部断开
        dlg.category_combo.setCurrentText("设备操作")
        dlg.sub_category_combo.setCurrentText("继电器")
        dlg.action_combo.setCurrentText("全部断开")
        dlg.on_action_changed()
        self.assertEqual(dlg.param_stack.currentIndex(), 14)

if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    unittest.main(testRunner=runner)
