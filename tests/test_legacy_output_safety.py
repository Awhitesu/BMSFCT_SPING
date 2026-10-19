from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.engine import ChannelWorker, SubStep, SubStepType


def setup(device):
    driver = Mock()
    driver.is_connected = True
    driver.last_error = ''
    driver.last_control = {}
    for method in ('set_voltage', 'set_current', 'output_control', 'set_source_data', 'set_source_output'):
        getattr(driver, method).return_value = True
    driver.set_source_func.return_value = ''
    driver.set_source_range.return_value = ''
    key = {'hv': 'hv_source', 'dut': 'dut_power', 'ca': 'ca550'}[device]
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(**{key: driver}))
    worker.is_running = True
    label = {'hv': 'NGI高压源', 'dut': 'IT6722A被测物供电', 'ca': 'CA550'}[device]
    return worker, driver, label


def run(worker, label, text):
    return worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device': label, 'params': text}))


@pytest.mark.parametrize('device', ['hv', 'dut', 'ca'])
def test_cancel_during_setup_never_enables_legacy_output(device):
    worker, driver, label = setup(device)
    setting = driver.set_source_data if device == 'ca' else driver.set_voltage
    setting.side_effect = lambda *a, **k: (worker.cancel_event.set(), True)[1]
    text = 'Type:V / Range:5V / Val:2.5 / Output:开启' if device == 'ca' else '12V / 1A / 开启'
    ok, _ = run(worker, label, text)
    assert not ok
    (driver.set_source_output if device == 'ca' else driver.output_control).assert_not_called()


@pytest.mark.parametrize('device', ['hv', 'dut', 'ca'])
def test_setup_exception_does_not_block_explicit_output_off(device):
    worker, driver, label = setup(device)
    setting = driver.set_source_func if device == 'ca' else driver.set_voltage
    setting.side_effect = ConnectionError('setup failed')
    text = 'Type:V / Range:5V / Val:2.5 / Output:关闭' if device == 'ca' else '12V / 1A / 关闭'
    ok, _ = run(worker, label, text)
    assert not ok
    output = driver.set_source_output if device == 'ca' else driver.output_control
    assert output.call_count == 1
    assert output.call_args.args == (0,)


def test_bad_calibrator_value_is_not_converted_to_zero_and_enabled():
    worker, driver, label = setup('ca')
    ok, _ = run(worker, label, 'Type:V / Range:5V / Val:NaN / Output:开启')
    assert not ok
    driver.set_source_data.assert_not_called()
    driver.set_source_output.assert_not_called()


@pytest.mark.parametrize('device', ['hv', 'dut', 'ca'])
def test_invalid_parameter_still_honors_explicit_off_and_stays_failed(device):
    worker, driver, label = setup(device)
    text = 'Type:V / Val:NaN / Output:关闭' if device == 'ca' else 'badV / 关闭'
    ok, result = run(worker, label, text)
    assert not ok
    output = driver.set_source_output if device == 'ca' else driver.output_control
    assert output.call_count == 1 and output.call_args.args == (0,)
    assert 'FAIL' in result


def test_calibrator_zeroing_exception_does_not_prevent_off():
    worker, driver, label = setup('ca')
    driver.set_source_data.side_effect = ConnectionError('zero failed')
    driver.set_source_output.side_effect = ConnectionError('off failed')
    ok, result = run(worker, label, '关闭')
    assert not ok
    assert 'zero failed' in result and 'off failed' in result
    driver.set_source_output.assert_called_once_with(0)


@pytest.mark.parametrize('device', ['hv', 'dut'])
def test_valid_power_sequence_preserves_setpoints_and_output(device):
    worker, driver, label = setup(device)
    ok, _ = run(worker, label, '12V / 1A / 开启')
    assert ok
    assert driver.set_voltage.call_args.args == (12.0,)
    assert driver.set_current.call_args.args == (1.0,)
    assert driver.output_control.call_args.args == (True,)


def test_calibrator_function_comes_from_type_instead_of_other_fields():
    worker, driver, label = setup('ca')
    ok, _ = run(worker, label, 'Type:V / Range:20mA / Val:2.5 / Output:保持现状')
    assert ok
    driver.set_source_func.assert_called_once_with(0)
    driver.set_source_output.assert_not_called()


@pytest.mark.parametrize('text', ['1.5V / 2V / 开启', '12V / invalidA / 开启', '12V / ON / OFF'])
def test_power_rejects_invalid_or_conflicting_parameters_without_enabling(text):
    worker, driver, label = setup('hv')
    ok, _ = run(worker, label, text)
    assert not ok
    driver.set_voltage.assert_not_called()
    assert all(not call.args[0] for call in driver.output_control.call_args_list)
