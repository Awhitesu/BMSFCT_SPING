"""CLI availability must never be reported as a physical E2 connection."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from devices.manager import DeviceManager
from devices.renesas_e2_rfp import RenesasE2Rfp
from devices.windows_usb import UsbDevice
from ui.dialogs.startup_dialog import StartupCheckDialog
from ui.tabs.renesas_e2_tab import RenesasE2Tab


app = QApplication.instance() or QApplication([])


@pytest.fixture
def ready_tool(tmp_path):
    exe = tmp_path / 'rfp-cli.exe'
    exe.touch()
    driver = RenesasE2Rfp(str(exe))
    with patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[]), \
         patch('devices.renesas_e2_rfp.subprocess.run', return_value=
               SimpleNamespace(returncode=0, stdout='RFP CLI version\n', stderr='')) as run:
        assert not driver.connect()
    run.assert_called_once()
    assert run.call_args.args[0] == [str(exe), '--version']
    return driver


def manager_for(driver):
    database = Mock()
    database.load_sys_config.return_value = {'e2_rfp_enabled': True}
    manager = DeviceManager(database)
    manager.is_enabled = lambda key: key == 'e2_rfp'
    manager.e2_rfp = driver
    return manager


def test_version_success_without_any_hardware_never_sets_connected(ready_tool):
    assert ready_tool.tool_ready is True
    assert ready_tool.is_connected is False
    with patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[]):
        assert ready_tool.query_state() == 'Lost'
    state = manager_for(ready_tool).get_all_device_status()[0]
    assert state['status'] == '离线'
    assert state['connection_verified'] is False
    assert state['color'] == '#DC3545'


@pytest.mark.parametrize('failure', ['missing', 'error', 'timeout'])
def test_failed_reinitialization_clears_cached_readiness(ready_tool, failure):
    ready_tool.is_connected = True  # Reject a stale legacy flag as well.
    with patch.object(ready_tool, '_resolve_exe', return_value='' if failure == 'missing' else ready_tool.rfp_cli_path), \
         patch('devices.renesas_e2_rfp.subprocess.run', return_value=
               SimpleNamespace(returncode=1, stdout='', stderr='failed'),
               side_effect=subprocess.TimeoutExpired('rfp-cli', 15) if failure == 'timeout' else None):
        assert not ready_tool.connect()
    assert not ready_tool.tool_ready
    assert not ready_tool.is_connected
    assert ready_tool.query_state() == 'Lost'
    assert manager_for(ready_tool).get_all_device_status()[0]['status'] == '离线'


def test_debug_badge_does_not_turn_green_after_cli_check_or_last_success(ready_tool):
    page = RenesasE2Tab(manager_for(ready_tool))
    try:
        for state in ('Ready', 'Pass', 'Error'):
            ready_tool._state = state
            page.refresh_connection_state()
            assert page.lbl_connection.text() == '●  E2未连接'
            assert '#FF4D5A' in page.lbl_connection.styleSheet()
            assert not page.btn_run.isEnabled()
        ready_tool.disconnect()
        page.refresh_connection_state()
        assert page.lbl_connection.text() == '●  工具未就绪'
        assert not page.btn_run.isEnabled()
    finally:
        page.shutdown()
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_initialization_logs_and_startup_summary_do_not_claim_hardware_pass(ready_tool):
    manager = manager_for(ready_tool)
    messages = []
    with patch('devices.renesas_e2_rfp.subprocess.run', return_value=
               SimpleNamespace(returncode=0, stdout='version', stderr='')), \
         patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[]):
        assert not manager.init_all_devices(logger=messages.append)
    assert any('offline' in message and '未检测到' in message for message in messages)
    assert not any('connected' in message for message in messages)
    # Exercise the actual finish slot without starting device initialization.
    with patch.object(StartupCheckDialog, '_start_check'), \
         patch.object(StartupCheckDialog, '_bring_to_front'):
        dialog = StartupCheckDialog(manager)
    try:
        dialog._on_check_finished(False)
        assert '自检失败' in dialog.lbl_title.text()
        assert '离线设备：1' in dialog.lbl_title.text()
        assert dialog._last_success is False
        assert '全部通过' not in dialog.lbl_title.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_engine_tool_status_uses_readiness_without_reconnecting(ready_tool):
    from core.engine import ChannelWorker, SubStep, SubStepType
    manager = manager_for(ready_tool)
    worker = ChannelWorker(0, [], device_manager=manager)
    step = SubStep(SubStepType.SET_INSTRUMENT,
                   {'device': '瑞萨 E2 仿真器 (rfp-cli)', 'action': '查询状态', 'params': ''})
    with patch.object(ready_tool, 'connect') as connect, \
         patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[]):
        ok, result = worker._execute_sub_step_logic(step)
    assert not ok and result == 'Lost'
    connect.assert_not_called()
    assert ready_tool.is_connected is False
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('state,success', [('Ready', True), ('Pass', True), ('Error', False)])
def test_query_explains_cached_status_without_claiming_current_connection(ready_tool, state, success):
    ready_tool._state = state
    page = RenesasE2Tab(manager_for(ready_tool))
    results = []
    page.operation_finished.connect(lambda ok, message: results.append((ok, message)))
    try:
        with patch('ui.tabs.renesas_e2_tab.threading.Thread') as thread, \
             patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[
                 UsbDevice(r'USB\VID_045B&PID_82A1\TEST_E2', True, 0)]):
            page.query_state()
            thread.call_args.kwargs['target']()
        assert results[0][0] is success
        assert '连接未验证' in results[0][1]
        assert page.lbl_connection.text() == '●  E2已识别'
        if state in ('Pass', 'Error'):
            assert '上次操作' in results[0][1]
    finally:
        page.shutdown()
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('node', [
    UsbDevice(r'USB\VID_045B&PID_82A0\E2_LITE', True, 0),
    UsbDevice(r'USB\VID_045B&PID_0250\IE850A', True, 0),
    UsbDevice(r'USB\VID_045B&PID_82A10\OTHER', True, 0),
    UsbDevice(r'USB\VID_045B&PID_82A1\DISABLED', False, 22),
    UsbDevice(r'USB\VID_045B&PID_82A1\NO_DRIVER', False, 28),
])
def test_other_emulators_or_unusable_nodes_never_pass(ready_tool, node):
    with patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[node]):
        assert not ready_tool.refresh_presence()
    assert not ready_tool.is_connected
    assert ready_tool.presence_error


def test_e2_usb_hotplug_clears_cached_success_and_recovers(ready_tool):
    node = UsbDevice(r'usb\vid_045b&pid_82a1&rev_0100\TEST_E2', True, 0)
    with patch('devices.renesas_e2_rfp.present_usb_devices', side_effect=[[node], [], [node]]):
        assert ready_tool.refresh_presence()
        assert manager_for(ready_tool).get_all_device_status()[0]['status'] == 'E2已识别（目标未验证）'
        assert ready_tool.query_state() == 'Lost'
        assert not ready_tool.is_connected and not ready_tool.usb_device_id
        assert ready_tool.refresh_presence()
    assert ready_tool.is_connected
    assert not ready_tool.presence_error and not ready_tool.last_error


def test_usb_enumeration_failure_cannot_leave_a_stale_connection(ready_tool):
    ready_tool.is_connected = True
    with patch('devices.renesas_e2_rfp.present_usb_devices', side_effect=OSError('enumeration failed')):
        assert not ready_tool.refresh_presence()
    assert not ready_tool.is_connected and not ready_tool.presence_checked
    assert 'enumeration failed' in ready_tool.last_error
