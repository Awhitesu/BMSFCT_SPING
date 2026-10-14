import serial
import time
import logging

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
        
        import threading
        self._lock = threading.Lock()
        self._last_connect_attempt = 0.0
        self._cooldown_time = 5.0  # 5秒冷却时间

    def connect(self) -> bool:
        """建立串口连接并执行握手验证"""
        if not self.port or self.port.strip() == "":
            logger.warning("CA550 串口未配置，跳过连接。")
            return False
            
        import time
        with self._lock:
            # 连接冷却机制：如果刚尝试过且失败，短时间内不再反复尝试，避免UI卡死
            if time.time() - self._last_connect_attempt < self._cooldown_time:
                return False
                
            self._last_connect_attempt = time.time()
            
            try:
                # 确保先关闭旧连接
                if self.ser and self.ser.is_open:
                    try: self.ser.close()
                    except: pass
                
                # 物理层连接
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=0.5,
                    write_timeout=0.5
                )
                # 激活 DTR/RTS 信号
                self.ser.dtr = True
                self.ser.rts = True
                
                self.is_connected = self.ser.is_open
                if self.is_connected:
                    # 强制等待硬件就绪并执行握手验证
                    time.sleep(0.3)
                    
                    # 内部发送指令，避免死锁
                    self.ser.reset_input_buffer()
                    self.ser.write(b"*IDN?\r\n")
                    self.ser.flush()
                    time.sleep(0.08)
                    response = self.ser.readline().decode("ascii", errors="ignore").strip()
                    if not response:
                        time.sleep(0.1)
                        response = self.ser.read_all().decode("ascii", errors="ignore").strip()
                    
                    idn = response
                    
                    if idn and "ERROR" not in idn:
                        logger.info(f"成功连接并验证 CA550: {self.port} (IDN: {idn})")
                        self._last_connect_attempt = 0.0 # 成功则重置冷却
                        return True
                    else:
                        self.ser.close()
                        self.is_connected = False
                        logger.error(f"CA550 握手失败 (无响应或响应错误): {self.port}")
                        return False
                return False
            except Exception as e:
                self.is_connected = False
                logger.error(f"CA550 连接异常: {e}")
                return False

    def disconnect(self):
        """断开连接"""
        with self._lock:
            if self.ser and self.ser.is_open:
                try: self.ser.close()
                except: pass
            self.is_connected = False
            logger.info(f"已断开物理连接: {self.port}")

    def _send_command(self, cmd: str, skip_conn_check=False) -> str:
        with self._lock:
            if not self.is_connected and not skip_conn_check:
                return "ERROR: Not Connected"
            
            try:
                self.ser.reset_input_buffer()
                # 根据参考项目建议，使用 \r\n (CRLF) 结束符
                full_cmd = f"{cmd}\r\n".encode("ascii")
                self.ser.write(full_cmd)
                self.ser.flush()
                
                # 给予响应缓冲区填充时间
                time.sleep(0.08)
                response = self.ser.readline().decode("ascii", errors="ignore").strip()
                
                # 二次读取尝试
                if not response:
                    time.sleep(0.1)
                    response = self.ser.read_all().decode("ascii", errors="ignore").strip()
                
                if not response:
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
                self.is_connected = False # 发生异常时断开连接，以便重连
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
    def set_source_data(self, value: float) -> bool:
        """
        设置源输出数值 (例如电压/电流)
        带有 2 次重试机制及物理状态回读校验 (SD?)
        """
        for attempt in range(2):
            res = self._send_command(f"SD{value:.4f}")
            if "ERROR" in res:
                time.sleep(0.5)
                continue
            
            time.sleep(0.3) # 增加延时等待硬件动作，避免读到旧状态
            
            check = self._send_command("SD?")
            if "ERROR" in check or not check:
                time.sleep(0.5)
                continue
                
            try:
                # 解析回读结果
                val = float(check.strip())
                # 放宽 CA550 设定值回读的容忍度，避免浮点精度引起偶发判定失败
                if abs(val - value) <= 0.05:
                    return True
                else:
                    logger.warning(f"CA550 输出数值校验失败: 设定 {value}, 回读 {val}，准备重试 (Attempt {attempt+1})")
            except Exception as e:
                logger.warning(f"CA550 输出数值解析异常: {e}, 返回内容: {check}")
                
            time.sleep(0.5)
            
        self.is_connected = False
        return False
    def set_source_output(self, state: int) -> bool:
        """
        设置输出状态 (0=关, 1=开)
        带有 2 次重试机制及物理状态回读校验
        """
        for attempt in range(2):
            res = self._send_command(f"SO{state}")
            if "ERROR" in res:
                time.sleep(0.5)
                continue
            
            time.sleep(0.3) # 增加延时等待硬件继电器动作，避免读到旧状态
            
            check = self._send_command("SO?")
            if "ERROR" in check or not check:
                time.sleep(0.5)
                continue
                
            try:
                # 解析回读结果 (例如回读出 '1' 或 '0')
                val = int(check.strip())
                if val == state:
                    return True
                else:
                    logger.warning(f"CA550 输出控制校验失败，准备重试 (Attempt {attempt+1})")
            except Exception as e:
                logger.warning(f"CA550 输出状态解析异常: {e}, 返回内容: {check}")
                
            time.sleep(0.5)
            
        self.is_connected = False
        return False
    def set_source_0_percent(self, value: float): return self._send_command(f"SL{value:.4f}")
    def set_source_100_percent(self, value: float): return self._send_command(f"SH{value:.4f}")

if __name__ == "__main__":
    dev = CA550Controller("COM5")
    if dev.connect():
        print(f"IDN: {dev.get_idn()}")
        dev.disconnect()
