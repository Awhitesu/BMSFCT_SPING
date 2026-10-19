import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from devices.renesas_e2_rfp import RenesasE2Rfp
from devices.windows_usb import UsbDevice


class FakeProc:
    """模拟 subprocess.Popen 进程对象"""

    def __init__(self, lines=(), returncode=0, timeout=False):
        self.stdout = iter(lines)
        self._returncode = returncode
        self._timeout = timeout
        self.killed = False

    @property
    def returncode(self):
        return self._returncode

    def wait(self, timeout=None):
        if self._timeout and timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired(cmd="rfp-cli", timeout=timeout)
        return self._returncode

    def kill(self):
        self.killed = True
        self._returncode = -9


class TestRenesasE2RfpDriver(unittest.TestCase):
    def setUp(self):
        self.drv = RenesasE2Rfp(
            rfp_cli_path="C:/rfp/rfp-cli.exe",
            device_family="RH850",
            interface="FINE",
            speed="1000000",
        )
        self.drv.tool_ready = True
        probe = patch('devices.renesas_e2_rfp.present_usb_devices', return_value=[
            UsbDevice(r'USB\VID_045B&PID_82A1\TEST_E2', True, 0)])
        probe.start()
        self.addCleanup(probe.stop)

    def test_build_flash_command(self):
        with patch("os.path.isfile", return_value=True):
            cmd = self.drv.build_flash_command("C:/proj/app.hex")
        self.assertEqual(
            cmd,
            ["C:/rfp/rfp-cli.exe", "-d", "RH850", "-t", "e2", "-if", "FINE",
             "-s", "1000000", "-erase", "-program", "-verify", "C:/proj/app.hex"],
        )

    def test_build_checksum_command(self):
        with patch("os.path.isfile", return_value=True):
            cmd = self.drv.build_checksum_command()
        self.assertEqual(
            cmd,
            ["C:/rfp/rfp-cli.exe", "-d", "RH850", "-t", "e2", "-if", "FINE",
             "-s", "1000000", "-checksum"],
        )

    def test_build_read_command(self):
        with patch("os.path.isfile", return_value=True):
            cmd = self.drv.build_read_command(0, 256, "C:/tmp/out.bin")
        self.assertEqual(
            cmd,
            ["C:/rfp/rfp-cli.exe", "-d", "RH850", "-t", "e2", "-if", "FINE",
             "-s", "1000000", "-read-bin", "0", "256", "C:/tmp/out.bin"],
        )

    def test_resolve_exe_configured_path(self):
        with patch("os.path.isfile", return_value=True):
            self.assertEqual(self.drv._resolve_exe(), "C:/rfp/rfp-cli.exe")

    def test_resolve_exe_glob_fallback(self):
        drv = RenesasE2Rfp(rfp_cli_path="")
        with patch("devices.renesas_e2_rfp.glob.glob",
                   return_value=["C:/tools/v1/rfp-cli.exe", "C:/tools/v2/rfp-cli.exe"]):
            self.assertEqual(drv._resolve_exe(), "C:/tools/v2/rfp-cli.exe")

    def test_connect_success(self):
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="RFP CLI V3.15\n", stderr="")
            self.assertTrue(self.drv.connect())
        self.assertTrue(self.drv.tool_ready)
        self.assertTrue(self.drv.is_connected)
        self.assertEqual(self.drv.query_state(), "Ready")
        self.assertEqual(self.drv.last_error, "")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["C:/rfp/rfp-cli.exe", "--version"])

    def test_connect_version_probe_failure(self):
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad")
            self.assertFalse(self.drv.connect())
        self.assertFalse(self.drv.is_connected)
        self.assertIn("返回码 1", self.drv.last_error)

    def test_connect_exe_missing(self):
        drv = RenesasE2Rfp(rfp_cli_path="")
        with patch("devices.renesas_e2_rfp.glob.glob", return_value=[]):
            self.assertFalse(drv.connect())
        self.assertIn("未找到 rfp-cli.exe", drv.last_error)

    def test_query_state_lost_when_disconnected(self):
        self.drv.tool_ready = False
        self.assertEqual(self.drv.query_state(), "Lost")
        self.drv.tool_ready = True
        self.assertEqual(self.drv.query_state(), "Ready")
        self.assertTrue(self.drv.is_connected)

    def test_run_project_success(self):
        fake = FakeProc(lines=["Connecting...\n", "Program OK\n"], returncode=0)
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.Popen", return_value=fake) as mock_popen:
            lines = []
            ok = self.drv.run_project("C:/proj/app.hex", timeout_s=60, progress_cb=lines.append)
        self.assertTrue(ok)
        self.assertEqual(self.drv._state, "Pass")
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "C:/rfp/rfp-cli.exe")
        self.assertIn("-erase", cmd)
        self.assertIn("C:/proj/app.hex", cmd)
        self.assertIn("Program OK", lines)

    def test_run_project_error_line_fails(self):
        fake = FakeProc(lines=["[Error] device not found\n"], returncode=0)
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.Popen", return_value=fake):
            ok = self.drv.run_project("C:/proj/app.hex", timeout_s=60)
        self.assertFalse(ok)
        self.assertEqual(self.drv._state, "Error")
        self.assertIn("returncode=0", self.drv.last_error)

    def test_run_project_timeout_kills(self):
        fake = FakeProc(lines=["Busy...\n"], timeout=True)
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.Popen", return_value=fake):
            ok = self.drv.run_project("C:/proj/app.hex", timeout_s=5)
        self.assertFalse(ok)
        self.assertTrue(fake.killed)
        self.assertIn("超时", self.drv.last_error)

    def test_run_project_rpj_uses_gui_exe(self):
        fake = FakeProc(lines=["done\n"], returncode=0)
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.Popen", return_value=fake) as mock_popen:
            ok = self.drv.run_project("C:/proj/demo.rpj", timeout_s=60)
        self.assertTrue(ok)
        cmd = mock_popen.call_args[0][0]
        self.assertIn("RenesasFlashProgrammer.exe", cmd[0])
        self.assertEqual(cmd[1:], ["C:/proj/demo.rpj", "/silent"])

    def test_run_project_invalid_extension(self):
        self.assertFalse(self.drv.run_project("C:/proj/readme.txt"))
        self.assertIn(".rpj", self.drv.last_error)

    def test_run_project_missing_file(self):
        with patch("os.path.isfile", return_value=False):
            self.assertFalse(self.drv.run_project("C:/proj/app.hex"))
        self.assertIn("不存在", self.drv.last_error)

    def test_get_recent_log_ring_buffer(self):
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.Popen", return_value=FakeProc(lines=["line1\n", "line2\n"])):
            self.drv.run_project("C:/proj/app.hex")
        log = self.drv.get_recent_log(max_lines=60)
        self.assertIn("line1", log)
        self.assertIn("line2", log)

    def test_get_checksum_parses_output(self):
        self.drv.tool_ready = True
        with patch("os.path.isfile", return_value=True), \
                patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Checksum: 0x00ABCDEF\n", stderr="")
            crc = self.drv.get_checksum()
        self.assertEqual(crc, "0x00ABCDEF")

    def test_disconnect(self):
        self.drv.tool_ready = True
        self.assertTrue(self.drv.disconnect())
        self.assertFalse(self.drv.is_connected)
        self.assertFalse(self.drv.tool_ready)
        self.assertEqual(self.drv.query_state(), "Lost")


if __name__ == "__main__":
    unittest.main()
