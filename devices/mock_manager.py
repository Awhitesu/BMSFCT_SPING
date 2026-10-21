import random

# BUG-12修复: 补全所有 DeviceManager 的公共属性和方法，防止 Dry Run 崩溃

def _make_mock_power():
    return type('MockPower', (), {
        'set_voltage': lambda *a, **k: True,
        'set_current': lambda *a, **k: True,
        'output_control': lambda *a, **k: True,
        'read_output_state': lambda *a, **k: True,
        'measure_voltage': lambda *a, **k: 12.0,
        'measure_current': lambda *a, **k: 1.0,
        'is_connected': True,
        'connect': lambda *a: True,
        'disconnect': lambda *a: None,
    })()

def _make_mock_sim():
    return type('MockSim', (), {
        'is_connected': True,
        'set_voltage': lambda *a, **k: True,
        'set_current_limit': lambda *a, **k: True,
        'set_range': lambda *a, **k: True,
        'output_control': lambda *a, **k: True,
        'read_output_state': lambda *a, **k: True,
        'measure_voltage': lambda *a, **k: 3.7 + random.uniform(-0.01, 0.01),
        'measure_current': lambda *a, **k: 0.0,
        'connect': lambda *a: True,
        'disconnect': lambda *a: None,
    })()

def _make_mock_can():
    return type('MockCAN', (), {
        'is_connected': True,
        'connect': lambda *a: True,
        'disconnect': lambda *a: None,
        'send_can_message': lambda *a, **k: True,
        'send_and_wait_response': lambda *a, **k: {'data': b'\x00' * 8},
        'wait_for_message': lambda *a, **k: {'data': b'\x00' * 8},
        'clear_rx_history': lambda *a: None,
    })()

def _make_mock_e2_rfp():
    return type('MockE2Rfp', (), {
        'is_connected': True,
        'last_error': '',
        'rfp_cli_path': 'MOCK',
        'device_family': 'RH850',
        'interface': 'MOCK',
        'speed': 'MOCK',
        'connect': lambda *a: True,
        'disconnect': lambda *a: True,
        'query_state': lambda *a: 'Ready',
        'run_project': lambda *a, **k: True,
        'get_checksum': lambda *a, **k: '0x00000000',
        'read_chip': lambda *a, **k: True,
        'get_recent_log': lambda *a, **k: '',
    })()


class MockDeviceManager:
    """
    虚拟设备管理器 - 用于脱机仿真 (Dry Run)
    完全模拟 DeviceManager 的接口，但不进行实际硬件通讯。
    """
    def __init__(self):
        self.is_virtual = True
        self.dut_power = _make_mock_power()
        self.hv_source = _make_mock_power()
        self.simulators = [_make_mock_sim() for _ in range(3)]
        self.enabled_devices = {}
        for number, sim in enumerate(self.simulators, 1):
            sim.sequence_number, sim.max_channels = number, 18
        self.pcan = _make_mock_can()
        self.e2_rfp = _make_mock_e2_rfp()
        self.easy320 = type('MockEasy', (), {
            'write_relay': lambda *a, **k: True,
            'read_relays': lambda *a, **k: [True] * 32,
            'batch_control': lambda *a, **k: True,
            'is_connected': True,
        })()
        self.ca550 = type('MockCA550', (), {
            'set_source_func': lambda *a, **k: True,
            'set_source_range': lambda *a, **k: True,
            'set_source_data': lambda *a, **k: True,
            'set_source_output': lambda *a, **k: True,
            'read_measure_data': lambda *a, **k: '12.345',
            'is_connected': True,
            'port': 'MOCK',
        })()

    def _get_sim_and_ch(self, global_ch: int):
        """与真实设备使用相同的连续全局通道规则。"""
        if isinstance(global_ch, bool):return None, None
        for channel, sim, local in self.simulator_channels():
            if channel == global_ch:return sim, local
        return None, None

    def simulator_channels(self):
        from .simulator_config import channel_routes
        return channel_routes((sim.sequence_number, sim.max_channels, sim)
                              for slot, sim in enumerate(self.simulators, 1)
                              if self.enabled_devices.get(f'sim{slot}', True))

    def set_voltage(self, global_ch, voltage, logger=None):
        if logger: logger(f"[仿真] 设置通道 {global_ch} 电压为 {voltage}V")
        return True

    def set_current(self, global_ch, current, logger=None):
        if logger: logger(f"[仿真] 设置通道 {global_ch} 电流为 {current}A")
        return True

    def output_control(self, global_ch, state, logger=None):
        if logger: logger(f"[仿真] 通道 {global_ch} 输出 {'开启' if state else '关闭'}")
        return True

    def measure_voltage(self, global_ch, logger=None):
        val = 12.0 + random.uniform(-0.1, 0.1)
        if logger: logger(f"[仿真] 读取通道 {global_ch} 电压: {val:.3f}V")
        return val

    def measure_current(self, global_ch, logger=None):
        val = 1.5 + random.uniform(-0.05, 0.05)
        if logger: logger(f"[仿真] 读取通道 {global_ch} 电流: {val:.3f}A")
        return val

    def broadcast_voltage(self, voltage, logger=None):
        if logger: logger(f"[仿真] 广播电压: {voltage}V")
        return True

    def broadcast_current(self, current, logger=None):
        if logger: logger(f"[仿真] 广播电流: {current}A")
        return True

    def broadcast_range(self, range_str, logger=None):
        return True

    def broadcast_output(self, state, logger=None):
        if logger: logger(f"[仿真] 全局输出控制: {state}")
        return True

    def init_all_devices(self, logger=None):
        if logger: logger("[仿真] 正在初始化虚拟硬件...")
        return True

    def disconnect_all(self):
        print("[仿真] 正在断开虚拟硬件...")

    def get_all_device_status(self):
        return [{"name": "虚拟设备", "info": "Dry Run 模式", "status": "仿真中", "color": "#4ECCA3"}]
