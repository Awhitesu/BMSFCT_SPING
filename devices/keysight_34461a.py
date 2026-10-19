"""Keysight 34461A SCPI socket driver (LAN port 5025)."""
import math
import socket
import threading
import time


class Keysight34461A:
    MODEL = '34461A'
    FUNCTIONS = {
        '直流电压': ('VOLT:DC', 'V'), '交流电压': ('VOLT:AC', 'V'),
        '直流电流': ('CURR:DC', 'A'), '交流电流': ('CURR:AC', 'A'),
        '电阻（两线）': ('RES', 'Ω'), '电阻（四线）': ('FRES', 'Ω'),
        '频率': ('FREQ', 'Hz'), '周期': ('PER', 's'),
        '二极管': ('DIOD', 'V'), '通断': ('CONT', 'Ω'),
    }

    def __init__(self, ip='', port=5025, timeout=5):
        self.ip, self.port, self.timeout = ip, int(port), timeout
        self.sock = None
        self.is_connected = False
        self.identity = ''
        self.last_error = ''
        self._buffer = b''
        self._lock = threading.RLock()

    def disconnect(self):
        with self._lock:
            sock = self.sock
            self.sock = None
            self._buffer = b''
            self.is_connected = False
            if sock:
                try:
                    sock.close()
                except OSError as exc:
                    self.last_error = str(exc)

    def connect(self):
        with self._lock:
            self.disconnect()
            try:
                self.sock = socket.create_connection((self.ip, self.port), self.timeout)
                self.sock.settimeout(self.timeout)
                self.is_connected = True
                self.identity = self.query('*IDN?')
                if self.MODEL not in self.identity.upper():
                    raise ValueError(f'设备型号不匹配：{self.identity}')
                self.last_error = ''
                self._ever_connected = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def command(self, text):
        with self._lock:
            if not self.is_connected or not self.sock:
                raise ConnectionError('34461A 未连接，请先连接设备。')
            text = text.strip()
            if not text or '\n' in text or '\r' in text:
                raise ValueError('请输入单行 SCPI 指令。')
            try:
                self.sock.sendall((text + '\n').encode('ascii'))
                if '?' not in text:
                    return '已发送'
                while b'\n' not in self._buffer:
                    data = self.sock.recv(4096)
                    if not data:
                        raise ConnectionError('仪表已关闭连接')
                    self._buffer += data
                    if len(self._buffer) > 65536:
                        raise ValueError('响应过长')
                line, self._buffer = self._buffer.split(b'\n', 1)
                return line.decode('ascii').strip()
            except (OSError, UnicodeError, ValueError) as exc:
                self.last_error = str(exc)
                self.disconnect()  # Prevent a late response being treated as the next query's result.
                raise

    def query(self, text):
        if '?' not in text:
            raise ValueError('查询指令必须包含 ?')
        return self.command(text)

    def measure(self, function, range_value='AUTO'):
        with self._lock:
            if function not in self.FUNCTIONS:
                raise ValueError('Unsupported 34461A measurement function')
            command,unit=self.FUNCTIONS[function]
            if command in ('DIOD','CONT'):
                query=f'MEAS:{command}?'
            else:
                if isinstance(range_value,bool):raise ValueError('Range must be AUTO or a positive number')
                if str(range_value).strip().upper()=='AUTO':
                    range_value='AUTO'
                else:
                    number=float(range_value)
                    if not math.isfinite(number) or number<=0:
                        raise ValueError('Range must be AUTO or a positive number')
                    range_value=str(number)
                query=f'MEAS:{command}? {range_value}'
            started=time.perf_counter()
            self.last_measurement=dict(status='pending',function=command,requested_range=range_value,unit=unit)
            try:
                raw=self.query(query)
                self.last_measurement['raw']=raw
                value=float(raw)
                self.last_measurement['value']=value
                if not math.isfinite(value):
                    self.last_measurement['status']='invalid'
                    raise ValueError(f'34461A invalid measurement: function={command}, range={range_value}, raw={raw}')
                if abs(value)>=9e37:
                    self.last_measurement['status']='overrange'
                    raise ValueError(f'34461A overrange: function={command}, range={range_value}, raw={raw}')
                self.last_measurement['status']='ok'
                self.last_error=''
                return value,unit
            except Exception as exc:
                if self.last_measurement['status']=='pending':self.last_measurement['status']='error'
                self.last_error=str(exc)
                raise
            finally:
                self.last_measurement['elapsed_s']=time.perf_counter()-started
