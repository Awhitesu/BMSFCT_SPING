from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.engine import ChannelWorker, _load_dtc_fault_items


def test_fault_file_failure_never_reuses_old_whitelist(tmp_path):
    first = tmp_path / 'first.txt'
    first.write_text('Byte\tbit\tFault\tWhitelist\n0\t0\told\t1\n', encoding='utf-8')
    assert _load_dtc_fault_items(str(first), []) == [(0, 0, 'old', True)]
    second = tmp_path / 'second.txt'
    second.write_text('Byte\tbit\tFault\tWhitelist\n', encoding='utf-8')
    with pytest.raises(ValueError):
        _load_dtc_fault_items(str(second), [(0, 0, 'default', False)])
    first.write_text('broken file', encoding='utf-8')
    with pytest.raises(ValueError):
        _load_dtc_fault_items(str(first), [])


def test_missing_fault_file_uses_its_own_defaults(tmp_path):
    first = [(0, 0, 'first', True)]
    second = [(1, 0, 'second', False)]
    assert _load_dtc_fault_items(str(tmp_path / 'first.txt'), first) == first
    assert _load_dtc_fault_items(str(tmp_path / 'second.txt'), second) == second
    first.clear()
    assert _load_dtc_fault_items(str(tmp_path / 'first.txt'), []) == [(0, 0, 'first', True)]


@pytest.mark.parametrize('rows', [
    '0\t8\tfault', '-1\t0\tfault', '8\t0\tfault',
    '0\t0\tfault\tyes', '0\t0\tfault\t1\n0\t0\tother',
    '0\t0\tfault\n1\t0\tfault', '0\t0\t',
])
def test_invalid_fault_mapping_fails_instead_of_ignoring_rows(tmp_path, rows):
    path = tmp_path / 'faults.txt'
    path.write_text('Byte\tbit\tFault\tWhitelist\n' + rows, encoding='utf-8')
    with pytest.raises(ValueError):
        _load_dtc_fault_items(str(path), [])


def csc_worker(kwargs):
    worker = ChannelWorker(1, [])
    worker._parse_eol_params = lambda _: dict(op_name='0x07 CSC批量读取',
        channel_id=0, timeout=.1, kwargs=kwargs)
    worker._get_can_board = Mock(return_value=SimpleNamespace(can=Mock()))
    return worker


@pytest.mark.parametrize('kwargs', [
    {'PARAM2': '0'}, {'PARAM2': '-1'}, {'PARAM1': '-1'},
    {'PARAM1': '65535', 'PARAM2': '2'}, {'PARAM3': '-1'},
    {'ARGS': 'MIN_V:NaN'}, {'ARGS': 'MAX_V:bad'},
    {'ARGS': 'MIN_V:4,MAX_V:3'}, {'ARGS': 'STEP_DELAY:-1'},
    {'ARGS': 'STEP_DELAY:bad'},
])
def test_invalid_csc_parameters_cannot_become_pass(kwargs):
    worker = csc_worker(kwargs)
    with pytest.raises(ValueError):
        worker._execute_eol_protocol(None, {'params': ''}, lambda _: None)
    worker._get_can_board.assert_not_called()
    assert worker.variables == {}


def test_csc_cancel_during_intercell_delay_stops_before_next_request():
    worker = csc_worker({'PARAM2': '2', 'PARAM3': '0', 'ARGS': 'STEP_DELAY:5000'})
    with patch('devices.eol_protocol.EOLProtocol') as protocol, \
            patch.object(worker.cancel_event, 'wait', return_value=True):
        protocol.return_value.execute.return_value = SimpleNamespace(success=True, value=2.5, raw_data=b'')
        ok, result = worker._execute_eol_protocol(None, {'params': ''}, lambda _: None)
        assert not ok and result == 'STOPPED'
        protocol.return_value.execute.assert_called_once()


def test_failed_csc_read_is_not_published_as_zero_volts():
    worker = csc_worker({'PARAM2': '1', 'PARAM3': '0'})
    with patch('devices.eol_protocol.EOLProtocol') as protocol:
        protocol.return_value.execute.return_value = SimpleNamespace(success=False, value=None, error='timeout')
        ok, _ = worker._execute_eol_protocol(None, {'params': ''}, lambda _: None)
    assert not ok
    assert worker.variables['CSC_CELL_0'] is None
    assert worker.variables['CSC_RAW'][0] is None


def test_cancellation_after_dtc_read_does_not_clear_faults():
    worker = ChannelWorker(1, [])
    worker._parse_eol_params = lambda _: dict(op_name='DTC诊断', channel_id=0,
        timeout=.1, kwargs={})
    worker._get_can_board = lambda _, params=None: SimpleNamespace(can=Mock())
    with patch('devices.eol_protocol.EOLProtocol') as protocol:
        def read(*args, **kwargs):
            worker.cancel_event.set()
            return SimpleNamespace(success=True, value=bytes(8))
        protocol.return_value.execute.side_effect = read
        ok, result = worker._execute_eol_protocol(None, {'params': ''}, lambda _: None)
        assert not ok and result == 'STOPPED'
        protocol.return_value.clear_dtc_faults.assert_not_called()
