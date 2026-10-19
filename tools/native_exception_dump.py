"""Opt-in Windows test-process minidump capture without an attached debugger.

Only used by diagnostic runners. Dumps remain local and may contain test data.
"""
import ctypes
from ctypes import wintypes
import os
import json
from pathlib import Path


def install(folder):
    if os.name != 'nt':
        raise RuntimeError('Windows only')
    if ctypes.sizeof(ctypes.c_void_p)!=8:
        raise RuntimeError('This diagnostic capture expects Windows x64 CONTEXT')
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Record(ctypes.Structure):
        _fields_=[('code',wintypes.DWORD),('flags',wintypes.DWORD),
                  ('previous',ctypes.c_void_p),('address',ctypes.c_void_p),
                  ('count',wintypes.DWORD),('parameters',ctypes.c_size_t*15)]
    class Pointers(ctypes.Structure):
        _fields_=[('record',ctypes.POINTER(Record)),('context',ctypes.c_void_p)]
    callback_type=ctypes.WINFUNCTYPE(wintypes.LONG,ctypes.POINTER(Pointers))
    kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateEventW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    event=kernel.CreateEventW(None,True,False,f'Local\\BMSFCTNativeCrash_{os.getpid()}')
    if not event:raise ctypes.WinError(ctypes.get_last_error())
    captured=False
    @callback_type
    def handler(pointers):
        nonlocal captured
        if pointers.contents.record.contents.code!=0xC0000005 or captured:return 0
        captured=True
        record=pointers.contents.record.contents
        evidence=dict(pid=os.getpid(),thread=kernel.GetCurrentThreadId(),
            address=record.address,parameters=list(record.parameters)[:record.count],
            context=ctypes.string_at(pointers.contents.context,1232).hex())
        # Do not call MiniDumpWriteDump inside a failing Qt thread: debugger
        # locks/GIL can deadlock. The parent captures this suspended process.
        path=folder/f'exception_{os.getpid()}.json'
        fd=os.open(str(path),os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_BINARY,0o600)
        try:os.write(fd,json.dumps(evidence).encode('ascii'))
        finally:os.close(fd)
        kernel.WaitForSingleObject(event,30000)
        return 0
    kernel.AddVectoredExceptionHandler.argtypes=[wintypes.ULONG,callback_type]
    kernel.AddVectoredExceptionHandler.restype=ctypes.c_void_p
    registration=kernel.AddVectoredExceptionHandler(1,handler)
    if not registration:raise ctypes.WinError(ctypes.get_last_error())
    return registration,handler,event
