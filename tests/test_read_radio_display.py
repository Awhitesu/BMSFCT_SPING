import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionButton
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])


def test_read_current_radio_is_round_and_units_still_follow_quantity():
    previous=app.styleSheet()
    app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
    dialog=StepDialog()
    try:
        dialog.param_stack.setCurrentWidget(dialog.page_read)
        dialog.resize(1000,700);dialog.show();app.processEvents()
        dialog.rb_curr.click();app.processEvents()
        assert dialog.rb_curr.isChecked() and not dialog.rb_volt.isChecked()
        assert {'A','mA'} <= {dialog.read_unit.itemText(i) for i in range(dialog.read_unit.count())}
        assert dialog.read_unit.findText('V') == -1
        button=dialog.rb_curr
        option=QStyleOptionButton();button.initStyleOption(option)
        rect=button.style().subElementRect(QStyle.SE_RadioButtonIndicator,option,button)
        pix=button.grab().toImage();center=pix.pixelColor(rect.center())
        corner=pix.pixelColor(rect.topLeft())
        assert center.green()>center.red()+50
        assert corner.green()<150  # selected dot cannot fill the square corner
        dialog.rb_volt.click();app.processEvents()
        assert dialog.rb_volt.isChecked() and not dialog.rb_curr.isChecked()
        assert {'V','mV'} <= {dialog.read_unit.itemText(i) for i in range(dialog.read_unit.count())}
        assert dialog.read_unit.findText('A') == -1
    finally:
        dialog.close();app.setStyleSheet(previous)
