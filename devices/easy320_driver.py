from pymodbus.client import ModbusTcpClient
from pymodbus.framer import FramerType
import time
import logging

# 配置基础日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Easy320Driver")

import threading

class Easy320Controller:
    """
    Easy320 继电器控制板驱动 (独立版)
    通道按零起始偏移寻址，通讯协议为 Modbus TCP。
    """
    def __init__(self, ip: str, port: int = 502, slave_id: int = 1):
        self.ip = ip
        self.port = port
        self.slave_id = slave_id
        self.client = ModbusTcpClient(
            self.ip, 
            port=self.port, 
            framer=FramerType.SOCKET,
            timeout=3
        )
        self.lock = threading.Lock()
        self.is_connected = False
        self.start_address = 0xFC08 

    def connect(self) -> bool:
        """建立连接并执行握手验证"""
        import socket
        with self.lock:
            # 1. 物理探测 IP 是否可达 (超时 0.8s)
            try:
                with socket.create_connection((self.ip, self.port), timeout=0.8):
                    pass
            except:
                self.is_connected = False
                return False

            # 2. 建立 Modbus 连接
            try:
                # 重新初始化 client 以更新超时设置
                self.client.close()
                self.client.timeout = 1.0
                
                if self.client.connect():
                    result = self.client.read_coils(address=self.start_address, count=1, device_id=self.slave_id)
                    if result and not result.isError():
                        self.is_connected = True
                        logger.info(f"成功连接并验证 Easy320: {self.ip}:{self.port}")
                        return True
                    else:
                        self.client.close()
                        self.is_connected = False
                        return False
                return False
            except Exception as e:
                logger.error(f"连接发生异常: {e}")
                self.is_connected = False
                return False

    def disconnect(self):
        """断开连接"""
        with self.lock:
            self.client.close()
            self.is_connected = False

    def write_relay(self, index: int, state: bool) -> bool:
        """控制单个继电器开关 (带自动重试与物理回读)"""
        if not isinstance(index,int) or not 0<=index<=0xffff-self.start_address:
            raise ValueError('DO通道地址无效')
        address = self.start_address + index
        
        for attempt in range(2):
            if not self.is_connected:
                if not self.connect():
                    if attempt == 1: return False
                    time.sleep(0.1)
                    continue

            with self.lock:
                try:
                    # 模拟调试 TAB 的操作原理：增加微小延时
                    time.sleep(0.05)
                    
                    # 优先尝试线圈写入 (FC05)
                    result = self.client.write_coil(address=address, value=state, device_id=self.slave_id)
                    if not (result and not result.isError()):
                        # 备选尝试寄存器写入 (FC06)
                        result = self.client.write_register(address=address, value=1 if state else 0, device_id=self.slave_id)
                        
                    if result and not result.isError():
                        # 增加物理回读校验
                        time.sleep(0.05)
                        check = self.client.read_coils(address=address, count=1, device_id=self.slave_id)
                        if not check.isError() and check.bits[0] == state:
                            return True
                        else:
                            logger.warning(f"Easy320 继电器 {index} 写入成功但回读校验失败，准备重试")
                    else:
                        logger.warning(f"Easy320 继电器 {index} 写入失败，准备重连重试")
                        
                    # 失败后主动断开触发重连
                    self.client.close()
                    self.is_connected = False
                except Exception as e:
                    logger.error(f"继电器控制异常: {e}")
                    self.client.close()
                    self.is_connected = False
            
            time.sleep(0.1)
            
        return False

    def read_register(self, address: int = 1, count: int = 1) -> list:
        """读取指定地址的 PLC 保持寄存器 (FC03, 用于开始测试等触发信号位)"""
        if not self.is_connected:
            return []
        with self.lock:
            # 候选物理地址: 1-based (address), 0-based (address-1), 以及 Easy320 偏移
            addrs_to_try = [address]
            if address > 0:
                addrs_to_try.append(address - 1)
            addrs_to_try.append(self.start_address + address - 1)

            for addr in addrs_to_try:
                # 兼容 device_id 与 slave 参数
                for p_name in ["device_id", "slave"]:
                    try:
                        kwargs = {p_name: self.slave_id}
                        res = self.client.read_holding_registers(address=addr, count=count, **kwargs)
                        if res and not res.isError() and hasattr(res, 'registers'):
                            return res.registers
                    except Exception:
                        pass

                # 备用：若保持寄存器无响应，尝试线圈 (FC01)
                for p_name in ["device_id", "slave"]:
                    try:
                        kwargs = {p_name: self.slave_id}
                        res = self.client.read_coils(address=addr, count=count, **kwargs)
                        if res and not res.isError() and hasattr(res, 'bits'):
                            return [1 if b else 0 for b in res.bits[:count]]
                    except Exception:
                        pass
            return []

    def write_register(self, address: int, value: int, verify: bool = True) -> bool:
        """写入 PLC 保持寄存器（FC06），可选回读校验。"""
        address = int(address)
        value = int(value)
        if not 0 <= address <= 0xFFFF or not 0 <= value <= 0xFFFF:
            raise ValueError("PLC寄存器地址和数值必须在0~65535之间")
        for attempt in range(2):
            if not self.is_connected and not self.connect():
                continue
            with self.lock:
                try:
                    result = None
                    for parameter in ("device_id", "slave"):
                        try:
                            result = self.client.write_register(
                                address=address, value=value, **{parameter: self.slave_id}
                            )
                            if result and not result.isError():
                                break
                        except TypeError:
                            continue
                    if not result or result.isError():
                        raise RuntimeError("PLC寄存器写入无响应")
                    if not verify:
                        return True
                    time.sleep(0.05)
                    for parameter in ("device_id", "slave"):
                        try:
                            checked = self.client.read_holding_registers(
                                address=address, count=1, **{parameter: self.slave_id}
                            )
                            if checked and not checked.isError() and checked.registers:
                                return int(checked.registers[0]) == value
                        except TypeError:
                            continue
                    raise RuntimeError("PLC配方号回读失败")
                except Exception as exc:
                    logger.warning(f"PLC寄存器 {address} 写入失败: {exc}")
                    self.client.close()
                    self.is_connected = False
            if attempt == 0:
                time.sleep(0.1)
        return False

    def read_relays(self, count: int = 32) -> list:
        return self.read_selected_relays(list(range(count)))

    def read_selected_relays(self,indices):
        """Read only configured addresses, preserving the caller's order."""
        indices=list(indices)
        if not indices:return []
        if len(set(indices))!=len(indices) or any(not isinstance(i,int) or not 0<=i<=0xffff-self.start_address for i in indices):
            raise ValueError('DO通道地址无效或重复')
        ordered=sorted(indices);groups=[]
        for index in ordered:
            if groups and index==groups[-1][-1]+1 and len(groups[-1])<125:groups[-1].append(index)
            else:groups.append([index])
        values={}
        for group in groups:
            states=self._read_relay_range(group[0],len(group))
            if len(states)!=len(group):return []
            values.update(zip(group,states))
        return [values[index] for index in indices]

    def _read_relay_range(self,index,count):
        """读取继电器当前状态"""
        if not self.is_connected:
            if not self.connect(): return []
        with self.lock:
            try:
                result = self.client.read_coils(address=self.start_address+index, count=count, device_id=self.slave_id)
                if result and not result.isError():
                    return result.bits[:count]
                
                result = self.client.read_holding_registers(address=self.start_address+index, count=count, device_id=self.slave_id)
                if result and not result.isError():
                    return [bool(r) for r in result.registers]
                return []
            except Exception as e:
                logger.error(f"读取状态异常: {e}")
                return []

    def control_selected_relays(self,indices,state):
        indices=list(indices)
        if not indices:return False
        if len(set(indices))!=len(indices) or any(not isinstance(i,int) or not 0<=i<=0xffff-self.start_address for i in indices):
            raise ValueError('DO通道地址无效或重复')
        for index in indices:
            if not self.write_relay(index,state):return False
        states=self.read_selected_relays(indices)
        return len(states)==len(indices) and all(s==state for s in states)

    def batch_control(self, state: bool, delay: float = 0.05):
        """批量控制所有继电器 (带回读确认)"""
        for attempt in range(2):
            success = True
            for i in range(32):
                if not self.write_relay(i, state):
                    success = False
                    break
                time.sleep(delay)
                
            if success:
                time.sleep(0.1)
                states = self.read_relays()
                if states and len(states) >= 32:
                    if all(s == state for s in states[:32]):
                        return True
                    else:
                        logger.warning(f"Easy320 批量控制完成，但回读校验有不一致通道 (尝试 {attempt+1})")
                else:
                    logger.warning(f"Easy320 批量控制回读失败 (尝试 {attempt+1})")
            
            if attempt == 0:
                logger.warning("Easy320 批量控制准备进行第 2 次重试...")
                time.sleep(0.5)
        return False

# --- 使用示例 ---
if __name__ == "__main__":
    # 请根据实际情况修改 IP 和端口
    DEVICE_IP = "192.168.1.190"
    DEVICE_PORT = 502
    
    ctrl = Easy320Controller(DEVICE_IP, DEVICE_PORT)
    
    if ctrl.connect():
        # 1. 开启第 1 路继电器
        ctrl.write_relay(0, True)
        time.sleep(1)
        
        # 2. 读取所有状态
        states = ctrl.read_relays()
        print(f"当前继电器状态: {states}")
        
        # 3. 关闭第 1 路继电器
        ctrl.write_relay(0, False)
        
        # 4. 批量操作演示
        # ctrl.batch_control(True)
        # time.sleep(2)
        # ctrl.batch_control(False)
        
        ctrl.disconnect()
