"""RS-WS-ETH-6 Modbus TCP: FC03, humidity 40001, temperature 40002."""
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import socket
import struct
import threading
import time


@dataclass(frozen=True)
class EnvironmentReading:
    temperature: float
    humidity: float
    captured_at: datetime
    elapsed_ms: float
    registers: tuple
    protocol: str = 'Modbus TCP'
    terminal_id: str = ''
    node: int = 1


class RSWSETH6:
    mode = 'modbus'
    def __init__(self, ip='', port=502, unit=1, timeout=2.0):
        self.ip = str(ip).strip()
        self.port, self.unit = int(port), int(unit)
        self.timeout = float(timeout)
        self.is_connected = False
        self.last_error = ''
        self.last_reading = None
        self.identity = 'RS-WS-ETH-6 温湿度传感器'
        self._socket = None
        self._transaction = 0
        self._lock = threading.RLock()

    @staticmethod
    def validate_configuration(ip, port, unit):
        ipaddress.IPv4Address(ip)
        if not 1 <= int(port) <= 65535:
            raise ValueError('温湿度传感器：端口应为 1–65535')
        if not 1 <= int(unit) <= 255:
            raise ValueError('温湿度传感器：Modbus 站号应为 1–255')

    def disconnect(self):
        with self._lock:
            try:
                if self._socket is not None:
                    self._socket.close()
            finally:
                self._socket = None
                self.is_connected = False
                self.last_reading = None

    def connect(self):
        """Only a valid temperature/humidity response verifies connection."""
        with self._lock:
            self.disconnect()
            try:
                self.validate_configuration(self.ip, self.port, self.unit)
                if not 0 < self.timeout <= 30:
                    raise ValueError('读取超时应大于 0 且不超过 30 秒')
                self._socket = socket.create_connection((self.ip, self.port), self.timeout)
                self._read()
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def _receive(self, count, deadline):
        result = bytearray()
        while len(result) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('温湿度传感器读取超时')
            self._socket.settimeout(remaining)
            part = self._socket.recv(count - len(result))
            if not part:
                raise ConnectionError('温湿度传感器已关闭连接')
            result.extend(part)
        return bytes(result)

    def _read(self):
        started = time.monotonic()
        deadline = started + self.timeout
        self._transaction = (self._transaction + 1) & 0xffff
        # Read both measurements in one request, without writes or calibration.
        frame = struct.pack('>HHHBBHH', self._transaction, 0, 6, self.unit, 3, 0, 2)
        self._socket.settimeout(self.timeout)
        self._socket.sendall(frame)
        transaction, protocol, length, unit = struct.unpack('>HHHB', self._receive(7, deadline))
        if transaction != self._transaction or protocol != 0 or unit != self.unit:
            raise ValueError('温湿度传感器 Modbus 响应头不匹配')
        if length not in (3, 7):
            raise ValueError('温湿度传感器 Modbus 响应长度错误')
        pdu = self._receive(length - 1, deadline)
        if pdu[0] == 0x83 and len(pdu) == 2:
            raise ValueError(f'温湿度传感器 Modbus 异常码 0x{pdu[1]:02X}')
        if len(pdu) != 6 or pdu[:2] != b'\x03\x04':
            raise ValueError('温湿度传感器功能码或数据长度错误')
        humidity, raw_temperature = struct.unpack('>HH', pdu[2:])
        if humidity > 1000:
            raise ValueError(f'温湿度传感器湿度数据无效：0x{humidity:04X}')
        temperature = raw_temperature if raw_temperature < 0x8000 else raw_temperature - 0x10000
        reading = EnvironmentReading(temperature / 10, humidity / 10, datetime.now(),
                                     (time.monotonic() - started) * 1000,
                                     (humidity, raw_temperature))
        self.last_reading = reading
        self.last_error = ''
        self.is_connected = True
        return reading

    def read_environment(self):
        """Return a fresh pair or raise; never substitute a previous reading."""
        with self._lock:
            try:
                if self._socket is None:
                    raise ConnectionError('温湿度传感器未连接，请先连接设备')
                return self._read()
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                raise
