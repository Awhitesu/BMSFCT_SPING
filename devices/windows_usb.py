"""Read currently present Windows USB nodes without opening any device."""
import ctypes
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class UsbDevice:
    instance_id: str
    started: bool
    problem_code: int


def present_usb_devices():
    """Enumerate PRESENT nodes only; driver installation alone is not presence."""
    if os.name != 'nt':
        raise OSError('USB在位检测目前仅支持Windows')
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                    ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]

    class DeviceInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('ClassGuid', GUID),
                    ('DevInst', wintypes.DWORD), ('Reserved', ctypes.c_void_p)]

    setup = ctypes.WinDLL('setupapi', use_last_error=True)
    config = ctypes.WinDLL('cfgmgr32', use_last_error=True)
    get_set = setup.SetupDiGetClassDevsW
    get_set.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    get_set.restype = wintypes.HANDLE
    enumerate_node = setup.SetupDiEnumDeviceInfo
    enumerate_node.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DeviceInfo)]
    enumerate_node.restype = wintypes.BOOL
    get_id = setup.SetupDiGetDeviceInstanceIdW
    get_id.argtypes = [wintypes.HANDLE, ctypes.POINTER(DeviceInfo), wintypes.LPWSTR,
                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_id.restype = wintypes.BOOL
    destroy = setup.SetupDiDestroyDeviceInfoList
    destroy.argtypes = [wintypes.HANDLE]
    destroy.restype = wintypes.BOOL
    get_status = config.CM_Get_DevNode_Status
    get_status.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
                           wintypes.DWORD, wintypes.ULONG]
    get_status.restype = wintypes.ULONG

    # DIGCF_PRESENT | DIGCF_ALLCLASSES. Never enumerate remembered unplugged nodes.
    handle = get_set(None, 'USB', None, 0x02 | 0x04)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    devices = []
    try:
        index = 0
        while True:
            info = DeviceInfo()
            info.cbSize = ctypes.sizeof(info)
            if not enumerate_node(handle, index, ctypes.byref(info)):
                error = ctypes.get_last_error()
                if error == 259:  # ERROR_NO_MORE_ITEMS
                    break
                raise ctypes.WinError(error)
            index += 1
            needed = wintypes.DWORD()
            buffer = ctypes.create_unicode_buffer(256)
            if not get_id(handle, ctypes.byref(info), buffer, len(buffer), ctypes.byref(needed)):
                error = ctypes.get_last_error()
                if error != 122:  # ERROR_INSUFFICIENT_BUFFER
                    raise ctypes.WinError(error)
                buffer = ctypes.create_unicode_buffer(needed.value)
                if not get_id(handle, ctypes.byref(info), buffer, len(buffer), ctypes.byref(needed)):
                    raise ctypes.WinError(ctypes.get_last_error())
            status, problem = wintypes.ULONG(), wintypes.ULONG()
            result = get_status(ctypes.byref(status), ctypes.byref(problem), info.DevInst, 0)
            if result != 0:
                raise OSError(f'读取USB设备状态失败，Configuration Manager代码: {result}')
            devices.append(UsbDevice(buffer.value, bool(status.value & 0x08),
                                     problem.value if status.value & 0x400 else 0))
    finally:
        destroy(handle)
    return devices
