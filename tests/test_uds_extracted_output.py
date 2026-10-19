import base64
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from core.engine import ChannelWorker,SubStep,SubStepType
from devices.uds_client import UDSResponse
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])
VIN='LFVBA21A1N0000001'


def execute(script,responses,cycles=1,metadata=None):
    uds=Mock()
    uds.request.side_effect=[UDSResponse(raw[0]-0x40,raw,True,raw[0],raw[1:]) for raw in responses]
    driver=Mock();driver.is_connected=True;driver.default_can_type=0;driver.create_uds_client.return_value=uds
    worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
    encoded=base64.urlsafe_b64encode(script.encode()).decode()
    params=dict(device='UDS',params=f'LIST64:{encoded} / CYCLES:{cycles} / INTERVAL:0',is_judgment=True)
    if metadata is not None:
        from core.uds_result_config import encode
        params['params'] += ' / UDSMETA64:' + encode(metadata)
    logs=[];worker.log_message.connect(lambda _,text:logs.append(text))
    ok,value=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,params))
    return ok,value,worker,logs


@pytest.mark.parametrize('cycles',[1,2])
def test_vin_without_expected_outputs_only_string_including_cycles(cycles):
    raw=b'\x62\xF1\x90'+VIN.encode()
    ok,value,worker,logs=execute('22 F1 90 | ASCII(offset=3,len=17,var=VIN)',[raw]*cycles,cycles)
    assert ok and value==VIN
    assert worker.variables['VIN']==VIN
    assert '62 F1 90' not in value
    assert any('RX=62 F1 90' in line for line in logs)


def test_control_response_and_two_extractions_never_mix_raw_data():
    script='10 03\n22 F1 90 | ASCII(offset=3,len=17)\n22 F1 91 | ASCII(offset=3,len=2)'
    ok,value,_,_=execute(script,[b'\x50\x03',b'\x62\xF1\x90'+VIN.encode(),b'\x62\xF1\x91AB'])
    assert ok and value==VIN+' | AB'


def test_invalid_extraction_fails_instead_of_returning_hex():
    ok,value,_,_=execute('22 F1 90 | BAD_RULE',[b'\x62\xF1\x90'+VIN.encode()])
    assert not ok and '规则无效' in value


def test_editor_read_expected_optional_and_manual_validation_retained():
    page=StepDialog()
    try:
        page.uds_request.setText('22 F1 90')
        assert page.uds_expected.text()==''
        page.uds_expected.setText('62 F1 90')
        assert page.uds_expected.text()=='62 F1 90'
        page.uds_request.setText('10 03')
        assert page.uds_expected.text()=='50 03'
    finally:page.close()


@pytest.mark.parametrize('expected,passed', [('AB',True), ('AC',False)])
def test_parsed_ascii_expected_is_compared_to_string(expected,passed):
    ok,value,_,_=execute('22 F1 90 | ASCII(offset=3,len=2)', [b'\x62\xF1\x90AB'],
                        metadata=[dict(expected=expected,output=True)])
    assert ok is passed
    assert value=='AB' if passed else '不匹配' in value


@pytest.mark.parametrize('expected,passed', [('10',True), ('10.0000',True), ('11',False), ('NaN',False)])
def test_parsed_numeric_expected_uses_calculated_value(expected,passed):
    ok,value,_,_=execute('22 01 01 | CALC(offset=3,len=2,scale=0.1)', [b'\x62\x01\x01\x00\x64'],
                        metadata=[dict(expected=expected,output=True)])
    assert ok is passed
    if passed:assert value=='10.0'


def test_choose_one_output_and_keep_latest_cycle():
    script='22 F1 90 | ASCII(offset=3,len=2)\n22 F1 91 | ASCII(offset=3,len=2)'
    raw=[b'\x62\xF1\x90AB',b'\x62\xF1\x91CD',b'\x62\xF1\x90EF',b'\x62\xF1\x91GH']
    ok,value,_,_=execute(script,raw,2,[dict(expected='',output=True),dict(expected='',output=False)])
    assert ok and value=='EF'
    ok,value,_,_=execute(script,raw[:2],metadata=[dict(expected='',output=True),dict(expected='WRONG',output=False)])
    assert not ok and '不匹配' in value


def test_invalid_multiple_output_rows_rejected_before_send():
    ok,_,_,_=execute('10 03\n10 03', [],metadata=[dict(output=True),dict(output=True)])
    assert not ok


def test_online_capture_selection_tracks_expected_and_manual_override(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox,'information',lambda *a:None)
    response=SimpleNamespace(positive=True,raw=b'\x62\xF1\x90ABCD')
    client=Mock();client.request.return_value=response
    driver=Mock();driver.is_connected=True;driver.create_uds_client.return_value=client
    page=StepDialog(device_manager=SimpleNamespace(pcan=driver))
    try:
        page.uds_request.setText('22 F1 90')
        page.uds_extract_mode.setCurrentIndex(1)
        page._on_live_sniff_clicked()
        import time
        deadline=time.monotonic()+2
        while page._uds_capture_task.isRunning() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert not page._uds_capture_task.isRunning()
        page.uds_byte_grid.set_selection(3,2,emit_signal=True)
        assert page.uds_result_expected.text()=='AB'
        assert page.uds_expected.text()==''
        page.uds_byte_grid.set_selection(4,3,emit_signal=True)
        assert page.uds_result_expected.text()=='BCD'
        page.uds_extract_mode.setCurrentIndex(2)
        page.uds_extract_len.setValue(1)
        page.uds_extract_scale.setValue(0.1)
        assert float(page.uds_result_expected.text())==6.6
        page.uds_extract_endian.setCurrentIndex(1)
        page.uds_extract_formula.setText('max(val, 0) / 2')
        page.uds_extract_mode.setCurrentIndex(3)
        assert float(page.uds_result_expected.text())==33
        page.uds_sync_expected.setChecked(False)
        page.uds_result_expected.setText('123')
        page.uds_extract_len.setValue(2)
        assert page.uds_result_expected.text()=='123'
    finally:page.close()


def test_output_and_expected_survive_reorder_delete_and_reload():
    from PySide6.QtCore import Qt
    page=StepDialog()
    try:
        page.category_combo.setCurrentIndex(1);page.device_combo.setCurrentIndex(1)
        for i in range(3):
            page._append_uds_table_row('test',f'22 F1 {0x90+i:02X}','','ASCII(offset=3,len=2)',f'V{i}')
        page.uds_table.item(0,6).setCheckState(Qt.Checked)
        page.uds_table.item(1,6).setCheckState(Qt.Checked)
        assert page.uds_table.item(0,6).checkState()==Qt.Unchecked
        page.uds_table.selectRow(1);page.move_uds_row(1)
        assert page.uds_table.item(2,6).checkState()==Qt.Checked
        assert page.uds_table.item(2,5).text()=='V1'
        page.uds_table.selectRow(0);page.delete_uds_row()
        data=page.get_data()
        restored=StepDialog(step_data=data)
        try:
            assert restored.uds_table.rowCount()==2
            assert restored.uds_table.item(1,6).checkState()==Qt.Checked
            assert restored.uds_table.item(1,5).text()=='V1'
        finally:restored.close()
    finally:page.close()


def test_capture_without_hardware_never_uses_synthetic_expected(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    warnings=[];success=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:warnings.append(a))
    monkeypatch.setattr(QMessageBox,'information',lambda *a:success.append(a))
    page=StepDialog()
    try:
        page.uds_request.setText('22 F1 90')
        page.uds_extract_mode.setCurrentIndex(1)
        page._on_live_sniff_clicked()
        assert warnings and not success
        assert not getattr(page,'_uds_response_preview','')
        assert page.uds_result_expected.text()==''
    finally:page.close()


def test_corrupt_metadata_cannot_be_silently_saved_without_checks():
    from core.uds_result_config import encode
    data={'type':'CAN交互','device':'UDS 交互列表','action':'顺序发送并校验',
          'params':'LIST64:'+base64.urlsafe_b64encode(b'10 03').decode()+' / UDSMETA64:'+encode([])}
    page=StepDialog(step_data=data)
    try:
        with pytest.raises(ValueError,match='完整载入'):page.get_data()
    finally:page.close()
