"""协议安全退出、异常兜底和主窗口就绪；全部替换进程操作。"""
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import host_process as host


@pytest.fixture
def shutdown(tmp_path, monkeypatch):
    program = tmp_path / 'spaces & 中文 %NAME%' / 'BMS_General.exe'
    program.parent.mkdir()
    program.touch()
    child = SimpleNamespace(returncode=0, communicate=Mock(return_value=(b'', b'')),
                            kill=Mock(), poll=Mock(return_value=None))
    launch = Mock(return_value=child)
    processes = Mock(return_value=set())
    forced, wait = Mock(), Mock()
    monkeypatch.setattr(host.subprocess, 'Popen', launch)
    monkeypatch.setattr(host, '_process_ids', processes)
    monkeypatch.setattr(host, 'stop_host', forced)
    monkeypatch.setattr(host, 'wait_seconds', wait)
    return SimpleNamespace(program=program, child=child, launch=launch, processes=processes,
                           forced=forced, wait=wait)


def test_absent_gui_still_invokes_shutdown_and_returns_without_waiting(shutdown):
    host.shutdown_host(shutdown.program)
    assert shutdown.launch.call_args.args[0] == [str(shutdown.program), '--shutdown']
    options = shutdown.launch.call_args.kwargs
    assert options['cwd'] == str(shutdown.program.parent)
    assert options['startupinfo'].wShowWindow == 0
    assert options['creationflags'] & subprocess.CREATE_NO_WINDOW
    shutdown.child.communicate.assert_called_once_with(timeout=20)
    shutdown.processes.assert_called_once_with(shutdown.program.name)
    shutdown.forced.assert_not_called()
    shutdown.child.kill.assert_not_called()
    shutdown.wait.assert_not_called()


@pytest.mark.parametrize('code', [2, 3, 9])
@pytest.mark.parametrize('remaining', [set(), {42}])
def test_nonzero_code_never_continues_and_only_force_cleans_confirmed_residue(shutdown, code, remaining):
    shutdown.child.returncode = code
    shutdown.processes.return_value = remaining
    logs = []
    with pytest.raises(RuntimeError, match=f'退出码 {code}.*操作员确认'):
        host.shutdown_host(shutdown.program, logger=logs.append)
    assert any('刷写程序安全退出失败 [' in line for line in logs)
    if remaining:
        shutdown.forced.assert_called_once_with(shutdown.program.name)
    else:
        shutdown.forced.assert_not_called()
    shutdown.wait.assert_called_once_with(3)


def test_timeout_kills_command_helper_then_checks_gui_residue(shutdown):
    shutdown.child.communicate.side_effect = [subprocess.TimeoutExpired('shutdown', 20), (b'', b'')]
    shutdown.processes.return_value = {42}
    with pytest.raises(RuntimeError, match='超过 20 秒.*操作员确认'):
        host.shutdown_host(shutdown.program)
    shutdown.child.kill.assert_called_once()
    shutdown.forced.assert_called_once_with(shutdown.program.name)
    shutdown.wait.assert_called_once_with(3)


def test_launch_failure_blocks_flow_and_performs_only_confirmed_cleanup(shutdown):
    shutdown.launch.side_effect = OSError('cannot start command')
    shutdown.processes.return_value = {42}
    with pytest.raises(RuntimeError, match='调用异常.*cannot start command'):
        host.shutdown_host(shutdown.program)
    shutdown.forced.assert_called_once_with(shutdown.program.name)
    shutdown.wait.assert_called_once_with(3)


def test_cleanup_failure_is_reported_after_cooldown(shutdown):
    shutdown.child.returncode = 2
    shutdown.processes.return_value = {42}
    shutdown.forced.side_effect = PermissionError('access denied')
    with pytest.raises(RuntimeError, match='清理失败.*access denied.*操作员确认'):
        host.shutdown_host(shutdown.program)
    shutdown.wait.assert_called_once_with(3)


def test_zero_with_remaining_gui_is_not_accepted_or_force_killed(shutdown):
    shutdown.processes.return_value = {42}
    with pytest.raises(RuntimeError, match='命令返回0.*旧进程'):
        host.shutdown_host(shutdown.program)
    shutdown.forced.assert_not_called()


def test_cancel_before_command_does_not_launch_and_cancel_after_reply_does_not_configure(shutdown):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match='取消'):
        host.shutdown_host(shutdown.program, cancel.is_set)
    shutdown.launch.assert_not_called()
    cancel.clear()
    def reply(**_):
        cancel.set()
        return b'', b''
    shutdown.child.communicate.side_effect = reply
    with pytest.raises(RuntimeError, match='取消'):
        host.shutdown_host(shutdown.program, cancel.is_set)
    shutdown.forced.assert_not_called()


def test_alive_process_without_ready_window_times_out_in_eight_seconds(shutdown, monkeypatch):
    clock = [0.0]
    child = SimpleNamespace(pid=77, poll=Mock(return_value=None), wait=Mock())
    shutdown.launch.return_value = child
    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, 'wait_seconds', lambda seconds, _: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(host.threading, 'Thread', Mock())
    monkeypatch.setattr(host, '_host_window_ready', Mock(return_value=False))
    with pytest.raises(TimeoutError, match='未检测到可响应的主窗口'):
        host.start_host(shutdown.program, timeout=8)
    assert 8 <= clock[0] < 8.1


def test_process_exiting_during_initialization_is_failure(shutdown, monkeypatch):
    clock = [0.0]
    child = SimpleNamespace(pid=77, poll=Mock(side_effect=lambda: 1 if clock[0] >= 1 else None), wait=Mock())
    shutdown.launch.return_value = child
    monkeypatch.setattr(host.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(host, 'wait_seconds', lambda seconds, _: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(host, '_host_window_ready', Mock(return_value=False))
    with pytest.raises(RuntimeError, match='启动后立即退出'):
        host.start_host(shutdown.program, timeout=8)


def test_shutdown_restores_frozen_dll_path_before_waiting_for_command(shutdown, monkeypatch):
    kernel = SimpleNamespace(GetDllDirectoryW=Mock(), SetDllDirectoryW=Mock(return_value=True))
    def read_directory(size, buffer):
        if not size:
            return 7
        buffer.value = 'bundle'
        return 6
    kernel.GetDllDirectoryW.side_effect = read_directory
    monkeypatch.setattr(host.ctypes, 'WinDLL', Mock(return_value=kernel))
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    def launch(*_, **__):
        assert kernel.SetDllDirectoryW.call_args.args == (None,)
        return shutdown.child
    def communicate(**_):
        assert kernel.SetDllDirectoryW.call_args.args == ('bundle',)
        return b'', b''
    shutdown.launch.side_effect = launch
    shutdown.child.communicate.side_effect = communicate
    host.shutdown_host(shutdown.program)
    assert [call.args for call in kernel.SetDllDirectoryW.call_args_list] == [(None,), ('bundle',)]


@pytest.mark.parametrize('code', [0, 2])
def test_real_preparation_chain_uses_safe_command_before_config_and_ready(shutdown, monkeypatch, code):
    from core import recipe_app_flash as flash
    ini = shutdown.program.parent / 'config.ini'
    original = b'[App]\nType=Old\nAddr=Old\n'
    ini.write_bytes(original)
    project = shutdown.program.parent / 'Flash' / 'New-project'
    project.mkdir(parents=True)
    record = project.parent / 'Record.txt'
    record.write_text('Pass - previous\n', encoding='utf-8')
    start = Mock()
    monkeypatch.setattr(flash, 'start_host', start)
    monkeypatch.setattr(flash, 'wait_seconds', Mock())
    shutdown.child.returncode = code
    shutdown.processes.return_value = {42} if code else set()
    config = dict(enabled=True, restart_name=shutdown.program.name, host_path=str(shutdown.program),
                  model='New', flash_path='New-project')
    def reply(**_):
        assert ini.read_bytes() == original and record.read_text() == 'Pass - previous\n'
        return b'', b''
    shutdown.child.communicate.side_effect = reply
    if code:
        with pytest.raises(RuntimeError, match='安全退出失败'):
            flash.prepare_app_flash(config)
        assert ini.read_bytes() == original and record.read_text() == 'Pass - previous\n'
        start.assert_not_called()
        shutdown.forced.assert_called_once_with(shutdown.program.name)
    else:
        assert flash.prepare_app_flash(config) == config
        assert b'Type=New' in ini.read_bytes() and b'Addr=New-project' in ini.read_bytes()
        assert record.read_text().strip() == 'Ready'
        start.assert_called_once_with(shutdown.program, None, timeout=8)
        shutdown.forced.assert_not_called()
    assert shutdown.launch.call_args.args[0] == [str(shutdown.program), '--shutdown']
