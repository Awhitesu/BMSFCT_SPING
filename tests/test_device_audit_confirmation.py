"""Reject incomplete, misaddressed and stale device confirmations."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from devices.ngi_83624 import NGI83624
from devices.easy320_driver import Easy320Controller
from devices.rigol_dg2052 import DG2052
from devices.renesas_e2_rfp import RenesasE2Rfp
from devices.itech_it6333b import IT6333B
from devices.tektronix_tbs2102b import TBS2102B


def test_simulator_short_batch_cannot_pass():
    driver = NGI83624('unused')
    driver._safe_send = Mock(return_value=True)
    driver._query_channels = Mock(return_value=['2.5'])
    assert not driver.set_voltages(2.5, [1, 2])
    assert 'Incomplete' in driver.last_error


def test_simulator_short_snapshot_cannot_hide_channels():
    driver = NGI83624('unused')
    driver._query_channels = Mock(side_effect=[['2.5'], ['2.5', '2.5'], ['1', '1']])
    with pytest.raises(ValueError, match='Incomplete'):
        driver.read_voltage_snapshot([1, 2])


@pytest.mark.parametrize('state,channels', [(2, [1]), (True, [1, 1]), (True, [True])])
def test_simulator_invalid_batch_does_not_write(state, channels):
    driver = NGI83624('unused')
    driver._query_channels = Mock()
    assert not driver.output_controls(state, channels)
    driver._query_channels.assert_not_called()


def test_plc_never_guesses_adjacent_address_or_coils():
    driver = Easy320Controller('unused')
    driver.is_connected = True
    driver.client = Mock()
    driver.client.read_holding_registers.return_value.isError.return_value = True
    assert driver.read_register(12) == []
    driver.client.read_holding_registers.assert_called_once_with(address=12, count=1, device_id=1)
    driver.client.read_coils.assert_not_called()


def test_plc_rejects_short_register_response():
    driver = Easy320Controller('unused')
    driver.is_connected = True
    driver.client = Mock()
    driver.client.read_holding_registers.return_value = SimpleNamespace(
        isError=lambda: False, registers=[1])
    assert driver.read_register(12, 2) == []


@pytest.mark.parametrize('field,value', [('幅值', '0.5'), ('频率(Hz)', '999'),
                                       ('偏置(V)', 'nan'), ('负载(Ω)', '50')])
def test_generator_rejects_clamped_or_invalid_readback(field, value):
    actual = {'波形': 'SIN', '幅值单位': 'VPP', '幅值': '1',
              '偏置(V)': '0', '频率(Hz)': '1000', '负载(Ω)': '9.9e37'}
    actual[field] = value
    with pytest.raises(ValueError):
        DG2052.verify_configuration(actual, 'SIN', 1000, 1, 0, 'INF')


@pytest.mark.parametrize('content', [None, b'123', b'1234'])
def test_e2_requires_fresh_complete_readback(tmp_path, content):
    executable = tmp_path / 'rfp-cli.exe'
    executable.touch()
    output = tmp_path / 'old.bin'
    output.write_bytes(b'OLD!')
    driver = RenesasE2Rfp(str(executable))
    driver.tool_ready = True
    def run(cmd, **kwargs):
        if content is not None:
            Path(cmd[-1]).write_bytes(content)
        return SimpleNamespace(returncode=0)
    with patch('devices.renesas_e2_rfp.subprocess.run', side_effect=run), \
         patch.object(driver, 'refresh_presence', return_value=True):
        assert driver.read_chip(0, 4, str(output)) == (content == b'1234')
    assert output.read_bytes() == (b'1234' if content == b'1234' else b'OLD!')


def test_supply_on_requires_measured_voltage_not_just_on():
    driver = IT6333B()
    driver.is_connected = True
    driver.control_timeout = .001
    driver.write = Mock(return_value=True)
    responses = {'INST:NSEL?': '2', 'VOLT?': '12', 'OUTP?': '1', 'MEAS:VOLT?': '0'}
    driver.query = Mock(side_effect=lambda cmd: responses[cmd])
    assert not driver.output_control(2, True)
    assert driver.last_control['actual']['measured_voltage'] == 0


def test_scope_nonfinite_trigger_never_sent():
    driver = TBS2102B()
    driver.apply = Mock()
    with pytest.raises(ValueError):
        driver.trigger('EDGE', 'CH1', 'AUTO', float('nan'), 'RISE', 'DC',
                       1e-6, 'POSITIVE', 'LESS', 1e-6, 0, 1)
    driver.apply.assert_not_called()


def test_simulator_wrong_identity_not_connected():
    driver = NGI83624('unused')
    driver._read_line = Mock(return_value='KEYSIGHT,34461A,123,1')
    sock = Mock()
    with patch('devices.ngi_83624.socket.socket', return_value=sock):
        assert not driver.connect()
    assert not driver.is_connected
    sock.close.assert_called_once()


def test_simulator_shutdown_failure_still_closes_socket():
    driver = NGI83624('unused')
    sock = driver.sock = Mock()
    sock.shutdown.side_effect = OSError('already closed')
    driver.disconnect()
    sock.close.assert_called_once()
    assert driver.sock is None


def test_windows_atomic_config_retries_transient_sharing_failure(tmp_path):
    from core import system_config
    path = tmp_path / 'config.json'
    system_config.update({'old': 1}, path)
    real_replace = system_config.os.replace
    failure = PermissionError('temporarily locked')
    failure.winerror = 32
    attempts = []
    def replace(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            raise failure
        return real_replace(source, destination)
    with patch.object(system_config.os, 'replace', side_effect=replace):
        system_config.update({'new': 2}, path)
    assert len(attempts) == 2
    assert system_config.load(path) == {'old': 1, 'new': 2}
