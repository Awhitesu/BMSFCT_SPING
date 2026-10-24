"""Windows 外部上位机的停止、启动与窗口就绪确认。"""
import ctypes
from datetime import datetime
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from core.background_process import background_process_options

_launch_lock = threading.RLock()


def _external_environment():
    environment = dict(os.environ)
    bundle = getattr(sys, '_MEIPASS', None) if getattr(sys, 'frozen', False) else None
    if not bundle:
        return environment
    bundle = os.path.normcase(os.path.abspath(bundle))

    def bundled(path):
        resolved = os.path.normcase(os.path.abspath(path.strip('"')))
        return resolved == bundle or resolved.startswith(bundle + os.sep)

    for key in ('PATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        if key in environment:
            paths = [path for path in environment[key].split(os.pathsep) if not bundled(path)]
            if paths:
                environment[key] = os.pathsep.join(paths)
            else:
                environment.pop(key)
    # 外部程序独立启动，不继承当前 PyInstaller 实例的私有启动状态。
    for key in list(environment):
        if key.startswith('_PYI_') or key == '_MEIPASS2':
            environment.pop(key)
    return environment


@contextmanager
def _external_dll_search_path():
    """仅在创建子进程时临时恢复 Windows DLL 搜索路径，随后还原主程序。"""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    with _launch_lock:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        kernel.GetDllDirectoryW.restype = wintypes.DWORD
        kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        kernel.SetDllDirectoryW.restype = wintypes.BOOL
        size = kernel.GetDllDirectoryW(0, None)
        if not size:
            yield
            return
        buffer = ctypes.create_unicode_buffer(size + 1)
        length = kernel.GetDllDirectoryW(len(buffer), buffer)
        if not length or length >= len(buffer):
            raise RuntimeError('读取主程序 DLL 搜索目录失败')
        previous = buffer.value
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous):
                raise RuntimeError('恢复主程序 DLL 搜索目录失败：' + str(ctypes.WinError(ctypes.get_last_error())))


def _startup_output(stream):
    stream.seek(0)
    data = stream.read(4096)
    try:
        return data.decode('utf-8-sig').strip()
    except UnicodeDecodeError:
        return data.decode('mbcs', errors='replace').strip()

def check_cancelled(cancel=None):
    if cancel is not None and cancel():
        raise RuntimeError('上位机准备已取消')


def wait_seconds(seconds, cancel=None):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled(cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.05, remaining))


def _process_ids(name):
    """读取原生进程快照，避免 tasklist 的语言、编码及列宽差异。"""
    if os.name != 'nt':
        raise RuntimeError('上位机重启仅支持 Windows')

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for method in (kernel.Process32FirstW, kernel.Process32NextW):
        method.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        method.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = set()
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            if entry.szExeFile.casefold() == name.casefold():
                found.add(entry.th32ProcessID)
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(error)
        return found
    finally:
        kernel.CloseHandle(snapshot)


def _cmd(command):
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe'
    return f'"{executable}" /d /v:off /s /c {command}'


def stop_host(name, cancel=None, timeout=10):
    """仅供安全退出失败后的强制清理；正常换型使用shutdown_host。"""
    check_cancelled(cancel)
    if not _process_ids(name):
        return  # 未运行时也已满足停止条件。
    command = 'taskkill /f /t /im "%BMSFCT_HOST_IMAGE%"'
    result = subprocess.run(_cmd(command), env=dict(os.environ, BMSFCT_HOST_IMAGE=name),
                            capture_output=True, timeout=timeout,
                            **background_process_options())
    deadline = time.monotonic() + timeout
    while True:
        check_cancelled(cancel)
        if not _process_ids(name):
            return
        if result.returncode:
            detail = (result.stderr or result.stdout).decode('mbcs', errors='replace').strip()
            raise RuntimeError(f'进程仍在运行，taskkill 失败：{detail or result.returncode}')
        if time.monotonic() >= deadline:
            raise TimeoutError(f'等待进程退出超时：{name}')
        wait_seconds(.05, cancel)


def shutdown_host(program, cancel=None, timeout=20, logger=None):
    """请求协议安全退出，失败后清理残留并终止正常换型流程。"""
    check_cancelled(cancel)
    program = Path(program).absolute()
    if program.suffix.casefold() != '.exe' or not program.is_file():
        raise ValueError(f'未找到可安全关闭的上位机 exe 文件：{program}')
    child, failure = None, ''
    try:
        with _external_dll_search_path():
            child = subprocess.Popen([str(program), '--shutdown'], cwd=str(program.parent),
                env=_external_environment(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **background_process_options())
        try:
            output, errors = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()  # 仅结束超时的命令进程，旧界面进程随后单独确认。
            child.communicate()
            failure = f'--shutdown 命令超过 {timeout:g} 秒未结束'
        else:
            if child.returncode != 0:
                meanings = {2: '15秒内未完成安全退出', 3: '安全退出通信异常'}
                failure = f'--shutdown 退出码 {child.returncode}：{meanings.get(child.returncode, "未知错误")}'
                detail = (errors or output or b'').decode('utf-8', errors='replace').strip()
                if detail:
                    failure += '；' + detail
    except OSError as exc:
        failure = f'--shutdown 调用异常：{exc}'
        try:
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate()
        except OSError as cleanup_error:
            failure += f'；命令进程清理失败：{cleanup_error}'
    if not failure:
        check_cancelled(cancel)
        if _process_ids(program.name):
            raise RuntimeError('刷写程序安全退出失败：命令返回0，但仍检测到旧进程；已停止换型，请人工确认后重新选择型号')
        return

    message = f'刷写程序安全退出失败 [{datetime.now():%Y-%m-%d %H:%M:%S}]：{failure}'
    if logger:
        logger('[上位机准备] ' + message)
    try:
        if _process_ids(program.name):
            if logger:
                logger('[上位机准备] 安全退出失败，确认残留进程后执行强制清理：' + program.name)
            stop_host(program.name)
    except Exception as exc:
        message += f'；残留进程清理失败：{exc}'
    finally:
        wait_seconds(3)  # 异常冷却期间不因取消而提前开放重新启动。
    raise RuntimeError(message + '；已停止换型，操作员确认故障后请重新选择型号')


def _host_window_ready(process, timeout_ms=50):
    """只检测新进程的可响应主窗口；对话框或单纯进程存活不算就绪。"""
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    signatures = {
        'WaitForInputIdle': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        'EnumWindows': ([callback_type, wintypes.LPARAM], wintypes.BOOL),
        'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
        'IsWindowEnabled': ([wintypes.HWND], wintypes.BOOL),
        'GetWindow': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        'SendMessageTimeoutW': ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_ssize_t),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(user, name)
        function.argtypes, function.restype = arguments, result
    # Windows' idle indication is latched; also probe the current window below.
    if user.WaitForInputIdle(process._handle, 0) != 0:
        return False
    windows, dialogs = [], []

    @callback_type
    def visit(window, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value != process.pid or not user.IsWindowVisible(window):
            return True
        name = ctypes.create_unicode_buffer(256)
        if not user.GetClassNameW(window, name, len(name)):
            return True  # Window may have been destroyed during enumeration.
        if name.value == '#32770':
            dialogs.append(window)
        elif not user.GetWindow(window, 4) and user.IsWindowEnabled(window):  # GW_OWNER
            windows.append(window)
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if dialogs or not windows:
        return False
    result = ctypes.c_size_t()
    # WM_NULL checks the GUI thread without changing any application state.
    return bool(user.SendMessageTimeoutW(windows[0], 0, 0, 0, 0x23, timeout_ms, ctypes.byref(result)))


def start_host(program, cancel=None, timeout=5):
    check_cancelled(cancel)
    program = Path(program).absolute()
    if program.suffix.casefold() != '.exe' or not program.is_file():
        raise ValueError(f'未找到可启动的上位机 exe 文件：{program}')
    environment = _external_environment()
    process = None
    with tempfile.TemporaryFile() as errors:
        try:
            try:
                with _external_dll_search_path():
                    # 直接创建目标进程；不等待 cmd/start，也不给 GUI 继承 SW_HIDE。
                    process = subprocess.Popen([str(program)], cwd=str(program.parent), env=environment,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
                        creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as exc:
                raise RuntimeError(f'无法启动上位机 {program.name}：{exc}；程序路径：{program}') from exc
            deadline = time.monotonic() + timeout
            while True:
                check_cancelled(cancel)
                code = process.poll()
                if code is not None:
                    detail = _startup_output(errors)
                    message = f'上位机 {program.name} 启动后立即退出（退出码 {code}，0x{code & 0xffffffff:08X}）；程序路径：{program}'
                    raise RuntimeError(message + (f'；错误输出：{detail}' if detail else ''))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'上位机 {program.name} 启动确认超时（{timeout:g} 秒）：未检测到可响应的主窗口；程序路径：{program}')
                ready = _host_window_ready(process, min(50, max(1, int(remaining * 1000))))
                check_cancelled(cancel)
                if ready and time.monotonic() < deadline:
                    if process.poll() is None:
                        return process.pid
                    continue  # Retain the exit code/error-output diagnosis above.
                wait_seconds(min(.05, max(0, deadline - time.monotonic())), cancel)
        finally:
            if process is not None and process.poll() is None:
                # 持有 Popen 并回收退出状态；等待线程不持有界面对象，也不阻塞主界面。
                threading.Thread(target=process.wait, name='ExternalHostWaiter', daemon=True).start()
