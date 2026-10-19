"""
单元测试：StepDialog 中 UDS 数据提取与数值计算配置界面的联动、持久化与回显
"""

import os
import sys
import unittest
import base64

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication(sys.argv)


class TestStepDialogUDSParser(unittest.TestCase):

    def setUp(self):
        self.dialog = StepDialog()
        self.dialog.category_combo.setCurrentText("报文交互")
        self.dialog.device_combo.setCurrentText("UDS 交互列表")
        self.dialog.action_combo.setCurrentText("顺序发送并校验")

    def tearDown(self):
        self.dialog.close()

    def test_extract_mode_ui_toggle(self):
        # 1. 默认“无数据提取”
        self.dialog.uds_extract_mode.setCurrentIndex(0)
        self.assertFalse(self.dialog.uds_extract_offset.isEnabled())
        self.assertTrue(self.dialog.uds_ext_detail_widget.isHidden())

        # 2. 切换到 ASCII
        self.dialog.uds_extract_mode.setCurrentIndex(1)
        self.assertTrue(self.dialog.uds_extract_offset.isEnabled())
        self.assertTrue(self.dialog.uds_ext_detail_widget.isHidden())

        # 3. 切换到 CALC (显示比例、偏移、单位，隐藏公式)
        self.dialog.uds_extract_mode.setCurrentIndex(2)
        self.assertFalse(self.dialog.uds_ext_detail_widget.isHidden())
        self.assertFalse(self.dialog.uds_extract_scale.isHidden())
        self.assertTrue(self.dialog.uds_extract_formula.isHidden())

        # 4. 切换到 FORMULA (显示公式，隐藏比例、偏移)
        self.dialog.uds_extract_mode.setCurrentIndex(3)
        self.assertFalse(self.dialog.uds_ext_detail_widget.isHidden())
        self.assertTrue(self.dialog.uds_extract_scale.isHidden())
        self.assertFalse(self.dialog.uds_extract_formula.isHidden())

    def test_add_and_load_uds_table_row_with_rules(self):
        # 添加一条带 ASCII 提取规则的指令
        self.dialog.uds_request.setText("22 F1 90")
        self.dialog.uds_expected.setText("62 F1 90")
        self.dialog.uds_extract_mode.setCurrentIndex(1) # ASCII
        self.dialog.uds_extract_offset.setValue(3)
        self.dialog.uds_extract_len.setValue(17)
        self.dialog.uds_extract_var.setText("ECU_VIN")
        self.dialog.add_uds_row()

        self.assertEqual(self.dialog.uds_table.rowCount(), 1)
        self.assertEqual(self.dialog.uds_table.item(0, 1).text(), "22 F1 90")
        self.assertEqual(self.dialog.uds_table.item(0, 4).text(), "ASCII(offset=3,len=17,var=ECU_VIN)")

        # 添加一条带 CALC 运算规则的指令
        self.dialog.uds_request.setText("22 01 01")
        self.dialog.uds_expected.setText("62 01 01")
        self.dialog.uds_extract_mode.setCurrentIndex(2) # CALC
        self.dialog.uds_extract_offset.setValue(3)
        self.dialog.uds_extract_len.setValue(2)
        self.dialog.uds_extract_scale.setValue(0.1)
        self.dialog.uds_extract_unit.setText("V")
        self.dialog.uds_extract_var.setText("BMS_PACK_VOLT")
        self.dialog.add_uds_row()

        self.assertEqual(self.dialog.uds_table.rowCount(), 2)
        self.assertEqual(self.dialog.uds_table.item(1, 4).text(), 'CALC(offset=3,len=2,scale=0.1,unit="V",var=BMS_PACK_VOLT)')

        # 双击第 0 行加载回显
        self.dialog.load_uds_row(0)
        self.assertEqual(self.dialog.uds_request.text(), "22 F1 90")
        self.assertEqual(self.dialog.uds_extract_mode.currentIndex(), 1)
        self.assertEqual(self.dialog.uds_extract_offset.value(), 3)
        self.assertEqual(self.dialog.uds_extract_len.value(), 17)
        self.assertEqual(self.dialog.uds_extract_var.text(), "ECU_VIN")

        # 生成脚本测试
        script = self.dialog._uds_script()
        lines = script.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "22 F1 90 => 62 F1 90 | ASCII(offset=3,len=17,var=ECU_VIN)")
        self.assertEqual(lines[1], '22 01 01 => 62 01 01 | CALC(offset=3,len=2,scale=0.1,unit="V",var=BMS_PACK_VOLT)')

    def test_save_and_reload_persistence(self):
        # 1. 构造工步数据
        script_text = "22 F1 90 => 62 F1 90 | ASCII(offset=3,len=17,var=ECU_VIN)\n22 01 01 => 62 01 01 | CALC(offset=3,len=2,scale=0.1,var=BMS_PACK_VOLT)"
        encoded = base64.urlsafe_b64encode(script_text.encode("utf-8")).decode("ascii")

        mock_step_data = {
            "name": "UDS测试工步",
            "type": "CAN交互",
            "device": "UDS 交互列表",
            "action": "顺序发送并校验",
            "params": f"TX_ID:0x7E0/RX_ID:0x7E8/TYPE:0/TIMEOUT:1000/CYCLES:1/INTERVAL:1/LIST64:{encoded}",
        }

        # 2. 反序列化加载到弹窗
        self.dialog._load_data(mock_step_data)

        self.assertEqual(self.dialog.uds_table.rowCount(), 2)
        self.assertEqual(self.dialog.uds_table.item(0, 4).text(), "ASCII(offset=3,len=17,var=ECU_VIN)")
        self.assertEqual(self.dialog.uds_table.item(1, 4).text(), "CALC(offset=3,len=2,scale=0.1,var=BMS_PACK_VOLT)")

        # 3. 再调用 get_data 导出保存
        exported = self.dialog.get_data()
        self.assertEqual(exported["device"], "UDS 交互列表")
        self.assertIn("LIST64:", exported["params"])


    def test_byte_grid_pdu_sync_and_selection_linkage(self):
        # 1. 输入期望响应 PDU，测试自动同步方块
        self.dialog.uds_workbench.response_sample.setText("62 01 01 0E 10 2A")
        self.dialog._sync_byte_grid_from_pdu()
        self.assertEqual(len(self.dialog.uds_byte_grid._blocks), 6)
        self.assertEqual(self.dialog.uds_byte_grid._blocks[0].lbl_hex.text(), "62")
        self.assertEqual(self.dialog.uds_byte_grid._blocks[3].lbl_hex.text(), "0E")
        self.assertEqual(self.dialog.uds_byte_grid._blocks[4].lbl_hex.text(), "10")

        # 2. 模拟拖拽选区选中 Byte 3~4 (0E 10)
        self.dialog.uds_byte_grid.set_selection(3, 2, emit_signal=True)
        self.assertEqual(self.dialog.uds_extract_offset.value(), 3)
        self.assertEqual(self.dialog.uds_extract_len.value(), 2)
        self.assertIn("3 ~ 4", self.dialog.lbl_byte_range.text())
        self.assertIn("0E 10", self.dialog.lbl_byte_hex.text())
        self.assertIn("3600", self.dialog.lbl_byte_int.text()) # 0x0E10 = 3600

        # 3. 校验方块的高亮状态
        self.assertFalse(self.dialog.uds_byte_grid._blocks[2].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[3].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[4].is_selected)
        self.assertFalse(self.dialog.uds_byte_grid._blocks[5].is_selected)

    def test_quick_action_buttons(self):
        # 测试点击快捷设定按钮切换模式
        self.dialog.btn_quick_ascii.click()
        self.assertEqual(self.dialog.uds_extract_mode.currentIndex(), 1) # ASCII

        self.dialog.btn_quick_calc.click()
        self.assertEqual(self.dialog.uds_extract_mode.currentIndex(), 2) # CALC

        self.dialog.btn_quick_formula.click()
        self.assertEqual(self.dialog.uds_extract_mode.currentIndex(), 3) # FORMULA

    def test_simplified_editor_controls_and_window_flags(self):
        from PySide6.QtCore import Qt
        self.assertTrue(self.dialog.windowFlags() & Qt.WindowMinimizeButtonHint)
        self.assertTrue(self.dialog.windowFlags() & Qt.WindowMaximizeButtonHint)
        for name in ('uds_simple_group', 'uds_proto_fmt', 'combo_uds_templates',
                     'btn_save_did_tpl', 'btn_import_did_csv', 'uds_update_btn'):
            self.assertFalse(hasattr(self.dialog, name), name)
        self.assertIs(self.dialog.btn_live_sniff.parentWidget(), self.dialog.uds_extract_group)
        self.dialog.uds_tabs.setCurrentIndex(2)
        self.dialog.show()
        app.processEvents()
        button = self.dialog.uds_add_btn
        table = self.dialog.uds_table
        self.assertLess(button.geometry().bottom(), table.geometry().top())
        self.assertGreater(button.geometry().center().x(), table.geometry().center().x())

    def test_inline_rule_edit_and_manual_offset_persist(self):
        from PySide6.QtCore import Qt
        self.dialog.uds_request.setText('22 F1 90')
        self.dialog.uds_extract_mode.setCurrentIndex(1)
        self.dialog.uds_extract_offset.setValue(4)
        self.dialog.uds_extract_len.setValue(17)
        self.dialog.uds_add_btn.click()
        rule = self.dialog.uds_table.item(0, 4)
        self.assertTrue(rule.flags() & Qt.ItemIsEditable)
        rule.setText('ASCII(offset=3,len=17,var=VIN)')
        self.dialog.uds_table.item(0, 2).setText('62 F1 90')
        self.assertEqual(self.dialog.uds_table.item(0, 3).text(), '\u6821\u9a8c')
        self.dialog.uds_table.item(0, 2).setText('')
        self.assertEqual(self.dialog.uds_table.item(0, 3).text(), '\u4e0d\u6821\u9a8c')
        self.assertIn('ASCII(offset=3,len=17,var=VIN)', self.dialog._uds_script())
        self.dialog.load_uds_row(0)
        self.assertEqual(self.dialog.uds_extract_offset.value(), 3)
        self.assertEqual(self.dialog.uds_extract_len.value(), 17)

    def test_workbench_preserves_parser_draft_and_list(self):
        self.assertEqual(self.dialog.uds_tabs.count(), 4)
        self.assertEqual(self.dialog.uds_tabs.currentIndex(), 2)
        self.dialog.uds_request.setText('22 F1 90')
        self.dialog.uds_tabs.setCurrentIndex(3)
        self.dialog.uds_extract_mode.setCurrentIndex(1)
        self.dialog.uds_extract_len.setValue(17)
        self.dialog.uds_tabs.setCurrentIndex(2)
        self.assertIn('22 F1 90', self.dialog.uds_draft_summary.text())
        self.dialog.uds_add_btn.click()
        self.assertEqual(self.dialog.uds_table.rowCount(), 1)
        self.assertIn('ASCII(offset=3,len=17)', self.dialog._uds_script())

    def test_response_sample_is_not_padded(self):
        self.dialog.uds_workbench.response_sample.setText('62 01 01')
        self.dialog.uds_extract_offset.setValue(3)
        self.dialog.uds_extract_len.setValue(17)
        self.assertEqual(self.dialog.uds_byte_grid._raw_bytes, bytes.fromhex('62 01 01'))

    def test_spinbox_to_bytegrid_selection_sync(self):
        self.dialog.uds_workbench.response_sample.setText("62 F1 90 41 42 43 44 45 46 47")
        self.dialog._sync_byte_grid_from_pdu()
        # 手动修改 SpinBox 偏移和长度
        self.dialog.uds_extract_offset.setValue(3)
        self.dialog.uds_extract_len.setValue(4)
        
        # 验证方块反向高亮 Byte 3 ~ 6
        self.assertFalse(self.dialog.uds_byte_grid._blocks[2].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[3].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[4].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[5].is_selected)
        self.assertTrue(self.dialog.uds_byte_grid._blocks[6].is_selected)
        self.assertFalse(self.dialog.uds_byte_grid._blocks[7].is_selected)
        self.assertIn("3 ~ 6", self.dialog.lbl_byte_range.text())


if __name__ == "__main__":
    unittest.main()


