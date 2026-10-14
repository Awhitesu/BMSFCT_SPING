"""Renesas E2 emulator driver via rfp-cli command line."""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from core.background_process import background_process_options


class RenesasE2Rfp:
    """Thin wrapper around Renesas Flash Programmer CLI (rfp-cli.exe).

    E2 仿真器无实体长连接，所有操作均通过 subprocess 调 rfp-cli 完成。
    """

    STATE_NAMES = {0: "Busy", 1: "Ready", 2: "Error", 3: "Pass", 4: "Lost"}

    # rfp-cli 命令模板（确切参数以安装目录 rfp-cli.md 为准，日后可在此校准）
    FLASH_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -erase -program -verify {file}"
    CHECKSUM_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -checksum"
    READ_CMD_TEMPLATE = "-d {family} -t e2 -if {interface} -s {speed} -read {start} {size} {out_file}"
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
            start=f"0x{int(start_addr):X}", size=int(size), out_file=out_file,
        )

    def build_rpj_command(self, file_path: str) -> list[str]:
        return self._build_command(self._resolve_rpj_exe(), self.RPJ_CMD_TEMPLATE, file=file_path)

    def connect(self) -> bool:
        with self._lock:
            self.last_error = ""
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
                    self.is_connected = True
                    version = (result.stdout or "").strip().splitlines()
                    self._log(f"rfp-cli 已就绪: {exe} ({version[0] if version else 'unknown'})")
                    return True
                self.is_connected = False
                self.last_error = f"rfp-cli --version 返回码 {result.returncode}: {(result.stderr or '').strip()}"
                return False
            except Exception as exc:
                self.is_connected = False
                self.last_error = str(exc)
                return False

    def disconnect(self) -> bool:
        with self._lock:
            self.is_connected = False
            self._state = "Ready"
            return True

    def query_state(self) -> str:
        with self._lock:
            if not self.is_connected:
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
                self._log(f"烧录完成 (returncode=0, 耗时统计见上位机)")
                return True
            self._state = "Error"
            self.last_error = f"rfp-cli 烧录失败 (returncode={proc.returncode})"
            self._log(self.last_error)
            return False

    def get_checksum(self) -> str:
        """调用 rfp-cli checksum 命令获取芯片实测校验和"""
        with self._lock:
            if not self.is_connected:
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
        """rfp-cli 回读芯片 Flash 数据到本地文件（深度比对用）"""
        with self._lock:
            if not self.is_connected:
                return False
            cmd = self.build_read_command(start_addr, size, out_file)
        if not cmd[0] or not os.path.isfile(cmd[0]):
            self.last_error = "未找到瑞萨烧录工具可执行文件"
            return False
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", timeout=120, **background_process_options(),
            )
            if result.returncode != 0 or not os.path.isfile(out_file):
                self.last_error = f"回读芯片数据失败 (returncode={result.returncode})"
                return False
            return True
        except Exception as exc:
            self.last_error = f"回读芯片数据异常: {exc}"
            return False

    def get_recent_log(self, max_lines: int = 60) -> str:
        """返回驱动内部环形日志缓冲（E2 无设备侧日志文件可拉取）"""
        with self._lock:
            lines = list(self._log_buffer)
        return "\n".join(lines[-max_lines:])
