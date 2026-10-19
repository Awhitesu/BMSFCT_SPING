from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from devices.ngi_n3618 import NGIN3618
from core.engine import ChannelWorker, SubStep, SubStepType
from ui.tabs.hv_tab import HVSourceTab

app = QApplication.instance() or QApplication([])


def source(voltage='100', current='1', output='1', measured=None):
    driver = NGIN3618('127.0.0.1')
    driver.is_connected = True
    driver.send_cmd = Mock()
    measured = iter(measured if measured is not None else ['100'] * 50)
    def query(command, logger=None):
        if command == 'MEAS:VOLT?':
            return next(measured)
        return {'SYST:ERR?':'0,"No error"', 'VOLT?':voltage,
                'CURR?':current,'OUTP?':output}[command]
    driver.query = Mock(side_effect=query)
    return driver


def clock():
    ticks = iter(n / 100 for n in range(1000))
    return patch('devices.ngi_n3618.time.monotonic', side_effect=lambda: next(ticks))


def test_enabling_waits_for_actual_voltage_not_just_on_state():
    driver = source(measured=['0','98','100'])
    with clock(), patch('devices.ngi_n3618.time.sleep'):
        assert driver.output_control(True)
    driver.send_cmd.assert_called_once_with('OUTP ON', None)
    assert driver.last_control['actual']['measured_voltage'] == 100
    assert driver.control_statistics['recovered'] == 1


def test_on_state_without_target_voltage_fails():
    driver = source(measured=['0'] * 50)
    driver.control_timeout = .05
    with clock(), patch('devices.ngi_n3618.time.sleep'):
        assert not driver.output_control(True)
    assert driver.last_control['actual']['output']
    assert driver.last_control['actual']['measured_voltage'] == 0
    assert driver.is_connected
    assert driver.control_statistics['failed'] == 1


def test_voltage_setting_while_off_confirms_setpoint_without_enabling():
    driver = source(output='0')
    assert driver.set_voltage(100)
    driver.send_cmd.assert_called_once_with('VOLT 100.000', None)
    assert 'measured_voltage' not in driver.last_control['actual']


def test_voltage_setting_while_on_requires_actual_voltage():
    driver = source(measured=['0'] * 50)
    driver.control_timeout = .05
    with clock(), patch('devices.ngi_n3618.time.sleep'):
        assert not driver.set_voltage(100)


def test_current_limit_requires_readback_not_only_error_queue():
    driver = source(current='0.1')
    driver.control_timeout = .05
    with clock(), patch('devices.ngi_n3618.time.sleep'):
        assert not driver.set_current(1)
    assert driver.last_control['actual']['current_limit'] == .1


def test_off_does_not_depend_on_voltage_query_or_zero_terminal_voltage():
    driver = source(voltage='ERROR', output='0', measured=['900'])
    assert driver.output_control(False)
    driver.send_cmd.assert_called_once_with('OUTP OFF', None)
    assert [c.args[0] for c in driver.query.call_args_list] == ['SYST:ERR?','OUTP?']


def test_protection_error_is_not_automatically_cleared_or_retried():
    driver = source()
    original = driver.query.side_effect
    driver.query.side_effect = lambda command, logger=None: '-200,"Protection"' if command == 'SYST:ERR?' else original(command, logger)
    assert not driver.output_control(True)
    driver.send_cmd.assert_called_once_with('OUTP ON', None)
    assert 'Protection' in driver.last_error


@pytest.mark.parametrize('bad', ['nan','inf','ERROR 0'])
def test_invalid_pre_enable_setpoint_never_enables(bad):
    driver = source(voltage=bad)
    assert not driver.output_control(True)
    driver.send_cmd.assert_not_called()


def test_failed_voltage_setup_does_not_enable_in_sequence():
    driver = Mock()
    driver.set_voltage.return_value = False
    driver.last_error = 'readback mismatch'
    driver.last_control = {}
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(hv_source=driver))
    success, result = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'NGI高压源', 'params':'100V / 1A / 开启'}))
    assert not success
    assert 'readback mismatch' in result
    driver.output_control.assert_not_called()


def test_ui_exposes_failed_confirmation_and_does_not_keep_old_measurement():
    driver = source()
    page = HVSourceTab(SimpleNamespace(hv_source=driver))
    page.timer.stop();page.connection_timer.stop()
    try:
        page.show_confirmation(dict(success=False,requested={'output':True,'voltage':100},
            actual={'output':True,'measured_voltage':0}, elapsed=1, reason='below target'))
        assert 'below target' in page.lbl_confirmation.text()
        assert '确认失败' in page.lbl_confirmation.text()
        assert '开启' in page.lbl_output.text()  # Actual ON state is separate from voltage confirmation.
        page.lbl_volt.setText('100.00')
        page.show();app.processEvents();page.timer.stop()
        driver.measure_voltage = Mock(return_value=-1)
        driver.measure_current = Mock(return_value=-1)
        page.update_readings()
        import time
        end = time.monotonic() + 2
        while page.operation.busy and time.monotonic() < end:
            app.processEvents();time.sleep(.005)
        assert page.lbl_volt.text() == '—'
        assert page.lbl_curr.text() == '—'
    finally:
        page.shutdown();page.close()
