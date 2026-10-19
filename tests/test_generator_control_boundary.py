import threading
from unittest.mock import Mock

import pytest

from core.device_steps import _generator
from core.instrument_control import control_generator


def device():
    d = Mock()
    d._lock = threading.RLock()
    d.last_error = ''
    d.configure.return_value = {'frequency': '1000'}
    d.output_control.return_value = {'输出': 'OFF'}
    return d


@pytest.mark.parametrize('reply', [None, False, True, {}, {'输出': 'ON'}, {'输出': 'unknown'}])
def test_close_requires_confirmed_off_snapshot(reply):
    d = device()
    d.output_control.return_value = reply
    with pytest.raises(RuntimeError):
        control_generator(d, 'output', 1, state=False)


def test_stopped_request_can_close_but_cannot_enable():
    d = device()
    with pytest.raises(RuntimeError):
        control_generator(d, 'output', 1, state=True, cancelled=lambda: True)
    d.output_control.assert_not_called()
    assert control_generator(d, 'output', 2, state=False, cancelled=lambda: True)['输出'] == 'OFF'


def test_cancel_during_configure_does_not_enable():
    d = device()
    stop = threading.Event()
    d.configure.side_effect = lambda *args: stop.set() or {'frequency': '1000'}
    parameters = dict(channel=1, waveform='正弦波', frequency=1000, amplitude=1,
                      offset=0, load='INF', state='ON')
    with pytest.raises(RuntimeError):
        _generator(d, 'configure', parameters, lambda _: None, stop.is_set)
    d.output_control.assert_not_called()


def test_failed_configuration_preserves_explicit_off():
    d = device()
    d.configure.side_effect = ConnectionError('configuration failed')
    parameters = dict(channel=2, waveform='正弦波', frequency=1000, amplitude=1,
                      offset=0, load='INF', state='OFF')
    with pytest.raises(ConnectionError):
        _generator(d, 'configure', parameters, lambda _: None)
    d.output_control.assert_called_once_with(2, False)
