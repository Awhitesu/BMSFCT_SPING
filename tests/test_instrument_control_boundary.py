import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.instrument_control import control_power, control_calibrator, shutdown_calibrator
from core.legacy_controls import execute_power
from core.device_steps import execute, encode
from ui.tabs.hv_tab import HVSourceTab
from ui.tabs.mainboard_power_tab import MainboardPowerTab
from ui.tabs.ca550_tab_standalone import CA550SerialWorker


app = QApplication.instance() or QApplication([])


def driver():
    item = Mock()
    item._lock = threading.RLock()
    item.is_connected = True
    item.port, item.baudrate, item.ip = 'COM1', 9600, '192.0.2.1'
    item.control_timeout, item.output_voltage_tolerance = 5., .05
    item.last_error = ''
    item.last_control = {}
    for name in ('set_voltage', 'set_current', 'output_control', 'set_source_data', 'set_source_output'):
        getattr(item, name).return_value = True
    return item


@pytest.mark.parametrize('failure', [False, True])
def test_debug_confirmation_overrides_are_scoped_to_one_operation(failure):
    item = driver()
    def configure(timeout, tolerance):
        item.control_timeout, item.output_voltage_tolerance = timeout, tolerance
    item.configure_confirmation.side_effect = configure
    observed = []
    def control(value):
        observed.append((item.control_timeout, item.output_voltage_tolerance))
        if failure:
            raise ConnectionError('transport failed')
        return True
    item.output_control.side_effect = control
    if failure:
        with pytest.raises(ConnectionError):
            control_power(item, 'output_control', True, confirmation=(.5, .01))
    else:
        assert control_power(item, 'output_control', True, confirmation=(.5, .01))
    assert observed == [(.5, .01)]
    assert (item.control_timeout, item.output_voltage_tolerance) == (5., .05)


def test_cancel_is_rechecked_after_waiting_for_shared_driver_lock():
    item = driver()
    entered, stopped = threading.Event(), threading.Event()
    failures = []
    def invoke():
        entered.set()
        try:
            control_power(item, 'output_control', True, cancelled=stopped.is_set)
        except RuntimeError as exc:
            failures.append(str(exc))
    with item._lock:
        thread = threading.Thread(target=invoke)
        thread.start()
        assert entered.wait(2)
        stopped.set()
    thread.join(2)
    assert not thread.is_alive() and failures
    item.output_control.assert_not_called()
    assert control_power(item, 'output_control', False, cancelled=stopped.is_set)


def test_whole_legacy_plan_holds_shared_lock_between_settings_and_enable():
    item = driver()
    entered, release = threading.Event(), threading.Event()
    failures = []
    def voltage(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return True
    item.set_voltage.side_effect = voltage
    def invoke():
        try:
            execute_power(item, '12V / 1A / ON', None, lambda: False)
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        assert entered.wait(2)
        acquired = item._lock.acquire(blocking=False)
        if acquired:
            item._lock.release()
        assert not acquired
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and not failures
    item.output_control.assert_called_once_with(True)


@pytest.mark.parametrize('page_type,key', [(HVSourceTab, 'hv_source'), (MainboardPowerTab, 'dut_power')])
def test_debug_output_routes_through_shared_boundary(page_type, key):
    item = driver()
    page = page_type(SimpleNamespace(**{key: item}))
    page.timer.stop()
    page.connection_timer.stop()
    try:
        with patch('ui.tabs.hv_tab.control_power' if key == 'hv_source' else
                   'ui.tabs.mainboard_power_tab.control_power', return_value=True) as control:
            if key == 'hv_source':
                page.run_device = lambda label, action, *a, **k: action()
            else:
                page.run_device = lambda action, *a, **k: action()
            page.control_output(True)
            assert control.call_count == 1
            assert control.call_args.args == (item, 'output_control', True)
            item.output_control.assert_not_called()
    finally:
        page.shutdown()
        page.close()


def test_structured_test_output_routes_through_shared_boundary():
    item = driver()
    manager = SimpleNamespace(hv_source=item, is_enabled=lambda _: True,
                              device_step_lock=lambda _: threading.RLock())
    with patch('core.instrument_control.control_power', return_value=True) as control:
        assert execute(manager, encode('hv_source', 'on', {}), {}, lambda _: None)[0]
        assert control.call_args.args == (item, 'output_control', True)
        item.output_control.assert_not_called()


def test_calibrator_zero_failure_still_sends_off_in_shared_shutdown():
    item = driver()
    item.set_source_data.side_effect = ConnectionError('zero failed')
    with pytest.raises(RuntimeError, match='zero failed'):
        shutdown_calibrator(item)
    item.set_source_output.assert_called_once_with(0)


def test_calibrator_source_worker_routes_to_common_control():
    item = driver()
    worker = CA550SerialWorker(item)
    responses = []
    worker.response_received.connect(lambda *args: responses.append(args))
    with patch('ui.tabs.ca550_tab_standalone.control_calibrator', return_value=True) as control:
        try:
            worker.add_task('set_source_output', 1)
            worker.start()
            deadline = time.monotonic() + 3
            while not responses and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            assert responses
            assert control.call_args.args == (item, 'set_source_output', 1)
            item.set_source_output.assert_not_called()
        finally:
            worker.stop()


@pytest.mark.parametrize('action,value', [('set_voltage', float('nan')), ('output_control', 'ON')])
def test_common_boundary_rejects_invalid_input_without_touching_driver(action,value):
    item = driver()
    with pytest.raises(ValueError):
        control_power(item, action, value)
    getattr(item, action).assert_not_called()


def test_common_calibrator_off_remains_available_after_cancel():
    item = driver()
    with pytest.raises(RuntimeError):
        control_calibrator(item, 'set_source_output', 1, cancelled=lambda: True)
    assert control_calibrator(item, 'set_source_output', 0, cancelled=lambda: True)
    item.set_source_output.assert_called_once_with(0)


def test_cancel_during_scoped_configuration_blocks_actual_enable():
    item = driver()
    stopped = threading.Event()
    item.configure_confirmation.side_effect = lambda *args: stopped.set()
    with pytest.raises(RuntimeError):
        control_power(item, 'output_control', True, confirmation=(1, .1), cancelled=stopped.is_set)
    item.output_control.assert_not_called()


def test_bad_confirmation_settings_cannot_prevent_explicit_off():
    item = driver()
    with pytest.raises(RuntimeError, match='确认参数无效'):
        control_power(item, 'output_control', False, confirmation=(float('nan'), .1))
    item.output_control.assert_called_once_with(False)


def test_manager_emergency_shutdown_uses_common_power_boundary():
    from devices.manager import DeviceManager
    manager = DeviceManager.__new__(DeviceManager)
    manager.simulator_channels = lambda: []
    manager.rn_boards = {}
    manager.dut_power, manager.hv_source = driver(), driver()
    manager.ca550 = manager.it6333b = manager.dg2052 = None
    with patch('devices.manager.control_power', return_value=True) as control:
        assert manager.safe_shutdown_outputs(emergency=True)
        assert [(call.args[1], call.args[2]) for call in control.call_args_list] == [
            ('output_control', False), ('output_control', False)]
    manager.dut_power.output_control.assert_not_called()
    manager.hv_source.output_control.assert_not_called()
