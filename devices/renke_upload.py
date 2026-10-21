"""Renke P2 upload receiver, checked against the manufacturer's SDK 2.2.9.

Only login/heartbeat/data acknowledgements are sent. Device settings, clock,
calibration and relay outputs are never written by this receiver.
"""
from datetime import datetime
import socket
import struct
import threading
import time
from .rs_ws_eth6 import EnvironmentReading, RSWSETH6


class RenkeP2Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 10:
            if self.buffer[0] != 0x68 or self.buffer[7] != 0x68:
                raise ValueError('仁科上传报文帧头错误')
            size = self.buffer[9] + 12
            if not 18 <= size <= 255:
                raise ValueError('仁科上传报文长度错误')
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            del self.buffer[:size]
            if (frame[8] & 0x80) == 0 or frame[-1] != 0x16 or (sum(frame[:-2]) & 0xff) != frame[-2]:
                raise ValueError('仁科上传报文校验失败')
            frames.append(frame)
        return frames

    @staticmethod
    def command(frame):
        return ((frame[11] - 0x33) & 0xff) << 8 | ((frame[10] - 0x33) & 0xff)

    @staticmethod
    def terminal(frame):
        digits = bytes((value - 0x33) & 0xff for value in frame[-6:-2])[::-1].hex()
        if not digits.isdecimal():
            raise ValueError('仁科设备终端地址格式错误')
        return digits

    @staticmethod
    def acknowledgement(frame):
        code = {0xea00: 0, 0xea02: 2, 0xea03: 3, 0xea09: 9, 0xec09: 9}.get(RenkeP2Decoder.command(frame))
        if code is None:
            return None
        payload = bytes([(code + 0x33) & 0xff, 0x1d]) + frame[-6:-2]
        ack = b'\x68' + b'\x88' * 6 + b'\x68\x20' + bytes([len(payload)]) + payload
        return ack + bytes([sum(ack) & 0xff, 0x16])

    @staticmethod
    def reading(frame, node):
        if RenkeP2Decoder.command(frame) != 0xea03:
            return None
        length = frame[9]
        if length < 27 or (length - 22) % 5:
            raise ValueError('仁科实时温湿度节点数据长度错误')
        matches = []
        for start in range(12, 12 + length - 22, 5):
            values = bytes((value - 0x33) & 0xff for value in frame[start:start+5])
            channel, temperature, humidity = struct.unpack('<BHH', values)
            if channel != node:
                continue
            if humidity > 1000:
                raise ValueError('仁科上传湿度值无效')
            # P2 uses sign/magnitude, unlike Modbus's signed 16-bit word.
            value = -(temperature & 0x7fff) if temperature & 0x8000 else temperature
            matches.append(EnvironmentReading(value / 10, humidity / 10, datetime.now(), 0,
                           (humidity, temperature), '仁科 P2 主动上传', RenkeP2Decoder.terminal(frame), node))
        if len(matches) != 1:
            raise ValueError(f'上传报文未包含唯一的温湿度节点 {node}')
        return matches[0]


class RenkeUploadSensor:
    mode = 'upload'
    identity = 'RS-WS-ETH-6 温湿度传感器（主动上传）'

    def __init__(self, ip='', port=2404, unit=1, stale_seconds=15):
        self.ip, self.port, self.unit = str(ip).strip(), int(port), int(unit)
        self.stale_seconds = float(stale_seconds)
        self.last_error = ''
        self.terminal_id = ''
        self.peer = ''
        self.received_frames = 0
        self.last_frame = ''
        self._reading = None
        self._received_at = 0
        self._listener = None
        self._peer_socket = None
        self._thread = None
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()

    @property
    def is_listening(self):
        return bool(self._listener and self._thread and self._thread.is_alive() and not self._stop.is_set())

    @property
    def data_age(self):
        with self._state_lock:
            return time.monotonic() - self._received_at if self._reading else None

    @property
    def last_reading(self):
        with self._state_lock:
            age = self.data_age
            return self._reading if age is not None and age <= self.stale_seconds and self.peer else None

    @property
    def is_connected(self):
        return self.is_listening and self.last_reading is not None

    def connect(self):
        with self._lifecycle_lock:
            if self.is_listening:
                return True
            self.disconnect()
            try:
                RSWSETH6.validate_configuration(self.ip, self.port, self.unit)
                if not 0 < self.stale_seconds <= 600:
                    raise ValueError('温湿度数据有效期应为 0–600 秒')
                listener = socket.socket()
                # Do not steal another application's listening port on Windows.
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                self._listener = listener
                listener.bind(('0.0.0.0', self.port))
                listener.listen(2)
                listener.settimeout(.25)
                self._stop.clear()
                self.last_error = ''
                self._thread = threading.Thread(target=self._receive_loop, name='Renke-temperature-upload', daemon=True)
                self._thread.start()
                return True
            except Exception as exc:
                self.last_error = f'监听端口 {self.port} 失败：{exc}'
                self.disconnect()
                return False

    def disconnect(self):
        with self._lifecycle_lock:
            self._stop.set()
            for sock in (self._peer_socket, self._listener):
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(2)
                if self._thread.is_alive():
                    raise RuntimeError('温湿度接收线程尚未停止')
            self._thread = self._listener = self._peer_socket = None
            with self._state_lock:
                self.peer = ''
                self._reading = None
                self._received_at = 0

    def _receive_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    conn, peer = self._listener.accept()
                except socket.timeout:
                    continue
                if peer[0] != self.ip:
                    conn.close()
                    continue
                self._peer_socket = conn
                conn.settimeout(.25)
                self.peer = peer[0]
                decoder = RenkeP2Decoder()
                try:
                    while not self._stop.is_set():
                        try:
                            data = conn.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            raise ConnectionError('传感器上传连接已断开')
                        for frame in decoder.feed(data):
                            terminal = decoder.terminal(frame)
                            reading = decoder.reading(frame, self.unit)
                            ack = decoder.acknowledgement(frame)
                            if ack:
                                conn.sendall(ack)
                            with self._state_lock:
                                self.received_frames += 1
                                self.last_frame = frame.hex(' ').upper()
                                self.terminal_id = terminal
                                if reading:
                                    self._reading = reading
                                    self._received_at = time.monotonic()
                                    self.last_error = ''
                except Exception as exc:
                    if not self._stop.is_set():
                        self.last_error = str(exc)
                finally:
                    conn.close()
                    self._peer_socket = None
                    with self._state_lock:
                        self.peer = ''
                        self._reading = None
        except Exception as exc:
            if not self._stop.is_set():
                self.last_error = str(exc)
        finally:
            if self._listener:
                self._listener.close()

    def read_environment(self):
        reading = self.last_reading
        if reading is None:
            raise ConnectionError('尚未收到有效温湿度数据，或上传数据已超时；请检查设备目标地址与监听端口')
        return reading
