import pytest
from core.uds_parser import parse_uds_response_data,parse_uds_rule_string,format_uds_rule_string
from core.uds_result_config import check_expected
from core.uds_sequence import validate_sequence,settings_from

def test_bcd_preserves_zeros_and_roundtrips():
    rule=dict(mode='BCD',offset=3,length=3,var_name='date')
    parsed=parse_uds_rule_string(format_uds_rule_string(rule))
    result=parse_uds_response_data(bytes.fromhex('62 1E 5E 00 12 26'),parsed)
    assert result['success'] and result['value']=='001226' and result['var_name']=='date'
    check_expected(result['value'],'001226','BCD')
    with pytest.raises(ValueError):check_expected(result['value'],'1226','BCD')
    validate_sequence(['22 1E 5E | BCD(offset=3,len=3)'],[{}],settings_from(''))

@pytest.mark.parametrize('raw',['62 1E 5E 1A','62 1E 5E FF'])
def test_bcd_invalid_nibbles_fail(raw):
    result=parse_uds_response_data(bytes.fromhex(raw),dict(mode='BCD',offset=3,length=1))
    assert not result['success'] and 'BCD' in result['error']

def test_bcd_editor_restores_mode():
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.step_dialog import StepDialog
    app=QApplication.instance() or QApplication([])
    d=StepDialog()
    try:
        d._set_uds_extract_from_rule_string('BCD(offset=3,len=2,var=DATE)')
        assert d.uds_extract_mode.currentIndex()==5
        assert d._get_uds_extract_rule_string()=='BCD(offset=3,len=2,var=DATE)'
    finally:d.close()
