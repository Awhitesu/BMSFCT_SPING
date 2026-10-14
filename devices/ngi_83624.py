import socket
import time
import struct
import threading
import math

class NGI83624:
    """
    NGI 83624 单/全通道 电池模拟器驱动 (基于 SCPI 协议)
    """
    def __init__(self, ip: str, port: int = 7000, max_channels: int = 24):
        self.ip = ip
        self.port = port
        self.max_channels = max_channels
        self.sock = None
        self.is_connected = False
        self._lock = threading.RLock()
        self._fail_count = 0
        self.last_error = ''
        self._rx_buffer = b''

    def connect(self) -> bool:
        with self._lock:
            return self._connect_locked()

    def _connect_locked(self):
        if self.sock:
            self.disconnect()

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            self.sock.settimeout(2.0)
            self.sock.connect((self.ip, self.port))
            self.sock.sendall(b"*IDN?\n")
            self._rx_buffer = b''
            identity = self._read_line()
            if not identity:
                raise TimeoutError("TCP端口可连接，但设备未响应 *IDN? 查询")
            self.is_connected = True
            self._fail_count = 0
            print(f"[*] NGI 83624A ({self.ip}) 通讯握手成功: {identity}")
            return True
        except Exception as e:
            print(f"[NGI83624] 通讯握手失败 ({self.ip}): {e}")
            try:
                if self.sock:
                    self.sock.close()
            except:
                pass
            self.sock = None
            self.is_connected = False
            return False

    def disconnect(self):
        with self._lock:
            self._disconnect_locked()

    def _disconnect_locked(self):
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except:
                pass
        self.sock = None
        self.is_connected = False
        self._rx_buffer = b''

    def _read_line(self):
        while b'\n' not in self._rx_buffer:
            chunk=self.sock.recv(1024)
            if not chunk: raise ConnectionError('设备已断开连接')
            self._rx_buffer+=chunk
            if len(self._rx_buffer)>8192: raise ValueError('SCPI响应过长')
        line,self._rx_buffer=self._rx_buffer.split(b'\n',1)
        return line.decode('ascii').strip()

    def _query(self, command, logger=None):
        if not self._safe_send((command+'\n').encode(),logger=logger):
            raise ConnectionError('查询发送失败')
        try:
            value=self._read_line()
            if logger: logger(f'[IP: {self.ip}] [RX] {value}')
            return value
        except Exception:
            self.disconnect()
            raise

    def read_set_voltage(self, channel, logger=None):
        with self._lock:
            try:
                value=float(self._query(f'SOUR{channel}:VOLT?',logger))
                if not math.isfinite(value): raise ValueError('设定值无效')
                return value
            except Exception as exc:
                self.last_error=str(exc)
                return None

    def _ensure_connected(self) -> bool:
        if self.is_connected and self.sock:
            return True
        return self.connect()

    def _safe_send(self, cmd_bytes: bytes, retries: int = 2, logger=None) -> bool:
        for i in range(retries + 1):
            try:
                if not self._ensure_connected(): continue
                self._clear_buffer()
                cmd_str = cmd_bytes.decode().strip()
                if logger: logger(f"[IP: {self.ip}] [TX] {cmd_str}")
                self.sock.sendall(cmd_bytes)
                return True
            except Exception as e:
                self.last_error=str(e)
                print(f"[NGI83624] 发送失败 (尝试 {i+1}): {e}")
                self.is_connected = False
                time.sleep(0.2)
        return False

    def set_voltage(self, channel: int, voltage: float, logger=None) -> bool:
        if channel==0:
            return self.set_voltages(voltage,logger=logger)
        if not 1<=channel<=self.max_channels or not math.isfinite(voltage) or voltage<0:
            self.last_error='通道或电压无效'
            return False
        with self._lock:
            try:
                cmd = f"SOUR{channel}:VOLT {voltage}\n"
                
                for attempt in range(3):
                    if not self._safe_send(cmd.encode(),logger=logger): continue
                    time.sleep(0.05)
                    actual=self.read_set_voltage(channel,logger)
                    if actual is not None and abs(actual-voltage)<=0.001:
                        self.last_error=''
                        return True
                    self.last_error=f'CH{channel}设压校验失败：目标{voltage}V，回读{actual}V'
                    if logger: logger(f'[IP: {self.ip}] {self.last_error}，重试{attempt+1}/3')
                return False
            except Exception as exc:
                self.last_error=str(exc)
                return False

    def _query_channels(self,template,channels,logger=None,prefix=''):
        # Send documented per-channel queries together; consume one full response per query.
        commands=prefix+''.join(template.format(ch=ch)+'\n' for ch in channels)
        if not self._safe_send(commands.encode(),logger=logger): raise ConnectionError('批量查询发送失败')
        try:
            return [self._read_line() for ch in channels]
        except Exception:
            self.disconnect()
            raise

    def set_voltages(self,voltage,channels=None,logger=None):
        channels=list(range(1,self.max_channels+1)) if channels is None else list(channels)
        if not channels or len(set(channels))!=len(channels) or any(not 1<=ch<=self.max_channels for ch in channels) or not math.isfinite(voltage) or voltage<0:
            self.last_error='通道或电压无效'; return False
        with self._lock:
            try:
                selection=','.join(map(str,channels))
                if not self._safe_send(f'SOUR:VOLT {voltage:g}(@{selection})\n'.encode(),logger=logger): return False
                replies=self._query_channels('SOUR{ch}:VOLT?',channels,logger)
                failed=[]
                for ch,reply in zip(channels,replies):
                    try:
                        actual=float(reply); ok=math.isfinite(actual) and abs(actual-voltage)<=.001
                    except ValueError: ok=False
                    if not ok and not self.set_voltage(ch,voltage,logger): failed.append(ch)
                self.last_error='' if not failed else '设压失败通道：'+','.join(f'CH{ch}' for ch in failed)
                return not failed
            except Exception as exc:
                self.last_error=f'批量设压/校验失败：{exc}'; return False

    def output_controls(self,state,channels=None,logger=None):
        channels=list(range(1,self.max_channels+1)) if channels is None else list(channels)
        if not channels or any(not 1<=ch<=self.max_channels for ch in channels): return False
        with self._lock:
            try:
                selection=','.join(map(str,channels))
                replies=self._query_channels('OUTP{ch}:ONOFF?',channels,logger,prefix=f'OUTP:ONOFF {int(state)}(@{selection})\n')
                if not self._verify_output_transition(channels,state,logger,initial=replies):
                    return False
                return self._verify_output_voltage(channels,logger) if state else True
            except Exception as exc:
                self.last_error=f'批量输出/校验失败：{exc}'; return False

    def read_voltage_snapshot(self,channels):
        with self._lock:
            measured=self._query_channels('MEAS{ch}:VOLT?',channels)
            settings=self._query_channels('SOUR{ch}:VOLT?',channels)
            outputs=self._query_channels('OUTP{ch}:ONOFF?',channels)
            result=[]
            for measured_value,setting,output in zip(measured,settings,outputs):
                voltage=float(measured_value);target=float(setting)
                if not math.isfinite(voltage) or not math.isfinite(target):raise ValueError('批量电压响应无效')
                state=output.upper()
                if state not in ('1','ON','TRUE','0','OFF','FALSE'):raise ValueError('输出状态响应无效')
                result.append((voltage,target,state in ('1','ON','TRUE')))
            return result
    def set_current_limit(self, channel: int, current: float, logger=None):
        if channel==0:
            return all([self.set_current_limit(ch,current,logger) for ch in range(1,self.max_channels+1)])
        if not 1<=channel<=self.max_channels or not math.isfinite(current) or current<0:
            self.last_error='通道或限流值无效'; return False
        return self._set_verified_parameter(channel,'OUTCURR',current*1000,0.01,logger)

    def _set_verified_parameter(self,channel,name,value,tolerance,logger=None):
        with self._lock:
            for attempt in range(3):
                try:
                    if not self._safe_send(f'SOUR{channel}:{name} {value:g}\n'.encode(),logger=logger): continue
                    actual=float(self._query(f'SOUR{channel}:{name}?',logger))
                    if math.isfinite(actual) and abs(actual-value)<=tolerance:
                        self.last_error=''; return True
                    self.last_error=f'CH{channel} {name}校验失败：目标{value}，回读{actual}'
                except Exception as exc: self.last_error=str(exc)
                if logger: logger(f'[IP: {self.ip}] {self.last_error}，重试{attempt+1}/3')
            return False

    def set_range(self, channel: int, range_str: str, logger=None) -> bool:
        if channel==0:
            return all([self.set_range(ch,range_str,logger) for ch in range(1,self.max_channels+1)])
        modes={'HIGH':0,'LOW':2,'AUTO':3}
        mode=range_str.upper().strip()
        if not 1<=channel<=self.max_channels or mode not in modes:
            self.last_error='通道或电流量程无效'; return False
        return self._set_verified_parameter(channel,'RANG',modes[mode],0,logger)

    def read_source_settings(self,channel):
        with self._lock:
            try:
                current=float(self._query(f'SOUR{channel}:OUTCURR?'))/1000
                mode=int(self._query(f'SOUR{channel}:RANG?'))
                if not math.isfinite(current) or mode not in (0,2,3): raise ValueError('电流参数响应无效')
                return {'current':current,'range':{0:'HIGH',2:'LOW',3:'AUTO'}[mode]}
            except Exception as exc:
                self.last_error=str(exc); return None

    def output_control(self, channel: int, state: bool, logger=None) -> bool:
        with self._lock:
            return self._output_control_locked(channel,state,logger)

    def _output_control_locked(self,channel,state,logger=None):
        if channel==0:
            return self.output_controls(state,logger=logger)
        if not 1<=channel<=self.max_channels:
            self.last_error='Invalid output channel'
            return False
        try:
            if not self._safe_send(f'OUTP{channel}:ONOFF {int(state)}\n'.encode(),logger=logger):
                return False
            if not self._verify_output_transition([channel],state,logger):
                return False
            return self._verify_output_voltage([channel],logger) if state else True
        except Exception as exc:
            self.last_error=f'CH{channel} output verification failed: {exc}'
            return False

    def _verify_output_transition(self,channels,state,logger=None,initial=None):
        # Relay/output state can lag behind a successfully accepted command.
        # Query until confirmed; do not reset the transition by resending writes.
        deadline=time.monotonic()+1.5
        pending=list(channels)
        replies=initial
        expected=('1','ON','TRUE') if state else ('0','OFF','FALSE')
        while pending:
            if replies is None:
                replies=([self._query(f'OUTP{pending[0]}:ONOFF?',logger)]
                         if len(pending)==1 else
                         self._query_channels('OUTP{ch}:ONOFF?',pending,logger))
            if len(replies)!=len(pending):
                raise ValueError('Incomplete output state replies')
            pending=[ch for ch,reply in zip(pending,replies)
                     if reply.strip().upper() not in expected]
            if not pending:
                self.last_error=''
                return True
            remaining=deadline-time.monotonic()
            if remaining<=0:
                self.last_error=('Output state not confirmed: '+
                                 ','.join(f'CH{ch}' for ch in pending))
                if logger: logger(f'[IP: {self.ip}] {self.last_error}')
                return False
            time.sleep(min(.1,remaining))
            replies=None
        return True

    def _verify_output_voltage(self,channels,logger=None):
        targets=self._query_channels('SOUR{ch}:VOLT?',channels,logger)
        if len(targets)!=len(channels):
            raise ValueError('Incomplete voltage setpoint replies')
        pending=dict(zip(channels,map(float,targets)))
        if any(not math.isfinite(v) or v<0 for v in pending.values()):
            raise ValueError('Invalid voltage setpoint')
        deadline=time.monotonic()+3.0
        latest={}
        while pending:
            local=list(pending)
            measured=self._query_channels('MEAS{ch}:VOLT?',local,logger)
            states=self._query_channels('OUTP{ch}:ONOFF?',local,logger)
            if len(measured)!=len(local) or len(states)!=len(local):
                raise ValueError('Incomplete output verification replies')
            for ch,value,state in zip(local,measured,states):
                actual=float(value)
                latest[ch]=(actual,state)
                if (math.isfinite(actual) and
                        abs(actual-pending[ch])<=0.05 and
                        state.strip().upper() in ('1','ON','TRUE')):
                    del pending[ch]
            if not pending:
                self.last_error=''
                return True
            remaining=deadline-time.monotonic()
            if remaining<=0:
                self.last_error='开启输出未达标（容差±0.05V）：'+ '; '.join(
                    f'CH{ch} 设定{target:g}V，实测{latest[ch][0]:g}V，状态{latest[ch][1]}'
                    for ch,target in pending.items())
                if logger: logger(f'[IP: {self.ip}] {self.last_error}')
                return False
            time.sleep(min(.1,remaining))

    def read_output_state(self, channel: int, logger=None):
        with self._lock:
            if not self._ensure_connected():
                if logger: logger(f"[IP: {self.ip}] 错误: 模拟器未连接")
                return None

            def query_one(ch: int):
                cmd = f"OUTP{ch}:ONOFF?\n"
                if logger: logger(f"[IP: {self.ip}] [TX] {cmd.strip()}")
                data = self._query(cmd.strip(),logger)
                if logger: logger(f"[IP: {self.ip}] [RX] {data}")
                text = data.upper()
                if text in ("1", "ON", "TRUE"):
                    return True
                if text in ("0", "OFF", "FALSE"):
                    return False
                try:
                    return bool(int(float(text.split(",")[0])))
                except Exception:
                    return None

            try:
                if channel == 0:
                    states = [query_one(ch) for ch in range(1, self.max_channels + 1)]
                    if any(state is None for state in states):
                        return None
                    return all(states)
                return query_one(channel)
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 读取输出状态异常: {e}")
                return None

    def _clear_buffer(self):
        if not self.sock: return
        self._rx_buffer=b''
        self.sock.setblocking(False)
        try:
            while True:
                if not self.sock.recv(4096): raise ConnectionError('设备已断开连接')
        except BlockingIOError: pass
        finally: self.sock.settimeout(2.0)

    def measure_voltage(self, channel: int, logger=None) -> float:
        with self._lock:
            if not self._ensure_connected():
                if logger: logger(f"[IP: {self.ip}] 错误: 模拟器未连接")
                return -1.0
            try:
                if channel == 0: return -1.0
                cmd = f"MEAS{channel}:VOLT?\n"
                if logger: logger(f"[IP: {self.ip}] [TX] {cmd.strip()}")
                data = self._query(cmd.strip(),logger)
                if logger: logger(f"[IP: {self.ip}] [RX] {data} V")
                val = float(data)
                if not math.isfinite(val): raise ValueError('测量电压无效')
                self._fail_count = 0  # 成功时清零计数器
                return val
            except Exception as e:
                self._fail_count += 1
                if self._fail_count >= 5:
                    self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 读取设定电压异常 (连续失败 {self._fail_count} 次): {e}")
                return -1.0

    def measure_current(self, channel: int, logger=None) -> float:
        with self._lock:
            if not self._ensure_connected():
                if logger: logger(f"[IP: {self.ip}] 错误: 模拟器未连接")
                return -1.0
            try:
                if channel == 0: return -1.0
                cmd = f"MEAS{channel}:CURR?\n"
                if logger: logger(f"[IP: {self.ip}] [TX] {cmd.strip()}")
                data = self._query(cmd.strip(),logger)
                if logger: logger(f"[IP: {self.ip}] [RX] {data} mA")
                val = float(data)
                self._fail_count = 0  # 成功时清零计数器
                return val
            except Exception as e:
                self._fail_count += 1
                if self._fail_count >= 5:
                    self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 读取设定电流异常 (连续失败 {self._fail_count} 次): {e}")
                return -1.0
