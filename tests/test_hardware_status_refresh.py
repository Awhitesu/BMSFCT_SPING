import threading
import time
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.hardware_status import configuration_matches, configuration_snapshot
from devices.manager import DeviceManager
from ui.tabs.hardware_config_tab import HardwareConfigTab


app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline=time.monotonic()+4
    while not predicate() and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


@pytest.fixture
def hardware():
    database=Mock()
    database.load_sys_config.return_value={'hv_ip':'192.0.2.1','hv_port':7000,'ca550_com':'COM1'}
    database.save_sys_config.return_value=True
    manager=DeviceManager(database)
    page=HardwareConfigTab(database)
    page.status_timer.stop()
    yield page,manager,database
    page.shutdown()
    page.close()


def row(page,key):
    return next(i for i,spec in enumerate(page.DEVICE_SPECS) if spec[0]==key)


def status(page,key):
    return page.table.item(row(page,key),6).text()


def test_slow_status_read_is_background_and_refreshes_do_not_accumulate(hardware):
    page,manager,_=hardware
    entered,release=threading.Event(),threading.Event()
    original=manager.get_all_device_status
    threads=[]
    def read():
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(4)
        return original()
    manager.get_all_device_status=read
    try:
        page.set_device_manager(manager)
        assert entered.wait(2)
        for _ in range(10):page.refresh_hardware_status()
        # A queued GUI callback must execute while the reader is still blocked.
        from PySide6.QtCore import QTimer
        ticks=[]
        QTimer.singleShot(0,lambda:ticks.append(True))
        pump(lambda:bool(ticks))
        assert threads==[threads[0]] and threads[0]!=threading.get_ident()
        release.set()
        pump(lambda:not page.status_task.isRunning())
        assert status(page,'hv')=='离线'
    finally:
        release.set()


def test_edited_parameters_never_claim_old_connection_as_applied(hardware):
    page,manager,_=hardware
    manager.hv_source.is_connected=True
    page.set_device_manager(manager)
    pump(lambda:not page.status_task.isRunning())
    assert status(page,'hv')=='已联机'
    item=page.table.item(row(page,'hv'),3)
    item.setText('192.0.2.2')
    assert status(page,'hv')=='修改未应用'
    item.setText('192.0.2.1')
    assert status(page,'hv')=='已联机'
    from PySide6.QtCore import Qt
    page.table.item(row(page,'hv'),0).setCheckState(Qt.Unchecked)
    assert status(page,'hv')=='停用未应用'
    manager.hv_source.is_connected=False


def test_manual_serial_text_survives_interface_scan_and_save(hardware):
    page,_,database=hardware
    combo=page.table.cellWidget(row(page,'ca550'),3)
    combo.setEditText('COM42')
    page._apply_interface_lists([('COM1','adapter')],['PCAN_USBBUS1'])
    assert combo.currentText()=='COM42'
    with patch.object(QMessageBox,'information'):
        page.save_config()
    assert database.save_sys_config.call_args.args[0]['ca550_com']=='COM42'


def test_failed_refresh_is_explicit_and_recovers(hardware):
    page,manager,_=hardware
    original=manager.get_all_device_status
    manager.get_all_device_status=Mock(side_effect=OSError('configuration unavailable'))
    page.set_device_manager(manager)
    pump(lambda:not page.status_task.isRunning())
    assert status(page,'hv')=='状态读取失败'
    assert 'configuration unavailable' in page.table.item(row(page,'hv'),6).toolTip()
    manager.get_all_device_status=original
    page.refresh_hardware_status()
    pump(lambda:not page.status_task.isRunning())
    assert status(page,'hv')=='离线'


def test_old_read_cannot_overwrite_new_manager(hardware):
    page,manager,database=hardware
    release,entered=threading.Event(),threading.Event()
    original=manager.get_all_device_status
    def old_read():
        entered.set()
        assert release.wait(4)
        return original()
    manager.get_all_device_status=old_read
    try:
        page.set_device_manager(manager)
        assert entered.wait(2)
        replacement=DeviceManager(database)
        replacement.hv_source.is_connected=True
        page.set_device_manager(replacement)
        release.set()
        pump(lambda:status(page,'hv')=='已联机')
        replacement.hv_source.is_connected=False
    finally:
        release.set()


def test_each_configurable_device_exposes_actual_parameters(hardware):
    page,manager,_=hardware
    applied=configuration_snapshot(manager)
    assert set(applied)=={spec[0] for spec in page.DEVICE_SPECS}
    for key,actual in applied.items():
        draft=page._row_configuration(row(page,key))
        # Optional rows can be disabled in the editor; parameter comparison is
        # still exercised for every driver, including simulator mapping.
        if 'enabled' in actual:draft['enabled']=actual['enabled']
        assert configuration_matches(key,draft,actual),key
        for field in actual:
            changed=dict(draft)
            changed[field]=not actual[field] if field=='enabled' else 'different'
            assert not configuration_matches(key,changed,actual),(key,field)


def test_status_before_apply_is_discarded_after_operation(hardware):
    page,manager,_=hardware
    manager.hv_source.is_connected=True
    release,entered=threading.Event(),threading.Event()
    original=manager.get_all_device_status
    reads=[]
    def read():
        captured=original()
        reads.append(True)
        if len(reads)==1:
            entered.set()
            assert release.wait(4)
        return captured
    manager.get_all_device_status=read
    try:
        page.set_device_manager(manager)
        assert entered.wait(2)
        def apply():
            manager.hv_source.is_connected=False
            manager.hv_source.ip='192.0.2.2'
        page.table.item(row(page,'hv'),3).setText('192.0.2.2')
        page.run_operation('applying',apply)
        pump(lambda:page.table.isEnabled())
        release.set()
        pump(lambda:len(reads)>=2 and not page.status_task.isRunning())
        assert status(page,'hv')=='离线'
        assert page.table.item(row(page,'hv'),3).text()=='192.0.2.2'
    finally:
        release.set()
