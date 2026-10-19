import queue
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from devices.infoscan_scanner import InfoscanScanner
from devices.manager import DeviceManager
from devices.rn_board import board_settings
from core.hardware_status import configuration_snapshot
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.widgets.serial_scanner import SerialScanner,ScannerPanel,ScannerDebugTab

app=QApplication.instance() or QApplication([])


class FakeSerial:
    def __init__(self,**kwargs):
        self.is_open=True;self.settings=kwargs;self.writes=[];self.input=queue.Queue()
    @property
    def in_waiting(self):return 0
    def read(self,size):
        try:return self.input.get(timeout=.01)
        except queue.Empty:return b''
    def write(self,data):self.writes.append(data);return len(data)
    def close(self):self.is_open=False
    def reset_input_buffer(self):
        while not self.input.empty():self.input.get_nowait()


def test_uniform_initialization_only_opens_serial_and_disconnects(monkeypatch):
    ports=[]
    def create(**kwargs):
        port=FakeSerial(**kwargs);ports.append(port);return port
    monkeypatch.setattr('devices.infoscan_scanner.serial.Serial',create)
    cfg=dict(scanner_enabled=True,scanner_com='COM5',scanner_baudrate=115200,
             rn_boards={s['key']:dict(enabled=False) for s in board_settings({})})
    db=Mock();db.load_sys_config.return_value=cfg
    manager=DeviceManager(db)
    manager.enabled_devices={key:False for key in manager.enabled_devices}
    manager.enabled_devices['scanner']=True
    try:
        assert manager.init_all_devices()
        assert len(ports)==1 and not ports[0].writes
        assert ports[0].settings['port']=='COM5'
        row=configuration_snapshot(manager)['scanner']
        assert row==dict(address='COM5',baud=115200,enabled=True)
        states=manager.get_all_device_status()
        assert states[0]['key']=='scanner' and states[0]['status']=='串口已连接'
        assert manager.disconnect_all() and not ports[0].is_open
        assert ports[0].writes==[]
    finally:manager.scanner.disconnect()


def test_managed_reader_uses_shared_driver_and_only_scan_sends_trigger(monkeypatch):
    monkeypatch.setattr('devices.infoscan_scanner.serial.Serial',FakeSerial)
    driver=InfoscanScanner(dict(scanner_enabled=True,scanner_com='COM5'))
    assert driver.connect()
    reader=SerialScanner(driver_provider=lambda:driver)
    values=[];reader.barcode.connect(values.append)
    try:
        assert reader.port.isOpen() and not driver._serial.writes
        assert reader.begin_scan(reader)
        assert driver._serial.writes==[b'TON']
        driver.received.put(b'ACTUAL_SN\r\n');reader._drain_received()
        assert values==['ACTUAL_SN']
        reader.end_scan(reader);assert driver._serial.writes==[b'TON',b'TOFF']
        reader.close()
        assert driver.is_connected # Final serial teardown belongs to DeviceManager.
    finally:reader.close();driver.disconnect()


def test_hardware_row_is_standard_and_detail_settings_live_inside_debug():
    db=Mock();db.load_sys_config.return_value={}
    hardware=HardwareConfigTab(db);hardware.status_timer.stop()
    driver=InfoscanScanner({})
    reader=SerialScanner(driver_provider=lambda:driver)
    panel=ScannerPanel(db,reader=reader);debug=ScannerDebugTab(panel)
    try:
        row=next(i for i,spec in enumerate(hardware.DEVICE_SPECS) if spec[0]=='scanner')
        assert hardware.table.cellWidget(row,3).currentText()=='COM5'
        assert hardware.table.item(row,5).text()=='115200'
        assert hardware.table.item(row,0).checkState()==Qt.Unchecked
        assert panel.parent() is debug
        assert not hardware.findChildren(ScannerPanel)
        assert not reader.connection_task.isRunning() and not driver.is_connected
        db.load_sys_config.return_value=dict(scanner_com='COM7',scanner_baudrate=9600)
        debug.show();app.processEvents()
        assert panel.com.text()=='COM7' and panel.baud.currentText()=='9600'
    finally:
        reader.close();driver.disconnect();debug.close();hardware.shutdown();hardware.close()
