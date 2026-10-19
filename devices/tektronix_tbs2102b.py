"""TBS2000B commands; LAN uses VXI-11 through PyVISA-py."""
import math
import threading


class TBS2102B:
    def __init__(self, ip=''):
        self.ip = ip
        self.is_connected = False
        self.identity = ''
        self.last_error = ''
        self.resource = self.rm = None
        self._lock = threading.RLock()

    def connect(self):
        with self._lock:
            self.disconnect()
            try:
                import pyvisa
                self.rm = pyvisa.ResourceManager('@py')
                self.resource = self.rm.open_resource(f'TCPIP0::{self.ip}::inst0::INSTR')
                self.resource.timeout = 10000
                self.resource.read_termination = '\n'
                self.resource.write_termination = '\n'
                self.identity = self.resource.query('*IDN?').strip()
                if 'TBS2102B' not in self.identity.upper():
                    raise ValueError(f'型号不匹配: {self.identity}')
                self.resource.write('HEADER OFF')
                self.is_connected = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def disconnect(self):
        with self._lock:
            resources = self.resource, self.rm
            self.resource = self.rm = None
            self.is_connected = False
            for resource in resources:
                if resource:
                    try:
                        resource.close()
                    except Exception as exc:
                        self.last_error = str(exc)

    def command(self, command):
        with self._lock:
            if not self.is_connected:
                raise ConnectionError('示波器未连接')
            if '\n' in command or '\r' in command:
                raise ValueError('请输入单行指令')
            try:
                if '?' in command:
                    return self.resource.query(command).strip()
                self.resource.write(command)
                return '已发送'
            except Exception:
                self.disconnect()
                raise

    def apply(self, commands):
        with self._lock:
            for command in commands:
                self.command(command)
            esr = int(self.command('*ESR?'))
            if esr & 60:
                raise ValueError(f'仪表报错: {self.command("ALLEv?")}；部分设置可能已生效')
            return '已应用；' + self.command('TRIGger:STATE?')

    def trigger(self, kind, source, mode, level, slope, coupling, holdoff,
                polarity, when, width, lower, upper):
        if not all(math.isfinite(float(value)) for value in (level, holdoff, width, lower, upper)):
            raise ValueError('触发参数必须为有限数值')
        if kind not in ('EDGE', 'WIDTH', 'RUNT') or source not in ('CH1', 'CH2', 'LINE'):
            raise ValueError('触发类型或来源错误')
        if not 20e-9 <= holdoff <= 8 or width < 1e-9:
            raise ValueError('释抑时间范围20ns–8s，脉宽至少1ns')
        commands = [f'TRIG:A:MODE {mode}', f'TRIG:A:HOLDOFF:TIME {holdoff:.12g}']
        if kind == 'EDGE':
            commands += ['TRIG:A:TYPE EDGE', f'TRIG:A:EDGE:SOURCE {source}',
                         f'TRIG:A:EDGE:SLOPE {slope}', f'TRIG:A:EDGE:COUPLING {coupling}']
        else:
            if kind == 'RUNT' and (source == 'LINE' or lower >= upper):
                raise ValueError('欠幅触发仅支持CH1/CH2，下阈值必须小于上阈值')
            commands += ['TRIG:A:TYPE PULSE', f'TRIG:A:PULSE:CLASS {kind}']
            prefix = 'TRIG:A:PULSE:WIDTH' if kind == 'WIDTH' else 'TRIG:A:RUNT'
            commands += [f'TRIG:A:PULSE:SOURCE {source}' if kind == 'WIDTH' else f'{prefix}:SOURCE {source}',
                         f'{prefix}:POLARITY {polarity}', f'{prefix}:WHEN {when}']
            if when != 'OCCURS':
                commands += [f'{prefix}:WIDTH {width:.12g}']
            if kind == 'RUNT':
                commands += [f'TRIG:A:LOWERTHRESHOLD:{source} {lower:.12g}',
                             f'TRIG:A:UPPERTHRESHOLD:{source} {upper:.12g}']
        if source != 'LINE' and kind != 'RUNT':
            commands.append(f'TRIG:A:LEVEL:{source} {level:.12g}')
        with self._lock:
            status=self.apply(commands)
            requested={}
            if source!='LINE':
                if kind=='RUNT':
                    requested={'lower':lower,'upper':upper}
                else:
                    requested={'level':level}
            return self.read_trigger_settings(kind,source,requested,status)

    def read_trigger_settings(self,kind,source,requested=None,status='已回读'):
        with self._lock:
            readings={}
            if source!='LINE':
                fields=({'lower':'LOWERTHRESHOLD','upper':'UPPERTHRESHOLD'}
                        if kind=='RUNT' else {'level':'LEVEL'})
                for name,command in fields.items():
                    actual=float(self.command(f'TRIG:A:{command}:{source}?'))
                    if not math.isfinite(actual):
                        raise ValueError('触发电平回读无效')
                    readings[name]=actual
            return {'type':'trigger_readback','source':source,'kind':kind,
                    'requested':requested or {},'actual':readings,'status':status,
                    'all_settings':self.command('TRIG:A?')}

    def waveform(self, channel):
        if channel not in ('CH1', 'CH2'):
            raise ValueError('波形通道错误')
        with self._lock:
            if self.command(f'SELECT:{channel}?').upper() not in ('1','ON'):
                raise ValueError(f'{channel} 未开启显示，请先在通道设置页开启并应用，再读取波形')
            running=self.command('ACQ:STATE?').upper() not in ('0','OFF')
            previous={name:self.command(f'DATA:{name}?')
                      for name in ('SOURCE','ENCDG','START','STOP')}
            try:
                if running:
                    self.command('ACQ:STATE OFF')
                    if self.command('*OPC?')!='1':
                        raise ValueError('仪器未确认停止采集')
                for command in (f'DATA:SOURCE {channel}', 'DATA:ENCDG ASCII', 'DATA:START 1'):
                    self.command(command)
                record=int(self.command('WFMOUTPRE:RECORDLENGTH?'))
                if record<=0:raise ValueError('没有可读取的波形记录，请先采集信号')
                self.command(f'DATA:STOP {record}')
                count=int(self.command('WFMOUTPRE:NR_PT?'))
                if not 0<count<=record:raise ValueError('波形有效点数异常')
                factors=[float(self.command(f'WFMOUTPRE:{name}?'))
                         for name in ('XINCR','XZERO','YMULT','YOFF','YZERO','PT_OFF')]
                samples=self.resource.query_ascii_values('CURVE?')
                if len(samples)!=count:
                    raise ValueError(f'波形点数不一致：{len(samples)}/{count}')
                dx,x0,ym,yo,yz,pt_off=factors
                points=[(x0+(i-pt_off)*dx,(value-yo)*ym+yz)
                        for i,value in enumerate(samples)]
                if not all(math.isfinite(x) and math.isfinite(y) for x,y in points):
                    raise ValueError('波形数据无效')
                return points
            finally:
                if self.is_connected:
                    # Resume the previous acquisition mode even when reading fails.
                    try:
                        for name,value in previous.items():
                            self.command(f'DATA:{name} {value}')
                    finally:
                        if running and self.is_connected:self.command('ACQ:STATE ON')
