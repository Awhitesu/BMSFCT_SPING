import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])


def test_header_alignment_stable_after_save_reopen_and_channel_mode_switch():
    class Parent(QWidget):
        def recipe_can_binding(self):return dict(channel='ZLG_CAN1',mode='经典 CAN')
    parent=Parent();first=StepDialog(parent);second=None
    try:
        first.resize(1100,850);first.show()
        first.category_combo.setCurrentText('报文交互')
        first.device_combo.setCurrentText('CAN 交互')
        first.action_combo.setCurrentText('发送指令')
        for _ in range(3):app.processEvents()
        initial=first.category_combo.x()
        assert first.device_selection_form.labelAlignment() & Qt.AlignLeft
        assert initial==first.device_combo.x()==first.action_combo.x()
        label=first.device_selection_form.labelForField(first.category_combo)
        assert label.x()==first.can_send_row.x()
        for _ in range(3):
            first.action_combo.setCurrentText('接收指定帧ID');app.processEvents()
            first.action_combo.setCurrentText('发送指令');app.processEvents()
            assert first.category_combo.x()==initial
        saved=first.get_data()
        second=StepDialog(parent,step_data=saved);second.resize(1100,850);second.show()
        for _ in range(3):app.processEvents()
        assert second.category_combo.x()==initial
        assert second.can_send_title.width()>label.width()
        assert second.main_scroll.horizontalScrollBar().maximum()==0
    finally:
        if second:second.close()
        first.close();parent.close()
