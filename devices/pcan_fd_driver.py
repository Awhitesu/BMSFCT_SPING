import queue
import threading
import time
from collections import deque
from typing import Callable, Optional


class PCANFDDriver:
    """USB PCAN-FD 单通道驱动。"""

    CAN_TYPE_CLASSIC = 0x00
    CAN_TYPE_FD = 0x01
    CAN_TYPE_FD_BRS = 0x02

    def __init__(self, channel="PCAN_USBBUS1", nominal_bitrate=500000,
                 data_bitrate=2000000, fd_enabled=True, logical_channel=0):
        self.channel = str(channel or "PCAN_USBBUS1")
        self.nominal_bitrate = int(nominal_bitrate)
        self.data_bitrate = int(data_bitrate)
        self.fd_enabled = bool(fd_enabled)
        self.logical_channel = int(logical_channel)
        self.is_connected = False
        self.last_error = ""
        self.bus = None
        self.lock = threading.RLock()
        self.msg_queue = queue.Queue(maxsize=10000)
        self.on_message_received: Optional[Callable] = None
        self._message_listeners = set()
        self._listener_lock = threading.Lock()
        self._rx_history = deque(maxlen=2000)
        self._rx_condition = threading.Condition()
        self._stop_event = threading.Event()
        self._receive_thread = None

    @property
    def ip(self):
        """兼容旧状态界面的地址字段。"""
        return self.channel

    def configure(self, channel=None, nominal_bitrate=None, data_bitrate=None, fd_enabled=None):
        if channel is not None:
            self.channel = str(channel)
        if nominal_bitrate is not None:
            self.nominal_bitrate = int(nominal_bitrate)
        if data_bitrate is not None:
            self.data_bitrate = int(data_bitrate)
        if fd_enabled is not None:
            self.fd_enabled = bool(fd_enabled)

    def connect(self, channel=None, _legacy_port=None) -> bool:
        # 兼容旧 connect(ip, port) 调用；只有 PCAN_* 参数才更新 USB 通道。
        if channel and str(channel).upper().startswith(("PCAN_", "ZLG_CAN")):
            self.channel = str(channel).upper()
        with self.lock:
            self.disconnect()
            try:
                import can
                kwargs = {
                    "interface": "pcan",
                    "channel": self.channel,
                    "receive_own_messages": False,
                }
                if self.fd_enabled:
                    kwargs["fd"] = True
                    kwargs["timing"] = can.BitTimingFd.from_sample_point(
                        f_clock=80_000_000,
                        nom_bitrate=self.nominal_bitrate,
                        nom_sample_point=80.0,
                        data_bitrate=self.data_bitrate,
                        data_sample_point=80.0,
                    )
                else:
                    kwargs["bitrate"] = self.nominal_bitrate
                if self.channel.startswith('ZLG_CAN'):
                    from .zlg_net_bus import ZLGNetBus
                    self.bus = ZLGNetBus(self.zlg_sdk_path, self.zlg_ip, self.zlg_port,
                                         int(self.channel.removeprefix('ZLG_CAN')) - 1)
                else:
                    self.bus = can.Bus(**kwargs)
                self.clear_message_buffers()
                self.is_connected = True
                self.last_error = ""
                self._stop_event.clear()
                self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
                self._receive_thread.start()
                print(f"[PCAN-FD] 已连接 {self.channel}, 仲裁={self.nominal_bitrate}, 数据={self.data_bitrate}")
                return True
            except Exception as exc:
                self.bus = None
                self.is_connected = False
                self.last_error = str(exc)
                print(f"[PCAN-FD] 连接失败: {exc}")
                return False

    def disconnect(self):
        self._stop_event.set()
        self.is_connected = False
        bus, self.bus = self.bus, None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    def _receive_loop(self):
        while not self._stop_event.is_set():
            try:
                bus = self.bus
                if bus is None:
                    break
                message = bus.recv(timeout=0.1)
                if message is None:
                    continue
                can_type = self.CAN_TYPE_CLASSIC
                if getattr(message, "is_fd", False):
                    can_type = self.CAN_TYPE_FD_BRS if getattr(message, "bitrate_switch", False) else self.CAN_TYPE_FD
                data = bytes(message.data)
                info = {
                    "timestamp_rel": time.time(),
                    "channel": self.logical_channel,
                    "can_id": int(message.arbitration_id),
                    "can_type": can_type,
                    "dlc": self.length_to_dlc(len(data)),
                    "data": data,
                    "timestamp": getattr(message, "timestamp", time.time()),
                    "direction": "RX",
                    "is_error_frame": bool(getattr(message, "is_error_frame", False)),
                }
                if self.channel.startswith('ZLG_CAN'):
                    physical = int(message.channel)
                    info.update(channel=physical, physical_channel=physical + 1,
                                interface=f'ZLG_CAN{physical + 1}')
                self._publish(info)
            except Exception as exc:
                if not self._stop_event.is_set():
                    self.last_error = str(exc)
                    self.is_connected = False
                    print(f"[PCAN-FD] 接收异常: {exc}")
                break

    def add_message_listener(self, callback):
        """Subscribe to live copies without taking frames from another consumer."""
        with self._listener_lock:
            self._message_listeners.add(callback)

    def remove_message_listener(self, callback):
        with self._listener_lock:
            self._message_listeners.discard(callback)

    def _publish(self, info):
        # 独立于配方逻辑通道记录真实接口，切换设备后历史行仍保留原来源。
        info.setdefault('interface', self.channel)
        if self.channel.startswith('ZLG_CAN'):
            info.setdefault('device', f"ZLG CANFDNET-400U ({getattr(self, 'zlg_ip', '')})")
            info.setdefault('physical_channel', int(self.channel.removeprefix('ZLG_CAN')))
        else:
            info.setdefault('device', 'PCAN')
            info.setdefault('physical_channel', self.channel)
        try:
            self.msg_queue.put_nowait(info)
        except queue.Full:
            pass
        with self._rx_condition:
            self._rx_history.append(info)
            self._rx_condition.notify_all()
        with self._listener_lock:
            listeners = tuple(self._message_listeners)
        for callback in listeners:
            try:
                callback(dict(info))
            except Exception:
                pass
        if self.on_message_received:
            try:
                self.on_message_received(info)
            except Exception:
                pass

    def clear_message_buffers(self):
        """清除上一次连接遗留的监控消息和接收匹配历史。"""
        while True:
            try:
                self.msg_queue.get_nowait()
            except queue.Empty:
                break
        with self._rx_condition:
            self._rx_history.clear()

    def send_can_message(self, channel_id, can_id, can_type, dlc, data) -> bool:
        if not self.is_connected and not self.connect():
            return False
        try:
            import can
            target_len = self.dlc_to_length(int(dlc))
            payload = bytes(data)[:target_len].ljust(target_len, b"\x00")
            is_fd = int(can_type) in (self.CAN_TYPE_FD, self.CAN_TYPE_FD_BRS)
            msg = can.Message(
                arbitration_id=int(can_id),
                is_extended_id=int(can_id) > 0x7FF,
                data=payload,
                is_fd=is_fd,
                bitrate_switch=int(can_type) == self.CAN_TYPE_FD_BRS,
                check=True,
            )
            with self.lock:
                self.bus.send(msg, timeout=1.0)
            self._publish({
                "timestamp_rel": time.time(), "channel": int(channel_id),
                "can_id": int(can_id), "can_type": int(can_type), "dlc": int(dlc),
                "data": payload, "timestamp": time.time(), "direction": "TX",
            })
            return True
        except Exception as exc:
            self.last_error = str(exc)
            print(f"[PCAN-FD] 发送失败: {exc}")
            return False

    def clear_rx_history(self, can_id=None, channel_id=None):
        with self._rx_condition:
            self._rx_history = deque(
                (m for m in self._rx_history if not (
                    (can_id is None or m.get("can_id") == can_id) and
                    (channel_id is None or m.get("channel") == channel_id)
                )), maxlen=self._rx_history.maxlen
            )

    def wait_for_message(self, can_id=None, channel_id=None, predicate=None,
                         timeout=1.0, since_time=None, consume=False):
        deadline = time.time() + max(0.0, float(timeout))
        start_time = since_time if since_time is not None else time.time() - 0.005
        zlg_channel = int(self.channel.removeprefix('ZLG_CAN')) - 1 if self.channel.startswith('ZLG_CAN') else None
        # USB PCAN-FD 是单通道设备，旧配方中的逻辑通道号不再用于过滤 RX。
        while time.time() < deadline:
            with self._rx_condition:
                for index in range(len(self._rx_history) - 1, -1, -1):
                    message = self._rx_history[index]
                    if message.get("timestamp_rel", 0) < start_time - 0.001:
                        break
                    if message.get("direction") != "RX":
                        continue
                    if zlg_channel is not None and message.get('channel') != zlg_channel:
                        continue
                    if can_id is not None and message.get("can_id") != can_id:
                        continue
                    if predicate and not predicate(message):
                        continue
                    if consume:
                        history = list(self._rx_history)
                        history.pop(index)
                        self._rx_history = deque(history, maxlen=self._rx_history.maxlen)
                    return message
                remaining = deadline - time.time()
                if remaining > 0:
                    self._rx_condition.wait(remaining)
        return None

    def send_and_wait_response(self, channel_id, can_id, can_type, dlc, data,
                               response_id, timeout=1.0, matcher=None):
        self.clear_rx_history(response_id)
        if not self.send_can_message(channel_id, can_id, can_type, dlc, data):
            return None
        return self.wait_for_message(response_id, predicate=matcher, timeout=timeout)

    def create_uds_client(self, tx_id=0x7E0, rx_id=0x7E8, can_fd=False,
                          brs=False, timeout=1.0, padding=0x00,
                          block_size=0, st_min=0, p2_star_timeout=5.0):
        """创建绑定到当前 PCAN 通道的 ISO-TP/UDS 客户端。"""
        from .uds_client import ISOTPTransport, UDSClient
        transport = ISOTPTransport(
            self, tx_id=tx_id, rx_id=rx_id, can_fd=can_fd, brs=brs,
            timeout=timeout, padding=padding, block_size=block_size, st_min=st_min,
        )
        return UDSClient(transport, p2_timeout=timeout, p2_star_timeout=p2_star_timeout)

    @staticmethod
    def dlc_to_length(dlc):
        value = int(dlc)
        return value if value <= 8 else {9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}.get(value, 8)

    @staticmethod
    def length_to_dlc(length):
        value = int(length)
        if value <= 8: return value
        if value <= 12: return 9
        if value <= 16: return 10
        if value <= 20: return 11
        if value <= 24: return 12
        if value <= 32: return 13
        if value <= 48: return 14
        return 15
