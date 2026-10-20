"""Windows 外部上位机的停止、启动与进程确认。"""
import ctypes
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
            stable_since = None
            while True:
                check_cancelled(cancel)
                code = process.poll()
                if code is not None:
                    detail = _startup_output(errors)
                    message = f'上位机 {program.name} 启动后立即退出（退出码 {code}，0x{code & 0xffffffff:08X}）；程序路径：{program}'
                    raise RuntimeError(message + (f'；错误输出：{detail}' if detail else ''))
                now = time.monotonic()
                if stable_since is not None and now - stable_since >= .2:
                    return process.pid
                if now >= deadline:
                    raise TimeoutError(f'上位机 {program.name} 启动确认超时（{timeout:g} 秒）；程序路径：{program}')
                if stable_since is None:
                    stable_since = now
                wait_seconds(.05, cancel)
        finally:
            if process is not None and process.poll() is None:
                # 持有 Popen 并回收退出状态；等待线程不持有界面对象，也不阻塞主界面。
                threading.Thread(target=process.wait, name='ExternalHostWaiter', daemon=True).start()
