import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from core.uds_parser import parse_uds_rule_string, format_uds_rule_string, parse_uds_response_data
from core.uds_result_config import check_expected, encode as encode_rows
from core.uds_sequence import validate_sequence, settings_from
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
RAW = bytes.fromhex('62 F1 03 01 02 03 41 42 43 44 12 34 45')
SEGMENTS = [dict(mode=mode, offset=offset, length=length) for mode, offset, length in
            [('HEX',3,3), ('ASCII',6,4), ('BCD',10,2), ('ASCII',12,1)]]
EXPECTED = '010203ABCD1234E'


def test_mixed_segments_roundtrip_and_final_string_comparison():
    rule = dict(mode='COMBINE',segments=SEGMENTS,separator='',var_name='SERIAL')
    decoded = parse_uds_rule_string(format_uds_rule_string(rule))
    assert decoded == rule
    result = parse_uds_response_data(RAW, decoded)
    assert result['success'] and result['value'] == EXPECTED
    assert result['var_name'] == 'SERIAL'
    check_expected(result['value'], EXPECTED, 'COMBINE')
    with pytest.raises(ValueError):
        check_expected('00123', '123', 'COMBINE')
    rule['separator'] = '-'
    assert parse_uds_response_data(RAW,rule)['value'] == '010203-ABCD-1234-E'


def test_invalid_later_segment_fails_entire_combination():
    rule = dict(mode='COMBINE',segments=SEGMENTS+[dict(mode='HEX',offset=100,length=2)])
    result = parse_uds_response_data(RAW,rule)
    assert not result['success'] and '5' in result['error']
    assert 'value' not in result
    assert parse_uds_rule_string('COMBINE(data=invalid)') is None
    with pytest.raises(ValueError):
        format_uds_rule_string(dict(mode='COMBINE',segments=[]))


def test_enabling_combination_keeps_existing_single_rule_as_first_segment():
    from ui.dialogs.step_dialog import StepDialog
    dialog = StepDialog()
    try:
        dialog._set_uds_extract_from_rule_string('ASCII(offset=6,len=4)')
        assert not dialog.uds_segments.enabled.isChecked()
        dialog.uds_segments.enabled.setChecked(True)
        assert len(dialog.uds_segments.rules) == 1
        assert dialog.uds_segments.rules[0]['offset'] == 6
        assert dialog.uds_segments.rules[0]['length'] == 4
        assert parse_uds_response_data(RAW, parse_uds_rule_string(dialog._get_uds_extract_rule_string()))['value'] == 'ABCD'
    finally:
        dialog.close()


def test_four_drag_records_persist_modify_reorder_and_run():
    from ui.dialogs.step_dialog import StepDialog
    from core.engine import ChannelWorker, SubStep, SubStepType
    from devices.uds_client import UDSResponse
    dialog = StepDialog()
    restored = StepDialog()
    try:
        dialog.uds_workbench._response(RAW.hex(' '))
        dialog.uds_sync_expected.setChecked(True)
        for index, rule in zip([4,1,5,1], SEGMENTS):
            dialog.uds_byte_grid.set_selection(rule['offset'],rule['length'])
            dialog._add_uds_segment(index)
        dialog.uds_extract_var.setText('SERIAL')
        editor = dialog.uds_segments
        assert editor.table.rowCount() == 4
        assert len(editor.rules) == 4
        assert EXPECTED in editor.preview.text()
        assert dialog.uds_result_expected.text() == EXPECTED
        assert dialog.uds_byte_grid._blocks[3].segment_color
        assert dialog.uds_byte_grid._blocks[3].toolTip() == '解析段：1'
        saved = dialog._get_uds_extract_rule_string()
        dialog._append_uds_table_row('mixed read', '22 F1 03', '', saved, EXPECTED)
        assert dialog.uds_table.item(0,4).text().startswith('组合4段')
        assert saved in dialog._uds_script()
        restored._set_uds_extract_from_rule_string(saved)
        assert restored._get_uds_extract_rule_string() == saved
        assert len(restored.uds_segments.rules) == 4
        # Selecting and changing an earlier record only changes that record.
        editor.table.selectRow(1)
        editor._select(1)
        dialog.uds_extract_len.setValue(2)
        dialog._add_uds_segment(replace=True)
        assert editor.rules[1]['length'] == 2
        assert len(editor.rules) == 4
        assert '010203AB1234E' in editor.preview.text()
        editor.table.selectRow(3)
        editor.move(-1)
        assert '010203ABE1234' in editor.preview.text()
        editor.remove()
        assert len(editor.rules) == 3
        assert '010203AB1234' in editor.preview.text()
        # The engine must receive the combined rule, not the active last selection.
        script = '22 F1 03 | ' + saved
        rows = [dict(output=True, expected=EXPECTED)]
        validate_sequence([script], rows, settings_from(''))
        client = MagicMock()
        client.request.return_value = UDSResponse(0x22, RAW, True, 0x62, RAW[1:])
        driver = MagicMock()
        driver.is_connected = True
        driver.default_can_type = 0
        driver.create_uds_client.return_value = client
        worker = ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
        params = 'LIST64:'+base64.urlsafe_b64encode(script.encode()).decode()+' / UDSMETA64:'+encode_rows(rows)
        ok, value = worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device='UDS',params=params)))
        assert ok and value == EXPECTED
        assert worker.variables['SERIAL'] == EXPECTED
    finally:
        dialog.close()
        restored.close()
