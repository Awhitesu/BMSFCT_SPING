"""OpteeQ SG400 single-channel programmer driver."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import re
import tempfile
from pathlib import Path


class OpteeQSG400:
    """Thin wrapper around the vendor's 64-bit OpteeQ DLL.

    The SG400 is reached over LAN.  This application only uses channel 1.
    """

    PORT = 10023
    CHANNEL = 1
    STATE_NAMES = {1: "Busy", 2: "Ready", 3: "Error", 4: "Pass"}

    def __init__(self, ip: str = "192.168.0.100", port: int = PORT):
        self.ip = str(ip).strip()
        self.port = int(port)
        self.is_connected = False
        self.last_error = ""
        self._dll = None
        self._lock = threading.RLock()

    @property
    def address(self) -> str:
        return f"LAN{self.ip}:{self.port}"

    @staticmethod
    def _dll_path() -> Path:
        if getattr(sys, "frozen", False):
            base = Path(sys._MEIPASS)
        else:
            base = Path(__file__).resolve().parent.parent
        return base / "vendor" / "力捷峰U盘" / "Demo" / "OpteeQDLLx64.dll"

    def _load_dll(self):
        if self._dll is not None:
            return self._dll
        dll_path = self._dll_path()
        if not dll_path.is_file():
            raise FileNotFoundError(f"未找到力捷丰DLL: {dll_path}")
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll_path.parent))
        dll = ctypes.CDLL(str(dll_path))
        dll.OpteeQ_Connect.argtypes = [ctypes.c_char_p]
        dll.OpteeQ_Connect.restype = ctypes.c_int
        dll.OpteeQ_Disconnect.argtypes = [ctypes.c_char_p]
        dll.OpteeQ_Disconnect.restype = ctypes.c_int
        dll.OpteeQ_QueryState.argtypes = [ctypes.c_char_p, ctypes.c_int]
        dll.OpteeQ_QueryState.restype = ctypes.c_int
        dll.OpteeQ_SendCommand.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
        dll.OpteeQ_SendCommand.restype = ctypes.c_int
        dll.OpteeQ_ExecuteScript.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
        dll.OpteeQ_ExecuteScript.restype = ctypes.c_int
        dll.OpteeQ_GetCommandReply.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.c_uint,
        ]
        dll.OpteeQ_GetCommandReply.restype = ctypes.c_int
        # V1.14+ 新增的同步执行接口：内部完成 下发+轮询状态+取应答+取错误信息
        self._has_sync = hasattr(dll, "OpteeQ_ExecuteScriptSync")
        if self._has_sync:
            dll.OpteeQ_ExecuteScriptSync.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
                ctypes.c_char_p,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_uint),
                ctypes.c_char_p,
                ctypes.c_void_p,
            ]
            dll.OpteeQ_ExecuteScriptSync.restype = ctypes.c_int
        dll.OpteeQ_GetFile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        dll.OpteeQ_GetFile.restype = ctypes.c_int
        dll.OpteeQ_SendFile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        dll.OpteeQ_SendFile.restype = ctypes.c_bool
        self._dll = dll
        return dll

    def _address_bytes(self) -> bytes:
        return self.address.encode("utf-8")

    def connect(self) -> bool:
        with self._lock:
            self.last_error = ""
            try:
                result = self._load_dll().OpteeQ_Connect(self._address_bytes())
                self.is_connected = bool(result)
                if not self.is_connected:
                    self.last_error = f"力捷丰SG400连接失败: {self.ip}:{self.port}"
                return self.is_connected
            except Exception as exc:
                self.is_connected = False
                self.last_error = str(exc)
                return False

    def disconnect(self) -> bool:
        with self._lock:
            if self._dll is None or not self.is_connected:
                # 从未建立连接时无需厂商DLL确认，直接视为已释放（设备不在场退出不报错）
                self.is_connected = False
                return True
            try:
                result = self._dll.OpteeQ_Disconnect(self._address_bytes())
                return bool(result)
            except Exception as exc:
                self.last_error = str(exc)
                return False
            finally:
                self.is_connected = False

    def query_state(self) -> str:
        with self._lock:
            if not self.is_connected:
                return "Lost"
            try:
                code = int(self._dll.OpteeQ_QueryState(self._address_bytes(), self.CHANNEL))
                return self.STATE_NAMES.get(code, "Lost")
            except Exception as exc:
                self.last_error = str(exc)
                return "Lost"

    def send_command(self, command: str) -> bool:
        with self._lock:
            if not self.is_connected and not self.connect():
                return False
            try:
                result = self._dll.OpteeQ_SendCommand(
                    self._address_bytes(), self.CHANNEL, str(command).encode("utf-8")
                )
                if not result:
                    self.last_error = f"力捷丰命令执行失败: {command}"
                return bool(result)
            except Exception as exc:
                self.last_error = str(exc)
                return False

    def run_project(self, project_path: str) -> bool:
        project = str(project_path).strip()
        if not project:
            self.last_error = "未配置力捷丰烧录工程文件"
            return False
        if project.lower().endswith(".oprj"):
            return self.send_command(f"CCMD RunProjectFile -f {project}")
        if project.lower().endswith(".opj"):
            proj_name = os.path.basename(project.replace("\\", "/"))
            with self._lock:
                if not self.is_connected and not self.connect():
                    return False
                try:
                    if os.path.isfile(project):
                        dll = self._load_dll()
                        remote = f"/proj/{proj_name}".encode("utf-8")
                        if not dll.OpteeQ_SendFile(self._address_bytes(), os.path.abspath(project).encode("utf-8"), remote):
                            raise RuntimeError("本地工程上传失败，未执行烧录")
                        with tempfile.TemporaryDirectory(prefix="sg400_verify_") as folder:
                            check_path = os.path.join(folder, proj_name)
                            if not dll.OpteeQ_GetFile(self._address_bytes(), remote, check_path.encode("utf-8")):
                                raise RuntimeError("上传工程回传核对失败，未执行烧录")
                            with open(project, "rb") as source, open(check_path, "rb") as actual:
                                if source.read() != actual.read():
                                    raise RuntimeError("上传工程内容不一致，未执行烧录")
                    elif os.path.isabs(project):
                        raise FileNotFoundError(f"本地工程不存在: {project}")
                    cmd = f"channel:runproject('{proj_name}')".encode("utf-8")
                    result = self._dll.OpteeQ_ExecuteScript(
                        self._address_bytes(), self.CHANNEL, cmd
                    )
                    if not result:
                        self.last_error = f"力捷丰工程启动失败: {proj_name}"
                    return bool(result)
                except Exception as exc:
                    self.last_error = str(exc)
                    return False
        self.last_error = "力捷丰工程文件必须是 .oprj 或 .opj"
        return False

    def get_project_list(self, timeout_ms: int = 2000) -> list[str]:
        """从力捷丰下载器中读取已存储的脱机烧录工程列表 (/home/s2g/proj/)"""
        with self._lock:
            if not self.is_connected and not self.connect():
                return []
            try:
                dll = self._load_dll()
                # 发送获取工程列表脚本指令：必须使用 setreplystring 包装方可写入应答区回传
                script_cmd = b"channel:setreplystring(channel:getfilelist('/proj'))"
                exec_ret = dll.OpteeQ_ExecuteScript(self._address_bytes(), self.CHANNEL, script_cmd)
                if not exec_ret:
                    self.last_error = "发送 getfilelist 命令失败"
                    return []

                # 获取回复报文
                buf_size = 65536
                buf = ctypes.create_string_buffer(buf_size)
                recv_size = ctypes.c_uint(0)
                reply_ret = dll.OpteeQ_GetCommandReply(
                    self._address_bytes(),
                    self.CHANNEL,
                    buf,
                    buf_size,
                    ctypes.byref(recv_size),
                    timeout_ms,
                )
                if not reply_ret or recv_size.value == 0:
                    return []

                raw_text = buf.raw[:recv_size.value].decode("utf-8", errors="ignore")
                files = []
                for line in raw_text.splitlines():
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#") and cleaned != "empty":
                        files.append(cleaned)
                files.sort()
                return files
            except Exception as exc:
                self.last_error = f"读取下载器工程列表异常: {exc}"
                return []

    def download_project_file(self, remote_name: str, local_path: str) -> bool:
        """从力捷丰下载器回传/下载指定的工程文件到本地"""
        remote_name = str(remote_name).strip()
        if not remote_name:
            self.last_error = "远程工程名不能为空"
            return False

        # 兼容相对文件名或全路径
        if not remote_name.startswith("/"):
            remote_path = f"/home/s2g/proj/{remote_name}"
        else:
            remote_path = remote_name

        with self._lock:
            if not self.is_connected and not self.connect():
                return False
            try:
                dll = self._load_dll()
                target_bytes = remote_path.encode("utf-8")
                local_bytes = os.path.normpath(local_path).encode("utf-8")
                ret = dll.OpteeQ_GetFile(self._address_bytes(), target_bytes, local_bytes)
                if not ret:
                    self.last_error = f"从下载器下载文件失败: {remote_path} -> {local_path}"
                    return False
                return True
            except Exception as exc:
                self.last_error = f"下载文件异常: {exc}"
                return False

    def get_checksum(self, timeout_ms: int = 2000) -> str:
        """从力捷丰下载器中获取当前芯片的硬件实测校验和"""
        with self._lock:
            if not self.is_connected and not self.connect():
                return ""
            try:
                dll = self._load_dll()
                cmd = b"channel:setreplystring(channel:getchecksum())"
                exec_ret = dll.OpteeQ_ExecuteScript(self._address_bytes(), self.CHANNEL, cmd)
                if not exec_ret:
                    return ""
                buf = ctypes.create_string_buffer(1024)
                recv_size = ctypes.c_uint(0)
                reply_ret = dll.OpteeQ_GetCommandReply(
                    self._address_bytes(),
                    self.CHANNEL,
                    buf,
                    1024,
                    ctypes.byref(recv_size),
                    timeout_ms,
                )
                if not reply_ret or recv_size.value == 0:
                    return ""
                return buf.raw[:recv_size.value].decode("utf-8", errors="ignore").strip()
            except Exception as exc:
                self.last_error = f"获取硬件校验和异常: {exc}"
                return ""

    def get_recent_log(self, max_lines: int = 50) -> str:
        """从力捷丰下载器内部拉取最新运行工序日志 (/log/ch.log)"""
        with self._lock:
            if not self.is_connected and not self.connect():
                return ""
            try:
                temp_log = os.path.join(
                    os.environ.get("TEMP", "."), f"sg400_ch_{time.time_ns()}.log"
                )
                dll = self._load_dll()
                ret = dll.OpteeQ_GetFile(
                    self._address_bytes(),
                    b"/log/ch.log",
                    os.path.normpath(temp_log).encode("utf-8"),
                )
                if not ret or not os.path.exists(temp_log):
                    return ""
                with open(temp_log, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                try:
                    os.remove(temp_log)
                except Exception:
                    pass
                return "".join(lines[-max_lines:])
            except Exception as exc:
                self.last_error = f"拉取运行日志异常: {exc}"
                return ""

    def read_chip(self, project: str, start_addr: int, size: int, timeout_ms: int = None) -> bytes:
        """调用 channel:readchip 从芯片内部 Flash 物理回读实际烧录的数据。

        设备应答可能是原始字节，也可能是 Intel HEX 文本（体积约 2.3 倍），
        缓冲按 3 倍预留，HEX 自动解码。
        DLL V1.14+ 优先走官方推荐的 ExecuteScriptSync（内部完成状态轮询和应答
        收取）；旧 DLL 回退到 ExecuteScript + 等状态完成 + GetCommandReply。
        """
        with self._lock:
            if not self.is_connected and not self.connect():
                return b""
            try:
                dll = self._load_dll()
                proj_name = os.path.basename(project.replace("\\", "/"))
                cmd = f"channel:readchip('{proj_name}', {start_addr}, {size})".encode("utf-8")
                if not timeout_ms:
                    # 总超时按回读大小自适应（394KB 约 39s，上限 180s）
                    timeout_ms = min(180000, max(30000, size // 10))
                buf_size = size * 3 + 4096

                if getattr(self, "_has_sync", False):
                    buf = ctypes.create_string_buffer(buf_size)
                    err_buf = ctypes.create_string_buffer(256)
                    recv_size = ctypes.c_uint(0)
                    ok = dll.OpteeQ_ExecuteScriptSync(
                        self._address_bytes(),
                        self.CHANNEL,
                        cmd,
                        timeout_ms,
                        buf,
                        buf_size,
                        ctypes.byref(recv_size),
                        err_buf,
                        None,
                    )
                    if not ok:
                        err = err_buf.value.decode("utf-8", errors="ignore").strip()
                        self.last_error = f"readchip 执行失败: {err or '未知错误'}"
                        return b""
                    data = buf.raw[: recv_size.value]
                else:
                    data = self._read_chip_legacy(cmd, buf_size, timeout_ms)
                    if not data and not self.last_error:
                        return b""

                if not data:
                    self.last_error = f"回读无数据返回 (超时 {timeout_ms}ms)"
                    return b""
                hex_text = re.sub(rb"\s+", b"", data.strip().rstrip(b"\x00"))
                if len(hex_text) == size * 2 and re.fullmatch(rb"[0-9a-fA-F]+", hex_text):
                    return bytes.fromhex(hex_text.decode("ascii"))
                # Intel HEX 文本应答则解码为原始二进制
                if data.lstrip()[:1] == b":":
                    decoded = self._decode_intel_hex(data, start_addr, size)
                    if decoded:
                        return decoded
                if len(data) != size:
                    self.last_error = f'回读长度不一致：期望{size}字节，实际{len(data)}字节'
                    return b''
                return data
            except Exception as exc:
                self.last_error = f"回读芯片数据异常: {exc}"
                return b""

    def _read_chip_legacy(self, cmd: bytes, buf_size: int, timeout_ms: int) -> bytes:
        """旧 DLL（V1.14 以下）的回读路径：先等通道跑完，再取应答。"""
        dll = self._load_dll()
        if not dll.OpteeQ_ExecuteScript(self._address_bytes(), self.CHANNEL, cmd):
            self.last_error = "readchip 命令下发失败"
            return b""
        deadline = time.monotonic() + timeout_ms / 1000.0
        # 先轮询通道状态，直到不为 Busy（官方流程：数据在执行完成后才可取）
        while time.monotonic() < deadline:
            code = int(dll.OpteeQ_QueryState(self._address_bytes(), self.CHANNEL))
            if code != 1:  # 非 Busy：Ready/Error/Pass 均表示执行已结束
                break
            time.sleep(0.2)
        if code == 1:
            self.last_error = f"readchip 执行超时 ({timeout_ms}ms)"
            return b""
        if code not in (2, 4):
            self.last_error = "readchip 执行报错（通道状态 Error）"
            return b""
        # 执行完成后再取应答，大数据量可能分多帧，循环收取
        buf = ctypes.create_string_buffer(buf_size)
        chunks = []
        while time.monotonic() < deadline:
            recv_size = ctypes.c_uint(0)
            # 尚未收到数据时每次等 10s；收到后用 1.5s 空闲判定结束
            wait_ms = 10000 if not chunks else 1500
            reply_ret = dll.OpteeQ_GetCommandReply(
                self._address_bytes(),
                self.CHANNEL,
                buf,
                buf_size,
                ctypes.byref(recv_size),
                wait_ms,
            )
            if reply_ret and recv_size.value > 0:
                chunks.append(buf.raw[: recv_size.value])
            elif chunks:
                break
        return b"".join(chunks)

    @staticmethod
    def _decode_intel_hex(data: bytes, base_addr: int, size: int) -> bytes:
        """把 Intel HEX 文本应答解码为从 base_addr 开始的连续二进制"""
        try:
            text = data.decode("ascii", errors="ignore")
            upper = 0
            out = bytearray()
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith(":") or len(line) < 11:
                    continue
                rectype = int(line[7:9], 16)
                if rectype == 0x04:  # 扩展线性地址
                    upper = int(line[9:13], 16) << 16
                elif rectype == 0x00:
                    addr = upper + int(line[3:7], 16)
                    count = int(line[1:3], 16)
                    payload = bytes.fromhex(line[9 : 9 + count * 2])
                    off = addr - base_addr
                    if off < 0:
                        continue
                    if off + len(payload) > len(out):
                        out.extend(b"\x00" * (off + len(payload) - len(out)))
                    out[off : off + len(payload)] = payload
                elif rectype == 0x01:  # EOF
                    break
            return bytes(out[:size]) if out else b""
        except Exception:
            return b""
