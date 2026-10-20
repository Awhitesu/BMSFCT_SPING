"""CANFDNET-400U TCP adapter using the vendor SDK's frame definitions."""
import ctypes as C
import importlib.util
import struct
import threading
import time
from pathlib import Path


class ZLGNetBus:
    def __init__(self, sdk_path, ip, port, channel=0):
        import can
        self.can = can
        self.lock = threading.RLock()
        self.device = self.handle = None
        self.handles = {}
        self.enabled_channels = set(range(4))
        self._rx_next = 0
        self.physical_channel = int(channel)
        root = Path(sdk_path).resolve()
        raw = (root / 'zlgcan.dll').read_bytes()
        machine = struct.unpack_from('<H', raw, struct.unpack_from('<I', raw, 60)[0] + 4)[0]
        required = 0x8664 if C.sizeof(C.c_void_p) == 8 else 0x14c
        if machine != required:
            raise RuntimeError('ZLG DLL位数不匹配：当前程序需要官方64位开发包' if required == 0x8664 else '需要官方32位开发包')
        definitions = root / 'zlgcan.py'
        if not definitions.is_file():
            definitions = Path(__file__).resolve().parents[1] / 'vendor' / 'zlgcan_python_260529' / 'zlgcan.py'
        spec = importlib.util.spec_from_file_location('_zlg_sdk_definitions', definitions)
        self.types = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.types)
        self._dll_dirs = [__import__('os').add_dll_directory(str(p)) for p in (root, root / 'kerneldlls') if p.is_dir()]
        self.dll = C.WinDLL(str(root / 'zlgcan.dll'))
        signatures = {
            'ZCAN_OpenDevice': ([C.c_uint, C.c_uint, C.c_uint], C.c_void_p),
            'ZCAN_CloseDevice': ([C.c_void_p], C.c_uint),
            'ZCAN_InitCAN': ([C.c_void_p, C.c_uint, C.c_void_p], C.c_void_p),
            'ZCAN_StartCAN': ([C.c_void_p], C.c_uint),
            'ZCAN_SetValue': ([C.c_void_p, C.c_char_p, C.c_void_p], C.c_uint),
            'ZCAN_GetReceiveNum': ([C.c_void_p, C.c_uint], C.c_uint),
            'ZCAN_Transmit': ([C.c_void_p, C.c_void_p, C.c_uint], C.c_uint),
            'ZCAN_TransmitFD': ([C.c_void_p, C.c_void_p, C.c_uint], C.c_uint),
            'ZCAN_Receive': ([C.c_void_p, C.c_void_p, C.c_uint, C.c_int], C.c_uint),
            'ZCAN_ReceiveFD': ([C.c_void_p, C.c_void_p, C.c_uint, C.c_int], C.c_uint),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result
        try:
            self.device = self.dll.ZCAN_OpenDevice(52, 0, 0)
            if not self.device:
                raise RuntimeError('ZLG打开设备失败')
            if not 0 <= channel <= 3:
                raise ValueError('ZLG通道必须为1–4')
            for index in range(4):
                prefix = str(index) + '/'
                for key, value in [('work_mode', '0'), ('ip', ip), ('work_port', str(port)), ('set_device_tx_echo', '0')]:
                    if self.dll.ZCAN_SetValue(self.device, (prefix + key).encode(), C.c_char_p(value.encode())) != 1:
                        raise RuntimeError(f'ZLG通道{index + 1}设置失败: ' + key)
                config = self.types.ZCAN_CHANNEL_INIT_CONFIG()
                handle = self.dll.ZCAN_InitCAN(self.device, index, C.byref(config))
                if not handle or self.dll.ZCAN_StartCAN(handle) != 1:
                    raise RuntimeError(f'ZLG通道{index + 1}连接失败，请检查TCP端口及四通道转发配置')
                self.handles[index] = handle
            self.handle = self.handles[channel]
        except Exception:
            self.shutdown()
            raise

    def send(self, message, timeout=None):
        message._check()
        fd = message.is_fd
        obj = self.types.ZCAN_TransmitFD_Data() if fd else self.types.ZCAN_Transmit_Data()
        obj.frame.can_id = (message.arbitration_id | (int(message.is_extended_id) << 31)
                            | (int(message.is_remote_frame) << 30)
                            | (int(message.is_error_frame) << 29))
        if fd:
            obj.frame.len = len(message.data)
            obj.frame.flags = int(message.bitrate_switch)
        else:
            obj.frame.can_dlc = message.dlc
        for i, byte in enumerate(message.data):
            obj.frame.data[i] = byte
        with self.lock:
            if not self.handle:
                raise RuntimeError('ZLG已断开')
            channel = self.physical_channel if message.channel is None else int(message.channel)
            handle = getattr(self, 'handles', {self.physical_channel: self.handle}).get(channel)
            if not handle:
                raise RuntimeError('ZLG物理通道句柄不存在')
            if channel not in getattr(self, 'enabled_channels', set(range(4))):
                raise RuntimeError('所选发送通道未使能')
            fn = self.dll.ZCAN_TransmitFD if fd else self.dll.ZCAN_Transmit
            if fn(handle, C.byref(obj), 1) != 1:
                raise RuntimeError('ZLG发送失败')

    def configure_channel(self, nominal_bitrate, data_bitrate, termination, persist=False):
        """Stage then apply the vendor's dynamic configuration for this physical channel."""
        with self.lock:
            if not self.device or not self.handle:
                raise RuntimeError('请先连接ZLG设备')
            for suffix, value in [('NOMINALBAUD', nominal_bitrate), ('DATABAUD', data_bitrate), ('USERES', int(termination))]:
                data = self.types.ZCAN_DYNAMIC_CONFIG_DATA()
                data.key = f'DYNAMIC_CONFIG_CAN{self.physical_channel}_{suffix}'.encode()
                data.value = str(value).encode()
                if self.dll.ZCAN_SetValue(self.device, b'0/add_dynamic_data', C.byref(data)) != 1:
                    raise RuntimeError('ZLG动态配置提交失败: ' + suffix)
            # SDK C example passes a UINT pointer, not the ASCII string '0'/'1'.
            if self.dll.ZCAN_SetValue(self.device, b'0/apply_dynamic_data', C.byref(C.c_uint(int(bool(persist))))) != 1:
                raise RuntimeError('ZLG动态配置应用失败')

    def configure_channels(self, settings, persist=False):
        if len(settings) != 4:
            raise ValueError('必须提供CH1–CH4全部参数')
        for index, config in enumerate(settings):
            if config['nominal'] not in (1000000, 800000, 500000, 250000, 125000, 100000, 50000) or config['data'] not in (5000000, 4000000, 2000000, 1000000, 800000, 500000, 250000, 125000, 100000):
                raise ValueError(f'CH{index + 1}不是官方动态接口支持的标准速率；自定义位时序需使用CANFDNET网络配置库，未下发任何配置。')
        with self.lock:
            if not self.device or not self.handle:
                raise RuntimeError('请先连接ZLG设备')
            for index, config in enumerate(settings):
                for suffix, value in [('ENABLE', int(config['enabled'])),
                                      ('NOMINALBAUD', config['nominal']),
                                      ('DATABAUD', config['data']),
                                      ('USERES', int(config['termination']))]:
                    data = self.types.ZCAN_DYNAMIC_CONFIG_DATA()
                    data.key = f'DYNAMIC_CONFIG_CAN{index}_{suffix}'.encode()
                    data.value = str(value).encode()
                    if self.dll.ZCAN_SetValue(self.device, b'0/add_dynamic_data', C.byref(data)) != 1:
                        raise RuntimeError(f'CH{index + 1}提交失败: {suffix}；未完成应用')
                # 官方手册要求切换到下一物理通道前先应用本通道配置。
                if self.dll.ZCAN_SetValue(self.device, b'0/apply_dynamic_data', C.byref(C.c_uint(int(bool(persist))))) != 1:
                    raise RuntimeError(f'CH{index + 1}配置应用失败；此前CH1–CH{index}可能已经生效，请检查设备配置。')
                if not hasattr(self, 'enabled_channels'):
                    self.enabled_channels = set(range(4))
                if config['enabled']:
                    self.enabled_channels.add(index)
                else:
                    self.enabled_channels.discard(index)

    def recv(self, timeout=0.1):
        deadline = time.monotonic() + (timeout or 0)
        while self.handle:
            with self.lock:
                if not self.handle:
                    return None
                channels = getattr(self, 'handles', {getattr(self, 'physical_channel', 0): self.handle})
                start = getattr(self, '_rx_next', 0)
                for offset in range(8):
                    index, fd = ((start + offset) // 2) % 4, bool((start + offset) % 2)
                    handle = channels.get(index)
                    if not handle or index not in getattr(self, 'enabled_channels', set(range(4))):
                        continue
                    count = self.dll.ZCAN_GetReceiveNum(handle, int(fd))
                    if count == 0xFFFFFFFF:
                        raise RuntimeError('ZLG连接异常')
                    if not count:
                        continue
                    obj = self.types.ZCAN_ReceiveFD_Data() if fd else self.types.ZCAN_Receive_Data()
                    fn = self.dll.ZCAN_ReceiveFD if fd else self.dll.ZCAN_Receive
                    if fn(handle, C.byref(obj), 1, 0) != 1:
                        continue
                    frame = obj.frame
                    length = frame.len if fd else frame.can_dlc
                    if length > (64 if fd else 8):
                        raise ValueError('ZLG接收帧长度无效')
                    self._rx_next = (index * 2 + int(fd) + 1) % 8
                    return self.can.Message(arbitration_id=frame.can_id & 0x1FFFFFFF,
                        channel=index,
                        is_extended_id=bool(frame.can_id & 0x80000000),
                        is_error_frame=bool(frame.can_id & 0x20000000),
                        is_remote_frame=bool(frame.can_id & 0x40000000),
                        is_fd=fd, bitrate_switch=bool(frame.flags & 1) if fd else False,
                        dlc=length,
                        data=b'' if frame.can_id & 0x40000000 else bytes(frame.data[:length]), timestamp=time.time())
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.002)
        return None

    def shutdown(self):
        with self.lock:
            self.handle = None
            self.handles = {}
            if self.device:
                self.dll.ZCAN_CloseDevice(self.device)
                self.device = None
