import pytest
from PySide6.QtWidgets import QApplication
from ui.dialogs.step_dialog import StepDialog
from ui.dialogs.flash_status_dialog import FlashStatusDialog
from devices.pcan_fd_driver import PCANFDDriver
from core.flash_progress import FlashProgress

app = QApplication.instance() or QApplication([])


def editor():
    page = StepDialog()
    page.category_combo.setCurrentText('设备操作')
    page.sub_category_combo.setCurrentText('下载器')
    page.device_combo.setCurrentText('APP刷写')
    page.action_combo.setCurrentIndex(page.action_combo.count()-1)
    return page


def test_app_step_monitor_ids_saved_and_restored():
    page = editor()
    try:
        page.app_flash_tx_id.setText('7E0')
        page.app_flash_rx_id.setText('0x7E8')
        saved = page.get_data()
        assert 'FLASH_TX_ID:0x7E0' in saved['params']
        assert 'FLASH_RX_ID:0x7E8' in saved['params']
        restored = StepDialog(step_data=saved)
        try:
            assert restored._app_flash_monitor_ids() == (0x7E0, 0x7E8)
            assert not restored.app_flash_tx_id.isHidden()
        finally:
            restored.close()
    finally:
        page.close()


@pytest.mark.parametrize('tx,rx', [('wrong','7E8'), ('7E0','7E0'), ('20000000','7E8')])
def test_invalid_ids_block_saving(tx,rx):
    page = editor()
    try:
        page.app_flash_tx_id.setText(tx); page.app_flash_rx_id.setText(rx)
        with pytest.raises(ValueError):
            page.get_data()
    finally:
        page.close()


def test_same_published_message_reaches_monitor_queue_and_flash_listener():
    driver = PCANFDDriver('PCAN_USBBUS1')
    tracker = FlashProgress(0x7E0,0x7E8)
    driver.add_message_listener(tracker.feed)
    try:
        driver._publish(dict(can_id=0x123,direction='RX',data=b'\x00'))
        driver._publish(dict(can_id=0x7E0,direction='RX',data=b'\x02\x10\x02'))
        assert driver.msg_queue.qsize() == 2
        state = tracker.snapshot()
        assert state['received_frames'] == 2
        assert state['phase'] == '切换编程会话'
        assert state['last_can_age'] is not None
    finally:
        driver.remove_message_listener(tracker.feed)


def test_popup_distinguishes_rx_data_from_matching_ids():
    page = FlashStatusDialog()
    try:
        state = dict(active=True,received_frames=12,monitor_ids='0x7E0/0x7E8',can_interface='PCAN_USBBUS1',
                     observed_ids=['0x123'],last_can_age=None)
        page.render_state(state)
        assert '有报文但监听ID未匹配' in page.lbl_flash_can.toolTip()
        assert '0x123' in page.lbl_flash_can.toolTip()
    finally:
        page.close()
