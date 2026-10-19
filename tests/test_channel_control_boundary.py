import threading
from unittest.mock import Mock

import pytest

from core.instrument_control import control_power
from core.device_steps import _supply


def supply():
    driver = Mock()
    driver._lock = threading.RLock()
    driver.last_error = 'readback mismatch'
    driver.set_current.return_value = True
    driver.set_voltage.return_value = True
    driver.output_control.return_value = True
    return driver


def test_cancel_during_current_setting_blocks_voltage_and_enable():
    driver = supply()
    stopped = threading.Event()
    driver.set_current.side_effect = lambda *args: stopped.set() or True
    with pytest.raises(RuntimeError):
        _supply(driver, 'configure', dict(channel=2, current=1, voltage=12, state='ON'), stopped.is_set)
    driver.set_voltage.assert_not_called()
    driver.output_control.assert_not_called()


def test_failed_configuration_still_attempts_requested_off():
    driver = supply()
    driver.set_current.return_value = False
    with pytest.raises(RuntimeError, match='readback mismatch'):
        _supply(driver, 'configure', dict(channel=3, current=1, voltage=4, state='OFF'))
    driver.output_control.assert_called_once_with(3, False)
    driver.set_voltage.assert_not_called()


def test_cancelled_channel_on_is_blocked_but_off_is_allowed():
    driver = supply()
    with pytest.raises(RuntimeError):
        control_power(driver, 'output_control', True, channel=2, cancelled=lambda: True)
    driver.output_control.assert_not_called()
    assert control_power(driver, 'output_control', False, channel=2, cancelled=lambda: True)
    driver.output_control.assert_called_once_with(2, False)


@pytest.mark.parametrize('channel', [True, 0, 4, 1.5, '2'])
def test_invalid_channel_never_reaches_driver(channel):
    driver = supply()
    with pytest.raises(ValueError):
        control_power(driver, 'set_voltage', 12, channel=channel)
    driver.set_voltage.assert_not_called()


def test_unconfirmed_enable_is_failure():
    driver = supply()
    driver.output_control.return_value = False
    with pytest.raises(RuntimeError, match='readback mismatch'):
        control_power(driver, 'output_control', True, channel=1)
