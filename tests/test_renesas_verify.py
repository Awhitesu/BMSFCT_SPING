from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, SubStep, SubStepType
from core.renesas_verify import firmware_segments, verify_readback

app = QApplication.instance() or QApplication([])


def hex_line(address, kind, body):
    data = bytes([len(body)]) + address.to_bytes(2, 'big') + bytes([kind]) + body
    return ':' + (data + bytes([-sum(data) & 255])).hex().upper()


def firmware(tmp_path):
    path = tmp_path / 'program.hex'
    path.write_text('\n'.join([hex_line(0, 4, b'\x00\x10'), hex_line(0x20, 0, b'\x12\x34'),
                               hex_line(0x80, 0, b'\x56'), hex_line(0, 1, b'')]), encoding='ascii')
    return path


@pytest.mark.parametrize('mode', ['match', 'mismatch', 'truncated', 'failed'])
def test_real_source_and_addressed_readback(tmp_path, mode):
    path = firmware(tmp_path)
    calls = []
    def read(address, size, output):
        calls.append((address, size, output))
        data = {0x100020: b'\x12\x34', 0x100080: b'\x56'}[address]
        if mode == 'failed':
            return False
        if mode == 'mismatch':
            data = bytes([data[0] ^ 1]) + data[1:]
        if mode == 'truncated':
            data = data[:-1]
        Path(output).write_bytes(data)
        return True
    driver = SimpleNamespace(read_chip=read)
    if mode in ('truncated', 'failed'):
        with pytest.raises(ValueError):
            verify_readback(driver, path)
    else:
        source, actual, address, diff = verify_readback(driver, path)
        assert source == b'\x12\x34\x56'
        assert address == 0x100020
        assert diff['is_match'] == (mode == 'match')
        assert (source == actual) == (mode == 'match')
        assert [(a, size) for a, size, _ in calls] == [(0x100020, 2), (0x100080, 1)]
    assert all(not Path(output).exists() for _, _, output in calls)


def test_source_checksum_and_binary_address_are_required(tmp_path):
    path = firmware(tmp_path)
    path.write_text(path.read_text().replace('1234', '1235'), encoding='ascii')
    with pytest.raises(ValueError):
        firmware_segments(path)
    binary = tmp_path / 'program.bin'
    binary.write_bytes(b'abc')
    with pytest.raises(ValueError):
        firmware_segments(binary)
    assert firmware_segments(binary, 0x200) == [(0x200, b'abc')]


def test_s_record_address_and_checksum(tmp_path):
    def record(kind, address, data, size):
        body = address.to_bytes(size, 'big') + data
        packed = bytes([len(body) + 1]) + body
        return f'S{kind}' + (packed + bytes([255 - (sum(packed) & 255)])).hex().upper()
    path = tmp_path / 'program.mot'
    path.write_text(record(3, 0x12345678, b'\x11\x22', 4) + '\n' + record(7, 0, b'', 4), encoding='ascii')
    assert firmware_segments(path) == [(0x12345678, b'\x11\x22')]
    path.write_text(path.read_text().replace('1122', '1123'), encoding='ascii')
    with pytest.raises(ValueError):
        firmware_segments(path)


def test_cancelled_verification_does_not_read(tmp_path):
    driver = Mock()
    with pytest.raises(RuntimeError):
        verify_readback(driver, firmware(tmp_path), cancelled=lambda: True)
    driver.read_chip.assert_not_called()


@pytest.mark.parametrize('mode', ['match', 'mismatch', 'failed'])
def test_sequence_verification_controls_final_result(tmp_path, mode):
    path = firmware(tmp_path)
    def read(address, size, output):
        if mode == 'failed':
            return False
        data = {0x100020: b'\x12\x34', 0x100080: b'\x56'}[address]
        Path(output).write_bytes(data if mode == 'match' else b'\x00' * size)
        return True
    driver = Mock()
    driver.run_project.return_value = True
    driver.read_chip.side_effect = read
    driver.get_checksum.return_value = ''
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(e2_rfp=driver))
    sub = SubStep(SubStepType.SET_INSTRUMENT, {'device': '瑞萨E2', 'action': '执行烧录工程',
        'params': f'FILE:{path}/VERIFY_DIFF:1/ARCHIVE_REPORT:0'})
    success, value = worker._execute_sub_step_logic(sub)
    assert success == (mode == 'match')
    assert (value == 'PASS') == (mode == 'match')


def test_debug_page_propagates_verification_failure():
    from ui.tabs.renesas_e2_tab import RenesasE2Tab
    driver = Mock()
    driver.get_recent_log.return_value = ''
    driver.get_checksum.return_value = ''
    page = RenesasE2Tab(Mock(e2_rfp=driver))
    try:
        with patch('core.renesas_verify.verify_readback', side_effect=ValueError('read failed')), \
             patch('ui.tabs.renesas_e2_tab.record_burn_event', return_value={}):
            success, state = page._record_burn_and_emit('file.hex', True, 'Pass', 1)
        assert not success
        assert 'read failed' in state
    finally:
        page.shutdown()
        page.close()
