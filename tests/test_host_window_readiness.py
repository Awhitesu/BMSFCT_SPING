"""Windows 窗口条件的离线验证，不连接 CAN 或启动现场上位机。"""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import host_process as host


@pytest.fixture
def desktop(monkeypatch):
    window = dict(pid=77, visible=True, enabled=True, owner=0, kind='WindowsForms10.Window')
    windows = {101: window}
    user = SimpleNamespace(**{name: Mock() for name in (
        'WaitForInputIdle', 'EnumWindows', 'GetWindowThreadProcessId', 'IsWindowVisible',
        'IsWindowEnabled', 'GetWindow', 'GetClassNameW', 'SendMessageTimeoutW')})
    user.WaitForInputIdle.return_value = 0
    user.SendMessageTimeoutW.return_value = 1

    def enumerate_windows(callback, _):
        for handle in windows:
            assert callback(handle, 0)
        return True

    def process_id(handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = windows[handle]['pid']
        return 88

    def class_name(handle, buffer, _):
        buffer.value = windows[handle]['kind']
        return len(buffer.value)

    user.EnumWindows.side_effect = enumerate_windows
    user.GetWindowThreadProcessId.side_effect = process_id
    user.IsWindowVisible.side_effect = lambda handle: windows[handle]['visible']
    user.IsWindowEnabled.side_effect = lambda handle: windows[handle]['enabled']
    user.GetWindow.side_effect = lambda handle, _: windows[handle]['owner']
    user.GetClassNameW.side_effect = class_name
    monkeypatch.setattr(host.ctypes, 'WinDLL', Mock(return_value=user))
    return SimpleNamespace(user=user, windows=windows, window=window,
                           process=SimpleNamespace(pid=77, _handle=9001))


def test_ready_window_is_probed_without_sending_application_commands(desktop):
    assert host._host_window_ready(desktop.process, 17)
    desktop.user.WaitForInputIdle.assert_called_once_with(9001, 0)
    args = desktop.user.SendMessageTimeoutW.call_args.args
    assert args[:6] == (101, 0, 0, 0, 0x23, 17)  # WM_NULL, bounded and abortable.


@pytest.mark.parametrize('idle_result', [0x102, 0xffffffff])
def test_pending_or_missing_message_queue_is_not_ready(desktop, idle_result):
    desktop.user.WaitForInputIdle.return_value = idle_result
    assert not host._host_window_ready(desktop.process)
    desktop.user.EnumWindows.assert_not_called()


@pytest.mark.parametrize(('key', 'value'), [
    ('pid', 78), ('visible', False), ('enabled', False), ('owner', 202),
    ('kind', '#32770'), ('kind', ''),
])
def test_other_hidden_disabled_owned_and_dialog_windows_are_not_ready(desktop, key, value):
    desktop.window[key] = value
    assert not host._host_window_ready(desktop.process)
    desktop.user.SendMessageTimeoutW.assert_not_called()


def test_no_window_cannot_pass_from_process_idle_alone(desktop):
    desktop.windows.clear()
    assert not host._host_window_ready(desktop.process)
    desktop.user.SendMessageTimeoutW.assert_not_called()


def test_unresponsive_main_window_is_not_ready(desktop):
    desktop.user.SendMessageTimeoutW.return_value = 0
    assert not host._host_window_ready(desktop.process)


def test_visible_error_dialog_prevents_ready_even_with_an_enabled_main_window(desktop):
    desktop.windows[202] = dict(desktop.window, kind='#32770', owner=101)
    assert not host._host_window_ready(desktop.process)
    desktop.user.SendMessageTimeoutW.assert_not_called()


def test_another_process_dialog_does_not_block_this_process(desktop):
    desktop.windows[202] = dict(desktop.window, kind='#32770', pid=78)
    assert host._host_window_ready(desktop.process)


def test_window_enumeration_error_is_reported(desktop, monkeypatch):
    desktop.user.EnumWindows.side_effect = None
    desktop.user.EnumWindows.return_value = False
    monkeypatch.setattr(host.ctypes, 'get_last_error', lambda: 5)
    with pytest.raises(OSError):
        host._host_window_ready(desktop.process)


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason='64-bit HWND')
def test_native_window_handles_are_not_truncated(desktop):
    handle = (1 << 40) + 101
    desktop.windows.clear()
    desktop.windows[handle] = desktop.window
    assert host._host_window_ready(desktop.process)
    assert desktop.user.SendMessageTimeoutW.call_args.args[0] == handle
