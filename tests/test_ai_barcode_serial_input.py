from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from ui.widgets.serial_scanner import SerialScanner
from ui.dialogs.ai_barcode_dialog import AiBarcodeRuleDialog

app=QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(monkeypatch):
    reader=SerialScanner();reader.config=dict(scanner_enabled=True)
    monkeypatch.setattr(reader.port,'isOpen',lambda:True)
    monkeypatch.setattr(reader.port,'clear',lambda *args:True)
    monkeypatch.setattr(reader.port,'write',Mock(return_value=3))
    page=AiBarcodeRuleDialog(scanner=reader);page.show();app.processEvents()
    yield page,reader
    page.close();reader.close();app.processEvents()


def test_continuous_samples_and_manual_draft_are_both_preserved(dialog):
    page,reader=dialog
    page.scan_input.setText('MANUAL003')
    reader.feed(b'SAMPLE001\r\nSAMPLE002\r\n')
    assert page.samples==['SAMPLE001','SAMPLE002']
    assert page.scan_input.text()=='MANUAL003' and reader.waiting
    QTest.keyClick(page.scan_input,Qt.Key_Return)
    assert page.samples==['SAMPLE001','SAMPLE002','MANUAL003']
    assert page.isVisible() and page.result()==0
    reader.feed(b'SAMPLE001\r\n');assert len(page.samples)==3
    QTest.qWait(180)
    calls=[c.args[0] for c in reader.port.write.call_args_list]
    assert calls[0]==b'TON' and b'TOFF' in calls[1:] and calls[-1]==b'TON'


def test_focus_routes_scan_to_verification_and_manual_edit_still_works(dialog):
    page,reader=dialog
    reader.feed(b'SAMPLE001\r\nSAMPLE002\r\n')
    page.verify_input.setFocus();app.processEvents()
    reader.feed(b'SAMPLE003\r\n')
    assert page.samples==['SAMPLE001','SAMPLE002']
    assert page.verify_input.text()=='SAMPLE003'
    assert '通过' in page.lbl_verify_status.text()
    page.verify_input.setText('WRONG')
    assert '不符合' in page.lbl_verify_status.text()
    page.scan_input.setFocus();app.processEvents();reader.feed(b'SAMPLE004\r\n')
    assert page.samples[-1]=='SAMPLE004'


def test_closing_releases_reader_and_late_data_does_not_modify_samples(dialog):
    page,reader=dialog
    reader.feed(b'SAMPLE001\r\n');page.reject();app.processEvents()
    before=list(page.samples);reader.feed(b'LATE\r\n');reader.barcode.emit('LATER')
    assert page.samples==before and not reader.waiting and reader.owner is None
    assert reader.port.write.call_args.args[0]==b'TOFF'
    owner=object();assert reader.begin_scan(owner);reader.end_scan(owner)


def test_busy_reader_keeps_manual_input_and_does_not_take_other_session(monkeypatch):
    reader=SerialScanner();reader.config=dict(scanner_enabled=True,scanner_trigger=False)
    monkeypatch.setattr(reader.port,'isOpen',lambda:True);monkeypatch.setattr(reader.port,'clear',lambda *args:True)
    other=object();reader.begin_scan(other)
    page=AiBarcodeRuleDialog(scanner=reader)
    try:
        page.show();app.processEvents();page.scan_input.setText('MANUAL001');page._on_add_sample_from_input()
        assert page.samples==['MANUAL001'] and reader.owner is other
        page.close();assert reader.owner is other
    finally:reader.end_scan(other);reader.close();page.close()


def test_no_read_and_silence_both_rearm_with_a_real_stop_interval(dialog):
    import time
    page,reader=dialog
    writes=[]
    reader.port.write.side_effect=lambda data:(writes.append((data,time.monotonic())) or len(data))
    reader.feed(b'NR\r\n')
    QTest.qWait(180)
    assert [item[0] for item in writes]==[b'TOFF',b'TON']
    assert writes[1][1]-writes[0][1]>=.075
    assert page.samples==[] and reader.waiting
    writes.clear();reader.trigger_watchdog.start(10)
    QTest.qWait(180)
    assert [item[0] for item in writes]==[b'TOFF',b'TON']
    reader.feed(b'SAMPLE001\r\n');QTest.qWait(180)
    reader.feed(b'SAMPLE002\r\n');QTest.qWait(180)
    assert page.samples==['SAMPLE001','SAMPLE002']


def test_closing_between_off_and_on_cancels_queued_trigger(dialog):
    page,reader=dialog
    reader.port.write.reset_mock()
    reader.feed(b'NR\r\n');app.processEvents()
    assert reader.trigger_on_timer.isActive()
    page.reject();QTest.qWait(180)
    assert all(call.args[0]!=b'TON' for call in reader.port.write.call_args_list)
    assert not reader.trigger_watchdog.isActive() and not reader.trigger_on_timer.isActive()
