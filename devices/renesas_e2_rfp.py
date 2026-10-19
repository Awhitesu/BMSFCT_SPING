"""Renesas E2 emulator driver via rfp-cli command line."""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import threading
import tempfile

from collections import deque
from pathlib import Path
from core.background_process import background_process_options
from .windows_usb import present_usb_devices


class RenesasE2Rfp:
    """Thin wrapper around Renesas Flash Programmer CLI (rfp-cli.exe).

    所有操作通过独立 rfp-cli 进程完成，不持有 E2/目标的持续连接。
    tool_ready表示工具可运行；is_connected表示Windows识别到可用E2 USB节点。
    USB识别不表示目标MCU连接已验证，也不持有目标的持续连接。
    """

    STATE_NAMES = {0: "Busy", 1: "Ready", 2: "Error", 3: "Pass", 4: "Lost"}

    # rfp-cli 命令模板（确切参数以安装目录 rfp-cli.md 为准，日后可在此校准）
    FLASH_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -erase -program -verify {file}"
    CHECKSUM_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -checksum"
    READ_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -read-bin {start} {size} {out_file}"
    RPJ_CMD_TEMPLATE = "{file} /silent"

    def __init__(self, rfp_cli_path: str = "", device_family: str = "RH850",
                 interface: str = "", speed: str = "", target_voltage: str = "",
                 logger=None):
        self.rfp_cli_path = str(rfp_cli_path).strip()
        self.device_family = str(device_family).strip() or "RH850"
        self.interface = str(interface).strip()
        self.speed = str(speed).strip()
        self.target_voltage = str(target_voltage).strip()
        self._logger = logger
        self.is_connected = False
        self.tool_ready = False
        self.presence_checked = False
        self.presence_error = ''
        self.usb_device_id = ''
        self.last_error = ""
        self._state = "Ready"
        self._lock = threading.RLock()
        self._log_buffer = deque(maxlen=500)

    def _log(self, message: str):
        message = str(message)
        self._log_buffer.append(message)
        print(f"[RenesasE2Rfp] {message}")
        if self._logger:
            try:
                self._logger(message)
            except Exception:
                pass

    @staticmethod
    def _default_install_root() -> str:
        return os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Renesas Electronics", "Programming Tools",
        )

    @staticmethod
    def _glob_exe(exe_name: str) -> str:
        pattern = os.path.join(RenesasE2Rfp._default_install_root(), "**", exe_name)
        matches = glob.glob(pattern, recursive=True)
        return sorted(matches)[-1] if matches else ""

    def _resolve_exe(self) -> str:
        if self.rfp_cli_path and os.path.isfile(self.rfp_cli_path):
            return self.rfp_cli_path
        if getattr(sys, "frozen", False):
            candidate = Path(sys._MEIPASS) / "rfp-cli" / "rfp-cli.exe"
            if candidate.is_file():
                return str(candidate)
        return self._glob_exe("rfp-cli.exe")

    def _resolve_rpj_exe(self) -> str:
        cli_exe = self._resolve_exe()
        if cli_exe:
            sibling = os.path.join(os.path.dirname(cli_exe), "RenesasFlashProgrammer.exe")
            if os.path.isfile(sibling):
                return sibling
        if getattr(sys, "frozen", False):
            candidate = Path(sys._MEIPASS) / "rfp-cli" / "RenesasFlashProgrammer.exe"
            if candidate.is_file():
                return str(candidate)
        return self._glob_exe("RenesasFlashProgrammer.exe")

    def _build_command(self, exe: str, template: str, **params) -> list[str]:
        return [exe] + [token.format(**params) for token in template.split()]

    def build_flash_command(self, file_path: str) -> list[str]:
        return self._build_command(
            self._resolve_exe(), self.FLASH_CMD_TEMPLATE,
            family=self.device_family, interface=self.interface,
            speed=self.speed, file=file_path,
        )

    def build_checksum_command(self) -> list[str]:
        return self._build_command(
            self._resolve_exe(), self.CHECKSUM_CMD_TEMPLATE,
            family=self.device_family, interface=self.interface, speed=self.speed,
        )

    def build_read_command(self, start_addr: int, size: int, out_file: str) -> list[str]:
        return self._build_command(
            self._resolve_exe(), self.READ_CMD_TEMPLATE,
            family=self.device_family, interface=self.interface, speed=self.speed,
            start=f"{int(start_addr):X}", size=int(size), out_file=out_file,
        )

    def build_rpj_command(self, file_path: str) -> list[str]:
        return self._build_command(self._resolve_rpj_exe(), self.RPJ_CMD_TEMPLATE, file=file_path)

    def connect(self) -> bool:
        """Check the local CLI and actual E2 USB presence, without touching the MCU."""
        with self._lock:
            self.last_error = ""
            self.is_connected = False
            self.tool_ready = False
            self.presence_checked = False
            self.presence_error = ''
            self.usb_device_id = ''
            self._state = "Ready"
            exe = self._resolve_exe()
            if not exe or not os.path.isfile(exe):
                self.is_connected = False
                self.last_error = "未找到 rfp-cli.exe，请在硬件管理中心配置路径或安装瑞萨烧录工具"
                return False
            try:
                result = subprocess.run(
                    [exe, "--version"], capture_output=True, text=True,
                    errors="replace", timeout=15, **background_process_options(),
                )
                if result.returncode == 0:
                    self.tool_ready = True
                    version = (result.stdout or "").strip().splitlines()
                    self._log(f"rfp-cli 工具就绪: {exe} ({version[0] if version else 'unknown'})")
                    return self.refresh_presence()
                self.is_connected = False
                self.last_error = f"rfp-cli --version 返回码 {result.returncode}: {(result.stderr or '').strip()}"
                return False
            except Exception as exc:
                self.is_connected = False
                self.last_error = str(exc)
                return False

    @staticmethod
    def _is_e2_node(instance_id):
        # Renesas official E2usb_x64.inf and E2usb_x64_winusb.inf:
        # 045B:82A1 = E2; 82A0 = E2 Lite, 0250 = IE850A (not this device).
        parts = instance_id.upper().split('\\')
        return (len(parts) >= 3 and parts[0] == 'USB'
                and {'VID_045B', 'PID_82A1'}.issubset(parts[1].split('&')))

    def refresh_presence(self):
        """Refresh only the USB node cache. Skip a busy driver rather than block UI tasks."""
        if not self._lock.acquire(blocking=False):
            return self.is_connected
        try:
            self.is_connected = False
            self.usb_device_id = ''
            self.presence_checked = False
            self.presence_error = ''
            if not self.tool_ready:
                return False
            try:
                matches = [node for node in present_usb_devices() if self._is_e2_node(node.instance_id)]
                self.presence_checked = True
                usable = [node for node in matches if node.started and node.problem_code == 0]
                if not matches:
                    error = '未检测到瑞萨E2 USB设备，请检查USB连接和E2供电（工具已就绪）'
                elif not usable:
                    error = '检测到E2，但设备未启动或驱动异常，问题码: ' + ', '.join(str(n.problem_code) for n in matches)
                else:
                    self.usb_device_id = usable[0].instance_id
                    self.is_connected = True
                    if self.last_error == getattr(self, '_last_presence_error', ''):
                        self.last_error = ''
                    self._last_presence_error = ''
                    return True
            except Exception as exc:
                error = f'E2 USB在位检测失败: {exc}'
            self.presence_error = self._last_presence_error = error
            self.last_error = error
            return False
        finally:
            self._lock.release()

    def disconnect(self) -> bool:
        with self._lock:
            self.is_connected = False
            self.tool_ready = False
            self.presence_checked = False
            self.presence_error = ''
            self.usb_device_id = ''
            self._state = "Ready"
            return True

    def query_state(self) -> str:
        """Refresh USB presence and return last-operation state; never probe the MCU."""
        if not self.refresh_presence():
            return 'Lost'
        with self._lock:
            if not self.tool_ready:
                return "Lost"
            return self._state

    def run_project(self, project_path: str, timeout_s: int = 300, progress_cb=None) -> bool:
        project = str(project_path).strip()
        if not project:
            self.last_error = "未配置瑞萨E2烧录工程文件"
            return False
        lower = project.lower()
        if not lower.endswith((".rpj", ".mot", ".hex", ".srec")):
            self.last_error = "瑞萨E2工程文件必须是 .rpj / .mot / .hex / .srec"
            return False
        if not os.path.isfile(project):
            self.last_error = f"工程文件不存在: {project}"
            return False
        if not self.tool_ready:
            if not self.connect():
                return False
        elif not self.refresh_presence():
            return False
        if lower.endswith(".rpj"):
            cmd = self.build_rpj_command(project)
        else:
            cmd = self.build_flash_command(project)
        if not cmd[0] or not os.path.isfile(cmd[0]):
            self.last_error = "未找到瑞萨烧录工具可执行文件"
            return False

        with self._lock:
            self._state = "Busy"
        self._log("执行命令: " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", **background_process_options(),
            )
        except Exception as exc:
            self.last_error = f"启动 rfp-cli 失败: {exc}"
            with self._lock:
                self._state = "Error"
            return False

        # Starting a CLI process proves tool availability, not a live session.
        with self._lock:
            self.tool_ready = True
        output_lines = []

        def _reader():
            try:
                for raw in proc.stdout:
                    line = str(raw).rstrip("\r\n")
                    output_lines.append(line)
                    self._log(line)
                    if progress_cb:
                        try:
                            progress_cb(line)
                        except Exception:
                            pass
            except Exception:
                pass

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=max(1, int(timeout_s)))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait()
        reader.join(timeout=5)

        has_error_line = any("[Error]" in line or "ERROR" in line.upper() for line in output_lines)
        with self._lock:
            if timed_out:
                self._state = "Error"
                self.last_error = f"rfp-cli 执行超时 ({timeout_s}s)，进程已强制结束"
                self._log(self.last_error)
                return False
            if proc.returncode == 0 and not has_error_line:
                self._state = "Pass"
                self._log("烧录完成 (returncode=0, 耗时统计见上位机)")
                return True
            self._state = "Error"
            self.last_error = f"rfp-cli 烧录失败 (returncode={proc.returncode})"
            self._log(self.last_error)
            return False

    def get_checksum(self) -> str:
        """调用 rfp-cli checksum 命令获取芯片实测校验和"""
        if not self.refresh_presence():
            return ''
        with self._lock:
            if not self.tool_ready:
                return ""
            cmd = self.build_checksum_command()
        if not cmd[0] or not os.path.isfile(cmd[0]):
            return ""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", timeout=60, **background_process_options(),
            )
            text = (result.stdout or "") + "\n" + (result.stderr or "")
            for line in text.splitlines():
                self._log_buffer.append(line.rstrip())
            if result.returncode != 0:
                return ""
            for line in text.splitlines():
                if "checksum" in line.lower():
                    found = re.findall(r"0x[0-9A-Fa-f]+|[0-9A-Fa-f]{8}", line)
                    if found:
                        return found[-1]
            found = re.findall(r"0x[0-9A-Fa-f]+", text)
            return found[-1] if found else ""
        except Exception as exc:
            self.last_error = f"获取硬件校验和异常: {exc}"
            return ""

    def read_chip(self, start_addr: int, size: int, out_file: str) -> bool:
        """Publish only a fresh, complete binary readback; preserve old files on failure."""
        if not self.refresh_presence():
            return False
        with self._lock:
            if not self.tool_ready:
                self.last_error = "E2 tool is not ready"
                return False
            try:
                if type(start_addr) is not int or start_addr < 0 or type(size) is not int or size <= 0:
                    raise ValueError("Invalid readback address or size")
                destination = Path(out_file).resolve()
                with tempfile.TemporaryDirectory(prefix="e2_read_", dir=destination.parent) as folder:
                    fresh = Path(folder) / "readback.bin"
                    cmd = self.build_read_command(start_addr, size, str(fresh))
                    if not cmd[0] or not os.path.isfile(cmd[0]):
                        raise FileNotFoundError("Renesas executable not found")
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, errors="replace", timeout=120,
                        **background_process_options())
                    if result.returncode != 0 or not fresh.is_file():
                        raise RuntimeError(f"Readback failed (returncode={result.returncode})")
                    if fresh.stat().st_size != size:
                        raise ValueError(f"Incomplete readback: {fresh.stat().st_size}/{size} bytes")
                    os.replace(fresh, destination)
                self.last_error = ""
                return True
            except Exception as exc:
                self.last_error = f"Readback failed: {exc}"
                return False

    def get_recent_log(self, max_lines: int = 60) -> str:
        """返回驱动内部环形日志缓冲（E2 无设备侧日志文件可拉取）"""
        with self._lock:
            lines = list(self._log_buffer)
        return "\n".join(lines[-max_lines:])
