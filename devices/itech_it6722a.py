import logging
import threading
import time

import serial


logger = logging.getLogger("IT6722A")


class IT6722A:
    """ITECH IT6722A RS232/SCPI driver (9600 8N1)."""

    MAX_VOLTAGE = 80.0
    MAX_CURRENT = 20.0

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self.ser = None
        self.is_connected = False
        self.idn = ""
        self._lock = threading.RLock()

    def connect(self) -> bool:
        with self._lock:
            self.disconnect()
            if not str(self.port).strip():
                logger.warning("IT6722A 串口未配置")
                return False
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )
                time.sleep(0.2)
                self.is_connected = True
                self.idn = self.query("*IDN?")
                if not self.idn:
                    raise RuntimeError("*IDN? 无响应")
                if "6722" not in self.idn.upper():
                    raise RuntimeError(f"型号不匹配: {self.idn}")
                logger.info("IT6722A 已连接: %s, %s", self.port, self.idn)
                return True
            except Exception as exc:
                logger.error("IT6722A 连接失败: %s", exc)
                self.disconnect()
                return False

    def disconnect(self):
        with self._lock:
            if self.ser:
                try:
                    if self.ser.is_open:
                        self.ser.close()
                except Exception:
                    pass
            self.ser = None
            self.is_connected = False

    def _write_line(self, command: str):
        if not self.is_connected or not self.ser or not self.ser.is_open:
            raise RuntimeError("IT6722A 未连接")
        self.ser.write((command.strip() + "\r\n").encode("ascii"))
        self.ser.flush()

    def write(self, command: str) -> bool:
        with self._lock:
            try:
                self._write_line(command)
                return True
            except Exception as exc:
                logger.error("IT6722A 写入失败 [%s]: %s", command, exc)
                self.is_connected = False
                return False

    def query(self, command: str) -> str:
        with self._lock:
            try:
                self.ser.reset_input_buffer()
                self._write_line(command)
                response = self.ser.readline().decode("ascii", errors="ignore").strip()
                return response
            except Exception as exc:
                logger.error("IT6722A 查询失败 [%s]: %s", command, exc)
                self.is_connected = False
                return ""

    @staticmethod
    def _to_float(value: str, default: float = -1.0) -> float:
        try:
            return float(str(value).strip().split(",")[0])
        except (TypeError, ValueError):
            return default

    def _set_and_verify(self, command: str, query: str, target: float, tolerance: float) -> bool:
        if not self.write(command):
            return False
        time.sleep(0.08)
        actual = self._to_float(self.query(query))
        return actual >= 0 and abs(actual - target) <= tolerance

    def set_voltage(self, voltage: float, logger=None) -> bool:
        value = float(voltage)
        if not 0.0 <= value <= self.MAX_VOLTAGE:
            if logger:
                logger(f"IT6722A 电压超出范围: {value}V (0~{self.MAX_VOLTAGE}V)")
            return False
        ok = self._set_and_verify(f"VOLT {value:.3f}", "VOLT?", value, 0.05)
        if logger:
            logger(f"IT6722A 设置电压 {value:.3f}V: {'成功' if ok else '失败'}")
        return ok

    def set_current(self, current: float, logger=None) -> bool:
        value = float(current)
        if not 0.0 <= value <= self.MAX_CURRENT:
            if logger:
                logger(f"IT6722A 电流超出范围: {value}A (0~{self.MAX_CURRENT}A)")
            return False
        ok = self._set_and_verify(f"CURR {value:.3f}", "CURR?", value, 0.02)
        if logger:
            logger(f"IT6722A 设置电流 {value:.3f}A: {'成功' if ok else '失败'}")
        return ok

    def output_control(self, state: bool, logger=None) -> bool:
        target = 1 if state else 0
        if not self.write(f"OUTP {'ON' if state else 'OFF'}"):
            return False
        time.sleep(0.08)
        response = self.query("OUTP?").strip().upper()
        ok = response in ({"1", "ON"} if state else {"0", "OFF"})
        if logger:
            logger(f"IT6722A 输出{'开启' if state else '关闭'}: {'成功' if ok else '失败'}")
        return ok

    def measure_voltage(self, logger=None) -> float:
        value = self._to_float(self.query("MEAS:VOLT?"))
        if logger:
            logger(f"IT6722A 实测电压: {value:.3f}V" if value >= 0 else "IT6722A 电压读取失败")
        return value

    def measure_current(self, logger=None) -> float:
        value = self._to_float(self.query("MEAS:CURR?"))
        if logger:
            logger(f"IT6722A 实测电流: {value:.3f}A" if value >= 0 else "IT6722A 电流读取失败")
        return value

