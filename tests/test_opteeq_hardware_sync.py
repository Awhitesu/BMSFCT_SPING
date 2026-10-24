"""力捷丰调试页复用硬件管理中心重新初始化后的当前设备连接。"""
import time
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from devices.manager import DeviceManager
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.opteeq_tab import OpteeQTab


app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic() + 4
    while not predicate():
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.002)


@pytest.fixture
def pages():
    cfg = dict(opteeq_enabled=True, opteeq_ip='192.0.2.10', opteeq_port=10023)
    db = Mock()
    db.load_sys_config.side_effect = lambda: deepcopy(cfg)
    db.save_sys_config.side_effect = lambda saved: cfg.update(saved) or True
    manager = DeviceManager(db)
    manager.safe_shutdown_outputs = Mock(return_value=True)

    def disconnect():
        manager.opteeq.is_connected = False
        return True

    def initialize(**kwargs):
        manager.opteeq.is_connected = True
        return True

    manager.disconnect_all = Mock(side_effect=disconnect)
    manager.init_all_devices = Mock(side_effect=initialize)
    hardware = HardwareConfigTab(db)
    hardware.status_timer.stop()
    hardware.device_manager = manager
    hardware.refresh_hardware_status = Mock()
    debug = OpteeQTab(manager)
    debug.status_timer.stop()
    hardware.configuration_changed.connect(debug.refresh_connection_state)
    yield manager, hardware, debug
    hardware.shutdown()
    debug.shutdown()
    hardware.close()
    debug.close()


@pytest.mark.parametrize('change_address', [False, True])
def test_hardware_initialization_restores_debug_connection_and_controls(pages, change_address):
    manager, hardware, debug = pages
    old_programmer = manager.opteeq
    assert not debug.btn_query.isEnabled()
    if change_address:
        row = next(i for i, spec in enumerate(hardware.DEVICE_SPECS) if spec[0] == 'opteeq')
        hardware.table.item(row, 3).setText('192.0.2.20')
        hardware.table.item(row, 4).setText('10024')
    debug._has_auto_read = True
    debug.lbl_device_state.setText('Pass')

    hardware.init_enabled_devices()
    pump(lambda: hardware.table.isEnabled())

    assert manager.opteeq is not old_programmer
    assert not old_programmer.is_connected
    assert manager.opteeq.is_connected
    assert not debug.isVisible()
    assert debug.programmer is manager.opteeq
    assert debug.lbl_connection.text() == '●  已连接'
    assert debug.edit_ip.text() == ('192.0.2.20' if change_address else '192.0.2.10')
    assert debug.edit_port.text() == ('10024' if change_address else '10023')
    assert debug.lbl_device_state.text() == '未查询'
    assert not debug._has_auto_read
    for button in (debug.btn_query, debug.btn_run, debug.btn_read_remote, debug.btn_browse):
        assert button.isEnabled()
    assert not debug.btn_export_remote.isEnabled()
    manager.init_all_devices.assert_called_once()


def test_query_uses_current_connection_before_next_status_refresh(pages):
    manager, _, debug = pages
    old_programmer = manager.opteeq
    old_programmer.query_state = Mock()
    manager.update_config()
    current = manager.opteeq
    current.is_connected = True
    current.query_state = Mock(return_value='Ready')
    current.connect = Mock()
    with patch('ui.tabs.opteeq_tab.QMessageBox.warning') as warning, \
         patch('ui.tabs.opteeq_tab.threading.Thread') as thread:
        debug.query_state()
        warning.assert_not_called()
        thread.call_args.kwargs['target']()
    assert debug.lbl_device_state.text() == 'Ready'
    assert debug.btn_query.isEnabled()
    current.query_state.assert_called_once()
    current.connect.assert_not_called()
    old_programmer.query_state.assert_not_called()


def test_disconnected_replacement_disables_controls_without_reconnecting(pages):
    manager, _, debug = pages
    manager.opteeq.is_connected = True
    debug.refresh_connection_state()
    manager.update_config()
    manager.opteeq.connect = Mock()
    debug.refresh_connection_state()
    assert debug.lbl_connection.text() == '●  未连接'
    assert not debug.btn_query.isEnabled()
    assert not debug.btn_run.isEnabled()
    with patch('ui.tabs.opteeq_tab.QMessageBox.warning') as warning:
        assert not debug._require_initialized_connection()
    warning.assert_called_once()
    manager.opteeq.connect.assert_not_called()


def test_active_operation_keeps_its_driver_until_completion(pages):
    manager, _, debug = pages
    original = manager.opteeq
    original.is_connected = True
    debug._running = True
    manager.update_config()
    manager.opteeq.is_connected = True
    debug.refresh_connection_state()
    assert debug.programmer is original
    assert not debug.btn_query.isEnabled()
    debug._finish_operation(True, 'Ready')
    assert debug.programmer is manager.opteeq
    assert debug.btn_query.isEnabled()
    assert debug.lbl_device_state.text() == '未查询'


def test_visible_page_reads_project_list_once_after_instance_replacement(pages):
    manager, _, debug = pages
    debug._has_auto_read = True
    manager.update_config()
    manager.opteeq.is_connected = True
    with patch.object(debug, 'read_remote_projects') as read:
        debug.show()
        debug.refresh_connection_state()
        debug.refresh_connection_state()
        pump(lambda: read.called)
        debug.refresh_connection_state()
        assert read.call_count == 1
