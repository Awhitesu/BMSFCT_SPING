import unittest
from PySide6.QtCore import Qt,QPoint,QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QScrollArea,
    QDoubleSpinBox,QSpinBox,QComboBox)
from ui.parameter_wheel_guard import install_parameter_wheel_guard

app=QApplication.instance() or QApplication([])


def wheel(widget,delta=-120):
    pos=QPoint(10,10)
    event=QWheelEvent(QPointF(pos),QPointF(widget.mapToGlobal(pos)),QPoint(),QPoint(0,delta),
                      Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    QApplication.sendEvent(widget,event)


class ParameterWheelTests(unittest.TestCase):
    def setUp(self):self.guard=install_parameter_wheel_guard(app)

    def test_numeric_values_do_not_change_with_or_without_focus(self):
        for cls in (QSpinBox,QDoubleSpinBox):
            control=cls();control.setRange(0,100);control.setValue(7)
            for focused in (False,True):
                if focused:control.setFocus()
                else:control.clearFocus()
                wheel(control,120);wheel(control.lineEdit(),-120)
                self.assertEqual(control.value(),7)
            control.stepUp();self.assertEqual(control.value(),8)
            control.close()

    def test_combo_does_not_switch_and_explicit_selection_still_works(self):
        control=QComboBox();control.addItems(['CH1','CH2','CH3']);control.setCurrentIndex(1)
        wheel(control,-120);self.assertEqual(control.currentIndex(),1)
        control.setCurrentIndex(2);self.assertEqual(control.currentText(),'CH3')
        control.close()

    def test_wheel_over_field_scrolls_page(self):
        area=QScrollArea();area.setWidgetResizable(True);body=QWidget();layout=QVBoxLayout(body)
        control=QSpinBox();control.setValue(7);layout.addWidget(control)
        filler=QWidget();filler.setMinimumHeight(1500);layout.addWidget(filler)
        area.setWidget(body);area.resize(400,300);area.show();app.processEvents()
        before=area.verticalScrollBar().value();wheel(control,-120)
        self.assertGreater(area.verticalScrollBar().value(),before)
        self.assertEqual(control.value(),7);area.close()

    def test_install_is_idempotent(self):
        self.assertIs(install_parameter_wheel_guard(app),self.guard)


if __name__=='__main__':unittest.main()
