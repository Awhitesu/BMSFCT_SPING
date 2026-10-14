import math
from .keysight_34461a import Keysight34461A


class DG2052(Keysight34461A):
    MODEL = 'DG2052'
    WAVEFORMS = {'正弦波': 'SIN', '方波': 'SQU', '三角/锯齿波': 'RAMP', '脉冲': 'PULS', '噪声': 'NOIS', '直流': 'DC'}

    def __init__(self, ip='', port=5555):
        super().__init__(ip, port)
        self.connect_mode='AUTO'
        self.transport=''
        self._visa=None
        self._rm=None

    def disconnect(self):
        with self._lock:
            instrument,self._visa=self._visa,None
            manager,self._rm=self._rm,None
            try:
                if instrument: instrument.close()
            finally:
                try:
                    if manager: manager.close()
                finally:
                    super().disconnect()
                    self.transport=''

    def connect(self):
        with self._lock:
            self.disconnect()
            errors=[]
            if self.connect_mode in ('AUTO','VISA'):
                resource=self.ip if self.ip.upper().startswith('TCPIP') else f'TCPIP0::{self.ip}::inst0::INSTR'
                for backend in ('','@py'):
                    try:
                        import pyvisa
                        self._rm=pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
                        self._visa=self._rm.open_resource(resource,open_timeout=5000)
                        self._visa.timeout=5000
                        self._visa.write_termination='\n'; self._visa.read_termination='\n'
                        self.is_connected=True
                        self.identity=self.query('*IDN?')
                        if self.MODEL not in self.identity.upper(): raise ValueError(f'设备型号不匹配：{self.identity}')
                        self.transport=f'VISA / {resource}'+(' / pyvisa-py' if backend else '')
                        self.last_error=''
                        return True
                    except Exception as exc:
                        errors.append(f'VISA{backend}: {exc}')
                        self.disconnect()
            if self.connect_mode in ('AUTO','SOCKET') and not self.ip.upper().startswith('TCPIP'):
                if super().connect():
                    self.transport=f'TCP Socket / {self.ip}:{self.port}'
                    return True
                errors.append(f'TCP Socket {self.ip}:{self.port}: {self.last_error}')
            self.last_error='；'.join(errors) or '连接方式或资源地址无效'
            return False

    def command(self,text):
        with self._lock:
            if not self._visa:
                if not self.is_connected:raise ConnectionError('DG2052未连接，请先连接设备')
                return super().command(text)
            text=text.strip()
            if not text or '\n' in text or '\r' in text:raise ValueError('请输入单行SCPI指令')
            try:
                if '?' in text:return self._visa.query(text).strip()
                self._visa.write(text);return '已发送'
            except Exception as exc:
                self.last_error=str(exc);self.disconnect();raise

    @staticmethod
    def channel(ch):
        if ch not in (1, 2):
            raise ValueError('DG2052 仅支持 CH1、CH2')

    def check_error(self):
        error = self.query(':SYST:ERR?')
        if int(error.split(',', 1)[0]) != 0:
            raise ValueError(f'仪表拒绝指令：{error}')

    def configure(self, ch, waveform, frequency, amplitude, offset, load):
        self.channel(ch)
        wave = self.WAVEFORMS[waveform]
        numbers = (frequency, amplitude, offset)
        if not all(math.isfinite(float(n)) for n in numbers):
            raise ValueError('参数必须为有限数值')
        if not 1e-6 <= frequency <= 50e6 or amplitude <= 0 or load not in ('50', 'INF'):
            raise ValueError('频率、幅值或负载参数错误')
        limit = 5 if load == '50' else 10
        if abs(offset) + amplitude / 2 > limit:
            raise ValueError('幅值与偏置组合超出输出范围')
        with self._lock:
            self.check_error()
            # Output remains under explicit user control; configure is not APPLy.
            commands = [f':OUTP{ch}:IMP {load}', f':SOUR{ch}:FUNC {wave}',
                        f':SOUR{ch}:VOLT:UNIT VPP', f':SOUR{ch}:VOLT:OFFS 0',
                        f':SOUR{ch}:VOLT {amplitude:.12g}', f':SOUR{ch}:VOLT:OFFS {offset:.12g}']
            if wave not in ('NOIS', 'DC'):
                commands.append(f':SOUR{ch}:FREQ {frequency:.12g}')
            for command in commands:
                self.command(command)
                self.check_error()
            return self.read_channel(ch)

    def output_control(self, ch, state):
        self.channel(ch)
        with self._lock:
            self.command(f':OUTP{ch} {"ON" if state else "OFF"}')
            self.check_error()
            actual = self.query(f':OUTP{ch}?').upper()
            if actual not in ({'1', 'ON'} if state else {'0', 'OFF'}):
                raise ValueError('输出状态回读不一致')
            return self.read_channel(ch)

    def read_channel(self, ch):
        self.channel(ch)
        with self._lock:
            return {name: self.query(command) for name, command in (
                ('波形', f':SOUR{ch}:FUNC?'), ('频率(Hz)', f':SOUR{ch}:FREQ?'),
                ('幅值', f':SOUR{ch}:VOLT?'), ('幅值单位', f':SOUR{ch}:VOLT:UNIT?'),
                ('偏置(V)', f':SOUR{ch}:VOLT:OFFS?'), ('负载(Ω)', f':OUTP{ch}:IMP?'),
                ('输出', f':OUTP{ch}?'))}
