import threading
import time
from unittest.mock import Mock, patch

import pytest

from devices.ngi_n3618 import NGIN3618
from devices.itech_it6722a import IT6722A
from devices.itech_it6333b import IT6333B
from devices.ngi_83624 import NGI83624
from devices.tektronix_tbs2102b import TBS2102B
from devices.ca550_driver import CA550Controller


def hv_with_replies(*replies):
    driver = NGIN3618('127.0.0.1')
    driver.sock = Mock()
    driver.sock.recv.side_effect = replies
    driver.is_connected = True
    return driver


@pytest.mark.parametrize('reply', [b'-100,"Command error"\n', b'100,"No error"\n', b'ERROR\n', b'\n'])
def test_hv_nonzero_error_is_never_success(reply):
    assert not hv_with_replies(reply).check_success()


def test_hv_fragmented_response_and_sendall():
    driver = hv_with_replies(b'+0,"No ', b'error"\r\n')
    assert driver.check_success()
    driver.sock.sendall.assert_called_once_with(b'SYST:ERR?\n')
    driver.sock.send.assert_not_called()


@pytest.mark.parametrize('target,actual', [(True,b'0\n'), (False,b'1\n')])
def test_hv_output_mismatch_is_failure(target,actual):
    driver = hv_with_replies(b'0,"No error"\n',actual,b'0,"No error"\n',actual)
    with patch('devices.ngi_n3618.time.sleep'):
        assert not driver.output_control(target)


@pytest.mark.parametrize('reply', [b'\n', b'ERROR 0\n', b'nan\n', b'inf\n', b''])
def test_hv_invalid_measurement_is_not_zero(reply):
    driver = hv_with_replies(reply)
    assert driver.measure_voltage() == -1


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), True, 'bad'])
def test_hv_invalid_setpoints_do_not_connect_or_send(value):
    driver = NGIN3618('127.0.0.1')
    driver.connect = Mock()
    assert not driver.set_voltage(value)
    assert not driver.set_current(value)
    driver.connect.assert_not_called()


def test_supply_partial_serial_write_is_failure():
    driver = IT6722A('COM1')
    driver.is_connected = True
    driver.ser = Mock(is_open=True)
    driver.ser.write.return_value = 1
    assert not driver.write('OUTP ON')
    assert not driver.is_connected


@pytest.mark.parametrize('reply', ['nan','inf','ERROR 0',''])
def test_three_channel_supply_invalid_measurement_fails(reply):
    driver = IT6333B()
    driver.write = Mock(return_value=True)
    driver.query = Mock(side_effect=['1',reply,'0.1','ON'])
    with pytest.raises(ConnectionError):
        driver.read_channel(1)


def test_supply_set_verify_cannot_be_interleaved_with_output():
    driver = IT6722A('COM1')
    driver.is_connected = True
    entered, release, output_done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def write(command):
        calls.append(command)
        if command.startswith('VOLT '):
            entered.set()
            assert release.wait(2)
        return True
    def query(command):
        calls.append(command)
        return '5' if command in ('VOLT?','MEAS:VOLT?') else '1'
    driver.write, driver.query = write, query
    first = threading.Thread(target=lambda: driver.set_voltage(5))
    second = threading.Thread(target=lambda: (driver.output_control(True),output_done.set()))
    first.start()
    try:
        assert entered.wait(1)
        second.start()
        time.sleep(.03)
        assert not output_done.is_set()
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert calls == ['SYST:REM','VOLT 5.000','VOLT?','OUTP?','MEAS:VOLT?',
                     'SYST:REM','VOLT?','OUTP ON','OUTP?','MEAS:VOLT?']


def test_scope_close_error_still_releases_all_resources():
    driver = TBS2102B()
    driver.is_connected = True
    resource, manager = Mock(), Mock()
    resource.close.side_effect = OSError('close failed')
    driver.resource, driver.rm = resource, manager
    driver.disconnect()
    assert not driver.is_connected
    assert driver.resource is None and driver.rm is None
    manager.close.assert_called_once()


@pytest.mark.parametrize('reply,expected', [('2',None), ('0.5',None), ('ERROR 0',None), ('nan',None), ('1.0',True), ('0.0',False)])
def test_simulator_output_state_only_accepts_zero_or_one(reply,expected):
    driver = NGI83624('127.0.0.1')
    driver._ensure_connected = Mock(return_value=True)
    driver._query = Mock(return_value=reply)
    assert driver.read_output_state(1) is expected


@pytest.mark.parametrize('device,reply', [('NGI高压源',-1), ('CA550','ERROR 0'), ('CA550','nan'), ('CA550','')])
def test_sequence_does_not_accept_instrument_read_errors(device,reply):
    from core.engine import ChannelWorker, SubStep, SubStepType
    manager = Mock()
    manager.hv_source.measure_voltage.return_value = reply
    manager.ca550.read_measure_data.return_value = reply
    worker = ChannelWorker(1, [], device_manager=manager)
    success, _ = worker._execute_sub_step_logic(SubStep(SubStepType.READ_INSTRUMENT,
        {'device':device,'params':'V'}))
    assert not success


def test_failed_calibrator_setpoint_does_not_enable_output():
    from core.engine import ChannelWorker, SubStep, SubStepType
    manager = Mock()
    manager.ca550.set_source_func.return_value = ''
    manager.ca550.set_source_range.return_value = ''
    manager.ca550.set_source_data.return_value = False
    worker = ChannelWorker(1, [], device_manager=manager)
    success, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'CA550','params':'Type:V / Range:5V / Val:2.5 / Output:开启'}))
    assert not success
    manager.ca550.set_source_output.assert_not_called()


def test_calibrator_nonfinite_value_is_never_sent():
    driver = CA550Controller('COM1')
    driver._send_command = Mock()
    assert not driver.set_source_data(float('nan'))
    driver._send_command.assert_not_called()
