import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import can
import pytest

from devices.pcan_fd_driver import PCANFDDriver
from devices.uds_client import ISOTPTransport, UDSClient


def test_reconnect_joins_old_receiver_and_discards_its_late_frame():
    entered, release = threading.Event(), threading.Event()
    class OldBus:
        def recv(self, timeout):
            entered.set()
            release.wait(2)
            return can.Message(arbitration_id=0x456, data=b'old')
        def shutdown(self):
            threading.Timer(.03, release.set).start()
    class NewBus:
        def recv(self, timeout):
            time.sleep(.01)
            return None
        def shutdown(self):
            pass
    driver = PCANFDDriver(fd_enabled=False)
    try:
        with patch('can.Bus', side_effect=[OldBus(), NewBus()]):
            assert driver.connect()
            assert entered.wait(2)
            old_thread, old_stop = driver._receive_thread, driver._stop_event
            assert driver.connect()
            assert not old_thread.is_alive()
            assert old_stop.is_set()
            assert old_stop is not driver._stop_event
            assert not any(message['can_id'] == 0x456 for message in driver._rx_history)
    finally:
        release.set()
        driver.disconnect()


def test_fast_response_before_send_returns_is_not_discarded():
    driver = PCANFDDriver()
    def send(*args):
        driver._publish({'direction': 'RX', 'timestamp_rel': time.time(), 'can_id': 0x708, 'data': b'OK'})
        time.sleep(.03)
        return True
    driver.send_can_message = send
    result = driver.send_and_wait_response(0, 0x700, 0, 1, b'1', 0x708, timeout=.05)
    assert result is not None and result['data'] == b'OK'


def test_iso_tp_consumption_preserves_order_of_buffered_consecutive_frames():
    driver = PCANFDDriver()
    driver.send_can_message = Mock(return_value=True)
    data = b'abcdefghijklmnopqrst'
    frames = [b'\x10\x14'+data[:6], b'\x21'+data[6:13], b'\x22'+data[13:20]]
    for frame in frames:
        driver._publish({'direction':'RX', 'timestamp_rel':time.time(), 'can_id':0x7E8, 'data':frame})
    link = ISOTPTransport(driver)
    link._response_since = 0
    assert link.receive() == data


def test_listener_and_queue_mutations_cannot_change_response_history():
    driver = PCANFDDriver()
    driver.on_message_received = lambda msg: msg.update(can_id=0)
    driver._publish({'direction':'RX', 'timestamp_rel':time.time(), 'can_id':0x708, 'data':b'OK'})
    driver.msg_queue.get_nowait()['can_id'] = 1
    assert driver.wait_for_message(0x708, timeout=0, since_time=0)['data'] == b'OK'


def test_connection_failure_releases_bus_created_before_receiver_start():
    driver = PCANFDDriver(fd_enabled=False)
    bus = Mock()
    with patch('can.Bus', return_value=bus), patch('threading.Thread.start', side_effect=RuntimeError('no thread')):
        assert not driver.connect()
    bus.shutdown.assert_called_once()
    assert driver.bus is None and not driver.is_connected


@pytest.mark.parametrize('dlc,data', [(-1, b''), (16, b''), (1, b'ab')])
def test_invalid_dlc_or_payload_never_sends_or_connects(dlc, data):
    driver = PCANFDDriver()
    driver.connect = Mock()
    assert not driver.send_can_message(0, 0x123, 0, dlc, data)
    driver.connect.assert_not_called()


def test_disconnect_wakes_response_waiter():
    driver = PCANFDDriver()
    result = []
    thread = threading.Thread(target=lambda: result.append(driver.wait_for_message(timeout=5)))
    thread.start()
    try:
        time.sleep(.02)
        assert driver.disconnect()
        thread.join(.5)
        assert not thread.is_alive()
        assert result == [None]
    finally:
        driver._stop_event.set()
        with driver._rx_condition:
            driver._rx_condition.notify_all()
        thread.join(1)


def transport(frames=(), send=True, can_fd=False):
    driver = PCANFDDriver()
    driver.clear_rx_history = Mock()
    driver.send_can_message = Mock(return_value=send)
    driver.wait_for_message = Mock(side_effect=[{'data': frame} for frame in frames])
    return ISOTPTransport(driver, can_fd=can_fd)


@pytest.mark.parametrize('frame', [b'\x05\x62\xf1', b'\x00', b'\x10', b'\x10\x00\x00', b'\x10\x01A'])
def test_truncated_iso_tp_frame_is_not_returned_as_complete_response(frame):
    with pytest.raises(RuntimeError):
        transport([frame]).receive()


def test_flow_control_transmission_failure_is_reported_immediately():
    link = transport([b'\x10\x09abcdef'], send=False)
    with pytest.raises(RuntimeError, match='flow-control send failed'):
        link.receive()
    assert link.driver.wait_for_message.call_count == 1


def test_later_block_wait_frame_and_new_flow_control_deadline_are_supported():
    link = transport([b'\x30\x01\x7f', b'\x31\x00\x00', b'\x30\x00\x00'])
    link.timeout = .02
    # The first block takes longer than the wait budget, but the subsequent
    # flow-control wait must get its own budget rather than an expired deadline.
    original_sleep = time.sleep
    with patch('devices.uds_client.time.sleep', side_effect=lambda _: original_sleep(.03)):
        link.send(bytes(range(25)))
    assert link.driver.wait_for_message.call_count == 3


@pytest.mark.parametrize('stmin', [0x80, 0xF0, 0xFA, 0xFF])
def test_reserved_separation_time_is_not_treated_as_zero_delay(stmin):
    with pytest.raises(ValueError, match='Reserved'):
        ISOTPTransport._stmin_seconds(stmin)


@pytest.mark.parametrize('sid,data,reply', [
    (0x22, b'\xf1\x90', b'\x62\xf1\x91bad'),
    (0x10, b'\x03', b'\x50\x01'),
    (0x31, b'\x01\x12\x34', b'\x71\x01\x12\x35'),
    (0x36, b'\x02data', b'\x76\x01'),
    (0x22, b'\xf1\x90', b'\x7f\x10\x78'),
    (0x22, b'\xf1\x90', b'\x7f\x22'),
])
def test_wrong_operation_echo_or_negative_service_never_passes(sid, data, reply):
    link = SimpleNamespace(send=Mock(), receive=Mock(return_value=reply))
    result = UDSClient(link).request(sid, data)
    assert not result.positive and result.error


def test_valid_iso_tp_and_uds_response_still_work():
    link = transport([b'\x06\x62\xf1\x90ABC'])
    result = UDSClient(link).request(0x22, b'\xf1\x90')
    assert result.positive and result.data == b'\xf1\x90ABC'


def test_shared_driver_serializes_entire_uds_transactions():
    driver = PCANFDDriver()
    first = ISOTPTransport(driver)
    second = ISOTPTransport(driver)
    entered, release, second_sent = threading.Event(), threading.Event(), threading.Event()
    first.send = Mock()
    first.receive = lambda timeout: (entered.set(), release.wait(2), b'\x50\x01')[-1]
    second.send = lambda payload: second_sent.set()
    second.receive = lambda timeout: b'\x50\x03'
    results = []
    one = threading.Thread(target=lambda: results.append(UDSClient(first).request(0x10, b'\x01')))
    two = threading.Thread(target=lambda: results.append(UDSClient(second).request(0x10, b'\x03')))
    one.start()
    try:
        assert entered.wait(1)
        two.start()
        assert not second_sent.wait(.08)
    finally:
        release.set()
        one.join(2)
        if two.ident is not None:
            two.join(2)
    assert len(results) == 2 and all(result.positive for result in results)
