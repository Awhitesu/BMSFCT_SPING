import pytest
from core.can_data_parser import parse_payload, validate_parser, bit_positions


def config(**changes):
    c=dict(length=8,start=0,bit=3,count=12,mode='bits',type='unsigned',
           endian='big',scale=1,offset=0,bit_order='motorola')
    c.update(changes)
    return c


def test_motorola_cross_byte_matches_dbc_encoder():
    import cantools
    text='''VERSION ""
NS_ :
BS_:
BU_: BMS
BO_ 256 Data: 8 BMS
 SG_ Value : 3|12@0+ (1,0) [0|4095] "" BMS
'''
    message=cantools.database.load_string(text,database_format='dbc').messages[0]
    for value in (0,1,0x123,0xABC,4095):
        payload=message.encode({'Value':value})
        assert parse_payload(payload,config())==(value,value)
    assert bit_positions(config())==[3,2,1,0,15,14,13,12,11,10,9,8]


def test_signed_motorola_and_bounds():
    assert parse_payload(bytes.fromhex('0F FF 00 00 00 00 00 00'),config(type='signed'))==(-1,-1)
    with pytest.raises(ValueError,match='超过'):
        validate_parser(config(start=7,bit=0,count=2))


def test_form_manual_motorola_roundtrip_and_capture():
    from PySide6.QtWidgets import QApplication
    from ui.widgets.can_parser_form import CanParserForm
    from core.can_data_parser import encode_parser
    app=QApplication.instance() or QApplication([])
    form=CanParserForm()
    try:
        form.load(encode_parser(config()))
        form.sample.setText('0A BC 00 00 00 00 00 00')
        assert form.config()['bit_order']=='motorola'
        assert '2748' in form.preview.text()
        form.bits_table.clearSelection()
        for position in bit_positions(config()):
            form.bits_table.item(position//8,7-position%8).setSelected(True)
        form.selected(True)
        assert form.start.value()==0 and form.bit.value()==3 and form.count.value()==12
    finally:form.close()


def test_capture_uses_response_id_and_updates_sample():
    import time
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.step_dialog import StepDialog
    app=QApplication.instance() or QApplication([])
    calls=[]
    def request(*args,**kwargs):
        calls.append(args)
        return {'data':bytes.fromhex('0A BC 00 00 00 00 00 00')}
    driver=SimpleNamespace(is_connected=True,channel='PCAN_USBBUS1',send_and_wait_response=request)
    dialog=StepDialog()
    dialog.device_manager=SimpleNamespace(pcan=driver)
    dialog.recipe_can_binding={'channel':'PCAN_USBBUS1'}
    try:
        dialog.category_combo.setCurrentText('报文交互')
        dialog.device_combo.setCurrentText('CAN 交互')
        dialog.action_combo.setCurrentText('交互/问答')
        dialog.c_id.setText('0x573');dialog.c_wait_id.setText('0x673')
        dialog._capture_can_response()
        deadline=time.monotonic()+2
        while dialog._can_capture_task.isRunning() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert calls[0][1]==0x573 and calls[0][5]==0x673
        assert dialog.can_parser.sample.text()=='0A BC 00 00 00 00 00 00'
        assert dialog.can_parser.length.value()==8
    finally:
        dialog._can_capture_task.shutdown()
        dialog.close()


@pytest.mark.parametrize('motorola,start,end,expected',[
    (True,(0,4),(1,7),(0,3,12)),
    (False,(0,1),(1,5),(0,6,5)),
])
def test_layout_drag_path_crosses_bytes_in_protocol_order(motorola,start,end,expected):
    from PySide6.QtWidgets import QApplication
    from ui.widgets.can_parser_form import CanParserForm
    app=QApplication.instance() or QApplication([])
    form=CanParserForm()
    try:
        form.enabled.setChecked(True)
        form.mode.setCurrentIndex(1);form.bit_order.setCurrentIndex(int(motorola))
        table=form.bits_table
        table.anchor=table._rank(table.item(*start))
        table._select_to(table.item(*end))
        table.anchor=None;table.commit_selection()
        assert (form.start.value(),form.bit.value(),form.count.value())==expected
        assert '[' in table.item(*start).text()
        from PySide6.QtCore import Qt
        selected={row*8+7-col for row in range(table.rowCount()) for col in range(8)
                  if table.item(row,col).data(Qt.UserRole)}
        assert selected==set(bit_positions(form.config()))
        table.clearSelection()
        assert table.item(*start).data(Qt.UserRole) is True
        assert '已选' in form.selection_summary.text()
    finally:form.close()


def test_saved_selection_paints_without_native_focus_or_selection():
    from PySide6.QtWidgets import QApplication,QStyleOptionViewItem,QTableWidget,QTableWidgetItem
    from PySide6.QtCore import Qt,QRect
    from PySide6.QtGui import QImage,QPainter,QColor
    from ui.widgets.can_parser_form import RangeHighlightDelegate
    app=QApplication.instance() or QApplication([])
    table=QTableWidget(1,1)
    item=QTableWidgetItem('1');item.setData(Qt.UserRole,True);table.setItem(0,0,item)
    image=QImage(100,60,QImage.Format_ARGB32);image.fill(QColor('black'))
    painter=QPainter(image);option=QStyleOptionViewItem();option.rect=QRect(0,0,100,60)
    try:RangeHighlightDelegate(table).paint(painter,option,table.model().index(0,0))
    finally:painter.end()
    assert image.pixelColor(10,10)==QColor('#087eaf')
    table.close()
