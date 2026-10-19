"""IT6333B three-channel supply, RS232 SCPI."""
from core.numeric_validation import finite_reading
import serial
from .itech_it6722a import IT6722A


class IT6333B(IT6722A):
    COMMAND_INTERVAL = 0.0
    DIAGNOSE_FAILURE = False
    MAX_VOLTAGES = {1: 60.0, 2: 60.0, 3: 5.0}
    MAX_CURRENT = 3.0

    def _prepare_control(self):
        # This model has its own remote-mode handshake and channel selection.
        pass

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
                self._ever_connected = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def _channel(self, channel):
        if isinstance(channel,bool) or not isinstance(channel,int) or channel not in self.MAX_VOLTAGES:
            raise ValueError('IT6333B channel must be 1, 2 or 3')
        return channel

    def select_channel(self, channel):
        self._channel(channel)
        with self._lock:
            if not self.write(f'INST:NSEL {channel}'):
                return False
            return self._to_float(self.query('INST:NSEL?')) == channel

    def _confirmed_channel(self, channel, action, command, requested, readback, matches, logger=None):
        with self._lock:
            def expected():
                if not self.select_channel(channel):
                    raise ConnectionError(f'CH{channel} selection not confirmed')
                values = requested() if callable(requested) else dict(requested)
                return {'channel':channel,**values}
            def actual(values):
                values['channel'] = channel
                return readback(values)
            return self._confirmed(action,command,expected,actual,matches,logger)

    def set_voltage(self, channel, voltage, logger=None):
        try:
            self._channel(channel)
            value = finite_reading(voltage)
            if not 0 <= value <= self.MAX_VOLTAGES[channel]:
                raise ValueError('Voltage is outside channel range')
        except (ValueError,TypeError) as exc:
            return self._invalid('set_voltage',{'channel':channel,'voltage':str(voltage)},exc,logger)
        def readback(actual):
            actual['voltage'] = self._number('VOLT?')
            actual['output'] = self.read_output_state()
            if actual['output']:actual['measured_voltage'] = self._number('MEAS:VOLT?')
            return actual
        return self._confirmed_channel(channel,'set_voltage',f'VOLT {value:.3f}',{'voltage':value},readback,
            lambda req,act: abs(act['voltage']-value)<=.01+1e-12 and
                (not act['output'] or abs(act['measured_voltage']-value)<=self.output_voltage_tolerance+1e-12),logger)

    def set_current(self, channel, current, logger=None):
        try:
            self._channel(channel)
            value = finite_reading(current)
            if not 0 <= value <= self.MAX_CURRENT:raise ValueError('Current outside channel range')
        except (ValueError,TypeError) as exc:
            return self._invalid('set_current',{'channel':channel,'current_limit':str(current)},exc,logger)
        def readback(actual):
            actual['current_limit'] = self._number('CURR?')
            return actual
        return self._confirmed_channel(channel,'set_current',f'CURR {value:.3f}',{'current_limit':value},readback,
            lambda req,act: abs(act['current_limit']-value)<=.005+1e-12,logger)

    def output_control(self, channel, state, logger=None):
        try:
            self._channel(channel)
            if state not in (False,True,0,1):raise ValueError('Output state must be ON/OFF')
        except (ValueError,TypeError) as exc:
            return self._invalid('output_control',{'channel':channel,'output':str(state)},exc,logger)
        state = bool(state)
        def expected():
            result = {'output':state}
            if state:
                result['voltage'] = self._number('VOLT?')
                if result['voltage']>self.MAX_VOLTAGES[channel]:raise ValueError('Invalid voltage setpoint')
            return result
        def readback(actual):
            actual['output'] = self.read_output_state()
            if state:actual['measured_voltage'] = self._number('MEAS:VOLT?')
            return actual
        return self._confirmed_channel(channel,'output_control',f'OUTP {int(state)}',expected,readback,
            lambda req,act: act['output']==state and
                (not state or abs(act['measured_voltage']-req['voltage'])<=self.output_voltage_tolerance+1e-12),logger)

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
