from types import SimpleNamespace
from unittest.mock import Mock, patch, call
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from devices.itech_it6722a import IT6722A
from core.engine import ChannelWorker, SubStep, SubStepType
from ui.tabs.mainboard_power_tab import MainboardPowerTab

app = QApplication.instance() or QApplication([])


def source(voltage='12', current='1', output='1', measured=None):
    driver = IT6722A('COM1')
    driver.is_connected = True
    driver.write = Mock(return_value=True)
    measured = iter(measured if measured is not None else ['12']*50)
    def query(command):
        if command == 'MEAS:VOLT?': return next(measured)
        return {'VOLT?':voltage,'CURR?':current,'OUTP?':output}[command]
    driver.query = Mock(side_effect=query)
    return driver


def clock():
    ticks = iter(n/100 for n in range(1000))
    return patch('devices.itech_it6722a.time.monotonic',side_effect=lambda: next(ticks))


def test_enable_waits_for_measured_voltage_without_resending():
    driver = source(measured=['0','11.8','12'])
    with clock(),patch('devices.itech_it6722a.time.sleep'):
        assert driver.output_control(True)
    assert driver.write.call_args_list == [call('SYST:REM'),call('OUTP ON')]
    assert driver.last_control['actual']['measured_voltage'] == 12
    assert driver.control_statistics['recovered'] == 1


def test_on_status_with_wrong_voltage_is_failure():
    driver = source(measured=['0']*50)
    driver.control_timeout = .05
    with clock(),patch('devices.itech_it6722a.time.sleep'):
        assert not driver.output_control(True)
    assert driver.last_control['actual'] == {'output':True,'measured_voltage':0}
    assert driver.is_connected


def test_off_does_not_need_voltage_setpoint_or_zero_voltage():
    driver = source(voltage='ERROR',output='0',measured=['70'])
    assert driver.output_control(False)
    assert driver.write.call_args_list == [call('SYST:REM'),call('OUTP OFF')]
    assert [c.args[0] for c in driver.query.call_args_list] == ['OUTP?']


@pytest.mark.parametrize('output,expected', [('0',True),('1',False)])
def test_set_voltage_distinguishes_off_and_energized_output(output,expected):
    driver = source(output=output,measured=['0']*50)
    driver.control_timeout = .05
    with clock(),patch('devices.itech_it6722a.time.sleep'):
        assert driver.set_voltage(12) is expected
    assert driver.write.call_args_list == [call('SYST:REM'),call('VOLT 12.000')]


def test_current_limit_is_verified_instead_of_assuming_load_current():
    driver = source(current='0.2')
    driver.control_timeout = .05
    with clock(),patch('devices.itech_it6722a.time.sleep'):
        assert not driver.set_current(1)
    assert driver.last_control['actual'] == {'current_limit':.2}


@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),81,-1])
def test_invalid_voltage_never_sends(value):
    driver = source()
    assert not driver.set_voltage(value)
    driver.write.assert_not_called()


def test_serial_query_partial_reply_is_not_a_valid_number():
    driver = IT6722A('COM1')
    port = Mock(is_open=True)
    port.write.side_effect = lambda data: len(data)
    port.readline.return_value = b'1'
    driver.ser,driver.is_connected = port,True
    assert driver.query('VOLT?') == ''
    assert not driver.is_connected and driver.ser is None
    port.close.assert_called_once()


def test_connection_checks_model_and_output_without_changing_output():
    driver = IT6722A('COM1')
    port = Mock(is_open=True)
    port.write.side_effect = lambda data: len(data)
    port.readline.side_effect = [b'ITECH,IT6722A,SN,1.0\r\n',b'OFF\r\n']
    with patch('devices.itech_it6722a.serial.Serial',return_value=port),patch('devices.itech_it6722a.time.sleep'):
        assert driver.connect()
    assert [c.args[0] for c in port.write.call_args_list] == [b'*IDN?\r\n',b'SYST:REM\r\n',b'OUTP?\r\n']
    driver.disconnect()


def test_failed_setpoint_does_not_enable_or_lose_failure_in_sequence():
    driver = Mock()
    driver.set_voltage.return_value = False
    driver.last_error = 'voltage not confirmed'
    driver.last_control = {}
    worker = ChannelWorker(1,[],device_manager=SimpleNamespace(dut_power=driver))
    ok,result = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'IT6722A被测物供电','params':'12V / 1A / 开启'}))
    assert not ok and 'voltage not confirmed' in result
    driver.output_control.assert_not_called()


def test_poll_does_not_disable_buttons_and_pending_control_runs_once():
    driver = Mock(port='COM1',baudrate=9600,is_connected=True)
    driver.measure_voltage.return_value = 12
    driver.measure_current.return_value = .1
    driver.read_output_state.return_value = True
    driver.output_control.return_value = True
    page = MainboardPowerTab(SimpleNamespace(dut_power=driver))
    page.show();app.processEvents();page.timer.stop();page.connection_timer.stop()
    entered,release = threading.Event(),threading.Event()
    driver.measure_voltage.side_effect = lambda: (entered.set(),release.wait(2),12)[-1]
    try:
        page.start_reading()
        assert entered.wait(1)
        assert page.btn_off.isEnabled()
        page.control_output(False)
        page.control_output(False)
        driver.output_control.assert_not_called()
        release.set()
        deadline = time.monotonic()+2
        while (page.operation.busy or page._pending_command) and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        driver.output_control.assert_called_once_with(False)
        assert page.btn_off.isEnabled()
        page.on_read_finished(-1,float('nan'))
        assert page.lbl_volt.text() == '—' and page.lbl_curr.text() == '—'
        page.hide()
        assert not page.timer.isActive()
    finally:
        release.set();page.shutdown();page.close()


def test_local_mode_12v_to_11v_requires_remote_and_command_spacing():
    ticks=[0.0]
    class Port:
        is_open=True
        remote=False
        voltage=12.0
        reply=b''
        commands=[]
        last=None
        def reset_input_buffer(self):self.reply=b''
        def flush(self):pass
        def close(self):self.is_open=False
        def write(self,data):
            if self.last is not None:
                assert ticks[0]-self.last >= .08-1e-9
            self.last=ticks[0]
            command=data.decode().strip();self.commands.append(command)
            if command=='SYST:REM':self.remote=True
            elif command.startswith('VOLT ') and self.remote:self.voltage=float(command.split()[1])
            elif command=='VOLT?':self.reply=f'{self.voltage}\r\n'.encode()
            elif command=='OUTP?':self.reply=b'0\r\n'
            else:raise AssertionError(command)
            return len(data)
        def readline(self):return self.reply
    port=Port();driver=IT6722A('COM1');driver.ser=port;driver.is_connected=True
    with patch('devices.itech_it6722a.time.monotonic',side_effect=lambda:ticks[0]), \
         patch('devices.itech_it6722a.time.sleep',side_effect=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds)):
        assert driver.set_voltage(11)
        assert driver.last_control['actual']=={'voltage':11.0,'output':False}
        port.remote=False  # Pressing Local between actions must not break control.
        assert driver.set_voltage(10)
    assert port.commands==['SYST:REM','VOLT 11.000','VOLT?','OUTP?',
                           'SYST:REM','VOLT 10.000','VOLT?','OUTP?']


def test_timeout_preserves_wrong_readback_and_captures_error_code():
    driver=source(voltage='12',output='0')
    previous=driver.query.side_effect
    driver.query.side_effect=lambda command: '-201,"Invalid while in local"' if command=='SYST:ERR?' else previous(command)
    driver.control_timeout=.02
    with clock(),patch('devices.itech_it6722a.time.sleep'):
        assert not driver.set_voltage(11)
    assert driver.last_control['actual']['voltage']==12
    assert '-201' in driver.last_error


def test_remote_write_failure_never_sends_setpoint_or_output():
    driver=source()
    driver.write.return_value=False
    assert not driver.set_voltage(11)
    driver.write.assert_called_once_with('SYST:REM')
    assert driver.last_control['attempts']==0
