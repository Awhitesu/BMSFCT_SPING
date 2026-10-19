import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import queue
import time
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication, QTableWidget, QLabel
from devices.pcan_fd_driver import PCANFDDriver
from ui.tabs.pcan_fd_tab import PCANFDTab

app=QApplication.instance() or QApplication([])


def test_overflow_keeps_newest_without_losing_diagnostic_listener():
    driver=PCANFDDriver()
    driver.msg_queue=queue.Queue(maxsize=2)
    observed=[]
    driver.add_message_listener(lambda msg:observed.append(msg['can_id']))
    for i in range(4):driver._publish(dict(can_id=i,data=b'\x01',direction='RX'))
    assert [driver.msg_queue.get_nowait()['can_id'] for _ in range(2)]==[2,3]
    assert observed==[0,1,2,3]
    assert len(driver._rx_history)==4 and driver.monitor_dropped==2


def test_monitor_catches_up_to_latest_in_one_refresh_and_bounds_rows():
    driver=PCANFDDriver()
    now=time.time()
    for i in range(10000):
        driver.msg_queue.put_nowait(dict(can_id=i,data=b'\x01',timestamp_rel=now))
    table=QTableWidget(2000,8)
    page=SimpleNamespace(driver=driver,isVisible=lambda:True,status=QLabel(),monitor=table,
                         lbl_msg_count=QLabel(),_monitor_skipped=0,
                         _monitor_message_matches=lambda msg:True)
    page._append_monitor_frames=lambda pending:PCANFDTab._append_monitor_frames(page,pending)
    try:
        PCANFDTab.update_monitor(page)
        assert driver.msg_queue.empty()
        assert table.rowCount()==2000
        assert table.item(1999,4).text()==hex(9999).upper().replace('X','x')
        assert page._monitor_skipped==9500
        assert table.updatesEnabled()
        # Repeated refreshes preserve the bound and remain at the current tail.
        for i in range(20):
            driver.msg_queue.put_nowait(dict(can_id=i,data=b'',timestamp_rel=now))
        PCANFDTab.update_monitor(page)
        assert table.rowCount()==2000 and table.item(1999,4).text()=='0x13'
    finally:table.close()
