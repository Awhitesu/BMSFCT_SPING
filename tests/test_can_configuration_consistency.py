import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.can_frame_config import can_id,response_matcher
from core.engine import ChannelWorker
from devices.pcan_fd_driver import PCANFDDriver


def test_hex_ids_are_identical_for_capture_save_and_engine():
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.step_dialog import StepDialog
    app=QApplication.instance() or QApplication([])
    page=StepDialog()
    try:
        page.category_combo.setCurrentText('报文交互');page.device_combo.setCurrentText('CAN 交互')
        page.action_combo.setCurrentText('交互/问答')
        page.c_id.setText('573');page.c_wait_id.setText('673');page.c_rx_prefix.setText('06 01')
        data=page.get_data()
        assert 'ID:0x573' in data['params'] and 'WAIT_ID:0x673' in data['params']
        worker=ChannelWorker(1,[])
        config=worker._parse_can_params(data)
        assert config['can_id']==can_id('573')==0x573
        assert config['wait_id']==0x673
        restored=StepDialog(step_data=data)
        try:assert restored.c_rx_prefix.text()=='06 01'
        finally:restored.close()
    finally:page.close()


def test_response_filters_reject_wrong_format_prefix_and_nondata_frames():
    match=response_matcher(0x673,'standard','06')
    message=dict(is_extended_id=False,data=b'\x06\x01')
    assert match(message)
    for change in (dict(is_extended_id=True),dict(data=b'\x07'),dict(is_remote_frame=True),dict(is_error_frame=True)):
        assert not match(dict(message,**change))
    with pytest.raises(ValueError):response_matcher(0x1234,'standard')


def test_send_failure_is_distinct_from_response_timeout():
    driver=PCANFDDriver();driver.send_can_message=Mock(return_value=False);driver.last_error='bus unavailable'
    with pytest.raises(ConnectionError,match='bus unavailable'):
        driver.send_and_wait_response(0,0x573,0,1,b'1',0x673,.01)
    driver.send_can_message.return_value=True
    assert driver.send_and_wait_response(0,0x573,0,1,b'1',0x673,.01) is None


def test_uds_capture_does_not_wait_on_gui_thread(monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from ui.dialogs.step_dialog import StepDialog
    app=QApplication.instance() or QApplication([])
    release=threading.Event();entered=threading.Event();threads=[]
    def request(*args):
        threads.append(threading.get_ident());entered.set();release.wait(2)
        return SimpleNamespace(positive=True,raw=b'\x62\xF1\x90AB')
    client=SimpleNamespace(request=request)
    driver=SimpleNamespace(is_connected=True,channel='PCAN_USBBUS1',create_uds_client=lambda **kwargs:client)
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:pytest.fail(str(a)))
    page=StepDialog(device_manager=SimpleNamespace(pcan=driver))
    try:
        page.uds_request.setText('22 F1 90')
        start=time.perf_counter();page._on_live_sniff_clicked()
        assert time.perf_counter()-start<.5
        assert entered.wait(1) and threads[0]!=threading.get_ident()
        assert not page.btn_live_sniff.isEnabled()
        release.set();deadline=time.monotonic()+2
        while page._uds_capture_task.isRunning() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert page._uds_response_preview=='62 F1 90 41 42'
        assert page.btn_live_sniff.isEnabled()
    finally:
        release.set()
        if hasattr(page,'_uds_capture_task'):page._uds_capture_task.shutdown()
        page.close()


def test_dbc_lists_only_enabled_zlg_channels_and_tracks_changes():
    from PySide6.QtWidgets import QApplication
    from ui.widgets.dbc_forms import DBCMonitorTab
    app=QApplication.instance() or QApplication([])
    service=SimpleNamespace(asset=None,context_key=None,last_error='',snapshot=lambda:{})
    driver=SimpleNamespace(is_connected=True,channel='ZLG_CAN1',bus=SimpleNamespace(enabled_channels={0,2}))
    page=DBCMonitorTab(SimpleNamespace(dbc_monitor=service,pcan=driver))
    try:
        page.show();app.processEvents();page.refresh()
        assert [page.channel.itemData(i) for i in range(page.channel.count())]==['ZLG_CAN1','ZLG_CAN3']
        driver.bus.enabled_channels={1};page.refresh()
        assert page.channel.count()==1 and page.channel.currentData()=='ZLG_CAN2'
        driver.is_connected=False;page.refresh()
        assert page.channel.currentData() is None and not page.channel.isEnabled()
    finally:page.close()


def test_driver_never_returns_error_or_remote_frames_as_responses():
    driver=PCANFDDriver()
    for flags in ({'is_error_frame':True},{'is_remote_frame':True}):
        driver._publish(dict(direction='RX',timestamp_rel=time.time(),can_id=0x673,data=b'bad',**flags))
    assert driver.wait_for_message(0x673,timeout=0,since_time=0) is None
