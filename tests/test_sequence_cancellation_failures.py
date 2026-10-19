import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.device_steps import execute, encode


def setup(device):
    driver = Mock()
    driver._lock = threading.RLock()
    driver.is_connected = True
    driver.last_error = 'instrument rejected configuration'
    driver.set_current.return_value = True
    driver.set_voltage.return_value = True
    driver.configure.return_value = {}
    driver.output_control.return_value = True
    manager = SimpleNamespace(is_enabled=lambda _: True,
                              device_step_lock=lambda _: threading.RLock())
    setattr(manager, device, driver)
    return manager, driver


@pytest.mark.parametrize('device', ['it6333b', 'dg2052'])
@pytest.mark.parametrize('state', ['ON', 'OFF', 'KEEP'])
def test_failed_configuration_still_closes_explicit_off_and_never_enables(device, state):
    manager, driver = setup(device)
    if device == 'it6333b':
        driver.set_current.return_value = False
    else:
        driver.configure.return_value = False
    with pytest.raises(RuntimeError, match='instrument rejected'):
        execute(manager, encode(device, 'configure', {'state': state}), {}, lambda _: None)
    if state == 'OFF':
        driver.output_control.assert_called_once_with(1, False)
    else:
        driver.output_control.assert_not_called()
    if device == 'it6333b':
        driver.set_voltage.assert_not_called()


@pytest.mark.parametrize('device', ['it6333b', 'dg2052'])
def test_cancellation_during_configuration_blocks_later_on(device):
    manager, driver = setup(device)
    stopped = threading.Event()
    if device == 'it6333b':
        driver.set_current.side_effect = lambda *args: (stopped.set(), True)[1]
    else:
        driver.configure.side_effect = lambda *args: (stopped.set(), {})[1]
    variables = {}
    with pytest.raises(RuntimeError, match='测试已停止'):
        execute(manager, encode(device, 'configure', {'state': 'ON'}), variables,
                lambda _: None, stopped.is_set)
    driver.output_control.assert_not_called()
    if device == 'it6333b':
        driver.set_voltage.assert_not_called()
    assert variables == {}


def test_stopped_step_does_not_even_connect_or_select_device():
    manager = Mock()
    with pytest.raises(RuntimeError, match='测试已停止'):
        execute(manager, encode('it6333b', 'on', {}), {}, lambda _: None, lambda: True)
    assert manager.mock_calls == []


def test_configuration_and_shutdown_errors_are_both_retained():
    manager, driver = setup('it6333b')
    driver.set_current.side_effect = ValueError('bad current setting')
    driver.output_control.side_effect = ConnectionError('OFF no response')
    with pytest.raises(RuntimeError) as error:
        execute(manager, encode('it6333b', 'configure', {'state': 'OFF'}), {}, lambda _: None)
    assert 'bad current setting' in str(error.value)
    assert 'OFF no response' in str(error.value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True])
def test_invalid_trigger_readback_never_passes_or_populates_variables(value):
    manager, driver = setup('tbs2102b')
    driver.trigger.return_value = {'requested': {'level': 1.65}, 'actual': {'level': value}}
    variables = {}
    with pytest.raises(ValueError):
        execute(manager, encode('tbs2102b', 'trigger', {}), variables, lambda _: None)
    assert variables == {}


def test_scope_stop_failure_does_not_hide_original_cancellation():
    manager, driver = setup('tbs2102b')
    stopped = threading.Event()
    def command(command):
        if command == 'ACQ:STATE?':
            stopped.set()
            return '1'
        raise ConnectionError('scope stopped responding')
    driver.command.side_effect = command
    with pytest.raises(RuntimeError) as error:
        execute(manager, encode('tbs2102b', 'single', {}), {}, lambda _: None, stopped.is_set)
    assert '测试已停止' in str(error.value)
    assert 'scope stopped responding' in str(error.value)


@pytest.mark.parametrize('action', ['on', 'all_off'])
def test_board_failure_return_is_not_unconditional_pass(action):
    manager, driver = setup('relay')
    driver.setting = {'kind': 'relay'}
    driver.set_relay.return_value = driver.all_relays.return_value = False
    manager.get_rn_board = lambda _: driver
    with pytest.raises(RuntimeError):
        execute(manager, encode('relay', action, {}), {}, lambda _: None)
