from unittest.mock import Mock
from PySide6.QtWidgets import QApplication,QInputDialog,QWidget
from PySide6.QtCore import QTimer
from ui.widgets.serial_scanner import SerialScanner,ScannerPanel,ScannerDebugTab
from ui.tabs.overview_tab import OverviewTab

app=QApplication.instance() or QApplication([])


def test_fragmented_crlf_accepts_once_and_discards_following_scan():
    reader=SerialScanner();values=[];reader.barcode.connect(values.append)
    reader.begin_scan();reader.feed(b'SCUx03');reader.feed(b'123\r\nOTHER\r\n')
    assert values==['SCUx03123']
    reader.feed(b'STALE\r\n');assert values==['SCUx03123']
    reader.close()


def test_raw_without_suffix_and_invalid_frames():
    reader=SerialScanner();values=[];reader.barcode.connect(values.append)
    reader.begin_scan();reader.feed(b'ABC123');reader._finish();assert values==['ABC123']
    for frame in [b'\x06ACK\r',b'\xff\r',b'NR\r',b'A'*4097+b'\r']:
        reader.end_scan();reader.begin_scan();reader.feed(frame)
    assert values==['ABC123'];reader.close()


def test_delimiter_timeout_never_submits_truncated_data():
    reader=SerialScanner();reader.config={'scanner_framing':'delimiter'};values=[];reader.barcode.connect(values.append)
    reader.begin_scan();reader.feed(b'PARTIAL');reader.timer.start(1)
    loop=__import__('PySide6.QtCore',fromlist=['QEventLoop']).QEventLoop();QTimer.singleShot(20,loop.quit);loop.exec()
    assert not values and not reader.buffer
    reader.feed(b'COMPLETE\r');assert values==['COMPLETE'];reader.close()


def test_sessions_cannot_stop_or_consume_other_owner():
    reader=SerialScanner();one=object();two=object()
    assert reader.begin_scan(one)
    assert not reader.begin_scan(two)
    reader.end_scan(two);assert reader.waiting
    reader.end_scan(one);assert not reader.waiting;reader.close()


def test_settings_save_and_debug_share_reader_without_production(tmp_path):
    db=Mock();db.load_sys_config.return_value={};db.save_sys_config.return_value=True
    panel=ScannerPanel(db);app.processEvents()
    assert panel.com.text()=='COM5' and not panel.reader.port.isOpen()
    panel.reader.configure=Mock();panel.apply()
    config=db.save_sys_config.call_args.args[0]
    assert config['scanner_baudrate']==115200 and config['scanner_com']=='COM5'
    debug=ScannerDebugTab(panel)
    debug.active=True;panel.reader.barcode.emit('SAMPLE')
    assert debug.result.text()=='SAMPLE' and debug.reader is panel.reader
    panel.can_edit=lambda:False;panel.reader.configure.reset_mock();panel.apply()
    panel.reader.configure.assert_not_called();debug.close();panel.close()


def test_production_prompt_uses_scanner_then_existing_start(monkeypatch):
    page=QWidget();page._closing=False;page._scan_pending=False;page._run_busy=lambda:False
    page.serial_scanner=SerialScanner(page);page.serial_scanner.config={'scanner_enabled':True}
    monkeypatch.setattr(page.serial_scanner.port,'isOpen',lambda:True)
    monkeypatch.setattr(page.serial_scanner.port,'clear',lambda *args:True)
    monkeypatch.setattr(page.serial_scanner.port,'write',lambda *args:3)
    page.txt_sn=Mock();page.start_single_channel_test=Mock(return_value=True)
    QTimer.singleShot(20,lambda:page.serial_scanner.feed(b'SN123\r\n'))
    assert OverviewTab.request_scan_and_start(page)
    assert page._confirmed_barcode=='SN123' and not page._scan_pending
    page.start_single_channel_test.assert_called_once()
    assert not page.serial_scanner.waiting;page.serial_scanner.close();page.close()
