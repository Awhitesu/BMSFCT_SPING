import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


NRC_NAMES = {
    0x10: "General Reject", 0x11: "Service Not Supported",
    0x12: "SubFunction Not Supported", 0x13: "Incorrect Message Length/Format",
    0x14: "Response Too Long", 0x21: "Busy Repeat Request",
    0x22: "Conditions Not Correct", 0x24: "Request Sequence Error",
    0x25: "No Response From Subnet Component", 0x26: "Failure Prevents Execution",
    0x31: "Request Out Of Range", 0x33: "Security Access Denied",
    0x35: "Invalid Key", 0x36: "Exceeded Number Of Attempts",
    0x37: "Required Time Delay Not Expired", 0x70: "Upload/Download Not Accepted",
    0x71: "Transfer Data Suspended", 0x72: "General Programming Failure",
    0x73: "Wrong Block Sequence Counter", 0x78: "Response Pending",
    0x7E: "SubFunction Not Supported In Active Session",
    0x7F: "Service Not Supported In Active Session",
    0x81: "RPM Too High", 0x82: "RPM Too Low", 0x83: "Engine Is Running",
    0x84: "Engine Is Not Running", 0x85: "Engine Run Time Too Low",
    0x86: "Temperature Too High", 0x87: "Temperature Too Low",
    0x88: "Vehicle Speed Too High", 0x89: "Vehicle Speed Too Low",
    0x8A: "Throttle Too High", 0x8B: "Throttle Too Low",
    0x8C: "Transmission Range Not In Neutral", 0x8D: "Transmission Range Not In Gear",
    0x8F: "Brake Switch Not Closed", 0x90: "Shifter Lever Not In Park",
    0x91: "Torque Converter Clutch Locked", 0x92: "Voltage Too High",
    0x93: "Voltage Too Low",
}


@dataclass
class UDSResponse:
    request_sid: int
    raw: bytes
    positive: bool
    service_id: int = 0
    data: bytes = b""
    nrc: Optional[int] = None
    error: str = ""

    @property
    def nrc_name(self):
        return NRC_NAMES.get(self.nrc, "Unknown NRC") if self.nrc is not None else ""


class ISOTPTransport:
    """ISO 15765-2 normal-addressing transport over PCANFDDriver."""

    def __init__(self, driver, tx_id=0x7E0, rx_id=0x7E8, can_fd=False,
                 brs=False, timeout=1.0, padding=0x00, block_size=0, st_min=0):
        self.driver = driver
        self.tx_id = int(tx_id)
        self.rx_id = int(rx_id)
        self.can_fd = bool(can_fd)
        self.brs = bool(brs)
        self.timeout = float(timeout)
        self.padding = int(padding) & 0xFF
        self.block_size = int(block_size) & 0xFF
        self.st_min = int(st_min) & 0xFF
        self._lock = threading.RLock()

    @property
    def frame_length(self):
        return 64 if self.can_fd else 8

    @property
    def can_type(self):
        if not self.can_fd:
            return self.driver.CAN_TYPE_CLASSIC
        return self.driver.CAN_TYPE_FD_BRS if self.brs else self.driver.CAN_TYPE_FD

    def _send_frame(self, payload):
        raw = bytes(payload)
        frame_len = 8 if not self.can_fd else min(64, max(8, len(raw)))
        dlc = self.driver.length_to_dlc(frame_len)
        return self.driver.send_can_message(0, self.tx_id, self.can_type, dlc,
                                            raw.ljust(frame_len, bytes([self.padding])))

    def _wait_frame(self, deadline, predicate=None):
        remain = deadline - time.monotonic()
        if remain <= 0:
            return None
        return self.driver.wait_for_message(
            self.rx_id, timeout=remain, since_time=time.time() - self.timeout - 0.1,
            consume=True, predicate=predicate,
        )

    @staticmethod
    def _stmin_seconds(value):
        if value <= 0x7F:
            return value / 1000.0
        if 0xF1 <= value <= 0xF9:
            return (value - 0xF0) / 10000.0
        return 0.0

    def send(self, payload: bytes):
        payload = bytes(payload)
        with self._lock:
            self.driver.clear_rx_history(self.rx_id)
            sf_capacity = self.frame_length - (2 if self.can_fd else 1)
            if len(payload) <= 7:
                if not self._send_frame(bytes([len(payload)]) + payload):
                    raise RuntimeError(self.driver.last_error or "ISO-TP single frame send failed")
                return
            if self.can_fd and len(payload) <= sf_capacity:
                if not self._send_frame(bytes([0x00, len(payload)]) + payload):
                    raise RuntimeError(self.driver.last_error or "ISO-TP FD single frame send failed")
                return

            if len(payload) > 0xFFF:
                header = b"\x10\x00" + len(payload).to_bytes(4, "big")
            else:
                header = bytes([0x10 | ((len(payload) >> 8) & 0x0F), len(payload) & 0xFF])
            first_size = self.frame_length - len(header)
            if not self._send_frame(header + payload[:first_size]):
                raise RuntimeError(self.driver.last_error or "ISO-TP first frame send failed")

            deadline = time.monotonic() + self.timeout
            fc = self._wait_frame(deadline, lambda m: bytes(m.get("data", b""))[:1] and
                                  (bytes(m["data"])[0] >> 4) == 3)
            if not fc:
                raise TimeoutError("ISO-TP flow-control timeout")
            fc_data = bytes(fc["data"])
            flow_status = fc_data[0] & 0x0F
            while flow_status == 1:
                fc = self._wait_frame(deadline, lambda m: (bytes(m.get("data", b"\x00"))[0] >> 4) == 3)
                if not fc:
                    raise TimeoutError("ISO-TP flow-control WAIT timeout")
                fc_data = bytes(fc["data"]); flow_status = fc_data[0] & 0x0F
            if flow_status != 0:
                raise RuntimeError("ISO-TP receiver overflow")
            bs, delay = fc_data[1], self._stmin_seconds(fc_data[2])
            offset, sequence, sent_in_block = first_size, 1, 0
            while offset < len(payload):
                chunk = payload[offset:offset + self.frame_length - 1]
                if not self._send_frame(bytes([0x20 | (sequence & 0x0F)]) + chunk):
                    raise RuntimeError(self.driver.last_error or "ISO-TP consecutive frame send failed")
                offset += len(chunk); sequence = (sequence + 1) & 0x0F; sent_in_block += 1
                if delay:
                    time.sleep(delay)
                if bs and sent_in_block >= bs and offset < len(payload):
                    fc = self._wait_frame(deadline, lambda m: (bytes(m.get("data", b"\x00"))[0] >> 4) == 3)
                    if not fc:
                        raise TimeoutError("ISO-TP block flow-control timeout")
                    fc_data = bytes(fc["data"])
                    if (fc_data[0] & 0x0F) != 0:
                        raise RuntimeError("ISO-TP invalid flow-control status")
                    bs, delay, sent_in_block = fc_data[1], self._stmin_seconds(fc_data[2]), 0

    def receive(self, timeout=None):
        with self._lock:
            deadline = time.monotonic() + (self.timeout if timeout is None else float(timeout))
            first = self._wait_frame(deadline, lambda m: bytes(m.get("data", b""))[:1] and
                                     (bytes(m["data"])[0] >> 4) in (0, 1))
            if not first:
                raise TimeoutError("ISO-TP response timeout")
            data = bytes(first["data"])
            frame_type = data[0] >> 4
            if frame_type == 0:
                length = data[0] & 0x0F
                start = 1
                if length == 0:
                    length, start = data[1], 2
                return data[start:start + length]

            length = ((data[0] & 0x0F) << 8) | data[1]
            start = 2
            if length == 0:
                length, start = int.from_bytes(data[2:6], "big"), 6
            result = bytearray(data[start:])
            self._send_frame(bytes([0x30, self.block_size, self.st_min]))
            expected, block_count = 1, 0
            while len(result) < length:
                msg = self._wait_frame(deadline, lambda m: bytes(m.get("data", b""))[:1] and
                                       (bytes(m["data"])[0] >> 4) == 2)
                if not msg:
                    raise TimeoutError("ISO-TP consecutive frame timeout")
                frame = bytes(msg["data"])
                if (frame[0] & 0x0F) != expected:
                    raise RuntimeError(f"ISO-TP sequence error: expected {expected}, got {frame[0] & 0x0F}")
                result.extend(frame[1:]); expected = (expected + 1) & 0x0F; block_count += 1
                if self.block_size and block_count >= self.block_size and len(result) < length:
                    self._send_frame(bytes([0x30, self.block_size, self.st_min])); block_count = 0
            return bytes(result[:length])

    def request(self, payload: bytes, timeout=None):
        self.send(payload)
        return self.receive(timeout)


class UDSClient:
    """ISO 14229 client with generic access plus standard service helpers."""

    def __init__(self, transport: ISOTPTransport, p2_timeout=1.0, p2_star_timeout=5.0):
        self.transport = transport
        self.p2_timeout = float(p2_timeout)
        self.p2_star_timeout = float(p2_star_timeout)

    def request(self, service_id: int, data=b"", suppress_positive_response=False):
        sid = int(service_id) & 0xFF
        payload = bytes([sid]) + bytes(data)
        self.transport.send(payload)
        if suppress_positive_response:
            return UDSResponse(sid, b"", True, sid + 0x40, b"")
        deadline = time.monotonic() + self.p2_star_timeout
        timeout = self.p2_timeout
        while time.monotonic() < deadline:
            try:
                raw = self.transport.receive(timeout)
            except TimeoutError as exc:
                return UDSResponse(sid, b"", False, error=str(exc))
            if len(raw) >= 3 and raw[0] == 0x7F:
                if raw[2] == 0x78:
                    timeout = min(self.p2_star_timeout, max(0.01, deadline - time.monotonic()))
                    continue
                return UDSResponse(sid, raw, False, 0x7F, raw[3:], raw[2])
            expected = (sid + 0x40) & 0xFF
            if raw and raw[0] == expected:
                return UDSResponse(sid, raw, True, raw[0], raw[1:])
            return UDSResponse(sid, raw, False, raw[0] if raw else 0,
                               raw[1:] if raw else b"", error="Unexpected UDS response")
        return UDSResponse(sid, b"", False, error="UDS P2* timeout")

    def diagnostic_session_control(self, session, suppress=False): return self.request(0x10, bytes([session | (0x80 if suppress else 0)]), suppress)
    def ecu_reset(self, reset_type, suppress=False): return self.request(0x11, bytes([reset_type | (0x80 if suppress else 0)]), suppress)
    def clear_diagnostic_information(self, group=0xFFFFFF): return self.request(0x14, int(group).to_bytes(3, "big"))
    def read_dtc_information(self, sub_function, parameters=b""): return self.request(0x19, bytes([sub_function]) + bytes(parameters))
    def read_data_by_identifier(self, *dids): return self.request(0x22, b"".join(int(x).to_bytes(2, "big") for x in dids))
    def read_memory_by_address(self, address, size, address_length=4, size_length=2): return self.request(0x23, bytes([(address_length << 4) | size_length]) + int(address).to_bytes(address_length, "big") + int(size).to_bytes(size_length, "big"))
    def read_scaling_data_by_identifier(self, did): return self.request(0x24, int(did).to_bytes(2, "big"))
    def security_access_request_seed(self, level, data=b""): return self.request(0x27, bytes([level & 0x7F]) + bytes(data))
    def security_access_send_key(self, level, key): return self.request(0x27, bytes([level & 0x7F]) + bytes(key))
    def communication_control(self, control_type, communication_type): return self.request(0x28, bytes([control_type, communication_type]))
    def read_data_by_periodic_identifier(self, mode, *identifiers): return self.request(0x2A, bytes([mode]) + bytes(identifiers))
    def dynamically_define_data_identifier(self, definition_type, payload=b""): return self.request(0x2C, bytes([definition_type]) + bytes(payload))
    def write_data_by_identifier(self, did, value): return self.request(0x2E, int(did).to_bytes(2, "big") + bytes(value))
    def input_output_control_by_identifier(self, did, control, data=b""): return self.request(0x2F, int(did).to_bytes(2, "big") + bytes([control]) + bytes(data))
    def routine_control(self, control_type, routine_id, data=b""): return self.request(0x31, bytes([control_type]) + int(routine_id).to_bytes(2, "big") + bytes(data))
    def request_download(self, address, size, data_format=0, address_length=4, size_length=4): return self.request(0x34, bytes([data_format, (address_length << 4) | size_length]) + int(address).to_bytes(address_length, "big") + int(size).to_bytes(size_length, "big"))
    def request_upload(self, address, size, data_format=0, address_length=4, size_length=4): return self.request(0x35, bytes([data_format, (address_length << 4) | size_length]) + int(address).to_bytes(address_length, "big") + int(size).to_bytes(size_length, "big"))
    def transfer_data(self, sequence, data=b""): return self.request(0x36, bytes([sequence]) + bytes(data))
    def request_transfer_exit(self, data=b""): return self.request(0x37, bytes(data))
    def request_file_transfer(self, mode, path, data_format=0, extra=b""): return self.request(0x38, bytes([mode]) + len(path.encode()).to_bytes(2, "big") + path.encode() + bytes([data_format]) + bytes(extra))
    def write_memory_by_address(self, address, data, address_length=4, size_length=2): return self.request(0x3D, bytes([(address_length << 4) | size_length]) + int(address).to_bytes(address_length, "big") + len(data).to_bytes(size_length, "big") + bytes(data))
    def tester_present(self, suppress=False): return self.request(0x3E, bytes([0x80 if suppress else 0x00]), suppress)
    def access_timing_parameter(self, access_type, data=b""): return self.request(0x83, bytes([access_type]) + bytes(data))
    def secured_data_transmission(self, data=b""): return self.request(0x84, bytes(data))
    def control_dtc_setting(self, setting_type, data=b""): return self.request(0x85, bytes([setting_type]) + bytes(data))
    def response_on_event(self, event_type, data=b""): return self.request(0x86, bytes([event_type]) + bytes(data))
    def link_control(self, control_type, data=b""): return self.request(0x87, bytes([control_type]) + bytes(data))

    def unlock_security_access(self, level: int, key_algorithm: Callable[[bytes, int], bytes]):
        seed_response = self.security_access_request_seed(level)
        if not seed_response.positive:
            return seed_response
        seed = seed_response.data[1:] if seed_response.data else b""
        key = key_algorithm(seed, level)
        return self.security_access_send_key(level + 1 if level % 2 else level, key)
