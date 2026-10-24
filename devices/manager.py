import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from core.instrument_control import control_power, shutdown_calibrator, connect_instrument, control_generator
from core.board_control import control_board
from core.simulator_control import control_simulator, read_simulator
from pathlib import Path

# Allow python-can to load the bundled 64-bit PCAN-Basic runtime without a
# machine-wide DLL installation.  Keep the directory handle alive for the
# lifetime of the process on Python 3.8+.
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys._MEIPASS)
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PCAN_BASIC_DIR = _PROJECT_ROOT / "vendor" / "PCAN-Basic" / "x64"
_ZLG_SDK_DEFAULT_DIR = _PROJECT_ROOT / "vendor" / "zlgcan(20260806)" / "zlgcan_x64"


def resolve_zlg_sdk_path(value):
    """返回 ZLG 官方 SDK 目录的绝对路径。

    相对路径按程序根目录解析（兼容省略 vendor 前缀的旧配置），
    并在多个候选中优先选择实际包含 zlgcan.dll 的目录；
    绝对路径视为用户显式选择，原样返回。
    """
    raw = str(value or '').strip()
    if raw:
        path = Path(raw)
        if path.is_absolute():
            return str(path)
        candidates = [_PROJECT_ROOT / path]
        if not path.parts or path.parts[0].lower() != 'vendor':
            candidates.append(_PROJECT_ROOT / 'vendor' / path)
        candidates.append(_ZLG_SDK_DEFAULT_DIR)
        for candidate in candidates:
            if (candidate / 'zlgcan.dll').is_file():
                return str(candidate)
        return str(candidates[0])
    return str(_ZLG_SDK_DEFAULT_DIR)
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
from .infoscan_scanner import InfoscanScanner
from .easy320_driver import Easy320Controller
from .opteeq_sg400 import OpteeQSG400
from .renesas_e2_rfp import RenesasE2Rfp
from .pcan_fd_driver import PCANFDDriver
from .rs_ws_eth6 import RSWSETH6
from .renke_upload import RenkeUploadSensor

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
        from core.dbc_signals import DBCMonitor
        self.dbc_monitor=DBCMonitor()
        
        
        
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
        sensor_class = RenkeUploadSensor if cfg.get('rs_ws_eth6_mode', 'modbus') == 'upload' else RSWSETH6
        old_sensor = getattr(self, 'rs_ws_eth6', None)
        if old_sensor is not None:
            old_sensor.disconnect()
        self.rs_ws_eth6 = sensor_class(cfg.get('rs_ws_eth6_ip', ''),
                                      cfg.get('rs_ws_eth6_port', 2404 if sensor_class is RenkeUploadSensor else 502),
                                      cfg.get('rs_ws_eth6_unit', 1))
        self.enabled_devices['rs_ws_eth6'] = bool(cfg.get('rs_ws_eth6_enabled', False))
        self.enabled_devices['dg2052'] = bool(cfg.get('dg2052_enabled', False))
        self.tbs2102b = TBS2102B(cfg.get('tbs2102b_ip', ''))
        self.enabled_devices['tbs2102b'] = bool(cfg.get('tbs2102b_enabled', False))

        # 6. CA550
        ca_com = cfg.get("ca550_com", "")
        ca_baudrate = int(cfg.get("ca550_baudrate", 9600))
        self.ca550 = CA550Controller(port=ca_com, baudrate=ca_baudrate)
        self.scanner=InfoscanScanner(cfg)
        self.enabled_devices['scanner']=bool(cfg.get('scanner_enabled',False))

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
        self.pcan.zlg_sdk_path = resolve_zlg_sdk_path(cfg.get('zlg_sdk_path'))
        self.pcan.zlg_ip = cfg.get('zlg_ip', '192.168.0.177')
        self.pcan.zlg_port = int(cfg.get('zlg_port', 8000))
        from .rn_board import board_settings
        for setting in board_settings(cfg):
            board = self.rn_boards.get(setting['key'])
            if board is not None and board.setting != setting:
                with board._lock:
                    board.disconnect()
                    board.setting = setting
                    board.ip, board.port = setting['ip'], setting['port']

    def device_step_lock(self,key):
        with self._device_step_guard:
            return self._device_step_locks.setdefault(key,threading.RLock())

    def get_rn_board(self,key,setting=None):
        from .rn_board import board_settings,RNBoard
        if setting is None:
            cfg=self.db_manager.load_sys_config() if self.db_manager else {}
            setting=next((s for s in board_settings(cfg or {}) if s['key']==key),None)
        if not setting or not setting['enabled'] or not setting['ip']:
            raise ValueError(f'{key} 未配置或未启用，请在板卡调试连接配置页保存配置')
        with self._device_step_guard:
            board=self.rn_boards.get(key)
            if board and board.setting!=setting:
                with board._lock:
                    board.disconnect()
                    board.setting = setting
                    board.ip, board.port = setting["ip"], setting["port"]
            if board is None:
                board=RNBoard(setting);self.rn_boards[key]=board
            return board

    def resistor_routes(self):
        from .rn_board import board_settings
        from .resistor_bank import channel_routes
        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
        boards = {s['key']: self.get_rn_board(s['key']) for s in board_settings(cfg or {})
                  if s['kind'] == 'resistor' and s['enabled']}
        return channel_routes(boards)

    def is_enabled(self, key: str) -> bool:
        return bool(getattr(self, "enabled_devices", {}).get(key, True))

    def _get_sim_and_ch(self, global_ch: int):
        """按启用设备序号和实际通道数量连续查找。"""
        try:
            channel = int(global_ch)
        except (TypeError, ValueError):
            return None, None
        if isinstance(global_ch, bool) or channel != global_ch or not 1 <= channel <= 144:
            return None, None
        for number, sim, local in self.simulator_channels():
            if channel == number:
                return sim, local
        return None, None

    def set_voltage(self, global_ch: int, voltage: float, logger=None, cancelled=lambda:False):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return control_simulator(sim,'voltage',[ch],voltage,logger=logger,cancelled=cancelled)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def simulator_channels(self):
        """所有已启用模拟器组成一套设备，按全局通道编号排序。"""
        from .simulator_config import channel_routes
        return channel_routes((sim.sequence_number, sim.max_channels, sim)
                              for slot, sim in enumerate(self.simulators, 1)
                              if self.is_enabled(f"sim{slot}"))

    def set_current(self, global_ch: int, current: float, logger=None, cancelled=lambda:False):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return control_simulator(sim,'current',[ch],current,logger=logger,cancelled=cancelled)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def output_control(self, global_ch: int, state: bool, logger=None, cancelled=lambda:False):
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return control_simulator(sim,'output',[ch],state,logger=logger,cancelled=cancelled)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return False

    def measure_voltage(self, global_ch: int, logger=None, cancelled=lambda:False) -> float:
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return read_simulator(sim,'voltage',ch,logger,cancelled)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return -1.0

    def measure_current(self, global_ch: int, logger=None, cancelled=lambda:False) -> float:
        sim, ch = self._get_sim_and_ch(global_ch)
        if sim: return read_simulator(sim,'current',ch,logger,cancelled)
        if logger: logger(f"错误: 找不到通道 {global_ch} 对应的模拟器")
        return -1.0

    def broadcast_voltage(self, voltage: float, logger=None, cancelled=lambda:False) -> bool:
        from devices.simulator_batch import run_simulator_batch
        result=run_simulator_batch(self.simulator_channels(),lambda sim,chs:control_simulator(sim,'voltage',chs,voltage,logger=logger,batch=True,cancelled=cancelled))
        if logger:
            for row in result['results']:
                if not row['ok']: logger(str(row))
        return result['ok']

    def configure_simulators(self, configuration, channels=None, logger=None, cancelled=lambda:False):
        from core.simulator_control import configure_simulator, simulator_configuration
        from devices.simulator_batch import run_simulator_batch
        configuration = simulator_configuration(configuration)
        routes = self.simulator_channels()
        if channels is not None:
            channels = list(channels)
            if (not channels or len(channels) != len(set(channels)) or
                    any(type(ch) is not int or ch <= 0 for ch in channels)):
                raise ValueError('模拟器通道无效或重复')
            selected = set(channels)
            routes = [route for route in routes if route[0] in selected]
            if {route[0] for route in routes} != selected:
                raise ValueError('模拟器通道未配置')
        result = run_simulator_batch(routes, lambda sim, chs: configure_simulator(
            sim, chs, configuration, logger=logger, cancelled=cancelled))
        if not result['ok']:
            if logger:
                for row in result['results']:
                    if not row['ok']:logger(str(row))
            # A peer may have completed ON before another device failed.
            cleanup = run_simulator_batch(routes, lambda sim, chs: control_simulator(
                sim, 'output', chs, False, logger=logger, batch=True))
            if logger:
                for row in cleanup['results']:
                    if not row['ok']:logger('快捷配置失败后关闭输出未确认：' + str(row))
        return result['ok']

    def broadcast_current(self, current: float, logger=None, cancelled=lambda:False) -> bool:
        return self._broadcast_simulator_parameter('current',current,logger,cancelled)

    def broadcast_range(self, range_str: str, logger=None, cancelled=lambda:False) -> bool:
        return self._broadcast_simulator_parameter('range',range_str,logger,cancelled)

    def _broadcast_simulator_parameter(self,action,value,logger,cancelled):
        from devices.simulator_batch import run_simulator_batch
        result=run_simulator_batch(self.simulator_channels(),
            lambda sim,chs:control_simulator(sim,action,chs,value,logger=logger,batch=True,cancelled=cancelled))
        if logger:
            for row in result['results']:
                if not row['ok']:logger(str(row))
        return result['ok']

    def broadcast_output(self, state: bool, logger=None, cancelled=lambda:False) -> bool:
        from devices.simulator_batch import run_simulator_batch
        result=run_simulator_batch(self.simulator_channels(),lambda sim,chs:control_simulator(sim,'output',chs,state,logger=logger,batch=True,cancelled=cancelled))
        if logger:
            for row in result['results']:
                if not row['ok']: logger(str(row))
        return result['ok']

    def init_all_devices(self, logger=None, cancelled=lambda:False):
        """
        初始化连接所有硬件，遵循方案书要求的顺序启动防浪涌逻辑
        """
        from .rn_board import board_settings
        operations = []
        devices = [('pcan', self.pcan), ('opteeq', self.opteeq), ('e2_rfp',self.e2_rfp),
                   ('easy320',self.easy320), ('ca550',self.ca550), ('scanner',getattr(self,'scanner',None)), ('hv',self.hv_source),
                   ('dut_pwr',self.dut_power), ('it6333b',self.it6333b),
                   ('tbs2102b',self.tbs2102b), ('dg2052',self.dg2052), ('dmm34461',self.dmm34461),
                   ('rs_ws_eth6',getattr(self,'rs_ws_eth6',None))]
        devices.extend((f'sim{i}',sim) for i,sim in enumerate(self.simulators,1))
        operations.extend((key, device) for key,device in devices if self.is_enabled(key) and device)
        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
        pending_boards = {}
        for setting in board_settings(cfg or {}):
            if setting['enabled']:
                pending_boards[setting['key']] = setting
                operations.append((setting['key'], None))
        from PySide6.QtCore import QThread
        def stop_requested():
            return cancelled() or QThread.currentThread().isInterruptionRequested()
        success = True
        for key, device in operations:
            if stop_requested():
                return False
            try:
                if key in pending_boards:
                    device = self.get_rn_board(key, pending_boards[key])
                if key in ('hv', 'dut_pwr', 'ca550', 'it6333b', 'dg2052'):
                    ok = connect_instrument(device, stop_requested)
                else:
                    ok = bool(device.connect())
            except Exception as exc:
                ok = False
                if logger: logger(f"[!] {key}: {exc}")
            if not ok and key == 'hv':
                try:
                    device.disconnect()
                except Exception as exc:
                    if logger: logger(f"[!] {key} disconnect: {exc}")
                finally:
                    device.is_connected = False
            success = success and ok
            if logger:
                detail = getattr(device, 'last_error', '') if not ok else getattr(device, 'identity', '')
                if key == 'e2_rfp' and ok:
                    logger('[+] e2_rfp: E2 USB已识别；目标连接未验证')
                elif key == 'rs_ws_eth6' and ok and device.mode == 'upload':
                    logger(f'[+] rs_ws_eth6: 本机 TCP {device.port} 监听已启动；等待传感器上传有效数据')
                else:
                    logger(f"{'[+]' if ok else '[!]'} {key}: {'connected' if ok else 'offline'}" + (f" ({detail})" if isinstance(detail, str) and detail else ''))
        return success and not stop_requested()

    def get_all_device_status(self):
        """获取所有设备的连接状态列表，供 UI 展示"""
        status_list = []
        
        # 定义辅助函数
        def add_status(key, name, device, info=""):
            if key == 'e2_rfp':
                ready = bool(device and device.tool_ready)
                present = bool(ready and device.is_connected)
                status_list.append({
                    'key': key, 'name': name, 'info': info,
                    'status': 'E2已识别（目标未验证）' if present else '离线',
                    'color': '#FFC107' if present else '#DC3545',
                    'connection_verified': False,
                    'usb_verified': present,
                    'detail': getattr(device, 'presence_error', '') or getattr(device, 'last_error', ''),
                })
                return
            is_conn = getattr(device, "is_connected", False) if device else False
            status_list.append({
                "key": key,
                "name": name,
                "info": info or (getattr(device, "ip", "") if device else ""),
                "status": "已联机" if is_conn else "离线",
                "color": "#28A745" if is_conn else "#DC3545",
                "detail": str(getattr(device, 'last_error', '') or '') if device else '设备尚未连接'
            })

        if self.is_enabled("hv") and self.hv_source: add_status("hv", "NGI 高压源", self.hv_source)
        if self.is_enabled("dut_pwr") and self.dut_power: add_status("dut_pwr", "IT6722A 被测物供电电源", self.dut_power, self.dut_power.port)
        if self.is_enabled("ca550") and self.ca550: add_status("ca550", "CA550 校准源", self.ca550, self.ca550.port)
        if self.is_enabled('scanner'):
            device=getattr(self,'scanner',None)
            status_list.append(dict(key='scanner',name='infoscan FV31-2110 扫码器',
                info=getattr(device,'port',''),status='串口已连接' if device and device.is_connected else '离线',
                color='#28A745' if device and device.is_connected else '#DC3545',
                detail=getattr(device,'last_error','') or '仅表示串口连接；扫码识别请在独立调试页验证。'))
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
        sensor = getattr(self, 'rs_ws_eth6', None)
        if sensor is not None and self.is_enabled('rs_ws_eth6'):
            add_status('rs_ws_eth6', sensor.identity, sensor,
                       f'{sensor.ip}:{sensor.port} / 站号 {sensor.unit}')
            if sensor.mode == 'upload':
                row = status_list[-1]
                row['connection_verified'] = sensor.is_connected
                if sensor.is_connected:
                    row['status'] = '已收到数据'
                    row['detail'] = f'终端 {sensor.terminal_id} / 数据距今 {sensor.data_age:.1f}s'
                elif sensor.is_listening:
                    age = sensor.data_age
                    row.update(status='数据超时' if age is not None else '监听中，等待上传', color='#FFC107',
                               detail=sensor.last_error or f'监听本机 TCP {sensor.port}，等待 {sensor.ip} 上传')
        from .rn_board import board_settings
        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
        for setting in board_settings(cfg or {}):
            if setting['enabled']:
                add_status(setting['key'], setting['name'], self.rn_boards.get(setting['key']))
        return status_list

    def disconnect_all(self):
        """Try every device; retain failures rather than reporting unconditional success."""
        failures = []
        try:
            monitor=getattr(self,'dbc_monitor',None)
            if monitor:monitor.stop()
        except Exception as exc:failures.append(f'DBC: {exc}')
        devices = [(key, board) for key, board in self.rn_boards.items()]
        devices.extend((key, getattr(self, key, None)) for key in (
            'dmm34461', 'tbs2102b', 'dg2052', 'it6333b', 'hv_source',
            'dut_power', 'easy320', 'ca550', 'scanner', 'pcan', 'opteeq', 'e2_rfp', 'rs_ws_eth6'))
        devices.extend((f'sim{index}', sim) for index, sim in
                       enumerate(getattr(self, 'simulators', []), 1))
        for name, device in devices:
            if device is None:
                continue
            try:
                if device.disconnect() is False:
                    failures.append(f"{name}: {getattr(device, 'last_error', '') or 'disconnect not confirmed'}")
            except Exception as exc:
                failures.append(f'{name}: {exc}')
        self.last_disconnect_errors = failures
        if failures:
            print('[DeviceManager] Disconnect failures: ' + '; '.join(failures))
        else:
            print('[DeviceManager] All device connections released')
        return not failures

    def emergency_stop(self):
        """紧急停止：关闭所有电源输出"""
        print("!!! 触发紧急停止 !!!")
        self.safe_shutdown_outputs(emergency=True)

    def safe_shutdown_outputs(self, channel_id=1, logger=None, emergency=False, check_offline=True):
        """尽最大可能关闭所有可能带电的输出，某个设备失败不影响其他设备。"""
        failures = []
        unverified = []

        def reachable(device, name):
            if device is None:
                return False
            if getattr(device, 'is_connected', False):
                return True
            if getattr(device, '_ever_connected', False) is True:
                message = name + ': 设备失联，无法确认输出关闭'
                unverified.append(message)
                if check_offline:
                    failures.append(message)
            return False

        def attempt(name, action):
            try:
                result = action()
                if result is False or result is None:
                    failures.append(name)
            except Exception as exc:
                failures.append(f"{name}: {exc}")

        grouped = {}
        for global_ch, sim, local_ch in self.simulator_channels():
            grouped.setdefault(id(sim), (sim, []))[1].append(local_ch)
        for sim, channels in grouped.values():
            if reachable(sim, f'Simulator {sim.sequence_number}'):
                attempt(f"Simulator {sim.sequence_number}", lambda sim=sim, channels=channels: control_simulator(sim,'output',channels,False,logger=logger,batch=True))
        resistor_boards = []
        for board in list(self.rn_boards.values()):
            if not reachable(board, board.setting['name']):
                continue
            kind = board.setting['kind']
            if kind == 'insulation':
                attempt(board.setting['name'], lambda board=board: control_board(board,'insulation',state=False))
            elif kind == 'resistor':
                resistor_boards.append(board)
            else:
                attempt(board.setting['name'], lambda board=board: control_board(board,'all_relays',state=False))
        if resistor_boards:
            def close_resistor(board):
                attempt(board.setting['name'], lambda: control_board(
                    board,'resistances',channels=range(1,board.setting['channels']+1),resistance=0))
            with ThreadPoolExecutor(max_workers=len(resistor_boards)) as pool:
                list(pool.map(close_resistor,resistor_boards))
        if emergency and reachable(self.dut_power, 'DUT power'):
            attempt("DUT power", lambda: control_power(self.dut_power, 'output_control', False))
        if reachable(self.hv_source, '高压源'):
            attempt("高压源", lambda: control_power(self.hv_source, 'output_control', False))
        if reachable(self.ca550, 'CA550'):
            attempt("CA550输出", lambda: shutdown_calibrator(self.ca550))
        if reachable(self.it6333b, 'IT6333B'):
            for channel in (1,2,3):
                attempt(f'IT6333B CH{channel}',lambda ch=channel:control_power(self.it6333b, 'output_control', False, channel=ch))
        if reachable(self.dg2052, 'DG2052'):
            for channel in (1,2):
                attempt(f'DG2052 CH{channel}',lambda ch=channel:control_generator(self.dg2052, 'output', ch, state=False))

        self.last_shutdown_errors = list(failures)
        self.last_shutdown_unverified = unverified
        if logger:
            if failures:
                logger("[WARN] 安全断电未全部成功: " + "; ".join(failures))
            elif unverified:
                logger("[WARN] 在线设备已执行关闭，但存在无法确认的失联输出: " + "; ".join(unverified))
            else:
                logger("[*] 已执行安全断电")
        return not failures
