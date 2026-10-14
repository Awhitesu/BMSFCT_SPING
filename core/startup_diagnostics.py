"""Short, read-only startup trace to distinguish cursor feedback from UI stalls."""
import ctypes
import json
import os
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication


def system_cursor_name():
    if os.name != 'nt':return 'unavailable'
    from ctypes import wintypes
    class CURSORINFO(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),
                  ('hCursor',wintypes.HANDLE),('ptScreenPos',wintypes.POINT)]
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.GetCursorInfo.argtypes=[ctypes.POINTER(CURSORINFO)]
    user32.LoadCursorW.argtypes=[wintypes.HINSTANCE,ctypes.c_void_p]
    user32.LoadCursorW.restype=wintypes.HANDLE
    info=CURSORINFO();info.cbSize=ctypes.sizeof(info)
    if not user32.GetCursorInfo(ctypes.byref(info)):return 'unavailable'
    for name,resource in [('arrow',32512),('wait',32514),('app_starting',32650),('hand',32649),('ibeam',32513)]:
        if info.hCursor==user32.LoadCursorW(None,ctypes.c_void_p(resource)):return name
    return 'other'


class StartupDiagnostics(QObject):
    def __init__(self,window,log_dir):
        super().__init__(window)
        self.window=window
        self.path=Path(log_dir)/f'ui_startup_{datetime.now():%Y%m%d_%H%M%S}.jsonl'
        self.started=self.last=time.monotonic()
        self.cpu=time.process_time()
        self.timer=QTimer(self);self.timer.setInterval(1000)
        self.timer.timeout.connect(self.sample)
        try:
            Path(log_dir).mkdir(parents=True,exist_ok=True)
        except OSError:
            return
        self.timer.start()
        self.write({'event':'main_window_ready','debugger':__import__('sys').gettrace() is not None})

    def write(self,record):
        try:
            with self.path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(record,ensure_ascii=False)+'\n')
        except OSError:
            self.timer.stop()

    def sample(self):
        now,cpu=time.monotonic(),time.process_time()
        override=QApplication.overrideCursor()
        try:cursor=system_cursor_name()
        except Exception:cursor='unavailable'
        self.write({'seconds':round(now-self.started,2),'ui_interval_ms':round((now-self.last)*1000),
                    'cpu_percent_one_core':round((cpu-self.cpu)/max(now-self.last,.001)*100,1),
                    'system_cursor':cursor,'qt_override':override.shape().name if override else None,
                    'window_cursor':self.window.cursor().shape().name,
                    'active':self.window.isActiveWindow()})
        self.last,self.cpu=now,cpu
        if now-self.started>=90:self.timer.stop()
