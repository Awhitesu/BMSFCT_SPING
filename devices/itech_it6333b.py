"""IT6333B three-channel supply, RS232 SCPI."""
import math
import serial
from .itech_it6722a import IT6722A


class IT6333B(IT6722A):
    MAX_VOLTAGES = {1: 60.0, 2: 60.0, 3: 5.0}
    MAX_CURRENT = 3.0

    def __init__(self, port='', baudrate=9600, timeout=2):
        super().__init__(port, baudrate, timeout)
        self.last_error = ''

    def connect(self):
        with self._lock:
            self.disconnect()
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout,
                                         write_timeout=self.timeout)
                self.is_connected = True
                self.idn = self.query('*IDN?')
                if 'IT6333B' not in self.idn.upper():
                    raise ValueError(f'型号不匹配或无响应: {self.idn}')
                if not self.write('SYST:REM'):
                    raise ConnectionError('进入远程控制失败')
                self.last_error = ''
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def select_channel(self, channel):
        if channel not in self.MAX_VOLTAGES:
            raise ValueError('IT6333B 通道必须为1、2、3')
        if not self.write(f'INST:NSEL {channel}'):
            return False
        return self._to_float(self.query('INST:NSEL?')) == channel

    def set_voltage(self, channel, voltage, logger=None):
        value = float(voltage)
        if channel not in self.MAX_VOLTAGES or not math.isfinite(value) or not 0 <= value <= self.MAX_VOLTAGES[channel]:
            return False
        with self._lock:
            return self.select_channel(channel) and self._set_and_verify(f'VOLT {value:.3f}', 'VOLT?', value, 0.01)

    def set_current(self, channel, current, logger=None):
        value = float(current)
        if not math.isfinite(value) or not 0 <= value <= 3:
            return False
        with self._lock:
            return self.select_channel(channel) and self._set_and_verify(f'CURR {value:.3f}', 'CURR?', value, 0.005)

    def output_control(self, channel, state, logger=None):
        with self._lock:
            if not self.select_channel(channel) or not self.write(f'OUTP {1 if state else 0}'):
                return False
            return self.query('OUTP?').strip().upper() in ({'1', 'ON'} if state else {'0', 'OFF'})

    def read_channel(self, channel):
        with self._lock:
            if not self.select_channel(channel):
                raise ConnectionError(f'CH{channel} 通道选择失败')
            voltage = self._to_float(self.query('MEAS:VOLT?'))
            current = self._to_float(self.query('MEAS:CURR?'))
            output = self.query('OUTP?').strip().upper()
            if voltage < 0 or current < 0 or output not in ('0', '1', 'OFF', 'ON'):
                raise ConnectionError(f'CH{channel} 回读失败')
            return voltage, current, output in ('1', 'ON')
