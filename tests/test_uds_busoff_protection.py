import ctypes
import threading
import time
from unittest.mock import Mock

import pytest

from core.uds_field_write import build
from devices.pcan_fd_driver import PCANFDDriver
from devices.uds_client import ISOTPTransport


def test_native_pcan_busoff_status_latches_and_blocks_reconnect():
    driver = PCANFDDriver()
    driver.bus = Mock()
    driver.bus.status.return_value = ctypes.c_uint(0x10)
    driver.is_connected = True
    driver.connect = Mock(return_value=True)
    for _ in range(3):
        assert not driver.send_can_message(0, 0x7e0, 0, 8, b'\x02\x10\x03')
    driver.bus.send.assert_not_called()
    driver.connect.assert_not_called()
    assert not driver.is_connected and 'BUS-OFF' in driver.last_error
    with pytest.raises(ConnectionError, match='BUS-OFF'):
        driver.wait_for_message(0x7e8, timeout=5)


def test_busoff_after_first_frame_stops_remaining_frames_and_preserves_context():
    driver = PCANFDDriver(fd_enabled=False)
    driver.bus = Mock()
    status = [0]
    driver.bus.status.side_effect = lambda: ctypes.c_uint(status[0])
    driver.bus.send.side_effect = lambda *a, **kw: status.__setitem__(0, 0x10)
    driver.is_connected = True
    driver.connect = Mock(return_value=True)
    link = ISOTPTransport(driver)
    with pytest.raises(RuntimeError, match='BUS-OFF'):
        link.send(b'\x2e\x1e\x5e' + b'12345678901234567890')
    assert driver.bus.send.call_count == 1
    driver.connect.assert_not_called()
    assert 'last_TX=' in driver.last_error and '10 17 2E 1E 5E' in driver.last_error


def test_receiver_failure_wakes_waiter_and_cannot_restart_on_next_send():
    driver = PCANFDDriver()
    driver.bus = Mock()
    driver.is_connected = True
    driver.connect = Mock()
    seen = []
    entered = threading.Event()
    def wait():
        entered.set()
        try:
            driver.wait_for_message(timeout=5)
        except ConnectionError as exc:
            seen.append(str(exc))
    thread = threading.Thread(target=wait)
    thread.start()
    assert entered.wait(1)
    driver._latch_bus_fault('BUS-OFF injected')
    thread.join(1)
    assert not thread.is_alive() and seen
    assert not driver.send_can_message(0, 1, 0, 1, b'\x01')
    driver.connect.assert_not_called()


def test_fd_frame_in_classic_connection_is_rejected_before_connect():
    driver = PCANFDDriver(fd_enabled=False)
    driver.connect = Mock()
    assert not driver.send_can_message(0, 0x7e0, 2, 8, b'\x02\x10\x03')
    driver.connect.assert_not_called()


@pytest.mark.parametrize('fd,brs,literal_length', [(False,False,2), (False,False,80),
    (True,False,2), (True,True,2), (True,True,80)])
def test_three_field_payload_frames_reassemble_exactly(fd, brs, literal_length):
    config = dict(tx_id=0x7e0,rx_id=0x7e8,did=0x1e5e,literal='X'*literal_length,
                  start=2,length=6,timeout=1000)
    payload, text = build(config, 'ABCDEFGHIJ')
    driver = PCANFDDriver()
    sent = []
    def send(channel, ident, kind, dlc, raw, **kw):
        assert len(raw) == driver.dlc_to_length(dlc)
        assert kind == (2 if brs else 1 if fd else 0)
        sent.append(raw)
        return True
    driver.send_can_message = send
    driver.wait_for_message = Mock(return_value={'data':b'\x30\x00\x00'})
    link = ISOTPTransport(driver, can_fd=fd, brs=brs)
    link.send(payload)
    if sent[0][0] >> 4 == 1:
        assert ((sent[0][0]&15)<<8 | sent[0][1]) == len(payload)
        recovered = sent[0][2:]
        for seq, frame in enumerate(sent[1:],1):
            assert frame[0] == 0x20 | (seq & 15)
            recovered += frame[1:]
        driver.wait_for_message.assert_called_once()
    else:
        offset = 2 if sent[0][0] == 0 else 1
        recovered = sent[0][offset:]
        assert sent[0][offset-1] == len(payload)
        driver.wait_for_message.assert_not_called()
    assert recovered[:len(payload)] == payload
    assert payload[3:].decode('ascii') == text


def test_missing_flow_control_never_sends_consecutive_frames():
    driver = PCANFDDriver()
    driver.send_can_message = Mock(return_value=True)
    driver.wait_for_message = Mock(return_value=None)
    with pytest.raises(TimeoutError, match='flow-control'):
        ISOTPTransport(driver).send(b'\x2e\x1e\x5e' + b'1'*20)
    assert driver.send_can_message.call_count == 1


def test_cancel_during_flow_control_wait_prevents_consecutive_frames():
    driver = PCANFDDriver()
    driver.send_can_message = Mock(return_value=True)
    link = ISOTPTransport(driver)
    link.cancel_event = threading.Event()
    def receive(*a, **kw):
        link.cancel_event.set()
        return {'data':b'\x30\x00\x00'}
    driver.wait_for_message = receive
    with pytest.raises(RuntimeError, match='UDS'):
        link.send(b'\x2e\x1e\x5e' + b'1'*20)
    assert driver.send_can_message.call_count == 1


def test_fd_padding_uses_configured_byte_for_entire_legal_dlc():
    driver = PCANFDDriver()
    driver.send_can_message = Mock(return_value=True)
    ISOTPTransport(driver, can_fd=True, padding=0xaa).send(b'12345678')
    args = driver.send_can_message.call_args.args
    assert args[3] == 9
    assert args[4] == b'\x00\x0812345678\xaa\xaa'


def test_worker_and_preflight_never_reconnect_faulted_adapter():
    from types import SimpleNamespace
    from core.engine import ChannelWorker, SubStep, SubStepType
    from core.uds_field_write import encode, DEVICE
    from ui.tabs.overview_tab import OverviewTab
    driver = PCANFDDriver()
    driver._latch_bus_fault('BUS-OFF injected')
    driver.connect = Mock(return_value=True)
    driver.disconnect = Mock(return_value=True)
    manager = SimpleNamespace(pcan=driver)
    config = dict(tx_id=0x7e0,rx_id=0x7e8,did=0x1e5e,literal='X',start=1,length=4,timeout=1000)
    worker = ChannelWorker(1, [], device_manager=manager)
    worker.sn_barcode = 'ABCD'
    for _ in range(3):
        ok, error = worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,
                    dict(device=DEVICE,params='WRITE64:'+encode(config))))
        assert not ok and 'BUS-OFF' in error
    page = SimpleNamespace(_prepare_cancel=threading.Event(),engine=SimpleNamespace(device_manager=manager))
    ok, error = OverviewTab._apply_recipe_hardware(page, {'can_config':dict(channel='PCAN_USBBUS1',mode='CAN-FD')})
    assert not ok and 'BUS-OFF' in error
    driver.connect.assert_not_called()
    driver.disconnect.assert_not_called()


def test_only_explicit_successful_connection_clears_fault():
    from unittest.mock import patch
    driver = PCANFDDriver(fd_enabled=False)
    driver._latch_bus_fault('BUS-OFF injected')
    class Bus:
        def recv(self, timeout):
            time.sleep(.005)
        def shutdown(self):
            pass
        def status(self):
            return ctypes.c_uint(0)
        send = Mock()
    with patch('can.Bus',return_value=Bus()):
        try:
            assert driver.connect()
            assert not driver._bus_fault
            assert driver.send_can_message(0, 1, 0, 1, b'\x01')
        finally:
            driver.disconnect()


@pytest.mark.parametrize('mode', [0, 1, 2])
@pytest.mark.parametrize('literal_length', [2, 80])
@pytest.mark.parametrize('advanced', [False, True])
@pytest.mark.parametrize('explicit_type', [False, True])
def test_three_fields_and_standard_2e_send_identical_frames(mode, literal_length, advanced, explicit_type):
    """Only the data preparation differs; exercise the actual common engine path."""
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import patch
    from core.engine import ChannelWorker, SubStep, SubStepType
    from core.uds_field_write import encode, execution_values, DEVICE

    config = dict(tx_id=0x7e0,rx_id=0x7e8,did=0x1e5e,literal='X'*literal_length,
                  start=2,length=6,timeout=1000)
    if advanced:
        config['uds_settings'] = dict(frame_format='extended',padding=0xaa,block_size=8,st_min=5,p2_star_ms=9000)
    compiled, expected_text = execution_values(config, 'ABCDEFGHIJ', datetime(2026,10,19))
    standard = ' / '.join(k+':'+v for k,v in compiled.items())

    def run(params):
        driver = PCANFDDriver(fd_enabled=True if explicit_type else mode>0)
        driver.default_can_type = 2 if explicit_type else mode
        if explicit_type:
            params += ' / TYPE:' + str(mode)
        driver.is_connected = True
        sent = []
        class Bus:
            length = 0
            data = b''
            def status(self):
                return ctypes.c_uint(0)
            def send(self, msg, timeout):
                raw = bytes(msg.data)
                sent.append((msg.arbitration_id,msg.is_fd,msg.bitrate_switch,msg.dlc,raw,msg.is_extended_id))
                def reply(data):
                    driver._publish(dict(direction='RX',can_id=0x7e8,data=data,timestamp_rel=time.time()))
                if raw[0]>>4 == 1:
                    self.length = ((raw[0]&15)<<8) | raw[1]
                    self.data = raw[2:]
                    reply(b'\x30\x00\x00')
                    return
                if raw[0]>>4 == 2:
                    self.data += raw[1:]
                    if len(self.data)<self.length:
                        return
                reply(b'\x03\x6e\x1e\x5e\x00\x00\x00\x00')
        driver.bus = Bus()
        worker = ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
        worker.sn_barcode = 'ABCDEFGHIJ'
        with patch('core.uds_field_write.datetime') as clock:
            clock.now.return_value = datetime(2026,10,19)
            result = worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,
                        dict(device=DEVICE if params.startswith('WRITE64:') else 'UDS',params=params)))
        assert result[0],result[1]
        assert all(frame[1] == (mode > 0) and frame[2] == (mode == 2) for frame in sent)
        return sent,result

    field_frames, field_result = run('WRITE64:'+encode(config))
    standard_frames, _ = run(standard)
    assert field_frames == standard_frames
    assert field_result[1] == '6E'
