import threading
from unittest.mock import Mock

import pytest

from core.simulator_control import control_simulator


def simulator():
    d=Mock();d._lock=threading.RLock();d.last_error='voltage mismatch'
    d.set_voltage.return_value=d.set_voltages.return_value=True
    d.output_control.return_value=d.output_controls.return_value=True
    return d


def test_cancel_after_setting_voltage_never_enables_output():
    d=simulator();stop=threading.Event()
    d.set_voltages.side_effect=lambda *args:stop.set() or True
    with pytest.raises(RuntimeError):
        control_simulator(d,'voltage_on',[1,2],2.7,batch=True,cancelled=stop.is_set)
    d.output_controls.assert_not_called()


def test_failed_voltage_confirmation_never_enables_output():
    d=simulator();d.set_voltages.return_value=False
    with pytest.raises(RuntimeError,match='voltage mismatch'):
        control_simulator(d,'voltage_on',[1,2],2.7,batch=True)
    d.output_controls.assert_not_called()


def test_cancelled_shutdown_can_still_close_all_selected_channels():
    d=simulator()
    assert control_simulator(d,'output',[1,6],False,batch=True,cancelled=lambda:True)
    d.output_controls.assert_called_once_with(False,[1,6],None)


@pytest.mark.parametrize('channels', [[],[1,1],[True],[0],[25],[1,1.5]])
def test_invalid_selection_does_not_send_any_command(channels):
    d=simulator()
    with pytest.raises(ValueError):control_simulator(d,'output',channels,True)
    d.output_control.assert_not_called();d.output_controls.assert_not_called()


def test_setting_and_enable_hold_same_shared_lock():
    d=simulator();entered=threading.Event();release=threading.Event();errors=[]
    def voltage(*args):
        entered.set();assert release.wait(2);return True
    d.set_voltages.side_effect=voltage
    def invoke():
        try:control_simulator(d,'voltage_on',[1,2],2.7,batch=True)
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=invoke);worker.start()
    try:
        assert entered.wait(2)
        acquired=d._lock.acquire(blocking=False)
        if acquired:d._lock.release()
        assert not acquired
    finally:release.set();worker.join(2)
    assert not worker.is_alive() and not errors

