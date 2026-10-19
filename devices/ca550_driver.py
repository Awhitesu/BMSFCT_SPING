from core.numeric_validation import finite_reading
import serial
import time
import logging
import re

# 配置基础日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CA550Driver")

class CA550Controller:
    """
    横河 (Yokogawa) CA550 校准仪独立驱动版 (V5 - 队列安全与动态参数版)
    默认串口参数: 9600, 7位数据位, 无校验, 1位停止位 (9600, 7N1)
    """
    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = serial.SEVENBITS
        self.parity = serial.PARITY_NONE
        self.stopbits = serial.STOPBITS_ONE
        
        self.ser = None
        self.is_connected = False
        self.identity = ""
        self.last_error = ""
        self._last_connect_port = None
        
        import threading
        self._lock = threading.RLock()
        self._last_connect_attempt = 0.0
        self.last_control = {}
        self.control_statistics = dict(total=0, first_pass=0, recovered=0, failed=0)
        self.control_timeout = 2.0
        self.setpoint_tolerance = 0.0001
        self._cooldown_time = 5.0  # 5秒冷却时间

    def connect(self) -> bool:
        """建立串口连接并执行握手验证"""
        with self._lock:
            self.disconnect()
            if not str(self.port or "").strip():
                self.last_error = "CA550 serial port is not configured"
                return False
            now = time.monotonic()
            if self._last_connect_port == self.port and now - self._last_connect_attempt < self._cooldown_time:
                self.last_error = "CA550 connection retry cooldown"
                return False
            self._last_connect_attempt = now
            self._last_connect_port = self.port
            try:
                self.ser = serial.Serial(port=self.port, baudrate=self.baudrate,
                    bytesize=self.bytesize, parity=self.parity, stopbits=self.stopbits,
                    timeout=0.5, write_timeout=0.5)
                self.ser.dtr = True
                self.ser.rts = True
                if not self.ser.is_open:
                    raise ConnectionError("CA550 serial port did not open")
                time.sleep(0.3)
                idn = self._send_command("*IDN?", skip_conn_check=True)
                if 'ERROR' in idn.upper() or not re.search(r'(?:^|[,\s])CA550(?:$|[,\s])', idn.upper()):
                    raise ConnectionError(f"CA550 identity mismatch or no response: {idn}")
                state = self._send_command("SO?", skip_conn_check=True)
                if state not in ('0', '1'):
                    raise ConnectionError(f"CA550 output state reply is invalid: {state}")
                self.identity = idn
                self.is_connected = True
                self.last_error = ""
                self._last_connect_attempt = 0.0
                logger.info("CA550 connected: %s (%s)", self.port, idn)
                self._ever_connected = True
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                logger.error("CA550 connection failed: %s", self.last_error)
                return False

    def disconnect(self):
        with self._lock:
            port, self.ser = self.ser, None
            self.is_connected = False
            self.identity = ""
            if port:
                try:
                    port.close()
                except Exception as exc:
                    logger.warning("CA550 serial close failed: %s", exc)

    def _send_command(self, cmd: str, skip_conn_check=False) -> str:
        with self._lock:
            if not self.is_connected and not skip_conn_check:
                return "ERROR: Not Connected"
            
            try:
                self.ser.reset_input_buffer()
                # 根据参考项目建议，使用 \r\n (CRLF) 结束符
                full_cmd = f"{cmd}\r\n".encode("ascii")
                if self.ser.write(full_cmd) != len(full_cmd):
                    raise ConnectionError("CA550 serial command was not fully written")
                self.ser.flush()
                
                # 给予响应缓冲区填充时间
                time.sleep(0.08)
                response = self.ser.readline().decode("ascii", errors="ignore").strip()
                
                # 二次读取尝试
                if not response:
                    time.sleep(0.1)
                    response = self.ser.read_all().decode("ascii", errors="ignore").strip()
                
                if not response:
                    if '?' in cmd:
                        raise TimeoutError("CA550 query returned no response")
                    return ""
                
                # 更加通用和健壮的回显与头部剥离逻辑
                cleaned_cmd = cmd.replace("?", "").strip()
                if response.startswith(cleaned_cmd):
                    response = response[len(cleaned_cmd):].strip()
                    if response.startswith("OD"):
                        response = response[2:].strip()
                elif response.startswith("OD"):
                    response = response[2:].strip()
                
                return response.strip()
            except Exception as e:
                logger.error(f"CA550 通讯错误: {e}")
                self.last_error = str(e)
                self.disconnect()
                return f"ERROR: {e}"

    # --- 指令集 ---
    def get_idn(self): return self._send_command("*IDN?")
    def get_sn(self): return self._send_command("BSN?")
    def get_factory_date(self): return self._send_command("BGD?0")
    def get_cal_date(self): return self._send_command("BGD?1")
    def get_battery(self): return self._send_command("PU?")
    def set_backlight(self, state: int): return self._send_command(f"BL{state}")
    def set_24v_power(self, state: int): return self._send_command(f"VO{state}")
    def set_250_resistor(self, state: int): return self._send_command(f"IO{state}")
    def set_break_detection(self, state: int): return self._send_command(f"BU{state}")
    def init_f1_f2(self): return self._send_command("YC")
    def full_reset(self): return self._send_command("RC")
    def set_measure_func(self, func_code: int): return self._send_command(f"MF{func_code}")
    def set_measure_range(self, range_code: int): return self._send_command(f"MR{range_code}")
    def set_measure_state(self, state: int): return self._send_command(f"MO{state}")
    def set_wiring(self, mode: int): return self._send_command(f"WC{mode}")
    def read_measure_data(self, mode: str = "source") -> str:
        """
        mode:
          - "source": 回读设定电压 (SD?)
          - "measure": 回读测量值 (OD0?)
        """
        if mode == "measure" or mode == "measurement":
            return self._send_command("OD0?")
        return self._send_command("SD?")
    def set_source_func(self, func_code: int): return self._send_command(f"SF{func_code}")
    def set_source_range(self, range_code: int): return self._send_command(f"SR{range_code}")
    def _confirm_control(self, action, command, query, target, state=False, tolerance=None):
        started = time.monotonic()
        actual = None
        attempts = 0
        reason = ""
        success = False
        reads = 0
        try:
            timeout = finite_reading(self.control_timeout)
            tolerance = finite_reading(self.setpoint_tolerance if tolerance is None else tolerance)
            if timeout <= 0 or tolerance < 0:
                raise ValueError("Invalid control timeout/tolerance")
            deadline = started + timeout
            # Only explicit command rejection permits one resend. A delayed
            # readback is polled without repeatedly resetting the operation.
            for _ in range(2):
                attempts += 1
                reply = self._send_command(command)
                if "ERROR" not in reply.upper():
                    break
                if not self.is_connected or time.monotonic() >= deadline:
                    raise ConnectionError(reply)
            else:
                raise RuntimeError(reply)
            while True:
                reply = self._send_command(query)
                reads += 1
                if "ERROR" in reply.upper():
                    raise ConnectionError(reply)
                if state:
                    if reply not in ('0', '1'):
                        raise ValueError(f"Invalid output state: {reply}")
                    actual = int(reply)
                    success = actual == target
                else:
                    actual = finite_reading(reply)
                    success = abs(actual - target) <= tolerance + 1e-12
                if success:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Readback mismatch: requested={target}, actual={actual}")
                time.sleep(min(.1, max(0, deadline-time.monotonic())))
        except Exception as exc:
            reason = str(exc)
        self.last_error = reason
        self._record_confirmation(dict(action=action, requested=target, actual=actual,
            success=success, attempts=attempts, elapsed=time.monotonic()-started,
            reason=reason, tolerance=None if state else tolerance),
            first_pass=success and attempts == 1 and reads == 1)
        return success

    def _record_confirmation(self, details, first_pass=False):
        self.control_statistics['total'] += 1
        key = 'failed' if not details['success'] else ('first_pass' if first_pass else 'recovered')
        self.control_statistics[key] += 1
        self.last_control = {**details, 'statistics': dict(self.control_statistics)}
        logger.info("CA550 control confirmation: %s", self.last_control)

    def set_source_data(self, value: float, tolerance=None) -> bool:
        with self._lock:
            try:
                value = finite_reading(value)
            except (ValueError, TypeError) as exc:
                self.last_error = str(exc)
                self._record_confirmation(dict(action='set_source_data', requested=str(value),
                    actual=None, success=False, attempts=0, elapsed=0, reason=str(exc)))
                return False
            return self._confirm_control('set_source_data', f'SD{value:.4f}', 'SD?', value, tolerance=tolerance)

    def set_source_output(self, state: int) -> bool:
        with self._lock:
            if state not in (0, 1):
                self.last_error = "Output state must be 0 or 1"
                self._record_confirmation(dict(action='set_source_output', requested=str(state),
                    actual=None, success=False, attempts=0, elapsed=0, reason=self.last_error))
                return False
            state = int(state)
            return self._confirm_control('set_source_output', f'SO{state}', 'SO?', state, state=True)

    def read_source_output(self):
        with self._lock:
            reply = self._send_command('SO?')
            if reply not in ('0', '1'):
                raise ValueError(f'Invalid output state: {reply}')
            return int(reply)

    def set_source_0_percent(self, value: float): return self._send_command(f"SL{value:.4f}")
    def set_source_100_percent(self, value: float): return self._send_command(f"SH{value:.4f}")

if __name__ == "__main__":
    dev = CA550Controller("COM5")
    if dev.connect():
        print(f"IDN: {dev.get_idn()}")
        dev.disconnect()
