"""用进程快照和时钟替身验证退出/启动确认；不杀真实上位机。"""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import host_process as host


@pytest.fixture
def program(tmp_path):
    executable = tmp_path / 'spaces & %NAME%' / 'Example.exe'
    executable.parent.mkdir()
    executable.touch()
    return executable


@pytest.fixture
def process(monkeypatch):
    child = SimpleNamespace(pid=77, poll=Mock(return_value=None), wait=Mock())
    monkeypatch.setattr(host.subprocess, 'Popen', Mock(return_value=child))
    monkeypatch.setattr(host.threading, 'Thread', Mock())
    return child

@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(host.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(host.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_absent_process_is_already_stopped(monkeypatch):
    monkeypatch.setattr(host, '_process_ids', Mock(return_value=set()))
    run = Mock()
    monkeypatch.setattr(host.subprocess, 'run', run)
    host.stop_host('Example.exe')
    run.assert_not_called()


def test_taskkill_failure_is_accepted_only_if_process_has_exited(monkeypatch):
    snapshots = Mock(side_effect=[{42}, set()])
    monkeypatch.setattr(host, '_process_ids', snapshots)
    run = Mock(return_value=SimpleNamespace(returncode=128, stdout=b'', stderr=b'already gone'))
    monkeypatch.setattr(host.subprocess, 'run', run)
    host.stop_host('Example Host.exe')
    command = run.call_args.args[0]
    assert 'cmd.exe' in command and '/c taskkill /f /t /im "%BMSFCT_HOST_IMAGE%"' in command
    assert run.call_args.kwargs['env']['BMSFCT_HOST_IMAGE'] == 'Example Host.exe'


def test_taskkill_access_denied_does_not_report_stopped(monkeypatch):
    monkeypatch.setattr(host, '_process_ids', Mock(return_value={42}))
    monkeypatch.setattr(host.subprocess, 'run', Mock(return_value=SimpleNamespace(
        returncode=1, stderr=b'Access denied', stdout=b'')))
    with pytest.raises(RuntimeError, match='仍在运行.*Access denied'):
        host.stop_host('Example.exe')


def test_taskkill_success_still_requires_exit_confirmation(monkeypatch, clock):
    monkeypatch.setattr(host, '_process_ids', Mock(return_value={42}))
    monkeypatch.setattr(host.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0)))
    with pytest.raises(TimeoutError, match='等待进程退出超时'):
        host.stop_host('Example.exe', timeout=.2)


def test_launch_resolves_full_path_and_working_directory_without_cmd_or_hiding_gui(program, process, clock, monkeypatch):
    run = Mock(side_effect=AssertionError('启动不得等待 cmd/start'))
    monkeypatch.setattr(host.subprocess, 'run', run)
    assert host.start_host(program) == 77
    options = host.subprocess.Popen.call_args.kwargs
    assert host.subprocess.Popen.call_args.args[0] == [str(program)]
    assert options['cwd'] == str(program.parent)
    assert 'startupinfo' not in options  # GUI 不继承 SW_HIDE。
    assert clock[0] >= .2
    run.assert_not_called()
    host.threading.Thread.return_value.start.assert_called_once()


@pytest.mark.parametrize('code', [0, 1, 0xc000007b])
def test_exited_process_is_not_reported_as_started(program, process, clock, code):
    process.poll.return_value = code
    with pytest.raises(RuntimeError, match='启动后立即退出.*退出码'):
        host.start_host(program)
    host.threading.Thread.assert_not_called()


def test_startup_stability_window_has_a_bounded_deadline(program, process, clock):
    with pytest.raises(TimeoutError, match='启动确认超时'):
        host.start_host(program, timeout=.1)


def test_launch_failure_includes_real_path_and_windows_error(program, monkeypatch):
    monkeypatch.setattr(host.subprocess, 'Popen', Mock(side_effect=OSError('WinError 740: elevation required')))
    with pytest.raises(RuntimeError) as error:
        host.start_host(program)
    assert str(program) in str(error.value) and '740' in str(error.value)
    assert '无法启动上位机' in str(error.value)


def test_early_exit_retains_program_error_output(program, process, clock):
    def launch(*_, **options):
        options['stderr'].write(b'Could not load vendor DLL')
        options['stderr'].flush()
        return process
    host.subprocess.Popen.side_effect = launch
    process.poll.return_value = 2
    with pytest.raises(RuntimeError, match='Could not load vendor DLL'):
        host.start_host(program)


def test_missing_executable_does_not_launch_anything(tmp_path, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(host.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='未找到可启动'):
        host.start_host(tmp_path / 'Missing.exe')
    launch.assert_not_called()


def test_frozen_child_environment_is_filtered_without_modifying_parent(tmp_path, monkeypatch):
    bundle = tmp_path / '_internal'
    system = tmp_path / 'System'
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(host.sys, '_MEIPASS', str(bundle), raising=False)
    original = dict(PATH=os.pathsep.join([str(bundle), str(system), str(bundle/'PySide6')]),
        QT_PLUGIN_PATH=str(bundle/'PySide6/plugins'), QT_QPA_PLATFORM_PLUGIN_PATH=str(system),
        _PYI_ARCHIVE_FILE='old.exe', _MEIPASS2=str(bundle), OTHER='preserved')
    monkeypatch.setattr(host.os, 'environ', dict(original))
    cleaned = host._external_environment()
    assert cleaned['PATH'] == str(system)
    assert cleaned['QT_QPA_PLATFORM_PLUGIN_PATH'] == str(system)
    assert cleaned['OTHER'] == 'preserved'
    assert 'QT_PLUGIN_PATH' not in cleaned and '_PYI_ARCHIVE_FILE' not in cleaned and '_MEIPASS2' not in cleaned
    assert host.os.environ == original


@pytest.mark.parametrize('launch_failure', [False, True])
def test_dll_search_path_is_restored_after_process_creation(program, process, clock, monkeypatch, launch_failure):
    kernel = SimpleNamespace(GetDllDirectoryW=Mock(), SetDllDirectoryW=Mock(return_value=True))
    def read_directory(size, buffer):
        if not size:return len('bundle')+1
        buffer.value = 'bundle'
        return len('bundle')
    kernel.GetDllDirectoryW.side_effect = read_directory
    monkeypatch.setattr(host.ctypes, 'WinDLL', Mock(return_value=kernel))
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    def launch(*_, **options):
        assert kernel.SetDllDirectoryW.call_args.args == (None,)
        if launch_failure:raise OSError('failed to create process')
        return process
    host.subprocess.Popen.side_effect = launch
    if launch_failure:
        with pytest.raises(RuntimeError, match='无法启动上位机'):host.start_host(program)
    else:
        host.start_host(program)
    assert [call.args for call in kernel.SetDllDirectoryW.call_args_list] == [(None,), ('bundle',)]


@pytest.mark.parametrize('operation', [lambda cancel: host.stop_host('Example.exe', cancel),
                                     lambda cancel: host.start_host(Path('Example.exe'), cancel),
                                     lambda cancel: host.wait_seconds(.5, cancel)])
def test_cancel_does_not_launch_command_or_wait(monkeypatch, operation):
    run = Mock()
    monkeypatch.setattr(host.subprocess, 'run', run)
    monkeypatch.setattr(host.subprocess, 'Popen', run)
    with pytest.raises(RuntimeError, match='取消'):
        operation(lambda: True)
    run.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Windows 进程快照')
def test_windows_snapshot_finds_current_process_case_insensitively():
    import sys
    assert os.getpid() in host._process_ids(Path(sys.executable).name.upper())
