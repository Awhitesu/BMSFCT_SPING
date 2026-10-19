from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from devices.ca550_driver import CA550Controller
from ui.tabs.ca550_tab_standalone import CA550StandaloneTab

app = QApplication.instance() or QApplication([])


def connected_driver(*responses):
    driver = CA550Controller('COM1')
    driver.is_connected = True
    driver._send_command = Mock(side_effect=responses)
    return driver


def test_small_setpoint_difference_is_not_accepted_as_success():
    driver = connected_driver('', '0.04')
    driver.control_timeout = .1
    with patch('devices.ca550_driver.time.monotonic', side_effect=[0,1,2]):
        assert not driver.set_source_data(0)
    assert driver.last_control['actual'] == .04
    assert driver.last_control['success'] is False
    assert 'mismatch' in driver.last_error
    assert driver.is_connected  # A mismatch does not imply a broken connection.


def test_delayed_setpoint_is_polled_without_resending_write():
    driver = connected_driver('', '2.4', '2.5')
    with patch('devices.ca550_driver.time.monotonic', side_effect=[0,.01,.02,.03]), patch('devices.ca550_driver.time.sleep'):
        assert driver.set_source_data(2.5)
    assert [c.args[0] for c in driver._send_command.call_args_list] == ['SD2.5000','SD?','SD?']
    assert driver.last_control['actual'] == 2.5
    assert driver.last_control['attempts'] == 1


def test_explicit_tolerance_and_requested_value_are_recorded():
    driver = connected_driver('', '2.49')
    assert driver.set_source_data(2.5, tolerance=.02)
    assert driver.last_control['tolerance'] == .02
    assert driver.last_control['requested'] == 2.5


def test_delayed_output_state_is_confirmed_without_repeated_enable():
    driver = connected_driver('', '0', '1')
    with patch('devices.ca550_driver.time.monotonic', side_effect=[0,.01,.02,.03]), patch('devices.ca550_driver.time.sleep'):
        assert driver.set_source_output(True)
    assert [c.args[0] for c in driver._send_command.call_args_list] == ['SO1','SO?','SO?']
    assert driver.last_control['actual'] == 1


def test_wrong_output_state_records_failed_confirmation():
    driver = connected_driver('', '1')
    driver.control_timeout = .1
    with patch('devices.ca550_driver.time.monotonic', side_effect=[0,1,2]):
        assert not driver.set_source_output(0)
    assert driver.last_control['requested'] == 0
    assert driver.last_control['actual'] == 1
    assert driver.last_control['success'] is False


def test_transport_failure_does_not_blindly_resend_command():
    driver = connected_driver('ERROR: disconnected')
    driver.is_connected = False
    assert not driver.set_source_output(1)
    driver._send_command.assert_called_once_with('SO1')


def test_ui_does_not_show_enabled_before_readback():
    driver = CA550Controller('COM1')
    driver.is_connected = True
    page = CA550StandaloneTab(driver)
    page.timer.stop()
    page.worker.stop()
    try:
        with patch.object(page.worker, 'add_task'):
            page.toggle_source_output(True)
        assert not page.btn_so.isChecked()
        assert not page.btn_so.isEnabled()
        page.on_control_confirmed('set_source_output', dict(success=False,requested=1,actual=0,elapsed=.2,reason='mismatch'))
        page.on_response('set_source_output','False',(1,))
        assert not page.btn_so.isChecked()
        assert page.btn_so.isEnabled()
        assert 'mismatch' in page.lbl_control_result.text()
        page.on_control_confirmed('set_source_output', dict(success=True,requested=1,actual=1,elapsed=.2,reason=''))
        page.on_response('set_source_output','True',(1,))
        assert page.btn_so.isChecked()
    finally:
        page.close()


def test_periodic_measurement_reads_measurement_not_source_setpoint():
    driver = CA550Controller('COM1')
    driver.is_connected = True
    page = CA550StandaloneTab(driver)
    page.timer.stop()
    page.worker.stop()
    try:
        page.show()
        app.processEvents()
        page.btn_mo.setChecked(True)
        with patch.object(page.worker, 'add_task') as task:
            page.periodic_update()
        assert [c.args for c in task.call_args_list] == [('read_source_output',),('read_measure_data','measure')]
    finally:
        page.close()


def test_statistics_distinguish_immediate_delayed_and_failed_confirmation():
    driver = connected_driver('', '1')
    assert driver.set_source_output(1)
    first = driver.last_control
    driver._send_command = Mock(side_effect=['','1','0'])
    with patch('devices.ca550_driver.time.monotonic', side_effect=[0,.01,.02,.03]), patch('devices.ca550_driver.time.sleep'):
        assert driver.set_source_output(0)
    driver.is_connected = False
    driver._send_command = Mock(return_value='ERROR: disconnected')
    assert not driver.set_source_output(1)
    assert driver.control_statistics == dict(total=3, first_pass=1, recovered=1, failed=1)
    assert first['statistics'] == dict(total=1, first_pass=1, recovered=0, failed=0)
