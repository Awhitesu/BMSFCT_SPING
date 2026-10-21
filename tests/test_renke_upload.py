import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from devices.renke_upload import RenkeP2Decoder, RenkeUploadSensor
from ui.tabs.environment_tab import EnvironmentTab
from devices.manager import DeviceManager
from core.hardware_status import configuration_snapshot


# Golden frames captured from 192.168.1.100, validated against the vendor SDK.
LOGIN = bytes.fromhex('68 88 88 88 88 88 88 68 a0 0a 33 1d 0c 1c 99 0c 84 ab 44 43 7d 16')
LIVE = bytes.fromhex('68 88 88 88 88 88 88 68 a0 1b 36 1d 34 15 33 57 35 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 33 84 ab 44 43 fc 16')
LOGIN_ACK = bytes.fromhex('68 88 88 88 88 88 88 68 20 06 33 1d 84 ab 44 43 2c 16')
LIVE_ACK = bytes.fromhex('68 88 88 88 88 88 88 68 20 06 36 1d 84 ab 44 43 2f 16')


def corrected(data):
    result = bytearray(data)
    result[-2] = sum(result[:-2]) & 255
    return bytes(result)


def test_captured_frames_and_ack_vectors():
    decoder = RenkeP2Decoder()
    assert decoder.feed(LOGIN[:3]) == []
    assert decoder.feed(LOGIN[3:] + LIVE + LIVE[:8]) == [LOGIN, LIVE]
    assert decoder.feed(LIVE[8:]) == [LIVE]
    assert decoder.terminal(LIVE) == '10117851'
    assert decoder.acknowledgement(LOGIN) == LOGIN_ACK
    assert decoder.acknowledgement(LIVE) == LIVE_ACK
    assert decoder.reading(LOGIN, 1) is None
    reading = decoder.reading(LIVE, 1)
    assert (reading.temperature, reading.humidity) == (22.6, 54.8)
    assert reading.node == 1 and reading.terminal_id == '10117851'


def test_upload_negative_temperature_is_sign_magnitude_not_twos_complement():
    frame = bytearray(LIVE)
    frame[13:15] = bytes([(123 & 255) + 0x33, 0x80 + 0x33])
    assert RenkeP2Decoder.reading(corrected(frame), 1).temperature == -12.3


@pytest.mark.parametrize('bad', [b'garbage1234', LIVE[:-2]+b'\x00\x16',
                                LIVE[:-1]+b'\x00', LIVE[:9]+b'\xff'+LIVE[10:]])
def test_corrupt_upload_rejected(bad):
    with pytest.raises(ValueError):
        RenkeP2Decoder().feed(bad)


def test_wrong_node_and_humidity_are_not_silently_used():
    with pytest.raises(ValueError):
        RenkeP2Decoder.reading(LIVE, 2)
    bad = bytearray(LIVE)
    bad[15:17] = b'\x32\x32'  # decoded FFFF
    with pytest.raises(ValueError):
        RenkeP2Decoder.reading(corrected(bad), 1)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        time.sleep(.005)
    assert predicate()


def receive(conn, size=18):
    result = b''
    while len(result) < size:
        part = conn.recv(size - len(result))
        assert part
        result += part
    return result


def test_listener_login_live_stale_heartbeat_and_reconnect():
    sensor = RenkeUploadSensor('127.0.0.1', free_port(), 1)
    try:
        assert sensor.connect() and sensor.is_listening
        assert not sensor.is_connected
        with socket.create_connection(('127.0.0.1', sensor.port), 1) as conn:
            conn.settimeout(1)
            conn.sendall(LOGIN)
            assert receive(conn) == LOGIN_ACK
            assert not sensor.is_connected  # Login is not a measurement.
            conn.sendall(LIVE[:10])
            conn.sendall(LIVE[10:])
            assert receive(conn) == LIVE_ACK
            wait_for(lambda: sensor.is_connected)
            assert sensor.read_environment().temperature == 22.6
            sensor._received_at -= 20
            assert not sensor.is_connected and sensor.last_reading is None
            heartbeat = bytearray(LOGIN)
            heartbeat[10] = 0x35
            conn.sendall(corrected(heartbeat))
            receive(conn)
            assert not sensor.is_connected  # Heartbeats must not refresh stale readings.
            conn.sendall(LIVE)
            assert receive(conn) == LIVE_ACK
            wait_for(lambda: sensor.is_connected)
        wait_for(lambda: not sensor.peer)
        assert not sensor.is_connected
        with socket.create_connection(('127.0.0.1', sensor.port), 1) as conn:
            conn.sendall(LOGIN + LIVE)
            assert receive(conn, 36) == LOGIN_ACK + LIVE_ACK
            wait_for(lambda: sensor.is_connected)
    finally:
        sensor.disconnect()
    assert not sensor.is_listening and not sensor.is_connected and sensor._thread is None


def test_listener_rejects_wrong_peer_and_port_in_use():
    sensor = RenkeUploadSensor('192.0.2.1', free_port(), 1)
    try:
        assert sensor.connect()
        with socket.create_connection(('127.0.0.1', sensor.port), 1) as conn:
            conn.settimeout(1)
            assert conn.recv(1) == b''
        assert not sensor.is_connected and sensor.received_frames == 0
        other = RenkeUploadSensor('192.0.2.1', sensor.port, 1)
        assert not other.connect() and not other.is_listening
        assert other.last_error
    finally:
        sensor.disconnect()


def test_bad_checksum_drops_connection_and_cached_data():
    sensor = RenkeUploadSensor('127.0.0.1', free_port(), 1)
    try:
        assert sensor.connect()
        with socket.create_connection(('127.0.0.1', sensor.port), 1) as conn:
            conn.settimeout(1)
            conn.sendall(LIVE)
            receive(conn)
            wait_for(lambda: sensor.is_connected)
            conn.sendall(LIVE[:-2]+b'\x00\x16')
            assert conn.recv(1) == b''
            wait_for(lambda: not sensor.is_connected)
            assert sensor.last_reading is None
    finally:
        sensor.disconnect()


def test_manager_upload_mode_reports_waiting_and_releases_listener_on_reload():
    port = free_port()
    db = Mock()
    db.load_sys_config.return_value = dict(rs_ws_eth6_enabled=True, rs_ws_eth6_mode='upload',
                                          rs_ws_eth6_ip='127.0.0.1', rs_ws_eth6_port=port)
    manager = DeviceManager(db)
    sensor = manager.rs_ws_eth6
    try:
        assert isinstance(sensor, RenkeUploadSensor)
        assert sensor.connect()
        state = next(s for s in manager.get_all_device_status() if s['key'] == 'rs_ws_eth6')
        assert '等待上传' in state['status'] and state['connection_verified'] is False
        assert configuration_snapshot(manager)['rs_ws_eth6']['mode'] == 'upload'
        manager.update_config()
        assert not sensor.is_listening
        assert manager.rs_ws_eth6.connect()
    finally:
        manager.rs_ws_eth6.disconnect()


app = QApplication.instance() or QApplication([])


def test_upload_page_waits_without_stopping_listener_and_shows_timestamp():
    sensor = RenkeUploadSensor('127.0.0.1', free_port(), 1)
    manager = SimpleNamespace(rs_ws_eth6=sensor, is_enabled=lambda _: True)
    page = EnvironmentTab(manager)
    page.timer.stop()
    try:
        assert sensor.connect()
        page.show()
        page.continuous.setChecked(True)
        # A manual refresh with no data is allowed to fail, not stop reception.
        end = time.monotonic() + 2
        while page.task.isRunning() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.002)
        page.poll()
        assert sensor.is_listening
        assert '等待上传' in page.status.text()
        with socket.create_connection(('127.0.0.1', sensor.port), 1) as conn:
            conn.sendall(LIVE)
            receive(conn)
            wait_for(lambda: sensor.is_connected)
            page.continuous.setChecked(True)
            page.poll()
            assert page.temperature.text() == '22.6 ℃'
            assert '10117851' in page.details.text()
            sensor._received_at -= 20
            page.poll()
            assert page.temperature.text() == '— ℃'
            assert '超时' in page.status.text()
    finally:
        page.shutdown()
        sensor.disconnect()
        page.close()
