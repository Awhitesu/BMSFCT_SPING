import base64
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox,QFileDialog
from PySide6.QtCore import Qt,QTimer
from core.engine import ChannelWorker,SubStep,SubStepType
from core.uds_result_config import encode,decode
from core.uds_sequence import encode_settings,settings_from,SessionKeepAlive,prepare_request
from devices.uds_client import UDSResponse,ISOTPTransport
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])


class Client:
    def __init__(self,events,tx_id):
        self._lock=threading.RLock();self.events=events;self.tx_id=tx_id
        self.transport=SimpleNamespace(extended_id=None)
    def request(self,sid,data=b'',suppress_positive_response=False):
        self.events.append((self.tx_id,bytes([sid])+data,suppress_positive_response))
        raw=(bytes([sid+0x40])+data if sid!=0x22 else b'\x62'+data+b'AB')
        return UDSResponse(sid,raw,True,sid+0x40,raw[1:])
    def tester_present(self,suppress=False):return self.request(0x3E,b'\x80' if suppress else b'\0',suppress)


def setup_driver():
    events=[];driver=Mock();driver.is_connected=True;driver.default_can_type=0;driver.channel='PCAN_USBBUS1'
    driver.create_uds_client.side_effect=lambda **kw:Client(events,kw['tx_id'])
    return driver,events


def execute(script,metadata=None,settings=None):
    driver,events=setup_driver()
    worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
    rows=script.splitlines();metadata=metadata if metadata is not None else [dict() for _ in rows]
    params=f'LIST64:{base64.urlsafe_b64encode(script.encode()).decode()} / UDSMETA64:{encode(metadata)} / INTERVAL:0'
    if settings is not None:params+=' / UDSCFG64:'+encode_settings(settings)
    ok,value=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device='UDS',params=params,is_judgment=True)))
    return ok,value,events,driver


def test_functional_and_physical_and_suppression_actually_change_sent_request():
    ok,value,events,_=execute('10 03\n22 F1 90 | ASCII(offset=3,len=2)',
        [dict(address='functional',suppress=True),dict(address='physical',output=True,expected='AB')])
    assert ok and value=='AB'
    assert events==[(0x7DF,b'\x10\x83',True),(0x7E0,b'\x22\xF1\x90',False)]


def test_delay_and_unchecked_rows_are_not_sent():
    ok,value,events,_=execute('11 01\n@DELAY 1\n22 F1 90 | ASCII(offset=3,len=2)',
        [dict(enabled=False),{},dict(output=True)])
    assert ok and value=='AB' and len(events)==1
    assert events[0][1]==b'\x22\xF1\x90'


def test_disabled_security_does_not_require_dll():
    ok,_,events,_=execute('@SECURITY\n10 03',[dict(enabled=False),{}])
    assert ok and len(events)==1


def test_check_switch_skips_content_comparison_but_not_protocol_failure():
    ok,_,_,_=execute('22 F1 90 => FF | ASCII(offset=3,len=2)',[dict(check=False,expected='WRONG')])
    assert ok


@pytest.mark.parametrize('row,options',[
    ('22 F1 90',dict(suppress=True)),
    ('10 03 => 50 03',dict(suppress=True)),
    ('@DELAY 5',dict(output=True)),
    ('10 03',dict(enabled=False,output=True)),
    ('22 01 02 03 04 05 06 07',dict(address='functional')),
])
def test_invalid_row_options_rejected(row,options):
    with pytest.raises(ValueError):decode(encode([options]),[row])


def test_advanced_settings_forward_to_transport_and_extended_id():
    ok,_,_,driver=execute('10 03',settings=dict(frame_format='extended',padding=0xAA,block_size=8,st_min=5,p2_star_ms=6000))
    assert ok
    kwargs=driver.create_uds_client.call_args_list[0].kwargs
    assert kwargs['padding']==0xAA and kwargs['block_size']==8 and kwargs['st_min']==5
    assert kwargs['p2_star_timeout']==6


def test_isotp_extended_frame_flag_is_forwarded():
    driver=Mock();driver.length_to_dlc.return_value=8;driver.send_can_message.return_value=True
    driver.CAN_TYPE_CLASSIC=0
    transport=ISOTPTransport(driver);transport.extended_id=True;transport.send(b'\x10\x03')
    assert driver.send_can_message.call_args.kwargs['extended_id'] is True
    def receive(*args,**kwargs):
        predicate=kwargs['predicate']
        assert not predicate(dict(data=b'\x02\x50\x03',is_extended_id=False))
        assert predicate(dict(data=b'\x02\x50\x03',is_extended_id=True))
        return dict(data=b'\x02\x50\x03',is_extended_id=True)
    driver.wait_for_message.side_effect=receive
    assert transport.receive()==b'\x50\x03'


def test_pcan_explicit_extended_format_and_standard_id_validation():
    from devices.pcan_fd_driver import PCANFDDriver
    driver=PCANFDDriver();driver.is_connected=True;driver.bus=Mock()
    assert driver.send_can_message(0,0x700,0,8,b'\0',extended_id=True)
    assert driver.bus.send.call_args.args[0].is_extended_id is True
    driver.bus.send.reset_mock()
    assert not driver.send_can_message(0,0x1800,0,8,b'\0',extended_id=False)
    driver.bus.send.assert_not_called()


def test_functional_request_flow_control_uses_physical_id():
    driver=Mock();driver.length_to_dlc.return_value=8;driver.send_can_message.return_value=True
    driver.CAN_TYPE_CLASSIC=0
    packets=iter([dict(data=b'\x10\x09\x62\xF1\x90ABC'),dict(data=b'\x21DEF\0\0\0\0')])
    driver.wait_for_message.side_effect=lambda *args,**kwargs:next(packets)
    transport=ISOTPTransport(driver,tx_id=0x7DF,rx_id=0x7E8)
    transport.flow_control_tx_id=0x7E0
    transport.send(b'\x22\xF1\x90')
    assert transport.receive()==b'\x62\xF1\x90ABCDEF'
    calls=driver.send_can_message.call_args_list
    assert calls[0].args[1]==0x7DF and calls[1].args[1]==0x7E0


def test_bad_later_request_prevents_all_sends():
    ok,_,events,_=execute('10 03\n22 F1 90 | BROKEN')
    assert not ok and not events


def test_keepalive_during_delay_stops_after_sequence():
    ok,_,events,_=execute('@DELAY 240\n10 03',settings=dict(keepalive=True,keepalive_ms=100))
    assert ok and any(event[1]==b'\x3E\x80' for event in events)
    count=len(events);time.sleep(.15);assert len(events)==count


def test_keepalive_failure_and_cancel_propagate_and_stop():
    client=Client([],0x7E0);client.tester_present=lambda **kw:UDSResponse(0x3E,b'',False,error='lost')
    config=settings_from(encode_settings(dict(keepalive=True,keepalive_ms=100)))
    with pytest.raises(RuntimeError,match='lost'):
        with SessionKeepAlive(client,config,lambda:False,lambda _:None) as pulse:pulse.wait(.4)
    assert not pulse.thread.is_alive()
    cancelled=threading.Event()
    with pytest.raises(RuntimeError,match='停止'):
        with SessionKeepAlive(client,config,cancelled.is_set,lambda _:None) as pulse:
            cancelled.set();pulse.wait(1)
    assert not pulse.thread.is_alive()


@pytest.fixture
def page():
    driver,events=setup_driver()
    page=StepDialog(device_manager=SimpleNamespace(pcan=driver))
    page.category_combo.setCurrentIndex(1);page.device_combo.setCurrentIndex(1)
    yield page,events
    deadline=time.monotonic()+2
    page.uds_workbench.cancel.set()
    while page.uds_workbench.task.isRunning() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    page.close();app.processEvents()


def wait_done(workbench):
    deadline=time.monotonic()+3
    while workbench.task.isRunning() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not workbench.task.isRunning()


def test_service_cases_prefill_only_and_settings_popup_restores_forms(page,monkeypatch):
    p,events=page;workbench=p.uds_workbench
    parent=next(workbench.services.topLevelItem(i) for i in range(workbench.services.topLevelItemCount())
                if workbench.services.topLevelItem(i).data(0,Qt.UserRole)=='22')
    workbench.select_case(parent.child(0),0)
    assert p.uds_request.text()=='22 F1 90' and not events
    def close_popup():
        dialog=workbench._settings_popup
        workbench.functional_id.setText('0x700');dialog.accept()
    QTimer.singleShot(0,close_popup);workbench.open_settings()
    assert p.uds_tabs.count()==4 and workbench.config()['functional_id']==0x700
    assert p.uds_tabs.currentIndex()==2


def test_immediate_send_and_capture_do_not_block_gui_and_fill_parser(page,monkeypatch):
    p,events=page;workbench=p.uds_workbench
    warnings=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args))
    p.uds_request.setText('22 F1 90');p.uds_extract_mode.setCurrentIndex(1);p.uds_extract_len.setValue(2)
    workbench.run(True,capture=True)
    assert workbench.task.isRunning() and workbench.stop_button.isEnabled()
    assert not p.btn_ok.isEnabled()
    wait_done(workbench)
    assert not warnings and events
    assert p.uds_result_expected.text()=='AB'
    assert workbench.last_response=='62 F1 90 41 42'
    assert workbench.passed==1 and p.btn_ok.isEnabled()


def test_settings_cancel_restores_form_values(page):
    p,_=page;workbench=p.uds_workbench
    original=p.uds_tx_id.text()
    def cancel_popup():
        p.uds_tx_id.setText('0x123');workbench.keepalive.setChecked(True)
        workbench._settings_popup.reject()
    QTimer.singleShot(0,cancel_popup);workbench.open_settings()
    assert p.uds_tx_id.text()==original
    assert not workbench.keepalive.isChecked() and p.uds_tabs.count()==4


def test_list_send_routes_entire_designated_response_to_parser(page,monkeypatch):
    p,_=page;w=p.uds_workbench
    warnings=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args))
    p._append_uds_table_row('session','10 03','')
    p._append_uds_table_row('output','22 F1 90','')
    p._append_uds_table_row('later','22 F1 91','')
    p.uds_table.item(1,6).setCheckState(Qt.Checked)
    w.run(False);wait_done(w)
    assert not warnings and w.passed==1
    assert p.uds_tabs.currentIndex()==3 and p._uds_edit_row()==1
    assert w.response_sample.text()=='62 F1 90 41 42'
    assert p.uds_byte_grid._raw_bytes==b'\x62\xf1\x90AB'
    assert p.uds_table.item(2,0).data(Qt.UserRole)['response_sample']=='62 F1 91 41 42'


def test_sequence_stop_closing_dialog_is_nonblocking(page):
    p,events=page;workbench=p.uds_workbench
    p._append_uds_table_row('wait','@DELAY 5000','')
    workbench.run(False)
    started=time.monotonic();p.reject()
    assert time.monotonic()-started<.2
    wait_done(workbench)
    assert not events and workbench.failed==1


def test_suppression_clears_incompatible_fields_and_does_not_regenerate_prefix(page):
    p,_=page;w=p.uds_workbench
    p.uds_request.setText('10 03');assert p.uds_expected.text()=='50 03'
    w.suppress.setChecked(True)
    assert p.uds_expected.text()=='' and not w.check.isChecked()
    p.uds_request.setText('10 01');assert p.uds_expected.text()==''
    p.uds_add_btn.click()
    assert p.get_data()['device']=='UDS 交互列表'


def test_metadata_settings_cases_and_dtc_roundtrip(page):
    p,_=page;w=p.uds_workbench
    w.add_cases([dict(name='custom',request='22 F1 90')],'test')
    w.dtc_dictionary={'123456':'test description'}
    p._append_uds_table_row('read','22 F1 90','','ASCII(offset=3,len=2)','AB')
    p._set_uds_row_options(0,dict(address='functional',suppress=False,check=False))
    p.uds_table.item(0,6).setCheckState(Qt.Checked)
    restored=StepDialog(step_data=p.get_data())
    try:
        options=restored._uds_row_metadata()[0]
        assert options['address']=='functional' and options['check'] is False and options['output']
        assert restored.uds_workbench.config()['cases'][0]['name']=='custom'
        assert restored.uds_workbench.config()['dtc_dictionary']==w.dtc_dictionary
        assert restored.uds_table.item(0,0).text()=='read'
    finally:restored.close()


def test_json_export_import_and_dtc_decode(page,tmp_path,monkeypatch):
    p,_=page;w=p.uds_workbench
    p._append_uds_table_row('test','10 03','50 03')
    path=tmp_path/'sequence.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args:(str(path),''))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:errors.append(args))
    w.export_sequence();assert path.exists()
    p.clear_uds_rows();w.import_sequence()
    assert not errors and p._uds_script()=='10 03 => 50 03'
    w.dtc_dictionary={'123456':'example'};w._response('59 02 FF 12 34 56 01')
    assert w.dtc_table.rowCount()==1
    assert w.dtc_table.item(0,2).text()=='example'


def test_combined_case_builds_list_without_transmitting(page):
    p,events=page;w=p.uds_workbench
    root=w.services.topLevelItem(w.services.topLevelItemCount()-1)
    w.select_case(root.child(1),0)
    assert p.uds_table.rowCount()==3 and not events
    assert '@SECURITY' in p._uds_script()
    assert p.uds_table.item(2,6).checkState()==Qt.Checked
