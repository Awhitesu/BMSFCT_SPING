from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication
from core.uds_field_write import build,encode,decode,DEVICE
from core.engine import ChannelWorker,SubStep,SubStepType
from devices.uds_client import UDSResponse

app=QApplication.instance() or QApplication([])


def config(**kw):
    return dict(dict(tx_id=0x7e0,rx_id=0x7e8,did=0x1e5e,literal='AB',start=3,length=4,timeout=1000),**kw)


def test_date_ascii_and_one_based_slice():
    c=config();pdu,text=build(c,'qwrtyyuui',datetime(2026,10,19))
    assert text=='AB261019rtyy'
    assert pdu==b'\x2e\x1e\x5eAB261019rtyy'
    assert decode(encode(c))==c
    assert build(config(length=0),'qwrtyyuui',datetime(2027,1,2))[1]=='AB270102rtyyuui'


@pytest.mark.parametrize('barcode,c',[('',config()),('ab',config()),('abcd',config()),('a中文xx',config()),('abcdef',config(start=0)),('abcdef',config(literal='中文'))])
def test_invalid_input_fails_before_send(barcode,c):
    with pytest.raises(ValueError):build(c,barcode)


def worker(response):
    client=MagicMock();client.request.return_value=UDSResponse(request_sid=0x2e,raw=response,positive=response[0]==0x6e,service_id=response[0],data=response[1:])
    driver=MagicMock();driver.is_connected=True;driver.default_can_type=0;driver.create_uds_client.return_value=client
    w=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver));w.sn_barcode='qwrtyyuui'
    return w,client


def test_engine_sends_only_2e_and_checks_matching_did():
    w,client=worker(bytes.fromhex('6e 1e 5e'))
    ok,value=w._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(config()))))
    assert ok,value
    assert client.request.call_count==1
    sid,data=client.request.call_args.args
    assert sid==0x2e and data[:2]==b'\x1e\x5e'
    assert data[2:].decode().startswith('AB') and data.endswith(b'rtyy')
    for response in (bytes.fromhex('6e 1e 5f'),bytes.fromhex('7f 2e 31')):
        w,client=worker(response)
        assert not w._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(config()))))[0]


def test_missing_scan_never_sends():
    w,client=worker(bytes.fromhex('6e 1e 5e'));w.sn_barcode=''
    ok,_=w._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(config()))))
    assert not ok
    client.request.assert_not_called()


def test_written_text_variable_only_published_on_success():
    w,client=worker(bytes.fromhex('6e 1e 5e'))
    c=config(variable='EXPECTED_WRITE')
    sub=SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(c)))
    assert w._execute_sub_step_logic(sub)[0]
    assert w.variables['EXPECTED_WRITE']==client.request.call_args.args[1][2:].decode('ascii')
    assert w.variables['主板序列号']==w.variables['EXPECTED_WRITE']
    w.sn_barcode=''
    assert not w._execute_sub_step_logic(sub)[0]
    assert 'EXPECTED_WRITE' not in w.variables
    assert '主板序列号' not in w.variables


def test_fixed_board_serial_without_optional_variable():
    w,client=worker(bytes.fromhex('6e 1e 5e'))
    sub=SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(config())))
    ok,value=w._execute_sub_step_logic(sub)
    assert ok and value=='6E'
    assert w.variables['主板序列号']==client.request.call_args.args[1][2:].decode('ascii')
    client.request.return_value=UDSResponse(request_sid=0x2e,raw=bytes.fromhex('6e 1e 5f'),positive=True,service_id=0x6e,data=bytes.fromhex('1e 5f'))
    assert not w._execute_sub_step_logic(sub)[0]
    assert '主板序列号' not in w.variables


@pytest.mark.parametrize('actual,expected,passed',[('00123','00123',True),('00123','123',False),('ABC','ABC',True),('ABC',None,False)])
def test_variable_judgment_uses_runtime_string(actual,expected,passed):
    from core.engine import TestStep,StepType
    step=TestStep('compare',StepType.CUSTOM);step.standard_type='变量';step.min_limit='EXPECTED_WRITE'
    w=ChannelWorker(1,[step]);w.is_running=True;w.current_step_results=[(actual,True)]
    if expected is not None:w.variables['EXPECTED_WRITE']=expected
    w.run_next_step=lambda:None
    w._perform_stop=lambda *_:None
    w.on_step_complete()
    assert w.step_statuses[(0,'compare')]==passed


def test_variable_standard_editor_selection():
    from PySide6.QtWidgets import QWidget,QVBoxLayout,QTreeWidget,QTreeWidgetItem
    from ui.dialogs.test_item_dialog import TestItemDialog
    parent=QWidget();layout=QVBoxLayout(parent);tree=QTreeWidget();tree.setColumnCount(3);layout.addWidget(tree)
    item=QTreeWidgetItem(tree,['write'])
    QTreeWidgetItem(item,['sub','','WRITE64:'+encode(config(variable='EXPECTED_WRITE'))])
    d=TestItemDialog(parent,data=dict(name='compare',standard_type='变量',min='EXPECTED_WRITE'))
    try:
        assert d.variable_combo.findText('EXPECTED_WRITE')>=0
        data=d.get_data();assert data['min']=='EXPECTED_WRITE' and data['max']=='--' and data['standard_type']=='变量'
    finally:d.close();parent.close()


def test_separate_editor_roundtrip_without_debug_barcode():
    from ui.dialogs.step_dialog import StepDialog
    d=StepDialog()
    try:
        d.category_combo.setCurrentText('报文交互');d.device_combo.setCurrentText(DEVICE)
        assert d.param_stack.currentWidget() is d.uds_field_write_form
        d.uds_field_write_form.load(config());d.uds_field_write_form.sample.setText('qwrtyyuui')
        data=d.get_data();assert data['type']=='CAN交互'
        assert 'qwrtyyuui' not in str(data)
        restored=StepDialog(step_data=data)
        try:
            assert restored.param_stack.currentWidget() is restored.uds_field_write_form
            assert restored.uds_field_write_form.config()==config()
            assert restored.uds_field_write_form.sample.text()==''
            assert restored.uds_table.rowCount()==0
        finally:restored.close()
    finally:d.close()


def test_online_capture_uses_debug_scan_and_shared_response_check():
    from ui.dialogs.step_dialog import StepDialog
    d=StepDialog();w,client=worker(bytes.fromhex('6e 1e 5e'))
    try:
        d.device_manager=w.device_manager
        form=d.uds_field_write_form;form.load(config());form.sample.setText('xxABCDEFG')
        form.task.run=lambda perform,done:done(True,perform())
        form.run_capture()
        assert client.request.call_count==1
        assert client.request.call_args.args[1].endswith(b'ABCD')
        assert 'PASS:' in form.log.toPlainText()
        assert form.capture.isEnabled() and not form.stop.isEnabled()
    finally:d.close()


def test_serial_debug_scan_releases_scanner_after_each_barcode():
    from PySide6.QtCore import QObject,Signal
    from ui.dialogs.step_dialog import StepDialog
    class Scanner(QObject):
        barcode=Signal(str)
        port=SimpleNamespace(isOpen=lambda:True)
        def begin_scan(self,owner,continuous=False):self.owner=owner;return True
        def end_scan(self,owner):
            if getattr(self,'owner',None) is owner:self.owner=None
    d=StepDialog();scanner=Scanner();d.serial_scanner=scanner
    try:
        form=d.uds_field_write_form
        for barcode in ('ABC123','DEF456'):
            form.scan_barcode();assert scanner.owner is form
            scanner.barcode.emit(barcode)
            assert form.sample.text()==barcode and scanner.owner is None
        form.scan_barcode();d.reject();assert scanner.owner is None
    finally:d.close()
