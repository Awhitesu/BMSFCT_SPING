from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSpinBox, QDoubleSpinBox, QStyleOptionSpinBox, QStyle

from main import _load_application_style

app = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('kind', [QSpinBox, QDoubleSpinBox])
def test_visible_plus_minus_and_real_clicks_from_other_working_directory(kind, tmp_path, monkeypatch):
    old_style=app.styleSheet()
    monkeypatch.chdir(tmp_path)
    _load_application_style(app, str(ROOT))
    control=kind();control.setRange(0,10);control.setValue(5);control.resize(280,48)
    try:
        control.show();app.processEvents()
        option=QStyleOptionSpinBox();control.initStyleOption(option)
        rects=[]
        for subcontrol in (QStyle.SC_SpinBoxUp,QStyle.SC_SpinBoxDown):
            rect=control.style().subControlRect(QStyle.CC_SpinBox,option,subcontrol,control)
            assert rect.width() >= 26 and rect.height() >= 14
            rects.append(rect)
            image=control.grab(rect).toImage()
            bright=sum(1 for x in range(image.width()) for y in range(image.height())
                       if min(image.pixelColor(x,y).red(),image.pixelColor(x,y).green(),image.pixelColor(x,y).blue()) > 180)
            assert bright >= 8, 'The plus/minus glyph must be visibly drawn'
        assert not rects[0].adjusted(1,1,-1,-1).intersects(rects[1].adjusted(1,1,-1,-1))
        QTest.mouseClick(control,Qt.LeftButton,pos=rects[0].center())
        assert control.value()==6
        QTest.mouseClick(control,Qt.LeftButton,pos=rects[1].center())
        assert control.value()==5
        control.setEnabled(False)
        QTest.mouseClick(control,Qt.LeftButton,pos=rects[0].center())
        assert control.value()==5
    finally:
        control.close();app.setStyleSheet(old_style)
