import os
import sys
import threading
from pathlib import Path

# Allow python-can to load the bundled 64-bit PCAN-Basic runtime without a
# machine-wide DLL installation.  Keep the directory handle alive for the
# lifetime of the process on Python 3.8+.
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys._MEIPASS)
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PCAN_BASIC_DIR = _PROJECT_ROOT / "PCAN-Basic" / "x64"
_pcan_dll_dir_handle = None
if os.name == "nt" and _PCAN_BASIC_DIR.is_dir():
    os.environ["PATH"] = str(_PCAN_BASIC_DIR) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        _pcan_dll_dir_handle = os.add_dll_directory(str(_PCAN_BASIC_DIR))

from .ngi_83624 import NGI83624
from .simulator_config import simulator_settings
from .ngi_n3618 import NGIN3618
from .itech_it6722a import IT6722A
from .itech_it6333b import IT6333B
from .rigol_dg2052 import DG2052
from .keysight_34461a import Keysight34461A
from .tektronix_tbs2102b import TBS2102B
from .ca550_driver import CA550Controller
from .easy320_driver import Easy320Controller
from .opteeq_sg400 import OpteeQSG400
from .renesas_e2_rfp import RenesasE2Rfp
from .pcan_fd_driver import PCANFDDriver

class DeviceManager:
    """
    设备驱动统一管理类 (单例模式)
    负责管理单通道测试所需的硬件仪器。
    """
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
        self.simulators = []
        self.hv_source = None
        self.dut_power = None
        self.ca550 = None
        self.easy320 = None
        self.opteeq = None
        self.e2_rfp = None
        self.pcan = None
        self.rn_boards = {}
        self._device_step_locks = {}
        self._device_step_guard = threading.RLock()
        
        
        
        self.update_config()


    def update_config(self):
        """根据数据库配置更新/重新初始化设备实例"""
        cfg = {}
        if self.db_manager:
            cfg = self.db_manager.load_sys_config() or {}

        # 旧配置没有使能字段时默认启用，保证升级兼容。
        enable_keys = (
            "hv", "dut_pwr",
            "ca550", "easy320",
            "sim1", "pcan", "opteeq", "e2_rfp"
        )
        self.enabled_devices = {
            key: bool(cfg.get(f"{key}_enabled", False if key in ("opteeq", "e2_rfp") else True))
            for key in enable_keys
        }

        # 1. 模拟电池
        self.simulators = []
        for setting in simulator_settings(cfg):
            sim = NGI83624(setting['ip'], setting['port'], setting['channels'])
            sim.sequence_number = setting['number']
            self.enabled_devices[f"sim{setting['slot']}"] = setting['enabled']
            self.simulators.append(sim)
        # BUG-11修复: 删除后台线程connect，避免与init_all_devices的串行connect产生竞态

        # 2. NGI 高压源
        hv_ip = cfg.get("hv_ip", "192.168.1.190")
        hv_port = int(cfg.get("hv_port", 7000))
        self.hv_source = NGIN3618(hv_ip, hv_port)

        # 4. 被测物供电电源 (DUT Power)
        dut_com = cfg.get("dut_pwr_com", "COM1")
        dut_baudrate = int(cfg.get("dut_pwr_baudrate", 9600))
        self.dut_power = IT6722A(dut_com, dut_baudrate)
        self.it6333b = IT6333B(cfg.get('it6333b_com', ''), cfg.get('it6333b_baudrate', 9600))
        self.enabled_devices['it6333b'] = bool(cfg.get('it6333b_enabled', False))
        self.dg2052 = DG2052(cfg.get('dg2052_ip', ''), cfg.get('dg2052_port', 5555))
        self.dg2052.connect_mode=cfg.get('dg2052_connect_mode','AUTO')
        self.dmm34461=Keysight34461A(cfg.get('dmm34461_ip',''),cfg.get('dmm34461_port',5025))
        self.enabled_devices['dmm34461']=bool(cfg.get('dmm34461_enabled',bool(cfg.get('dmm34461_ip',''))))
        self.enabled_devices['dg2052'] = bool(cfg.get('dg2052_enabled', False))
        self.tbs2102b = TBS2102B(cfg.get('tbs2102b_ip', ''))
        self.enabled_devices['tbs2102b'] = bool(cfg.get('tbs2102b_enabled', False))

        # 6. CA550
        ca_com = cfg.get("ca550_com", "")
        ca_baudrate = int(cfg.get("ca550_baudrate", 9600))
        self.ca550 = CA550Controller(port=ca_com, baudrate=ca_baudrate)

        # 7. Easy320
        e320_ip = cfg.get("easy320_ip", "192.168.1.88")
        e320_port = int(cfg.get("easy320_port", 502))
        self.easy320 = Easy320Controller(e320_ip, e320_port)

        # 力捷丰 SG400 单通道下载器（LAN，固定使用 CH1）
        opteeq_ip = cfg.get("opteeq_ip", "192.168.0.100")
        opteeq_port = int(cfg.get("opteeq_port", 10023))
        self.opteeq = OpteeQSG400(opteeq_ip, opteeq_port)

        # 瑞萨 E2 仿真器（rfp-cli 命令行，无实体长连接）
        self.e2_rfp = RenesasE2Rfp(
            cfg.get("e2_rfp_cli_path", ""),
            cfg.get("e2_device_family", "RH850"),
            cfg.get("e2_interface", ""),
            cfg.get("e2_speed", ""),
            cfg.get("e2_target_voltage", ""),
        )

        # 10. 全局共享 USB PCAN-FD（单通道）
        self.pcan = PCANFDDriver(
            channel=cfg.get("pcan_channel", "PCAN_USBBUS1"),
            nominal_bitrate=int(cfg.get("pcan_nominal_bitrate", 500000)),
            data_bitrate=int(cfg.get("pcan_data_bitrate", 2000000)),
            fd_enabled=bool(cfg.get("pcan_fd_enabled", True)),
            logical_channel=0,
        )
        self.pcan.zlg_sdk_path = cfg.get('zlg_sdk_path') or str(_PROJECT_ROOT / 'zlgcan(20260806)' / 'zlgcan_x64')
        self.pcan.zlg_ip = cfg.get('zlg_ip', '192.168.0.177')
        self.pcan.zlg_port = int(cfg.get('zlg_port', 8000))

    def device_step_lock(self,key):
        with self._device_step_guard:
            return self._device_step_locks.setdefault(key,threading.RLock())

    def get_rn_board(self,key):
        from .rn_board import board_settings,RNBoard
        cfg=self.db_manager.load_sys_config() if self.db_manager else {}
        setting=next((s for s in board_settings(cfg or {}) if s['key']==key),None)
        if not setting or not setting['enabled'] or not setting['ip']:
            raise ValueError(f'{key} 未配置或未启用，请在板卡调试连接配置页保存配置')
        with self._device_step_guard:
            board=self.rn_boards.get(key)
            if board and board.setting!=setting:
                board.disconnect();board=None
            if board is None:
                board=RNBoard(setting);self.rn_boards[key]=board
            return board

    def is_enabled(self, key: str) -> bool:
        return bool(getattr(self, "enabled_devices", {}).get(key, True))

    def _get_sim_and_ch(self, global_ch: int):
        """按照每台固定 24 通道的编号块查找实际通道。"""
        try:
            channel = int(global_ch)
        except (TypeError, ValueError):
            return None, None
        if channel != global_ch or not 1 <= channel <= 144:
            return None, None
        number, local = divmod(channel - 1, 24)
        for slot, sim in enumerate(self.simulators, 1):
            if (self.is_enabled(f"sim{slot}") and sim.sequence_number == number + 1
                    and local < sim.max_channels):
                return sim, local + 1
        return None, None

    def set_voltage(self, global_ch: int, voltage: float, logger=None):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return sim.set_voltage(ch, voltage, logger)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def simulator_channels(self):
        """所有已启用模拟器组成一套设备，按全局通道编号排序。"""
        return sorted(((sim.sequence_number - 1) * 24 + ch, sim, ch)
                      for slot, sim in enumerate(self.simulators, 1)
                      if self.is_enabled(f"sim{slot}")
                      for ch in range(1, sim.max_channels + 1))

    def set_current(self, global_ch: int, current: float, logger=None):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return sim.set_current_limit(ch, current, logger)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def output_control(self, global_ch: int, state: bool, logger=None):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return sim.output_control(ch, state, logger)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def measure_voltage(self, global_ch: int, logger=None) -> float:
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return sim.measure_voltage(ch, logger)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return -1.0

    def measure_current(self, global_ch: int, logger=None) -> float:
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return sim.measure_current(ch, logger)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return -1.0

    def broadcast_voltage(self, voltage: float, logger=None) -> bool:
        from devices.simulator_batch import run_simulator_batch
        result=run_simulator_batch(self.simulator_channels(),lambda sim,chs:sim.set_voltages(voltage,chs,logger))
        if logger:
            for row in result['results']:
                if not row['ok']: logger(str(row))
        return result['ok']

    def broadcast_current(self, current: float, logger=None) -> bool:
        """单设备单通道设置电流。"""
        if logger: logger(f"[*] 全系统同步设置电流限制: {current}A")
        success = True
        attempted = 0
        for i, sim in enumerate(self.simulators):
            if self.is_enabled(f"sim{i+1}"):
                for ch in range(1, sim.max_channels + 1):
                    attempted += 1
                    if not sim.set_current_limit(ch, current, logger):
                        success = False
                        if logger: logger(f'[!] 第{getattr(sim,"sequence_number",i+1)}台CH{ch}限流设置失败: {getattr(sim,"last_error","")}')
        return success and attempted>0

    def broadcast_range(self, range_str: str, logger=None) -> bool:
        """全系统广播设置量程: HIGH / LOW"""
        if logger: logger(f"[*] 全系统同步设置量程: {range_str}")
        success = True
        attempted = 0
        for i, sim in enumerate(self.simulators):
            if self.is_enabled(f"sim{i+1}"):
                for ch in range(1, sim.max_channels + 1):
                    attempted += 1
                    if not sim.set_range(ch, range_str, logger):
                        success = False
                        if logger: logger(f'[!] 第{getattr(sim,"sequence_number",i+1)}台CH{ch}量程设置失败: {getattr(sim,"last_error","")}')
        return success and attempted>0

    def broadcast_output(self, state: bool, logger=None) -> bool:
        from devices.simulator_batch import run_simulator_batch
        result=run_simulator_batch(self.simulator_channels(),lambda sim,chs:sim.output_controls(state,chs,logger))
        if logger:
            for row in result['results']:
                if not row['ok']: logger(str(row))
        return result['ok']

    def init_all_devices(self, logger=None):
        """
        初始化连接所有硬件，遵循方案书要求的顺序启动防浪涌逻辑
        """
        if logger: logger("[*] 开始系统硬件全量初始化...")

        if self.is_enabled("pcan") and self.pcan:
            self.pcan.connect()
            if logger: logger(f"[*] USB PCAN-FD 状态: {'已联机' if self.pcan.is_connected else '离线'} ({self.pcan.channel})")

        if self.is_enabled("opteeq") and self.opteeq:
            self.opteeq.connect()
            state = self.opteeq.query_state() if self.opteeq.is_connected else self.opteeq.last_error
            if logger: logger(f"[*] 力捷丰 SG400 下载器状态: {'已联机' if self.opteeq.is_connected else '离线'} ({state})")

        if self.is_enabled("e2_rfp") and self.e2_rfp:
            self.e2_rfp.connect()
            state = self.e2_rfp.query_state() if self.e2_rfp.is_connected else self.e2_rfp.last_error
            if logger: logger(f"[*] 瑞萨 E2 下载器状态: {'已联机' if self.e2_rfp.is_connected else '离线'} ({state})")
        
        # 1. 基础控制与校准设备初始化
        if self.is_enabled("easy320") and self.easy320: 
            self.easy320.connect()
            if logger: logger(f"[*] Easy320 PLC 状态: {'已联机' if self.easy320.is_connected else '离线'}")
        if self.is_enabled("ca550") and self.ca550: 
            self.ca550.connect()
            if logger: logger(f"[*] CA550 校准源 状态: {'已联机' if self.ca550.is_connected else '离线'}")
        
        # 2. 电源类设备初始化
        if self.is_enabled("hv") and self.hv_source: 
            self.hv_source.connect()
            if logger: logger(f"[*] NGI 高压源 状态: {'已联机' if self.hv_source.is_connected else '离线'}")
        
        if self.is_enabled("dut_pwr") and self.dut_power: 
            self.dut_power.connect()
            if logger: logger(f"[*] IT6722A 被测物供电电源 状态: {'已联机' if self.dut_power.is_connected else '离线'}")
            

        # 3. NGI 83624A 电池模拟器初始化
        if self.is_enabled('it6333b'):
            ok = self.it6333b.connect()
            if logger:
                logger(f"[*] IT6333B 三通道电源: {'已连接' if ok else self.it6333b.last_error}")
        sim_conn_count = 0
        if self.is_enabled('tbs2102b'):
            ok = self.tbs2102b.connect()
            if logger:
                logger(f"[*] TBS2102B 示波器: {'已连接' if ok else self.tbs2102b.last_error}")
        if self.is_enabled('dg2052'):
            ok = self.dg2052.connect()
            if logger:
                logger(f"[*] DG2052 信号发生器: {'已连接' if ok else self.dg2052.last_error}")
        if self.is_enabled('dmm34461'):
            ok=self.dmm34461.connect()
            if logger:logger(f"[*] 34461A 万用表: {'已连接' if ok else self.dmm34461.last_error}")
        enabled_sims = [sim for idx, sim in enumerate(self.simulators, 1) if self.is_enabled(f"sim{idx}")]
        for sim in enabled_sims:
            if sim.connect():
                sim_conn_count += 1
        if logger: logger(f"[*] NGI 83624A 电池模拟器 状态: {sim_conn_count} / {len(enabled_sims)} 已联机")
            

        return True

    def get_all_device_status(self):
        """获取所有设备的连接状态列表，供 UI 展示"""
        status_list = []
        
        # 定义辅助函数
        def add_status(key, name, device, info=""):
            is_conn = getattr(device, "is_connected", False) if device else False
            status_list.append({
                "key": key,
                "name": name,
                "info": info or (getattr(device, "ip", "") if device else ""),
                "status": "已联机" if is_conn else "离线",
                "color": "#28A745" if is_conn else "#DC3545"
            })

        if self.is_enabled("hv") and self.hv_source: add_status("hv", "NGI 高压源", self.hv_source)
        if self.is_enabled("dut_pwr") and self.dut_power: add_status("dut_pwr", "IT6722A 被测物供电电源", self.dut_power, self.dut_power.port)
        if self.is_enabled("ca550") and self.ca550: add_status("ca550", "CA550 校准源", self.ca550, self.ca550.port)
        if self.is_enabled("easy320") and self.easy320: add_status("easy320", "Easy320 PLC", self.easy320)
        if self.is_enabled("pcan") and self.pcan: add_status("pcan", "USB PCAN-FD", self.pcan, self.pcan.channel)
        if self.is_enabled("opteeq") and self.opteeq: add_status("opteeq", "力捷丰 SG400 下载器", self.opteeq, f"{self.opteeq.ip}:{self.opteeq.port}")
        if self.is_enabled("e2_rfp") and self.e2_rfp: add_status("e2_rfp", "瑞萨E2下载器 (rfp-cli)", self.e2_rfp, self.e2_rfp.rfp_cli_path or "自动搜索")
        
        for idx, sim in enumerate(self.simulators, 1):
            if self.is_enabled(f"sim{idx}"):
                add_status(f"sim{idx}", f"{idx}# NGI 83624A 电池模拟器", sim)
        if self.is_enabled('it6333b'):
            add_status('it6333b', 'IT6333B 三通道电源', self.it6333b, self.it6333b.port)
            
        if self.is_enabled('dg2052'):
            add_status('dg2052', 'DG2052 双通道信号发生器', self.dg2052, f'{self.dg2052.ip}:{self.dg2052.port}')
        if self.is_enabled('tbs2102b'):
            add_status('tbs2102b', '泰克 TBS2102B 示波器', self.tbs2102b)
        if self.is_enabled('dmm34461'):
            add_status('dmm34461','34461A 万用表',self.dmm34461,f'{self.dmm34461.ip}:{self.dmm34461.port}')
        return status_list

    def disconnect_all(self):
        """断开所有设备的连接并释放资源 (新增系统安全退出逻辑)"""
        for board in self.rn_boards.values():
            try:board.disconnect()
            except Exception:pass
        # 释放所有电源设备
        power_devices = [
            getattr(self,'dmm34461',None),
            getattr(self, "tbs2102b", None),
            getattr(self, "dg2052", None),
            getattr(self, "it6333b", None),
            getattr(self, "hv_source", None),
            getattr(self, "dut_power", None)
        ]
        for pwr in power_devices:
            if pwr:
                try: pwr.disconnect()
                except: pass

        # 释放其他设备
        if getattr(self, "easy320", None):
            try: self.easy320.disconnect()
            except: pass
        if getattr(self, "ca550", None):
            try: self.ca550.disconnect()
            except: pass
            
        # 释放所有电池模拟器
        for sim in getattr(self, "simulators", []):
            try: sim.disconnect()
            except: pass
        if getattr(self, "pcan", None):
            try: self.pcan.disconnect()
            except: pass
        if getattr(self, "opteeq", None):
            try: self.opteeq.disconnect()
            except: pass
        if getattr(self, "e2_rfp", None):
            try: self.e2_rfp.disconnect()
            except: pass
            
        print("[DeviceManager] 所有硬件设备通讯句柄已安全释放")

    def emergency_stop(self):
        """紧急停止：关闭所有电源输出"""
        print("!!! 触发紧急停止 !!!")
        self.safe_shutdown_outputs()

    def safe_shutdown_outputs(self, channel_id=1, logger=None):
        """尽最大可能关闭所有可能带电的输出，某个设备失败不影响其他设备。"""
        failures = []

        def attempt(name, action):
            try:
                result = action()
                if result is False:
                    failures.append(name)
            except Exception as exc:
                failures.append(f"{name}: {exc}")

        sim, sim_ch = self._get_sim_and_ch(channel_id)
        if sim and getattr(sim, "is_connected", False):
            attempt("电池模拟器", lambda: sim.output_control(sim_ch, False))
        if self.dut_power and getattr(self.dut_power, "is_connected", False):
            attempt("DUT电源", lambda: self.dut_power.output_control(False))
        if self.hv_source and getattr(self.hv_source, "is_connected", False):
            attempt("高压源", lambda: self.hv_source.output_control(False))
        if self.ca550 and getattr(self.ca550, "is_connected", False):
            attempt("CA550归零", lambda: self.ca550.set_source_data(0.0))
            attempt("CA550输出", lambda: self.ca550.set_source_output(0))
        if self.it6333b and self.it6333b.is_connected:
            for channel in (1,2,3):
                attempt(f'IT6333B CH{channel}',lambda ch=channel:self.it6333b.output_control(ch,False))
        if self.dg2052 and self.dg2052.is_connected:
            for channel in (1,2):
                attempt(f'DG2052 CH{channel}',lambda ch=channel:self.dg2052.output_control(ch,False))

        if logger:
            if failures:
                logger("[WARN] 安全断电未全部成功: " + "; ".join(failures))
            else:
                logger("[*] 已执行安全断电")
        return not failures
