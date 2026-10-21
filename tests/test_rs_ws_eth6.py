"""Wire-level sensor simulation; no physical instruments are contacted."""
from contextlib import contextmanager
from datetime import datetime
import socket
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from core.hardware_status import configuration_matches, configuration_snapshot
from devices.manager import DeviceManager
from devices.rs_ws_eth6 import RSWSETH6, EnvironmentReading
from ui.tabs.environment_tab import EnvironmentTab
from ui.tabs.hardware_config_tab import HardwareConfigTab


@contextmanager
def sensor_server(transform=None, registers=(501, 253), fragment=False, count=2):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(3)
    requests, errors = [], []
    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(2)
                for _ in range(count):
                    raw = b''
                    while len(raw) < 12:
                        chunk = conn.recv(12-len(raw))
                        if not chunk:
                            return
                        raw += chunk
                    requests.append(raw)
                    tid, proto, length, unit, fc, start, size = struct.unpack('>HHHBBHH', raw)
                    assert (proto, length, fc, start, size) == (0, 6, 3, 0, 2)
                    response = struct.pack('>HHHBBBHH', tid, 0, 7, unit, 3, 4, *registers)
                    if transform:
                        response = transform(response)
                    if fragment:
                        for part in (response[:2], response[2:7], response[7:9], response[9:]):
                            conn.sendall(part)
                            time.sleep(.002)
                    else:
                        conn.sendall(response)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield listener.getsockname()[1], requests
    finally:
        thread.join(4)
        listener.close()
        assert not thread.is_alive()
        assert not errors, errors


def test_fresh_pair_and_tcp_fragmentation():
    with sensor_server(fragment=True) as (port, requests):
        driver = RSWSETH6('127.0.0.1', port, 7)
        try:
            assert driver.connect()
            initial = driver.last_reading
            value = driver.read_environment()
            assert value.temperature == 25.3 and value.humidity == 50.1
            assert value.captured_at >= initial.captured_at and value is not initial
            assert value.elapsed_ms > 0 and value.registers == (501, 253)
            assert driver.is_connected and driver.last_error == ''
            assert all(r[6] == 7 for r in requests)
        finally:
            driver.disconnect()
        assert driver.last_reading is None and not driver.is_connected


def test_negative_temperature_signed_word():
    with sensor_server(registers=(1000, 0xff85), count=1) as (port, _):
        driver = RSWSETH6('127.0.0.1', port)
        try:
            assert driver.connect()
            assert driver.last_reading.temperature == -12.3
            assert driver.last_reading.humidity == 100
        finally:
            driver.disconnect()


@pytest.mark.parametrize('change', [
    lambda r: b'\xff\xff'+r[2:],
    lambda r: r[:2]+b'\x00\x01'+r[4:],
    lambda r: r[:6]+b'\x02'+r[7:],
    lambda r: r[:4]+b'\x00\xff'+r[6:],
    lambda r: r[:7]+b'\x04'+r[8:],
    lambda r: r[:8]+b'\x02'+r[9:],
    lambda r: r[:9]+b'\xff\xff'+r[11:],
    lambda r: r[:4]+b'\x00\x03'+r[6:7]+b'\x83\x02',
])
def test_invalid_response_never_claims_connection(change):
    with sensor_server(transform=change, count=1) as (port, _):
        driver = RSWSETH6('127.0.0.1', port)
        assert not driver.connect()
        assert not driver.is_connected and driver.last_reading is None
        assert driver.last_error and driver._socket is None


def test_failed_read_clears_prior_value():
    # Peer closes after handshake; a subsequent read cannot return the handshake cache.
    with sensor_server(count=1) as (port, _):
        driver = RSWSETH6('127.0.0.1', port)
        assert driver.connect() and driver.last_reading is not None
        with pytest.raises((ConnectionError, OSError)):
            driver.read_environment()
        assert not driver.is_connected and driver.last_reading is None


@pytest.mark.parametrize('ip,port,unit', [('invalid',502,1), ('127.0.0.1',0,1),
                                        ('127.0.0.1',502,0), ('127.0.0.1',502,256)])
def test_bad_configuration_does_not_open_socket(ip, port, unit):
    driver = RSWSETH6(ip, port, unit)
    with patch('devices.rs_ws_eth6.socket.create_connection') as connect:
        assert not driver.connect()
        connect.assert_not_called()


def database(**config):
    db = Mock()
    db.load_sys_config.return_value = config
    db.save_sys_config.return_value = True
    return db


def test_optional_device_and_manager_lifecycle():
    db = database(rs_ws_eth6_ip='192.0.2.1', rs_ws_eth6_port=2404, rs_ws_eth6_unit=1)
    manager = DeviceManager(db)
    assert not manager.is_enabled('rs_ws_eth6')
    manager.enabled_devices = {k: False for k in manager.enabled_devices}
    with patch.object(manager.rs_ws_eth6, 'connect', return_value=True) as connect:
        assert manager.init_all_devices()
        connect.assert_not_called()
        manager.enabled_devices['rs_ws_eth6'] = True
        assert manager.init_all_devices()
        connect.assert_called_once()
    manager.rs_ws_eth6.is_connected = True
    with patch.object(manager.rs_ws_eth6, 'read_environment') as read:
        status = next(s for s in manager.get_all_device_status() if s['key'] == 'rs_ws_eth6')
        assert status['status'] == '已联机'
        assert '2404' in status['info']
        read.assert_not_called()
    snap = configuration_snapshot(manager)['rs_ws_eth6']
    assert snap == dict(address='192.0.2.1', port=2404, baud=1, enabled=True, mode='modbus')
    assert configuration_matches('rs_ws_eth6', dict(snap), snap)
    with patch.object(manager.rs_ws_eth6, 'disconnect') as disconnect:
        manager.disconnect_all()
        disconnect.assert_called_once()


app = QApplication.instance() or QApplication([])


def row(page):
    return next(i for i,spec in enumerate(page.DEVICE_SPECS) if spec[0] == 'rs_ws_eth6')


def test_hardware_row_optional_and_save_preserves_other_settings():
    db = database(custom_setting='keep')
    page = HardwareConfigTab(db)
    page.status_timer.stop()
    try:
        r = row(page)
        assert page.table.item(r,0).checkState() == Qt.Unchecked
        page.table.item(r,0).setCheckState(Qt.Checked)
        page.table.item(r,3).setText('192.168.1.100')
        page.table.item(r,4).setText('2404')
        page.table.item(r,5).setText('1')
        with patch.object(QMessageBox, 'information'):
            page.save_config()
        saved = db.save_sys_config.call_args.args[0]
        assert saved['rs_ws_eth6_enabled'] is True
        assert saved['rs_ws_eth6_ip'] == '192.168.1.100'
        assert saved['rs_ws_eth6_port'] == 2404 and saved['rs_ws_eth6_unit'] == 1
        assert saved['custom_setting'] == 'keep'
        db.save_sys_config.reset_mock()
        page.table.item(r,5).setText('0')
        with patch.object(QMessageBox, 'warning') as warning:
            page.save_config()
            warning.assert_called_once()
        db.save_sys_config.assert_not_called()
    finally:
        page.shutdown()
        page.close()


def pump(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


def test_debug_read_is_nonblocking_and_does_not_queue_or_show_failed_cache():
    driver = RSWSETH6('192.0.2.1')
    driver.is_connected = True
    manager = SimpleNamespace(rs_ws_eth6=driver, is_enabled=lambda _: True)
    page = EnvironmentTab(manager)
    page.timer.stop()
    release, entered = threading.Event(), threading.Event()
    reading = EnvironmentReading(25.3, 50.1, datetime.now(), 4.2, (501,253))
    def slow():
        entered.set()
        assert release.wait(2)
        return reading
    driver.read_environment = Mock(side_effect=slow)
    try:
        page.read_sensor()
        assert entered.wait(1)
        page.read_sensor()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        pump(lambda: bool(ticks))
        assert driver.read_environment.call_count == 1
        release.set()
        pump(lambda: not page.task.isRunning())
        assert page.temperature.text() == '25.3 ℃'
        assert page.humidity.text() == '50.1 %RH'
        assert '4.2 ms' in page.details.text()
        driver.read_environment.side_effect = TimeoutError('read timed out')
        page.read_sensor()
        pump(lambda: not page.task.isRunning())
        assert page.temperature.text() == '— ℃'
        assert not page.continuous.isChecked()
        assert 'timed out' in page.log.toPlainText()
    finally:
        release.set()
        page.shutdown()
        page.close()


def test_replaced_driver_cannot_display_old_reading():
    driver = RSWSETH6('192.0.2.1')
    driver.is_connected = True
    manager = SimpleNamespace(rs_ws_eth6=driver, is_enabled=lambda _: True)
    page = EnvironmentTab(manager)
    page.timer.stop()
    gate = threading.Event()
    driver.read_environment = lambda: (gate.wait(2) and EnvironmentReading(20,40,datetime.now(),1,(400,200)))
    try:
        page.read_sensor()
        manager.rs_ws_eth6 = RSWSETH6('192.0.2.2',2404,3)
        gate.set()
        pump(lambda: not page.task.isRunning())
        assert page.temperature.text() == '— ℃'
        assert '192.0.2.2' in page.address.text() and '2404' in page.address.text()
    finally:
        gate.set()
        page.shutdown()
        page.close()
