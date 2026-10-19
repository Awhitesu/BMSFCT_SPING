import socket
import time
import struct
import threading
import re
from core.numeric_validation import finite_reading

class NGIN3618:
    """
    NGI N3618 高压直流电源驱动 (通过 SYST:ERR? 判定成功)
    """
    def __init__(self, ip: str, port: int = 5025):
        self.ip = ip
        self.port = port
        self.sock = None
        self.is_connected = False
        self.lock = threading.RLock()
        self._lock = self.lock
        self.control_timeout = 5.0
        self.output_voltage_tolerance = 1.0
        self.voltage_setpoint_tolerance = .01
        self.current_setpoint_tolerance = .001
        self.last_control = {}
        self.control_statistics = dict(total=0, first_pass=0, recovered=0, failed=0)
        self.TERMINATOR = "\n"
        self._rx_buffer = bytearray()
        self.last_error = ""
        self.identity = ""

    def connect(self) -> bool:
        with self.lock:
            self.disconnect()
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                self.sock.settimeout(2.0)
                self.sock.connect((self.ip, self.port))
                
                # Startup checks are passive: recheck the actual instrument
                # without clearing its protection state or changing outputs.
                self.send_cmd("*IDN?")
                idn = self._read_reply()
                
                if not idn:
                    raise ConnectionError("Empty identity response")
                if not re.search(r'(?:^|[,\s])N3618[A-Z0-9._-]*(?:$|[,\s])', idn.upper()):
                    raise ConnectionError(f"高压源型号不匹配：{idn}")
                self.send_cmd("OUTP?")
                output = self._read_reply().upper()
                if output not in ('0', '1', 'OFF', 'ON'):
                    raise ConnectionError(f"高压源输出状态回读无效：{output}")
                self.identity = idn
                print(f"[NGI N3618] 联机成功: {idn}")
                self.is_connected = True
                self.last_error = ""
                self._ever_connected = True
                return True
            except Exception as e:
                self.last_error = str(e)
                print(f"[NGI N3618] 连接失败: {e}")
                self.disconnect()
                return False

    def disconnect(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self.sock.close()
                except Exception:
                    pass
            self.sock = None
            self._rx_buffer.clear()
            self.is_connected = False
            self.identity = ""

    def clear_errors(self, logger=None) -> bool:
        """清除设备错误与保护锁定状态"""
        with self.lock:
            if not self.is_connected:
                if not self.connect():
                    return False
            try:
                self.send_cmd("*CLS", logger)
                self.send_cmd("PROT:CLE", logger)
                return self.check_success(logger)
            except Exception as e:
                if logger: logger(f"[IP: {self.ip}] [!] 清除错误异常: {e}")
                return False

    def _read_reply(self):
        while b"\n" not in self._rx_buffer:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("Device disconnected before completing reply")
            self._rx_buffer.extend(chunk)
            if len(self._rx_buffer) > 65536:
                raise ValueError("Reply exceeds size limit")
        line, _, rest = self._rx_buffer.partition(b"\n")
        self._rx_buffer = bytearray(rest)
        return line.decode('ascii').strip()

    def send_cmd(self, cmd: str, logger=None):
        with self.lock:
            if not self.sock:
                raise ConnectionError("HV source is not connected")
            if logger:
                logger(f"[IP: {self.ip}] [TX] {cmd}")
            try:
                self.sock.sendall(f"{cmd}{self.TERMINATOR}".encode('ascii'))
            except OSError as exc:
                self.last_error = str(exc)
                self.disconnect()
                raise

    def query(self, command, logger=None):
        with self.lock:
            try:
                self.send_cmd(command, logger)
                reply = self._read_reply()
                if logger:
                    logger(f"[IP: {self.ip}] [RX] {reply}")
                return reply
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                raise

    def check_success(self, logger=None):
        with self.lock:
            try:
                reply = self.query('SYST:ERR?', logger)
                ok = int(reply.split(',', 1)[0].strip()) == 0
                self.last_error = '' if ok else reply
                return ok
            except Exception as exc:
                self.last_error = str(exc)
                return False

    def configure_confirmation(self, timeout, voltage_tolerance):
        timeout, voltage_tolerance = finite_reading(timeout), finite_reading(voltage_tolerance)
        if timeout <= 0 or voltage_tolerance < 0:
            raise ValueError('Invalid confirmation timeout/tolerance')
        with self.lock:
            self.control_timeout, self.output_voltage_tolerance = timeout, voltage_tolerance

    def _number(self, command, logger=None):
        value = finite_reading(self.query(command, logger))
        if value < 0:
            raise ValueError(f'Invalid negative readback for {command}: {value}')
        return value

    def read_output_state(self, logger=None):
        with self.lock:
            reply = self.query('OUTP?', logger).upper()
            if reply not in ('0', '1', 'OFF', 'ON'):
                raise ValueError(f'Invalid output state: {reply}')
            return reply in ('1', 'ON')

    def _confirm(self, action, command, expected, readback, matches, logger=None):
        started = time.monotonic()
        actual, requested = {}, {}
        success, reads, attempts, reason = False, 0, 0, ''
        with self.lock:
            try:
                timeout = finite_reading(self.control_timeout)
                if timeout <= 0:
                    raise ValueError('Confirmation timeout must be positive')
                if not self.is_connected and not self.connect():
                    raise ConnectionError(self.last_error or 'HV source is offline')
                requested = expected() if callable(expected) else expected
                deadline = time.monotonic() + timeout
                # Acknowledged commands are never repeatedly resent while waiting.
                attempts = 1
                self.send_cmd(command, logger)
                if not self.check_success(logger):
                    raise RuntimeError(self.last_error or 'Instrument rejected command')
                while True:
                    actual = readback(actual)
                    reads += 1
                    success = matches(requested, actual)
                    if success:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Confirmation timeout: requested={requested}, actual={actual}')
                    time.sleep(min(.1, max(0, deadline-time.monotonic())))
            except Exception as exc:
                reason = str(exc)
            self.last_error = reason
            self.control_statistics['total'] += 1
            category = 'failed' if not success else ('first_pass' if reads == 1 else 'recovered')
            self.control_statistics[category] += 1
            self.last_control = dict(action=action,requested=requested,actual=actual,success=success,
                attempts=attempts,elapsed=time.monotonic()-started,reason=reason,
                statistics=dict(self.control_statistics),voltage_tolerance=self.output_voltage_tolerance)
            if logger:
                logger(f'[HV control confirmation] {self.last_control}')
            return success

    def _invalid_control(self, action, requested, reason):
        with self.lock:
            self.last_error = str(reason)
            self.control_statistics['total'] += 1
            self.control_statistics['failed'] += 1
            self.last_control = dict(action=action, requested=requested, actual={}, success=False,
                attempts=0, elapsed=0, reason=self.last_error, statistics=dict(self.control_statistics))
        return False

    def set_voltage(self, voltage, logger=None):
        try:
            voltage = finite_reading(voltage)
            if voltage < 0:
                raise ValueError('Voltage must be non-negative')
            setting_tolerance = finite_reading(self.voltage_setpoint_tolerance)
            output_tolerance = finite_reading(self.output_voltage_tolerance)
            if min(setting_tolerance, output_tolerance) < 0:
                raise ValueError('Tolerance must be non-negative')
        except (ValueError, TypeError) as exc:
            return self._invalid_control('set_voltage', {'voltage':str(voltage)}, exc)
        def readback(actual):
            actual['voltage'] = self._number('VOLT?', logger)
            actual['output'] = self.read_output_state(logger)
            if actual['output']:
                actual['measured_voltage'] = self._number('MEAS:VOLT?', logger)
            else:
                actual.pop('measured_voltage', None)
            return actual
        return self._confirm('set_voltage', f'VOLT {voltage:.3f}', {'voltage':voltage}, readback,
            lambda req, act: abs(act['voltage']-voltage) <= setting_tolerance+1e-12 and
                (not act['output'] or abs(act['measured_voltage']-voltage) <= output_tolerance+1e-12), logger)

    def set_current(self, current, logger=None):
        try:
            current = finite_reading(current)
            tolerance = finite_reading(self.current_setpoint_tolerance)
            if current < 0 or tolerance < 0:
                raise ValueError('Current/tolerance must be non-negative')
        except (ValueError, TypeError) as exc:
            return self._invalid_control('set_current', {'current':str(current)}, exc)
        def readback(actual):
            actual['current_limit'] = self._number('CURR?', logger)
            return actual
        return self._confirm('set_current', f'CURR {current:.3f}', {'current_limit':current}, readback,
            lambda req, act: abs(act['current_limit']-current) <= tolerance+1e-12, logger)

    def output_control(self, state, logger=None):
        if state not in (False, True, 0, 1):
            return self._invalid_control('output_control', {'output':str(state)}, 'Output must be ON/OFF')
        state = bool(state)
        try:
            tolerance = finite_reading(self.output_voltage_tolerance)
            if tolerance < 0:
                raise ValueError('Voltage tolerance must be non-negative')
        except (ValueError, TypeError) as exc:
            return self._invalid_control('output_control', {'output':state}, exc)
        def expected():
            request = {'output':state}
            if state:
                request['voltage'] = self._number('VOLT?', logger)
            return request
        def readback(actual):
            actual['output'] = self.read_output_state(logger)
            if state:
                actual['measured_voltage'] = self._number('MEAS:VOLT?', logger)
            return actual
        return self._confirm('output_control', 'OUTP ON' if state else 'OUTP OFF', expected, readback,
            lambda req, act: act['output'] == state and
                (not state or abs(act['measured_voltage']-req['voltage']) <= tolerance+1e-12), logger)

    def measure_voltage(self, logger=None) -> float:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return -1.0
        with self.lock:
            try:
                data = self.query("MEAS:VOLT?", logger)
                if logger: logger(f"[IP: {self.ip}] [RX] {data} V")
                return finite_reading(data)
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 测量电压异常: {e}")
                return -1.0

    def measure_current(self, logger=None) -> float:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return -1.0
        with self.lock:
            try:
                data = self.query("MEAS:CURR?", logger)
                if logger: logger(f"[IP: {self.ip}] [RX] {data} A")
                return finite_reading(data)
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 测量电流异常: {e}")
                return -1.0

