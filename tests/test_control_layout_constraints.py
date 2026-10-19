from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication,QTabWidget,QWidget,QToolButton

from ui.tabs.board_debug_tab import BoardDebugTab
from ui.tabs.easy320_tab_standalone import Easy320StandaloneTab
from ui.widgets.byte_grid_widget import ByteGridWidget
from ui.widgets.eliding_label import ElidingLabel
from ui.dialogs.step_dialog import StepDialog


app=QApplication.instance() or QApplication([])


@pytest.fixture
def theme():
    old=app.styleSheet()
    app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
    yield
    app.setStyleSheet(old)


def test_board_ip_editor_stays_within_its_table_cell(theme):
    db=Mock();db.load_sys_config.return_value={'rn_boards':{
        f'resistor{i}':dict(enabled=True,ip=f'192.0.2.{i}') for i in range(1,7)}}
    page=BoardDebugTab(Mock(db_manager=db),'resistor')
    try:
        page.resize(650,480);page.show();app.processEvents()
        for row,fields in enumerate(page.fields):
            ip=fields[0]
            cell=page.config.visualRect(page.config.model().index(row,2))
            assert ip.width()>=150
            assert ip.geometry().right()<=cell.right()
    finally:
        page.shutdown();page.close()


@pytest.mark.parametrize('kind',['easy320','hsd'])
def test_two_line_relay_buttons_keep_their_height_with_theme(theme,kind):
    db=Mock()
    if kind=='hsd':
        db.load_sys_config.return_value={'rn_boards':{'hsd1':dict(enabled=True,ip='192.0.2.1')}}
        page=BoardDebugTab(Mock(db_manager=db),'hsd')
        buttons=page.relay_buttons
    else:
        db.load_sys_config.return_value={'easy320_channels':[dict(do='DO0',name='上升气缸')]}
        driver=SimpleNamespace(ip='192.0.2.1',port=502,is_connected=False)
        page=Easy320StandaloneTab(driver,SimpleNamespace(easy320=driver,db_manager=db))
        buttons=page.relay_buttons
    try:
        page.resize(650,480);page.show();app.processEvents()
        for button in buttons:
            assert button.height()>=56
            assert button.fontMetrics().lineSpacing()*2<=button.height()
    finally:
        page.shutdown();page.close()


def test_byte_labels_do_not_inherit_frame_border_and_fit_when_selected(theme):
    widget=ByteGridWidget();widget.set_bytes(bytes(range(32)))
    try:
        widget.resize(600,140);widget.show();app.processEvents()
        for selected in (False,True):
            widget.set_selection(0,32 if selected else 0,emit_signal=False)
            app.processEvents()
            for block in widget._blocks:
                for label in (block.lbl_idx,block.lbl_hex,block.lbl_ascii):
                    assert label.contentsRect().height()>=label.fontMetrics().tightBoundingRect(label.text()).height()
            assert widget.scroll_area.viewport().height()>=widget._blocks[0].height()+16
    finally:widget.close()


def test_long_heading_keeps_full_value_without_forcing_parent_width(theme):
    label=ElidingLabel('长标题'*200)
    try:
        label.resize(260,64);label.show();app.processEvents()
        assert label.minimumSizeHint().width()==0
        assert label.text()==label.toolTip()=='长标题'*200
        assert label.grab().deviceIndependentSize().width()==260
    finally:label.close()


def test_ascii_preview_escapes_control_characters_and_retains_full_tooltip(theme):
    dialog=StepDialog()
    try:
        dialog.uds_extract_mode.setCurrentIndex(1)
        dialog._refresh_byte_grid_preview(0,6,b'A\nB\tC\x01')
        text=dialog.lbl_byte_phys.text()
        assert '\n' not in text and '\t' not in text and '\x01' not in text
        assert r'\n' in text and r'\t' in text and r'\u0001' in text
        assert dialog.lbl_byte_phys.toolTip()==text
    finally:dialog.close()


def test_tab_scroll_arrows_have_separate_click_areas(theme):
    tabs=QTabWidget()
    try:
        for index in range(8):tabs.addTab(QWidget(),'长名称标签 '+str(index))
        tabs.resize(350,200);tabs.show();app.processEvents()
        buttons=tabs.tabBar().findChildren(QToolButton)
        assert len(buttons)==2
        assert all(button.width()>=28 for button in buttons)
        assert not buttons[0].geometry().adjusted(2,2,-2,-2).intersects(buttons[1].geometry().adjusted(2,2,-2,-2))
    finally:tabs.close()


def test_uds_preview_labels_receive_nonzero_shared_width(theme):
    dialog=StepDialog()
    try:
        # Select the actual UDS list page via public navigation.
        dialog.category_combo.setCurrentText('报文交互')
        dialog.device_combo.setCurrentText('UDS 交互列表')
        dialog.resize(800,600);dialog.show();app.processEvents()
        dialog.uds_tabs.setCurrentIndex(3)  # Data parser follows the service sequence.
        app.processEvents()
        labels=(dialog.lbl_byte_range,dialog.lbl_byte_hex,dialog.lbl_byte_int,dialog.lbl_byte_phys)
        assert all(label.isVisibleTo(dialog) and label.width()>30 for label in labels)
    finally:dialog.close()
