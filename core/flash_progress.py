"""Passive ISO-TP/UDS observation; this module never transmits CAN frames."""

import threading
import time


class FlashProgress:
    MAX_PAYLOAD = 1024 * 1024

    def __init__(self, request_id=0x7E4, response_id=0x7EC, clock=time.monotonic):
        self.request_id = request_id
        self.response_id = response_id
        self.clock = clock
        self._lock = threading.RLock()
        self._assemblies = {}
        self.phase = "等待刷写报文"
        self.warning = ""
        self.last_message = None
        self.segment = 0
        self.total = 0
        self.confirmed = 0
        self.reliable = False
        self.accepted = False
        self.download_request = None
        self.pending = None
        self.next_block = 1
        self.last_block = None
        self.received_frames = 0
        self.observed_ids = {}

    def invalidate(self, reason):
        with self._lock:
            self.reliable = False
            self.warning = reason

    def feed(self, message):
        """Called by the existing receive thread, without consuming its queues."""
        if message.get("direction", "RX") != "RX":
            return
        can_id = message.get("can_id")
        with self._lock:
            self.received_frames += 1
            if can_id in self.observed_ids or len(self.observed_ids) < 32:
                self.observed_ids[can_id] = self.observed_ids.get(can_id, 0) + 1
        if message.get("is_error_frame"):
            self.invalidate("CAN错误帧，进度未知")
            return
        if can_id not in (self.request_id, self.response_id, 0x7DF):
            return
        data = bytes(message.get("data", b""))
        if not data:
            return
        with self._lock:
            now = self.clock()
            self.last_message = now
            kind = data[0] >> 4
            if kind == 3:  # Flow control belongs to the real flasher/ECU.
                return
            payload = None
            if kind == 0:
                if can_id in self._assemblies:
                    self._assemblies.pop(can_id)
                    self.invalidate("ISO-TP数据不完整，进度未知")
                size, offset = data[0] & 0x0F, 1
                if size == 0:
                    if len(data) < 2:
                        self.invalidate("ISO-TP单帧不完整")
                        return
                    size, offset = data[1], 2  # CAN-FD escaped single frame
                if not size or size > len(data) - offset:
                    self.invalidate("ISO-TP单帧长度异常")
                    return
                payload = data[offset:offset + size]
            elif kind == 1:
                if len(data) < 2:
                    self.invalidate("ISO-TP首帧不完整")
                    return
                size, offset = ((data[0] & 0x0F) << 8) | data[1], 2
                if size == 0:
                    if len(data) < 6:
                        self.invalidate("ISO-TP扩展首帧不完整")
                        return
                    size, offset = int.from_bytes(data[2:6], "big"), 6
                if not 0 < size <= self.MAX_PAYLOAD:
                    self.invalidate("ISO-TP长度超出监听范围")
                    return
                if can_id in self._assemblies:
                    self.invalidate("ISO-TP报文丢失，进度未知")
                self._assemblies[can_id] = [size, bytearray(data[offset:]), 1, now]
            elif kind == 2:
                assembly = self._assemblies.get(can_id)
                if assembly is None:
                    self.invalidate("未捕获ISO-TP首帧，进度未知")
                    return
                size, buffer, sequence, updated = assembly
                if (data[0] & 0x0F) != sequence or now - updated > 2.0:
                    self._assemblies.pop(can_id, None)
                    self.invalidate("ISO-TP漏帧或超时，进度未知")
                    return
                buffer.extend(data[1:])
                assembly[2:] = [(sequence + 1) & 0x0F, now]
                if len(buffer) >= size:
                    payload = bytes(buffer[:size])
                    self._assemblies.pop(can_id, None)
            if payload:
                if can_id == self.response_id:
                    self._response(payload)
                elif can_id == self.request_id:
                    self._request(payload)
                elif payload[0] == 0x10:
                    self.phase = "切换诊断会话"

    def _request(self, payload):
        sid = payload[0]
        if sid == 0x34:
            if len(payload) < 3:
                self.invalidate("下载请求不完整，进度未知")
                return
            address_len = payload[2] & 0x0F
            size_len = payload[2] >> 4
            if not address_len or not size_len or len(payload) < 3 + address_len + size_len:
                self.invalidate("下载长度缺失，进度未知")
                return
            # A repeated, not-yet-accepted RequestDownload is not a new segment.
            if self.download_request == payload and not self.accepted:
                return
            self.download_request = payload
            self.segment += 1
            self.total = int.from_bytes(payload[3 + address_len:3 + address_len + size_len], "big")
            self.confirmed = 0
            self.pending = None
            self.next_block = 1
            self.last_block = None
            self.accepted = False
            self.reliable = payload[1] == 0 and self.total > 0
            self.warning = "" if self.reliable else "压缩/加密或长度未知，仅显示阶段"
            self.phase = "请求下载"
        elif sid == 0x36:
            self.phase = "数据传输中"
            if len(payload) < 2:
                self.invalidate("数据块不完整，进度未知")
                return
            block = payload[1]
            count = len(payload) - 2  # exclude SID and block counter, not CAN padding
            if not self.accepted:
                self.invalidate("未捕获完整下载起点，进度未知")
            if self.pending and self.pending != (block, count):
                self.invalidate("缺少数据块确认，进度未知")
            if block not in (self.next_block, self.last_block):
                self.invalidate("数据块序号不连续，进度未知")
            self.pending = (block, count)
        elif sid == 0x37:
            self.phase = "结束数据传输"
        elif sid == 0x10:
            self.phase = "切换编程会话" if len(payload) > 1 and payload[1] == 2 else "切换诊断会话"
        elif sid == 0x27:
            self.phase = "安全解锁"
        elif sid == 0x31 and len(payload) >= 4:
            routine = int.from_bytes(payload[2:4], "big")
            self.phase = {0xFF00: "擦除中", 0x0202: "校验中", 0xFF01: "检查编程结果"}.get(
                routine, f"执行例程 0x{routine:04X}"
            )
        elif sid == 0x11:
            self.phase = "ECU复位中"

    def _response(self, payload):
        sid = payload[0]
        if sid == 0x74:
            if self.download_request is None:
                self.invalidate("未捕获下载请求，进度未知")
            else:
                self.accepted = True
                self.phase = "准备传输"
        elif sid == 0x76 and len(payload) >= 2:
            block = payload[1]
            if self.pending is None:
                if block != self.last_block:
                    self.invalidate("缺少已确认数据块，进度未知")
                return
            pending_block, count = self.pending
            if block != pending_block:
                self.invalidate("确认序号与数据块不一致，进度未知")
                return
            if block == self.next_block:
                self.confirmed += count
                self.last_block = block
                self.next_block = (block + 1) & 0xFF
                if self.confirmed > self.total:
                    self.invalidate("累计数据超过下载长度，进度未知")
            # A retransmission of the last acknowledged block does not increment.
            self.pending = None
            self.phase = "数据传输中"
            if self.total and self.confirmed == self.total:
                self.phase = "传输完成，等待校验"
        elif sid == 0x77:
            if self.confirmed != self.total or self.pending:
                self.invalidate("传输结束但抓包不完整，进度未知")
            self.phase = "传输结束，等待校验"
        elif sid == 0x51:
            self.phase = "复位完成，等待Record结果"
        elif sid == 0x7F and len(payload) >= 3:
            if payload[2] == 0x78:
                # ResponsePending is normal during erase/checksum, not failure.
                return
            self.warning = f"ECU否定应答：服务0x{payload[1]:02X} / NRC 0x{payload[2]:02X}"
            if payload[1] == 0x36:
                self.pending = None

    def snapshot(self):
        with self._lock:
            now = self.clock()
            for can_id, assembly in list(self._assemblies.items()):
                if now - assembly[3] > 2.0:
                    self._assemblies.pop(can_id)
                    self.invalidate("ISO-TP接收超时，进度未知")
            percent = None
            if self.reliable and self.accepted and self.total:
                percent = 100.0 if self.confirmed == self.total else min(
                    99.9, round(100.0 * self.confirmed / self.total, 1)
                )
            return {
                "received_frames": self.received_frames,
                "observed_ids": [f'0x{key:X}' for key in self.observed_ids if isinstance(key, int)],
                "phase": self.phase, "percent": percent, "segment": self.segment,
                "confirmed_bytes": self.confirmed, "total_bytes": self.total,
                "can_warning": self.warning,
                "last_can_age": None if self.last_message is None else max(0.0, now - self.last_message),
            }

