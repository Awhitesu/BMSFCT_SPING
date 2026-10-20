import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from core.uds_parser import parse_uds_rule_string,format_uds_rule_string,parse_uds_response_data
from core.uds_result_config import check_expected
from ui.dialogs.step_dialog import StepDialog
from test_uds_extracted_output import execute

app=QApplication.instance() or QApplication([])


@pytest.mark.parametrize('length',[0,3])
def test_hex_preserves_all_selected_bytes_including_zero_and_padding_values(length):
    rule=dict(mode='HEX',offset=3,length=length,var_name='RAW')
    encoded=format_uds_rule_string(rule)
    assert parse_uds_rule_string(encoded)==rule
    result=parse_uds_response_data(b'\x62\xf1\x90\x00\x1a\xff',rule)
    assert result['success'] and result['value']=='00 1A FF' and result['var_name']=='RAW'
    assert result['display']=='00 1A FF'


def test_hex_expected_compares_bytes_not_numeric_value_or_ascii():
    check_expected('00 1A FF','001aff','HEX')
    with pytest.raises(ValueError):check_expected('00 1A FF','1A FF','HEX')
    with pytest.raises(ValueError):check_expected('00 1A FF','invalid','HEX')
    result=parse_uds_response_data(b'\x62\xf1\x90\x01',dict(mode='HEX',offset=3,length=2))
    assert not result['success']


def test_runtime_final_output_and_variable_are_selected_hex_only():
    ok,value,worker,_=execute('10 03\n22 F1 90 | HEX(offset=3,len=3,var=RAW)',
        [b'\x50\x03',b'\x62\xf1\x90\x00\x1a\xff'],metadata=[{},dict(output=True,expected='001aff')])
    assert ok and value=='00 1A FF' and worker.variables['RAW']=='00 1A FF'


def test_mode_selection_expected_and_recipe_roundtrip():
    p=StepDialog()
    try:
        p.category_combo.setCurrentIndex(1);p.device_combo.setCurrentIndex(1)
        p.uds_request.setText('22 F1 90')
        p.uds_workbench._response('62 F1 90 00 1A FF')
        p.uds_byte_grid.set_selection(3,3,emit_signal=True)
        p.uds_extract_mode.setCurrentIndex(4)
        p.uds_extract_var.setText('RAW')
        assert p.uds_extract_mode.currentIndex()==4
        assert p.uds_result_expected.text()=='00 1A FF'
        assert p._get_uds_extract_rule_string()=='HEX(offset=3,len=3,var=RAW)'
        assert p.uds_ext_detail_widget.isHidden()
        p.add_uds_row();p.uds_table.item(0,6).setCheckState(Qt.Checked)
        saved=p.get_data()
        restored=StepDialog(step_data=saved)
        try:
            restored.uds_tabs.setCurrentIndex(3)
            assert restored.uds_extract_mode.currentIndex()==4
            assert restored.uds_extract_offset.value()==3 and restored.uds_extract_len.value()==3
            assert restored.uds_result_expected.text()=='00 1A FF'
            assert 'HEX(offset=3,len=3,var=RAW)' in restored._uds_script()
        finally:restored.close()
    finally:p.close()
