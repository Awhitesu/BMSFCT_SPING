from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.dialogs.step_dialog import StepDialog
from test_uds_extracted_output import execute

app=QApplication.instance() or QApplication([])


def test_final_row_response_drives_parser_and_runtime_output():
    p=StepDialog()
    try:
        p.category_combo.setCurrentIndex(1);p.device_combo.setCurrentIndex(1)
        w=p.uds_workbench
        assert p.uds_tabs.tabText(2)=='服务与发送序列'
        assert p.uds_tabs.tabText(3)=='数据解析'
        for name,request in [('session','10 03'),('read','22 F1 90'),('other','22 F1 91')]:
            p._append_uds_table_row(name,request,'')
        p.uds_table.item(1,6).setCheckState(Qt.Checked)
        w._run_immediate=False;w._run_items=[p.uds_table.item(r,0) for r in range(3)]
        w._log('UDS[轮次1/1 序号1] TX=10 03 RX=50 03 PASS')
        w._log('UDS[轮次1/1 序号2] TX=22 F1 90 RX=62 F1 90 41 42 43 PASS')
        w._log('UDS[轮次1/1 序号3] TX=22 F1 91 RX=62 F1 91 44 45 PASS')
        p.uds_tabs.setCurrentIndex(3)
        assert p._uds_edit_row()==1
        assert w.response_sample.text()=='62 F1 90 41 42 43'
        assert p.uds_expected.text()==''  # sample must not become a fixed HEX comparison
        assert p.uds_byte_grid._raw_bytes==b'\x62\xf1\x90ABC'
        assert '6 字节' in w.response_length.text()
        p._on_byte_grid_range_selected(3,3,b'ABC')
        p.uds_extract_mode.setCurrentIndex(1)
        assert p.uds_result_expected.text()=='ABC'
        assert p.save_uds_edited_row(show_list=False)
        assert p.uds_table.item(1,6).checkState()==Qt.Checked
        assert p.uds_table.item(1,0).data(Qt.UserRole)['response_sample']=='62 F1 90 41 42 43'
        script=p._uds_script();metadata=p._uds_row_metadata()
        ok,value,_,_=execute(script,[b'\x50\x03',b'\x62\xf1\x90ABC',b'\x62\xf1\x91DE'],metadata=metadata)
        assert ok and value=='ABC'
        data=p.get_data()
        restored=StepDialog(step_data=data)
        try:
            restored.uds_tabs.setCurrentIndex(3)
            assert restored.uds_workbench.response_sample.text()=='62 F1 90 41 42 43'
            assert restored.uds_extract_offset.value()==3
            assert restored.uds_extract_len.value()==3
        finally:restored.close()
    finally:p.close()


def test_manual_sample_no_padding_and_numeric_parser():
    p=StepDialog()
    try:
        p.uds_request.setText('22 01 01')
        assert p.uds_byte_grid._raw_bytes==b''
        w=p.uds_workbench
        w.response_sample.setText('62 01 01 00 64')
        p._on_byte_grid_range_selected(3,2,b'\0\x64')
        p.uds_extract_mode.setCurrentIndex(2);p.uds_extract_scale.setValue(.1)
        assert float(p.uds_result_expected.text())==10
        p.uds_extract_len.setValue(20)
        assert p.uds_byte_grid._raw_bytes==b'\x62\x01\x01\x00\x64'
        p.uds_request.setText('22 01 02')
        assert w.response_sample.text()==''
        assert p.uds_byte_grid._raw_bytes==b''
    finally:p.close()


def test_negative_response_does_not_replace_successful_sample():
    p=StepDialog()
    try:
        w=p.uds_workbench
        w._log('UDS[收发] TX=22 F1 90 RX=62 F1 90 41')
        w._log('UDS[收发] TX=22 F1 90 RX=7F 22 31')
        assert w.response_sample.text()=='62 F1 90 41'
    finally:p.close()


def test_loading_sample_does_not_overwrite_manual_expected_value():
    p=StepDialog()
    try:
        p._append_uds_table_row('read','22 F1 90','','ASCII(offset=3,len=2)','MANUAL')
        p._set_uds_row_options(0,dict(response_sample='62 F1 90 41 42'))
        p.load_uds_row(0)
        assert p.uds_result_expected.text()=='MANUAL'
        assert p.uds_workbench.response_sample.text()=='62 F1 90 41 42'
    finally:p.close()


def test_manual_hex_partial_input_is_safe_and_compact_hex_displays_all_bytes():
    p=StepDialog()
    try:
        p.uds_extract_mode.setCurrentIndex(1)
        w=p.uds_workbench
        w.response_sample.setText('6')
        assert p.uds_byte_grid._raw_bytes==b''
        w.response_sample.setText('62F1904142')
        assert p.uds_byte_grid._raw_bytes==b'\x62\xf1\x90AB'
        w.response_sample.setText('invalid')
        assert p.uds_byte_grid._raw_bytes==b''
    finally:p.close()


def test_isotp_multiframe_response_exposes_complete_pdu_and_length():
    from unittest.mock import Mock
    from devices.uds_client import ISOTPTransport
    raw=b'\x62\xf1\x90LFVBA21A1N0000001'
    packets=iter([dict(data=b'\x10\x14'+raw[:6]),
                  dict(data=b'\x21'+raw[6:13]),dict(data=b'\x22'+raw[13:20])])
    driver=Mock();driver.wait_for_message.side_effect=lambda *a,**kw:next(packets)
    driver.length_to_dlc.return_value=8;driver.CAN_TYPE_CLASSIC=0;driver.send_can_message.return_value=True
    transport=ISOTPTransport(driver)
    rebuilt=transport.receive()
    assert rebuilt==raw and len(rebuilt)==20
    assert driver.send_can_message.call_args.args[4][0]==0x30
    p=StepDialog()
    try:
        p.uds_workbench._response(rebuilt.hex(' ').upper())
        assert p.uds_byte_grid._raw_bytes==raw
        assert '20 字节' in p.uds_workbench.response_length.text()
    finally:p.close()
