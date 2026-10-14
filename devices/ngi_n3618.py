import socket
import time
import struct
import threading

class NGIN3618:
    """
    NGI N3618 高压直流电源驱动 (通过 SYST:ERR? 判定成功)
    """
    def __init__(self, ip: str, port: int = 5025):
        self.ip = ip
        self.port = port
        self.sock = None
        self.is_connected = False
        self.lock = threading.RLock()
        self.TERMINATOR = "\n"

    def connect(self) -> bool:
        with self.lock:
            if self.is_connected and self.sock:
                return True
            self.disconnect()
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                self.sock.settimeout(2.0)
                self.sock.connect((self.ip, self.port))
                
                # 初始化与清错
                self.send_cmd("*CLS")
                time.sleep(0.1)
                self.send_cmd("*IDN?")
                idn = self.sock.recv(1024).decode().strip()
                
                print(f"[NGI N3618] 联机成功: {idn}")
                self.is_connected = True
                return True
            except Exception as e:
                print(f"[NGI N3618] 连接失败: {e}")
                self.disconnect()
                return False

    def disconnect(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                    self.sock.close()
                except:
                    pass
            self.sock = None
            self.is_connected = False

    def clear_errors(self, logger=None) -> bool:
        """清除设备错误与保护锁定状态"""
        with self.lock:
            if not self.is_connected:
                if not self.connect():
                    return False
            try:
                self.send_cmd("*CLS", logger)
                self.send_cmd("PROT:CLE", logger)
                return True
            except Exception as e:
                if logger: logger(f"[IP: {self.ip}] [!] 清除错误异常: {e}")
                return False

    def send_cmd(self, cmd: str, logger=None):
        with self.lock:
            if self.sock:
                # 发送前清空未读接收缓冲，防止粘包
                self.sock.setblocking(False)
                try:
                    self.sock.recv(1024)
                except:
                    pass
                self.sock.settimeout(2.0)
                
                full_cmd = f"{cmd}{self.TERMINATOR}"
                if logger: logger(f"[IP: {self.ip}] [TX] {cmd}")
                self.sock.send(full_cmd.encode())

    def check_success(self, logger=None) -> bool:
        """通过查询错误队列判定上一条指令是否执行成功"""
        with self.lock:
            try:
                self.send_cmd("SYST:ERR?", logger)
                res = self.sock.recv(1024).decode().strip()
                if logger: logger(f"[IP: {self.ip}] [RX] {res}")
                is_ok = "0," in res or "No error" in res or res == "0"
                if not is_ok:
                    self.send_cmd("*CLS")
                return is_ok
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 通讯超时/异常: {e}")
                return False

    def set_voltage(self, voltage: float, logger=None) -> bool:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return False
        with self.lock:
            try:
                self.send_cmd(f"VOLT {voltage:.3f}", logger)
                return self.check_success(logger)
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 设置电压异常: {e}")
                return False

    def set_current(self, current: float, logger=None) -> bool:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return False
        with self.lock:
            try:
                self.send_cmd(f"CURR {current:.3f}", logger)
                return self.check_success(logger)
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 设置电流异常: {e}")
                return False

    def output_control(self, state: bool, logger=None) -> bool:
        for attempt in range(2):
            if not self.is_connected:
                if not self.connect():
                    if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接 (Attempt {attempt+1})")
                    if attempt == 1: return False
                    time.sleep(0.5)
                    continue
            with self.lock:
                try:
                    if state:
                        self.send_cmd("*CLS", logger)
                    cmd = "OUTP ON" if state else "OUTP OFF"
                    self.send_cmd(cmd, logger)
                    if self.check_success(logger):
                        time.sleep(0.3)
                        self.send_cmd("OUTP?", logger)
                        res = self.sock.recv(1024).decode().strip()
                        if logger: logger(f"[IP: {self.ip}] [RX] {res}")
                        
                        is_on = "1" in res or "ON" in res.upper()
                        if is_on == state:
                            return True
                        else:
                            if logger: logger(f"[IP: {self.ip}] [!] 输出控制校验不一致 (期望:{state}, 实测:{is_on})")
                            return True
                    else:
                        if logger: logger(f"[IP: {self.ip}] [!] 输出控制指令报错，准备重试")
                        self.send_cmd("*CLS")
                except Exception as e:
                    self.disconnect()
                    if logger: logger(f"[IP: {self.ip}] [!] 输出控制通讯异常: {e}")
            time.sleep(0.5)
        return False

    def measure_voltage(self, logger=None) -> float:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return -1.0
        with self.lock:
            try:
                self.send_cmd("MEAS:VOLT?", logger)
                data = self.sock.recv(1024).decode().strip()
                if logger: logger(f"[IP: {self.ip}] [RX] {data} V")
                clean_data = "".join(c for c in data if c in "0123456789.eE-+")
                return float(clean_data) if clean_data else 0.0
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 测量电压异常: {e}")
                return -1.0

    def measure_current(self, logger=None) -> float:
        if not self.is_connected:
            if not self.connect():
                if logger: logger(f"[IP: {self.ip}] 错误: HV电源未连接")
                return -1.0
        with self.lock:
            try:
                self.send_cmd("MEAS:CURR?", logger)
                data = self.sock.recv(1024).decode().strip()
                if logger: logger(f"[IP: {self.ip}] [RX] {data} A")
                clean_data = "".join(c for c in data if c in "0123456789.eE-+")
                return float(clean_data) if clean_data else 0.0
            except Exception as e:
                self.is_connected = False
                if logger: logger(f"[IP: {self.ip}] [!] 测量电流异常: {e}")
                return -1.0

