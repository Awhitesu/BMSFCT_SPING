import socket
import time
import struct
import threading
import math

class NGI83624:
    """
    NGI 83624 单/全通道 电池模拟器驱动 (基于 SCPI 协议)
    """
    # Unloaded H2.00/S1.42 tests found a LOW-range rebound after an initial
    # in-range reading, as late as 0.69 s after ON. Observe startup and require
    # a fresh, continuous all-channel pass window; never reuse an early pass.
    OUTPUT_MIN_SETTLE_SECONDS = 1.0
    OUTPUT_STABLE_SECONDS = 0.3
    OUTPUT_CONFIRM_TIMEOUT = 3.0
    OUTPUT_POLL_INTERVAL = 0.1
    LIVE_MIN_SETTLE_SECONDS = 0.5
    READ_STABLE_SECONDS = 0.3
    READ_TIMEOUT_SECONDS = 3.0

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
            if "83624" not in identity.upper():
                raise ValueError(f"Unexpected simulator identity: {identity}")
            self.identity = identity
            self.last_error = ""
            self.is_connected = True
            self._fail_count = 0
            print(f"[*] NGI 83624A ({self.ip}) 通讯握手成功: {identity}")
            self._ever_connected = True
            return True
        except Exception as e:
            self.last_error = str(e)
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
            except OSError:
                pass
            finally:
                try:
                    self.sock.close()
                except OSError:
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
                if len(replies) != len(channels):
                    raise ValueError('Incomplete voltage setpoint replies')
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
        if (state not in (True, False) or not channels or len(set(channels)) != len(channels)
                or any(type(ch) is not int or not 1<=ch<=self.max_channels for ch in channels)):
            self.last_error = "Invalid output state or channels"
            return False
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
        channels = list(channels)
        with self._lock:
            measured=self._query_channels('MEAS{ch}:VOLT?',channels)
            settings=self._query_channels('SOUR{ch}:VOLT?',channels)
            outputs=self._query_channels('OUTP{ch}:ONOFF?',channels)
            if any(len(values) != len(channels) for values in (measured, settings, outputs)):
                raise ValueError('Incomplete voltage snapshot replies')
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
        return self._set_verified_parameter(channel,'OUTCURR',current*1000,0.00001,logger)

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

    def _verify_output_voltage(self,channels,logger=None,*,minimum_settle=None,cancelled=None):
        minimum_settle=self.OUTPUT_MIN_SETTLE_SECONDS if minimum_settle is None else minimum_settle
        started=time.monotonic()
        deadline=started+self.OUTPUT_CONFIRM_TIMEOUT
        self.last_output_confirmation={'ok':False,'channels':list(channels)}
        targets=self._query_channels('SOUR{ch}:VOLT?',channels,logger)
        if len(targets)!=len(channels):
            raise ValueError('Incomplete voltage setpoint replies')
        targets=dict(zip(channels,map(float,targets)))
        if any(not math.isfinite(v) or v<0 for v in targets.values()):
            raise ValueError('Invalid voltage setpoint')
        stable_since=None
        rounds=0
        unstable_rounds=0
        latest={}
        while True:
            if cancelled and cancelled():raise RuntimeError('Simulator control cancelled')
            # Recheck EVERY selected channel. A prior pass is not evidence that
            # the channel remained in range while another channel was ramping.
            local=list(targets)
            measured=self._query_channels('MEAS{ch}:VOLT?',local,logger)
            states=self._query_channels('OUTP{ch}:ONOFF?',local,logger)
            if len(measured)!=len(local) or len(states)!=len(local):
                raise ValueError('Incomplete output verification replies')
            pending=[]
            for ch,value,state in zip(local,measured,states):
                actual=float(value)
                latest[ch]=(actual,state)
                if (not math.isfinite(actual) or
                        abs(actual-targets[ch])>0.05 or
                        state.strip().upper() not in ('1','ON','TRUE')):
                    pending.append(ch)
            rounds+=1
            now=time.monotonic()
            if pending:
                stable_since=None
                unstable_rounds+=1
            elif stable_since is None:
                stable_since=now
            elapsed=now-started
            stable=0.0 if stable_since is None else now-stable_since
            if cancelled and cancelled():raise RuntimeError('Simulator control cancelled')
            ok=(not pending and elapsed>=minimum_settle and
                stable>=self.OUTPUT_STABLE_SECONDS and now<=deadline)
            self.last_output_confirmation=dict(ok=ok,channels=local,
                duration_s=round(elapsed,6),stable_s=round(stable,6),
                rounds=rounds,unstable_rounds=unstable_rounds,
                targets=targets.copy(),readings=latest.copy())
            if ok:
                self.last_error=''
                if logger:
                    logger(f'[IP: {self.ip}] Output voltage stable: '
                           f'{self.last_output_confirmation}')
                return True
            remaining=deadline-now
            if remaining<=0:
                detail='; '.join(
                    f'CH{ch} target={targets[ch]:g}V, actual={latest[ch][0]:g}V, state={latest[ch][1]}'
                    for ch in (pending or local))
                self.last_error=(f'Output voltage not stable (tolerance 0.05V; '
                    f'observe>={minimum_settle:g}s, '
                    f'stable>={self.OUTPUT_STABLE_SECONDS:g}s): '+detail)
                if logger:logger(f'[IP: {self.ip}] {self.last_error}')
                return False
            time.sleep(min(self.OUTPUT_POLL_INTERVAL,remaining))

    def apply_confirmed(self,action,channels,value,*,logger=None,cancelled=lambda:False,batch=False):
        """Application transaction: preserve OFF channels; verify live outputs."""
        with self._lock:
            def active():
                if cancelled():raise RuntimeError('Simulator control cancelled')
            def checked(result):
                if result is not True:raise RuntimeError(self.last_error or 'Simulator control not confirmed')
            def failure(channels,reason):
                try:
                    if channels and self.output_controls(False,channels,logger) is not True:
                        reason+='; OFF cleanup failed: '+self.last_error
                except Exception as exc:
                    reason+='; OFF cleanup raised: '+str(exc)
                self.last_error=reason
                return RuntimeError(reason)
            if action=='output' and value is False:
                checked(self.output_controls(False,channels,logger))
                return True
            active()
            if action=='output':
                try:
                    checked(self.output_controls(True,channels,logger))
                    active()
                    return True
                except Exception as exc:
                    raise failure(channels,str(exc)) from exc
            states={ch:self.read_output_state(ch,logger) for ch in channels}
            if any(state is None for state in states.values()):
                raise RuntimeError('Cannot confirm output states before changing parameters')
            enabled=[ch for ch,state in states.items() if state]
            cleanup_channels=list(enabled)
            if action in ('range','current'):
                saved={ch:self.read_source_settings(ch) for ch in channels}
                if any(v is None for v in saved.values()):
                    raise RuntimeError('Cannot read settings before parameter change')
                if action=='range' and value=='LOW' and any(v['current']>.001000001 for v in saved.values()):
                    raise ValueError('LOW range requires current limit <= 1 mA; set limit first')
                if action=='current' and value>.001000001 and any(v['range']=='LOW' for v in saved.values()):
                    raise ValueError('LOW range current limit must not exceed 1 mA')
            try:
                if action=='range':
                    if enabled:checked(self.output_controls(False,enabled,logger))
                    for ch in channels:
                        active()
                        checked(self.set_range(ch,value,logger))
                        checked(self.set_current_limit(ch,saved[ch]['current'],logger))
                    if enabled:
                        active()
                        checked(self.output_controls(True,enabled,logger))
                elif action in ('voltage','voltage_on'):
                    active()
                    checked(self.set_voltage(channels[0],value,logger) if len(channels)==1 and not batch
                            else self.set_voltages(value,channels,logger))
                    active()
                    if action=='voltage_on':
                        cleanup_channels=list(channels)
                        checked(self.output_controls(True,channels,logger))
                    elif enabled:
                        checked(self._verify_output_voltage(enabled,logger,
                            minimum_settle=self.LIVE_MIN_SETTLE_SECONDS,cancelled=cancelled))
                elif action=='current':
                    for ch in channels:
                        active()
                        checked(self.set_current_limit(ch,value,logger))
                    if enabled:
                        checked(self._verify_output_voltage(enabled,logger,
                            minimum_settle=self.LIVE_MIN_SETTLE_SECONDS,cancelled=cancelled))
                else:raise ValueError('Unsupported simulator action')
                active()
                return True
            except Exception as exc:
                raise failure(cleanup_channels,str(exc)) from exc

    def read_stable_snapshot(self,channels,cancelled=lambda:False):
        channels=list(channels)
        if (not channels or len(set(channels))!=len(channels) or
                any(type(ch) is not int or not 1<=ch<=self.max_channels for ch in channels)):
            raise ValueError('Invalid simulator snapshot channels')
        with self._lock:
            deadline=time.monotonic()+self.READ_TIMEOUT_SECONDS
            stable_since=None
            low=high=None
            previous=None
            while True:
                if cancelled():raise RuntimeError('Simulator read cancelled')
                values=self.read_voltage_snapshot(channels)
                now=time.monotonic()
                config=[(target,state) for _,target,state in values]
                volts=[v for v,_,_ in values]
                changed=(stable_since is None or config!=previous or
                    any(max(hi,v)-min(lo,v)>.005 for lo,hi,v in zip(low or volts,high or volts,volts)))
                if changed:
                    stable_since=now
                    low=volts[:];high=volts[:]
                else:
                    low=[min(a,b) for a,b in zip(low,volts)]
                    high=[max(a,b) for a,b in zip(high,volts)]
                previous=config
                if cancelled():raise RuntimeError('Simulator read cancelled')
                if now-stable_since>=self.READ_STABLE_SECONDS and now<=deadline:return values
                if now>=deadline:raise RuntimeError('Simulator snapshot did not stabilize')
                time.sleep(min(self.OUTPUT_POLL_INTERVAL,deadline-now))

    def read_stable(self,channel,quantity,logger=None,cancelled=lambda:False):
        """Return latest stable sample, never average away an out-of-range value.

        Voltage is in V, current in mA. Voltage checks temporal stability.
        Current returns the latest sample after a fresh observation window;
        real load-current changes must be judged by recipe limits, not rejected
        as a transport/settling error. Neither read switches an output.
        """
        if type(channel) is not int or not 1<=channel<=self.max_channels or quantity not in ('voltage','current'):
            raise ValueError('Invalid simulator measurement selection')
        with self._lock:
            if cancelled():raise RuntimeError('Simulator read cancelled')
            self.last_measurement_confirmation=dict(ok=False,channel=channel,quantity=quantity)
            tolerance=.005
            if quantity=='current':
                settings=self.read_source_settings(channel)
                if settings is None:raise RuntimeError(self.last_error or 'Cannot read current range')
                tolerance=None
            started=time.monotonic()
            deadline=started+self.READ_TIMEOUT_SECONDS
            stable_since=None
            low=high=None
            count=0
            while True:
                if cancelled():raise RuntimeError('Simulator read cancelled')
                value=float(self._query(f'MEAS{channel}:'+('VOLT?' if quantity=='voltage' else 'CURR?'),logger))
                if not math.isfinite(value):raise ValueError('Non-finite simulator measurement')
                now=time.monotonic()
                if stable_since is None or (tolerance is not None and max(high,value)-min(low,value)>tolerance):
                    stable_since=now
                    low=high=value
                else:
                    low=min(low,value);high=max(high,value)
                count+=1
                if logger:logger(f'[IP: {self.ip}] CH{channel} {quantity} sample={value:g}, observation={now-stable_since:.3f}s')
                if cancelled():raise RuntimeError('Simulator read cancelled')
                if now-stable_since>=self.READ_STABLE_SECONDS and now<=deadline:
                    self.last_measurement_confirmation=dict(ok=True,channel=channel,quantity=quantity,
                        value=value,tolerance=tolerance,samples=count,duration_s=now-started,
                        window_s=now-stable_since,stable_s=now-stable_since if quantity=='voltage' else None)
                    self.last_error=''
                    return value
                if now>=deadline:
                    self.last_error=f'CH{channel} {quantity} did not stabilize; last={value:g}'
                    raise RuntimeError(self.last_error)
                time.sleep(min(self.OUTPUT_POLL_INTERVAL,deadline-now))

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
                    number = float(text)
                    return bool(number) if number in (0, 1) else None
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
                if not math.isfinite(val): raise ValueError('Invalid current measurement')
                self._fail_count = 0  # 成功时清零计数器
                return val
            except Exception as e:
                self._fail_count += 1
                if self._fail_count >= 5:
                    self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 读取设定电流异常 (连续失败 {self._fail_count} 次): {e}")
                return -1.0
