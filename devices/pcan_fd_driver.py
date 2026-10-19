import queue
import threading
import time
import math
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
        self._lifecycle_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self.msg_queue = queue.Queue(maxsize=10000)
        self.monitor_dropped = 0
        self.on_message_received: Optional[Callable] = None
        self._message_listeners = set()
        self._listener_lock = threading.Lock()
        self._rx_history = deque(maxlen=2000)
        self._rx_condition = threading.Condition()
        self._stop_event = threading.Event()
        self._receive_thread = None
        self._bus_fault = ''
        self._last_tx = None

    def _latch_bus_fault(self, reason):
        with self.lock:
            if not self._bus_fault:
                context = (f' last_TX={self._last_tx}' if self._last_tx else '')
                self._bus_fault = f'{self.channel}: {reason}; nominal={self.nominal_bitrate}, data={self.data_bitrate}{context}'
            self.last_error = self._bus_fault
            self.is_connected = False
            self._stop_event.set()
        with self._rx_condition:
            self._rx_condition.notify_all()

    def check_bus_health(self):
        """Latch BUS-OFF until an explicit successful connect, never auto-reset it."""
        with self.lock:
            if self._bus_fault:
                raise ConnectionError(self._bus_fault + '; CAN发送已锁定，请检查总线后手动重新连接')
            if self.is_connected and self.bus is not None and self.channel.startswith('PCAN_'):
                status = getattr(self.bus, 'status', None)
                if callable(status):
                    result = status()
                    code = getattr(result, 'value', result)
                    # PCAN-Basic PCAN_ERROR_BUSOFF = 0x10 (status is a bitmask).
                    if isinstance(code, int) and code & 0x10:
                        self._latch_bus_fault(f'BUS-OFF status=0x{code:X}')
                        raise ConnectionError(self._bus_fault)

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
        with self._lifecycle_lock:
            return self._connect_locked(channel, _legacy_port)

    def _connect_locked(self, channel=None, _legacy_port=None) -> bool:
        # 兼容旧 connect(ip, port) 调用；只有 PCAN_* 参数才更新 USB 通道。
        if channel and str(channel).upper().startswith(("PCAN_", "ZLG_CAN")):
            self.channel = str(channel).upper()
        if not self.disconnect():
            return False
        with self.lock:
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
                self._bus_fault = ''
                self._last_tx = None
                self.is_connected = True
                self.last_error = ""
                self._stop_event = threading.Event()
                self._receive_thread = threading.Thread(target=self._receive_loop,
                    args=(self._stop_event, self.bus, self.channel), daemon=True)
                self._receive_thread.start()
                print(f"[PCAN-FD] 已连接 {self.channel}, 仲裁={self.nominal_bitrate}, 数据={self.data_bitrate}")
                return True
            except Exception as exc:
                self._stop_event.set()
                failed_bus, self.bus = self.bus, None
                if failed_bus is not None:
                    try:
                        failed_bus.shutdown()
                    except Exception:
                        pass
                self.is_connected = False
                self.last_error = str(exc)
                print(f"[PCAN-FD] 连接失败: {exc}")
                return False

    def disconnect(self):
        with self._lifecycle_lock:
            with self.lock:
                self._stop_event.set()
                self.is_connected = False
                bus, self.bus = self.bus, None
                thread = self._receive_thread
            errors = []
            if bus is not None:
                try:
                    bus.shutdown()
                except Exception as exc:
                    errors.append(str(exc))
            with self._rx_condition:
                self._rx_condition.notify_all()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
                if thread.is_alive():
                    errors.append('Previous CAN receiver has not stopped')
            if errors:
                self.last_error = '; '.join(errors)
                return False
            return True

    def _receive_loop(self, stop_event=None, bus=None, channel=None):
        stop_event = self._stop_event if stop_event is None else stop_event
        bus = self.bus if bus is None else bus
        channel = self.channel if channel is None else channel
        while not stop_event.is_set():
            try:
                if bus is None:
                    break
                message = bus.recv(timeout=0.1)
                if stop_event.is_set() or self.bus is not bus:
                    return
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
                    "is_extended_id": bool(getattr(message, 'is_extended_id',False)),
                    "can_type": can_type,
                    "dlc": self.length_to_dlc(int(getattr(message, 'dlc', len(data)))),
                    "data": data,
                    "timestamp": getattr(message, "timestamp", time.time()),
                    "direction": "RX",
                    "is_error_frame": bool(getattr(message, "is_error_frame", False)),
                    "is_remote_frame": bool(getattr(message, "is_remote_frame", False)),
                }
                if channel.startswith('ZLG_CAN'):
                    physical = int(message.channel)
                    info.update(channel=physical, physical_channel=physical + 1,
                                interface=f'ZLG_CAN{physical + 1}')
                info.setdefault('interface', channel)
                info.setdefault('device', f"ZLG CANFDNET-400U ({getattr(self, 'zlg_ip', '')})" if channel.startswith('ZLG_CAN') else 'PCAN')
                info.setdefault('physical_channel', channel)
                with self.lock:
                    if stop_event.is_set() or self.bus is not bus:
                        return
                    self._publish(info)
            except Exception as exc:
                with self.lock:
                    if not stop_event.is_set() and self.bus is bus:
                        self._latch_bus_fault('CAN RX failed: ' + str(exc))
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
            self.msg_queue.put_nowait(dict(info))
        except queue.Full:
            # This queue is only the rolling UI feed. Preserve fresh frames;
            # diagnostic matching and listeners below have independent paths.
            try:
                self.msg_queue.get_nowait()
                self.monitor_dropped += 1
            except queue.Empty:
                pass
            try:
                self.msg_queue.put_nowait(dict(info))
            except queue.Full:
                self.monitor_dropped += 1
        with self._rx_condition:
            self._rx_history.append(dict(info))
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
                self.on_message_received(dict(info))
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

    def send_can_message(self, channel_id, can_id, can_type, dlc, data, physical_channel=None,extended_id=None) -> bool:
        try:
            import can
            can_type = int(can_type)
            if can_type not in (self.CAN_TYPE_CLASSIC, self.CAN_TYPE_FD, self.CAN_TYPE_FD_BRS):
                raise ValueError('Invalid CAN frame type')
            target_len = self.dlc_to_length(int(dlc))
            raw = bytes(data)
            if len(raw) > target_len:
                raise ValueError('CAN payload exceeds the requested DLC')
            payload = raw.ljust(target_len, b"\x00")
            physical = (int(self.channel.removeprefix('ZLG_CAN')) if self.channel.startswith('ZLG_CAN') else 1) if physical_channel is None else int(physical_channel)
            if not 1 <= physical <= (4 if self.channel.startswith('ZLG_CAN') else 1):
                raise ValueError('所选物理通道不属于当前CAN卡')
            is_fd = int(can_type) in (self.CAN_TYPE_FD, self.CAN_TYPE_FD_BRS)
            if is_fd and not self.fd_enabled:
                raise ValueError('当前连接为Classic CAN，禁止发送CAN FD报文')
            msg = can.Message(
                arbitration_id=int(can_id),
                is_extended_id=int(can_id) > 0x7FF if extended_id is None else bool(extended_id),
                data=payload,
                is_fd=is_fd,
                bitrate_switch=int(can_type) == self.CAN_TYPE_FD_BRS,
                check=True,
            )
            self.check_bus_health()
            if not self.is_connected and not self.connect():
                return False
            with self.lock:
                self.check_bus_health()
                if self.channel.startswith('ZLG_CAN'):
                    msg.channel = physical - 1
                self._last_tx = dict(id=f'0x{int(can_id):X}', type=can_type,
                                     dlc=int(dlc), data=payload.hex(' ').upper())
                self.bus.send(msg, timeout=1.0)
                self.check_bus_health()
            self._publish({
                "timestamp_rel": time.time(), "channel": int(channel_id),
                "can_id": int(can_id), "can_type": int(can_type), "dlc": int(dlc),
                "data": payload, "timestamp": time.time(), "direction": "TX",
                'physical_channel': physical if self.channel.startswith('ZLG_CAN') else self.channel,
                'interface': f'ZLG_CAN{physical}' if self.channel.startswith('ZLG_CAN') else self.channel,
            })
            return True
        except Exception as exc:
            self.last_error = self._bus_fault or str(exc)
            # Write may report BUS-OFF before the receiver sees it.
            text = str(exc).lower().replace('-', '').replace(' ', '')
            if 'busoff' in text and not self._bus_fault:
                self._latch_bus_fault(str(exc))
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
                         timeout=1.0, since_time=None, consume=False, physical_channel=None):
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError('CAN timeout must be finite and nonnegative')
        deadline = time.monotonic() + timeout
        if self._bus_fault:
            raise ConnectionError(self._bus_fault)
        stop_event = self._stop_event
        start_time = since_time if since_time is not None else time.time() - 0.005
        zlg_channel = int(self.channel.removeprefix('ZLG_CAN')) - 1 if self.channel.startswith('ZLG_CAN') else None
        if physical_channel is not None:
            physical = int(physical_channel)
            if not 1 <= physical <= (4 if zlg_channel is not None else 1):
                raise ValueError('所选物理通道不属于当前CAN卡')
            if zlg_channel is not None:
                zlg_channel = physical - 1
        # USB PCAN-FD 是单通道设备，旧配方中的逻辑通道号不再用于过滤 RX。
        while not stop_event.is_set():
            with self._rx_condition:
                indices = range(len(self._rx_history)) if consume else range(len(self._rx_history) - 1, -1, -1)
                for index in indices:
                    message = self._rx_history[index]
                    if message.get("timestamp_rel", 0) < start_time - 0.001:
                        if consume:
                            continue
                        break
                    if message.get("direction") != "RX":
                        continue
                    if message.get('is_error_frame') or message.get('is_remote_frame'):
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._rx_condition.wait(remaining)
        if self._bus_fault:
            raise ConnectionError(self._bus_fault)
        return None

    def send_and_wait_response(self, channel_id, can_id, can_type, dlc, data,
                               response_id, timeout=1.0, matcher=None):
        with self._request_lock:
            self.clear_rx_history(response_id)
            sent_at = time.time()
            if not self.send_can_message(channel_id, can_id, can_type, dlc, data):
                raise ConnectionError(self.last_error or 'CAN报文发送失败')
            return self.wait_for_message(response_id, predicate=matcher, timeout=timeout, since_time=sent_at)

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
        if not 0 <= value <= 15:
            raise ValueError('CAN DLC must be 0..15')
        return value if value <= 8 else {9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}[value]

    @staticmethod
    def length_to_dlc(length):
        value = int(length)
        if not 0 <= value <= 64:
            raise ValueError('CAN data length must be 0..64')
        if value <= 8: return value
        if value <= 12: return 9
        if value <= 16: return 10
        if value <= 20: return 11
        if value <= 24: return 12
        if value <= 32: return 13
        if value <= 48: return 14
        return 15
