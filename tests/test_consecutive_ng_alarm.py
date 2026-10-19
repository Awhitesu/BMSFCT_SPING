import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from core.consecutive_ng import ConsecutiveNgAlarm
from ui.tabs.overview_tab import OverviewTab
from ui.tabs.settings_tab import SettingsTab

app = QApplication.instance() or QApplication([])


def test_threshold_alarm_once_and_pass_resets():
    alarm = ConsecutiveNgAlarm(True, 2)
    assert not alarm.record(False,'A','1')
    assert alarm.record(False,'B','2')
    assert not alarm.record(False,'C','3')
    assert not alarm.record(True,'D','4')
    assert alarm.count == 0
    assert not alarm.record(False,'A','5')
    assert alarm.record(False,'B','6')


def test_repeated_barcode_and_duplicate_signal_do_not_count_twice():
    alarm = ConsecutiveNgAlarm(True, 2)
    assert not alarm.record(False,'A','1')
    assert not alarm.record(False,'A','2')
    assert not alarm.record(False,'B','2')
    assert alarm.count == 1
    assert alarm.record(False,'B','3')


def test_disable_and_save_reset_counter():
    alarm=ConsecutiveNgAlarm(False,1)
    assert not alarm.record(False,'A','1')
    alarm.configure(True,1)
    assert alarm.record(False,'B','2')
    alarm.configure(True,2)
    assert alarm.count == 0 and not alarm.alarmed


@pytest.mark.parametrize('value',[0,-1,1.5,True,10001])
def test_invalid_threshold_rejected(value):
    with pytest.raises(ValueError): ConsecutiveNgAlarm(True,value)


def test_settings_emit_enable_and_threshold():
    page=SettingsTab(); captured=[]
    try:
        page.set_ng_alarm_settings(True,5)
        page.ng_alarm_save_requested.connect(lambda e,n:captured.append((e,n)))
        page.btn_save_ng_alarm.click()
        assert captured == [(True,5)]
    finally:page.close()


def test_finished_hook_ignores_other_channel_cancelled_and_record_error():
    page=OverviewTab();page.stop_monitor()
    worker=SimpleNamespace(sn_barcode='A',execution_id='1',record_error='',cancel_event=threading.Event())
    page.engine=SimpleNamespace(workers={1:worker})
    page.ng_alarm.configure(True,2)
    try:
        with patch.object(page,'_show_consecutive_ng_alarm') as popup:
            page.on_worker_test_finished(2,False)
            assert page.ng_alarm.count == 0
            worker.cancel_event.set()
            page.on_worker_test_finished(1,False)
            assert page.ng_alarm.count == 0
            worker.cancel_event.clear()
            worker.record_error='disk full'
            page.on_worker_test_finished(1,False)
            assert page.ng_alarm.count == 0
            worker.record_error=''
            page.on_worker_test_finished(1,False)
            worker.sn_barcode='B';worker.execution_id='2'
            page.on_worker_test_finished(1,False)
            popup.assert_called_once_with('B')
            page.on_worker_test_finished(1,False)
            popup.assert_called_once()
            worker.sn_barcode='C';worker.execution_id='3'
            page.on_worker_test_finished(1,True)
            assert page.ng_alarm.count == 0
    finally:page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close()


def test_popup_opens_without_blocking_completion_slot():
    page=OverviewTab();page.stop_monitor();page.ng_alarm.configure(True,1);page.ng_alarm.count=1
    try:
        page._show_consecutive_ng_alarm('SN1')
        assert page._ng_alarm_box is not None
        assert '1 片' in page._ng_alarm_box.text()
        page._ng_alarm_box.accept()
        assert page._ng_alarm_box is None
    finally:page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close()
