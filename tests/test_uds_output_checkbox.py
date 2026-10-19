from PySide6.QtCore import Qt,QRect
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication,QStyleOptionViewItem,QStyle
from PySide6.QtTest import QTest
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])


def test_whole_cell_click_toggle_is_exclusive_and_visible_selected():
    p=StepDialog()
    try:
        p.category_combo.setCurrentIndex(1);p.device_combo.setCurrentIndex(1)
        for request in ('22 F1 90','22 F1 91','@SECURITY'):
            p._append_uds_table_row('request',request,'')
        p.resize(1600,1000);p.show();app.processEvents()
        table=p.uds_table
        for row in (0,1):
            table.scrollToItem(table.item(row,6));app.processEvents()
            rectangle=table.visualItemRect(table.item(row,6))
            QTest.mouseClick(table.viewport(),Qt.LeftButton,pos=rectangle.center())
            assert table.item(row,6).checkState()==Qt.Checked
        assert table.item(0,6).checkState()==Qt.Unchecked
        index=table.model().index(1,6)
        option=QStyleOptionViewItem();option.rect=QRect(0,0,100,40)
        option.widget=table;option.state=QStyle.State_Enabled|QStyle.State_Selected
        image=QImage(100,40,QImage.Format_ARGB32);image.fill(Qt.black)
        painter=QPainter(image)
        table.itemDelegateForColumn(6).paint(painter,option,index);painter.end()
        assert any(image.pixelColor(x,y).green()>140 and image.pixelColor(x,y).red()<100
                   for x in range(35,65) for y in range(8,32))
        table.scrollToItem(table.item(1,6));app.processEvents()
        QTest.mouseClick(table.viewport(),Qt.LeftButton,pos=table.visualItemRect(table.item(1,6)).center())
        assert table.item(1,6).checkState()==Qt.Unchecked
        assert not (table.item(2,6).flags() & Qt.ItemIsEnabled)
    finally:p.close()


def test_frozen_checkbox_styles_use_bundle_assets_not_working_directory(tmp_path,monkeypatch):
    import sys
    from ui.dialogs.step_dialog import _resolve_resource_stylesheet
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'_internal'),raising=False)
    css=_resolve_resource_stylesheet('image: url(resources/checkbox_green_tick.svg);')
    assert (tmp_path/'_internal/resources/checkbox_green_tick.svg').as_posix() in css
    assert 'url(resources/' not in css
