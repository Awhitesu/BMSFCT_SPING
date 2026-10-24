"""Manual CAN/UDS channel choices stay separate from device connection."""
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from devices.pcan_fd_driver import PCANFDDriver
from ui.tabs.pcan_fd_tab import PCANFDTab


app=QApplication.instance() or QApplication([])


def pump(predicate):
    deadline=time.perf_counter()+2
    while not predicate():
        assert time.perf_counter()<deadline
        app.processEvents()
        time.sleep(.002)


@pytest.fixture
def page(monkeypatch):
    driver=PCANFDDriver('ZLG_CAN2')
    driver.bus=Mock(enabled_channels={0,1,2,3})
    driver.is_connected=True
    driver.connect=Mock(return_value=True)
    driver.disconnect=Mock(return_value=True)
    for name in ('warning','critical','information'):
        monkeypatch.setattr(QMessageBox,name,Mock())
    widget=PCANFDTab(SimpleNamespace(pcan=driver))
    widget.timer.stop()
    yield widget
    widget.shutdown()
    widget.close()


def test_zlg_connection_has_no_physical_channel_selector_and_send_tabs_share_choice(page):
    assert page.channel.isHidden()
    assert page.pcan_channel_label.isHidden()
    assert page.refresh_usb.isHidden()
    assert page.zlg_connection.isHidden() is False
    assert page.send_channel.currentText()==page.uds_channel.currentText()=='ZLG_CAN2'
    assert [page.send_channel.itemText(i) for i in range(4)]==[f'ZLG_CAN{i}' for i in range(1,5)]
    page.send_channel.setCurrentText('ZLG_CAN3')
    assert page.uds_channel.currentText()=='ZLG_CAN3'
    page.uds_channel.setCurrentText('ZLG_CAN4')
    assert page.send_channel.currentText()=='ZLG_CAN4'
    assert page.driver.channel=='ZLG_CAN2'
    page.driver.connect.assert_not_called()
    page.driver.disconnect.assert_not_called()


def test_manual_send_uses_selected_channel_without_reconnecting_or_changing_recipe_default(page):
    bus=page.driver.bus
    page.send_channel.setCurrentText('ZLG_CAN3')
    page.can_id.setText('0x507')
    page.data.setText('1D 4C 1D 4C 1D 4C 1D 4C')
    page.send_message()
    pump(lambda:not page.operation.isRunning())
    sent=bus.send.call_args.args[0]
    assert sent.channel==2 and sent.arbitration_id==0x507
    assert bytes(sent.data)==bytes.fromhex('1D 4C 1D 4C 1D 4C 1D 4C')
    assert page.driver.channel=='ZLG_CAN2' and page.driver.bus is bus
    page.driver.connect.assert_not_called()
    QMessageBox.warning.assert_not_called()


@pytest.mark.parametrize('action',['request','security'])
def test_uds_request_and_security_bind_tx_and_rx_to_selected_channel(page,monkeypatch,action):
    captured=[]
    def worker(client,sid,params,security=None):
        captured.append((client,sid,params,security))
        return SimpleNamespace(result=Mock(),start=Mock(),wait=lambda _=None:True,requestInterruption=Mock())
    monkeypatch.setattr('ui.tabs.pcan_fd_tab.UDSRequestWorker',worker)
    page.uds_channel.setCurrentText('ZLG_CAN4')
    if action=='security':
        page.uds_security.config=lambda:dict(level=1)
        page.unlock_uds_security()
    else:
        page.send_uds_request()
    assert len(captured)==1
    client=captured[0][0]
    view=client.transport.driver
    assert view.driver is page.driver and view.physical_channel==4
    client.transport.send(b'\x10\x03')
    assert page.driver.bus.send.call_args.args[0].channel==3
    now=time.time()
    for channel in (1,3):
        page.driver._publish(dict(direction='RX',channel=channel,timestamp_rel=now,
            can_id=0x7E8,data=bytes([channel])))
    assert view.wait_for_message(0x7E8,timeout=0)['data']==b'\x03'
    assert page.driver.channel=='ZLG_CAN2'
    page.driver.connect.assert_not_called()


def test_four_channel_application_does_not_depend_on_manual_send_selection(page):
    page.send_channel.setCurrentText('ZLG_CAN4')
    page.zlg_channel_controls[2][0].setChecked(False)
    page.apply_zlg_parameters()
    pump(lambda:not page.operation.isRunning())
    settings,persist=page.driver.bus.configure_channels.call_args.args
    assert len(settings)==4 and persist is False
    assert [value['enabled'] for value in settings]==[True,True,False,True]
    assert page.driver.channel=='ZLG_CAN2'
    QMessageBox.information.assert_called_once()
    QMessageBox.warning.assert_not_called()


def test_disabled_physical_channel_is_rejected_before_send(page):
    page.driver.bus.enabled_channels={0,1}
    page.send_channel.setCurrentText('ZLG_CAN3')
    page.send_message()
    page.driver.bus.send.assert_not_called()
    assert '未启用' in QMessageBox.warning.call_args.args[-1]


def test_pcan_connection_selection_and_bitrates_are_preserved(page,monkeypatch):
    monkeypatch.setattr('core.can_choices.CAN_CHANNELS',('PCAN_USBBUS1','PCAN_USBBUS2','ZLG_CAN1'))
    driver=page.driver
    driver.is_connected=False
    driver.channel='PCAN_USBBUS1'
    page.device_type.setCurrentIndex(0)
    page.refresh_pcan_channels()
    page.channel.setCurrentText('PCAN_USBBUS2')
    page.nominal.setCurrentText('250000')
    page.data_rate.setCurrentText('1000000')
    assert not page.channel.isHidden() and not page.refresh_usb.isHidden()
    assert page.zlg_connection.isHidden()
    assert page.nominal.isEnabled() and page.data_rate.isEnabled()
    assert not page.send_channel.isEnabled()
    assert page.send_channel.currentText()=='PCAN_USBBUS2'
    page.device_type.setCurrentIndex(1)
    page.device_type.setCurrentIndex(0)
    assert page.channel.currentText()=='PCAN_USBBUS2'
    page.connect_device()
    pump(lambda:not page.operation.isRunning())
    driver.connect.assert_called_once()
    assert driver.channel=='PCAN_USBBUS2'
    assert driver.nominal_bitrate==250000 and driver.data_bitrate==1000000
    driver.is_connected=True
    page._update_transmit_channels()
    page.send_message()
    pump(lambda:not page.operation.isRunning())
    assert driver.bus.send.call_args.args[0].channel is None
    assert page._selected_debug_driver() is driver


def test_first_zlg_connection_keeps_preselected_manual_channel(page):
    page.driver.is_connected=False
    page.driver.channel='PCAN_USBBUS1'
    page._update_transmit_channels()
    page.send_channel.setCurrentText('ZLG_CAN4')
    page.zlg_ip.setText('192.0.2.1')
    page.zlg_sdk.setText('dummy-sdk')
    def connect():
        page.driver.is_connected=True
        return True
    page.driver.connect.side_effect=connect
    page.connect_device()
    pump(lambda:not page.operation.isRunning())
    assert page.driver.channel=='ZLG_CAN1'
    assert page.send_channel.currentText()==page.uds_channel.currentText()=='ZLG_CAN4'


def test_external_recipe_channel_change_refreshes_manual_default(page):
    page.send_channel.setCurrentText('ZLG_CAN4')
    page.driver.channel='ZLG_CAN3'
    page._update_transmit_channels()
    assert page.send_channel.currentText()==page.uds_channel.currentText()=='ZLG_CAN3'


def test_rebuilt_driver_uses_saved_sdk_and_network_settings_before_connect(page, tmp_path):
    original = page.driver
    original.zlg_sdk_path = str(tmp_path / 'old-sdk')
    page.zlg_sdk.setText(original.zlg_sdk_path)
    replacement = PCANFDDriver('ZLG_CAN3', 250000, 1000000)
    replacement.zlg_sdk_path = str(tmp_path / 'new-sdk')
    replacement.zlg_ip, replacement.zlg_port = '192.0.2.20', 8001
    replacement.connect = Mock(return_value=True)
    page.device_manager.pcan = replacement

    page.connect_device()
    pump(lambda: not page.operation.isRunning())

    assert replacement.zlg_sdk_path == str(tmp_path / 'new-sdk')
    assert (replacement.zlg_ip, replacement.zlg_port) == ('192.0.2.20', 8001)
    assert (replacement.nominal_bitrate, replacement.data_bitrate) == (250000, 1000000)
    replacement.connect.assert_called_once()
    original.connect.assert_not_called()


def test_reopening_debug_page_uses_rebuilt_hardware_configuration(page, tmp_path):
    replacement = PCANFDDriver('PCAN_USBBUS1', 250000, 1000000)
    replacement.zlg_sdk_path = str(tmp_path / 'saved-sdk')
    replacement.zlg_ip, replacement.zlg_port = '192.0.2.30', 8002
    page.device_manager.pcan = replacement

    page.show()
    app.processEvents()

    assert page.device_type.currentIndex() == 0
    assert page.zlg_sdk.text() == replacement.zlg_sdk_path
    assert page.zlg_ip.text() == '192.0.2.30'
    assert page.zlg_port.value() == 8002


def test_refresh_keeps_manual_edits_until_hardware_driver_is_replaced(page):
    page.zlg_sdk.setText('manually-selected-sdk')
    page.zlg_ip.setText('192.0.2.40')
    page.refresh_configuration()
    assert page.zlg_sdk.text() == 'manually-selected-sdk'
    assert page.zlg_ip.text() == '192.0.2.40'


@pytest.mark.parametrize('busy_kind', ['operation', 'uds'])
def test_configuration_refresh_waits_for_active_debug_operation(page, tmp_path, busy_kind):
    original_path = page.zlg_sdk.text()
    replacement = PCANFDDriver('ZLG_CAN1')
    replacement.zlg_sdk_path = str(tmp_path / 'replacement-sdk')
    page.device_manager.pcan = replacement
    if busy_kind == 'operation':
        page.operation.busy = True
    else:
        page.uds_worker = SimpleNamespace(wait=Mock(return_value=False), requestInterruption=Mock())
    page.refresh_configuration()
    assert page.zlg_sdk.text() == original_path
    if busy_kind == 'operation':
        page.operation.busy = False
        page.operation.finished.emit()
    else:
        page.uds_worker.wait.return_value = True
        page.refresh_configuration()
    assert page.zlg_sdk.text() == replacement.zlg_sdk_path


def test_hardware_save_updates_debug_form_without_reopening(tmp_path):
    from devices.manager import DeviceManager
    from ui.tabs.hardware_config_tab import HardwareConfigTab

    old_sdk, new_sdk = tmp_path / 'old-sdk', tmp_path / 'new-sdk'
    for sdk in (old_sdk, new_sdk):
        sdk.mkdir()
        (sdk / 'zlgcan.dll').write_bytes(b'test')
    cfg = dict(pcan_channel='ZLG_CAN2', zlg_sdk_enabled=True,
               zlg_sdk_path=str(old_sdk), zlg_ip='192.0.2.10', zlg_port=8000)
    db = Mock()
    db.load_sys_config.side_effect = lambda: deepcopy(cfg)
    db.save_sys_config.side_effect = lambda saved: cfg.update(saved) or True
    manager = DeviceManager(db)
    manager.safe_shutdown_outputs = Mock(return_value=True)
    manager.disconnect_all = Mock(return_value=True)
    hardware = HardwareConfigTab(db)
    hardware.status_timer.stop()
    hardware.device_manager = manager
    hardware.refresh_hardware_status = Mock()
    debug = PCANFDTab(manager)
    debug.timer.stop()
    hardware.configuration_changed.connect(debug.refresh_configuration)
    try:
        previous = manager.pcan
        sdk_row = next(i for i, spec in enumerate(hardware.DEVICE_SPECS) if spec[0] == 'zlg_sdk')
        hardware.table.cellWidget(sdk_row, 3).path_edit.setText(str(new_sdk))
        hardware.save_config()
        pump(lambda: hardware.table.isEnabled())
        assert manager.pcan is not previous
        assert cfg['zlg_sdk_path'] == str(new_sdk)
        assert manager.pcan.zlg_sdk_path == debug.zlg_sdk.text() == str(new_sdk)
    finally:
        hardware.shutdown()
        debug.shutdown()
        hardware.close()
        debug.close()
