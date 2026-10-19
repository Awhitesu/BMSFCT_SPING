import logging
import threading
import time
import re

import serial
from core.numeric_validation import finite_reading


logger = logging.getLogger("IT6722A")


class IT6722A:
    """ITECH IT6722A RS232/SCPI driver (9600 8N1)."""

    MAX_VOLTAGE = 80.0
    MAX_CURRENT = 20.0
    COMMAND_INTERVAL = .08
    DIAGNOSE_FAILURE = True

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self.ser = None
        self.is_connected = False
        self.idn = ""
        self._lock = threading.RLock()
        self.last_error = ""
        self.identity = ""
        self.control_timeout = 5.0
        self.output_voltage_tolerance = .05
        self.last_control = {}
        self.control_statistics = dict(total=0,first_pass=0,recovered=0,failed=0)
        self._last_command_at = None

    def connect(self) -> bool:
        with self._lock:
            self.disconnect()
            if not str(self.port).strip():
                self.last_error = "IT6722A serial port is not configured"
                logger.warning("IT6722A 串口未配置")
                return False
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )
                time.sleep(0.2)
                self.is_connected = True
                self.idn = self.query("*IDN?")
                if not self.idn:
                    raise RuntimeError("*IDN? 无响应")
                if "ERROR" in self.idn.upper() or not re.search(r"(?:^|[,\s])IT6722A?(?:$|[,\s])", self.idn.upper()):
                    raise RuntimeError(f"型号不匹配: {self.idn}")
                self._prepare_control()
                self.read_output_state()
                self.identity = self.idn
                self.last_error = ""
                logger.info("IT6722A 已连接: %s, %s", self.port, self.idn)
                self._ever_connected = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("IT6722A 连接失败: %s", exc)
                self.disconnect()
                return False

    def disconnect(self):
        with self._lock:
            if self.ser:
                try:
                    if self.ser.is_open:
                        self.ser.close()
                except Exception:
                    pass
            self.ser = None
            self.is_connected = False
            self.identity = ""
            self.idn = ""
            self._last_command_at = None

    def _write_line(self, command: str):
        if not self.is_connected or not self.ser or not self.ser.is_open:
            raise RuntimeError("IT6722A 未连接")
        if self._last_command_at is not None:
            remaining = self.COMMAND_INTERVAL - (time.monotonic() - self._last_command_at)
            if remaining > 0:
                time.sleep(remaining)
        data = (command.strip() + "\r\n").encode("ascii")
        if self.ser.write(data) != len(data):
            raise ConnectionError("串口指令未完整写入")
        self.ser.flush()
        self._last_command_at = time.monotonic()
        logger.debug('TX %s', command)

    def write(self, command: str) -> bool:
        with self._lock:
            try:
                self._write_line(command)
                return True
            except Exception as exc:
                logger.error("IT6722A 写入失败 [%s]: %s", command, exc)
                self.last_error = str(exc)
                self.disconnect()
                return False

    def query(self, command: str) -> str:
        with self._lock:
            try:
                self.ser.reset_input_buffer()
                self._write_line(command)
                raw = self.ser.readline()
                if not raw or not raw.endswith((b"\r", b"\n")):
                    raise TimeoutError("Incomplete or empty serial reply")
                response = raw.decode("ascii").strip()
                if not response:
                    raise TimeoutError("Empty instrument reply")
                logger.debug('RX [%s] %s', command, response)
                return response
            except Exception as exc:
                logger.error("IT6722A 查询失败 [%s]: %s", command, exc)
                self.last_error = str(exc)
                self.disconnect()
                return ""

    @staticmethod
    def _to_float(value: str, default: float = -1.0) -> float:
        try:
            return finite_reading(value)
        except (TypeError, ValueError):
            return default

    def _set_and_verify(self, command: str, query: str, target: float, tolerance: float) -> bool:
        with self._lock:
            if not self.write(command):
                return False
            time.sleep(0.08)
            actual = self._to_float(self.query(query))
            return actual >= 0 and abs(actual - target) <= tolerance

    def configure_confirmation(self, timeout, voltage_tolerance):
        timeout, voltage_tolerance = finite_reading(timeout), finite_reading(voltage_tolerance)
        if timeout <= 0 or voltage_tolerance < 0:
            raise ValueError("Invalid control timeout/tolerance")
        with self._lock:
            self.control_timeout, self.output_voltage_tolerance = timeout, voltage_tolerance

    def read_output_state(self):
        with self._lock:
            reply = self.query('OUTP?').upper()
            if not reply:
                raise ConnectionError(self.last_error or 'No output state reply')
            if reply not in ('0','1','OFF','ON'):
                raise ValueError(f'Invalid output state: {reply}')
            return reply in ('1','ON')

    def _number(self, command):
        reply = self.query(command)
        if not reply:
            raise ConnectionError(self.last_error or f'No reply for {command}')
        value = finite_reading(reply)
        if value < 0:
            raise ValueError(f'Invalid negative reading: {command} {value}')
        return value

    def _record_control(self, action, requested, actual, success, attempts, reads, started, reason, log=None):
        self.last_error = reason
        self.control_statistics['total'] += 1
        key = 'failed' if not success else ('first_pass' if reads == 1 else 'recovered')
        self.control_statistics[key] += 1
        self.last_control = dict(action=action,requested=requested,actual=actual,success=success,
            attempts=attempts,elapsed=time.monotonic()-started,reason=reason,
            statistics=dict(self.control_statistics),voltage_tolerance=self.output_voltage_tolerance)
        if log:
            log(f'[{type(self).__name__} control confirmation] {self.last_control}')
        else:
            logger.info("%s control confirmation: %s", type(self).__name__, self.last_control)

    def _prepare_control(self):
        # RS232 requires remote mode, including after an operator presses Local.
        if not self.write('SYST:REM'):
            raise ConnectionError(self.last_error or 'IT6722A进入远程模式失败')

    def _confirmed(self, action, command, expected, readback, matches, log=None):
        started = time.monotonic()
        requested = expected if isinstance(expected,dict) else {}
        actual, attempts, reads, success, reason = {}, 0, 0, False, ''
        with self._lock:
            try:
                timeout = finite_reading(self.control_timeout)
                if timeout <= 0:
                    raise ValueError('Control timeout must be positive')
                if not self.is_connected:
                    raise ConnectionError('IT6722A is not connected; initialize first')
                self._prepare_control()
                requested = expected() if callable(expected) else expected
                deadline = time.monotonic() + timeout
                attempts = 1
                if not self.write(command):
                    raise ConnectionError(self.last_error or 'Serial write failed')
                while True:
                    actual = readback(actual)
                    reads += 1
                    success = matches(requested,actual)
                    if success:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Confirmation timeout: requested={requested}, actual={actual}')
                    time.sleep(min(.1,max(0,deadline-time.monotonic())))
            except Exception as exc:
                reason = str(exc)
                if self.is_connected and self.DIAGNOSE_FAILURE:
                    try:
                        error = self.query('SYST:ERR?')
                        if error:
                            actual['instrument_error'] = error
                            reason += '; 仪器错误队列=' + error
                    except Exception as diagnostic_error:
                        logger.warning('IT6722A error diagnostics failed: %s', diagnostic_error)
            self._record_control(action, requested, actual, success, attempts, reads, started, reason, log)
            return success

    def _invalid(self, action, requested, reason, log=None):
        with self._lock:
            self._record_control(action,requested,{},False,0,0,time.monotonic(),str(reason),log)
        return False

    def set_voltage(self, voltage, logger=None):
        try:
            value = finite_reading(voltage)
            tolerance = finite_reading(self.output_voltage_tolerance)
            if not 0 <= value <= self.MAX_VOLTAGE or tolerance < 0:
                raise ValueError('Voltage/tolerance is outside allowed range')
        except (ValueError,TypeError) as exc:
            return self._invalid('set_voltage', {'voltage':str(voltage)},exc,logger)
        def readback(actual):
            actual['voltage'] = self._number('VOLT?')
            actual['output'] = self.read_output_state()
            if actual['output']:
                actual['measured_voltage'] = self._number('MEAS:VOLT?')
            else:
                actual.pop('measured_voltage',None)
            return actual
        return self._confirmed('set_voltage',f'VOLT {value:.3f}',{'voltage':value},readback,
            lambda req,act: abs(act['voltage']-value) <= .005+1e-12 and
                (not act['output'] or abs(act['measured_voltage']-value) <= tolerance+1e-12),logger)

    def set_current(self, current, logger=None):
        try:
            value = finite_reading(current)
            if not 0 <= value <= self.MAX_CURRENT:
                raise ValueError('Current is outside allowed range')
        except (ValueError,TypeError) as exc:
            return self._invalid('set_current',{'current_limit':str(current)},exc,logger)
        def readback(actual):
            actual['current_limit'] = self._number('CURR?')
            return actual
        return self._confirmed('set_current',f'CURR {value:.3f}',{'current_limit':value},readback,
            lambda req,act: abs(act['current_limit']-value) <= .005+1e-12,logger)

    def output_control(self, state, logger=None):
        if state not in (False,True,0,1):
            return self._invalid('output_control',{'output':str(state)},'Output must be ON/OFF',logger)
        state = bool(state)
        try:
            tolerance = finite_reading(self.output_voltage_tolerance)
            if tolerance < 0:
                raise ValueError('Voltage tolerance must be non-negative')
        except (ValueError,TypeError) as exc:
            return self._invalid('output_control',{'output':state},exc,logger)
        def expected():
            result = {'output':state}
            if state:
                value = self._number('VOLT?')
                if value > self.MAX_VOLTAGE:
                    raise ValueError('Readback voltage setpoint is outside allowed range')
                result['voltage'] = value
            return result
        def readback(actual):
            actual['output'] = self.read_output_state()
            if state:
                actual['measured_voltage'] = self._number('MEAS:VOLT?')
            return actual
        return self._confirmed('output_control','OUTP ON' if state else 'OUTP OFF',expected,readback,
            lambda req,act: act['output'] == state and
                (not state or abs(act['measured_voltage']-req['voltage']) <= tolerance+1e-12),logger)

    def measure_voltage(self, logger=None) -> float:
        value = self._to_float(self.query("MEAS:VOLT?"))
        if logger:
            logger(f"IT6722A 实测电压: {value:.3f}V" if value >= 0 else "IT6722A 电压读取失败")
        return value

    def measure_current(self, logger=None) -> float:
        value = self._to_float(self.query("MEAS:CURR?"))
        if logger:
            logger(f"IT6722A 实测电流: {value:.3f}A" if value >= 0 else "IT6722A 电流读取失败")
        return value

