"""Shared serial connection. Connecting never triggers or configures the scanner."""
import queue
import threading
import serial


class InfoscanScanner:
    def __init__(self,config=None):
        self._lock=threading.RLock();self._rx_lock=threading.Lock();self._serial=None;self._thread=None
        self._stop=threading.Event();self._clear_pending=threading.Event();self.received=queue.Queue(maxsize=128)
        self.last_error='';self.config={}
        self.update_config(config or {})

    def update_config(self,config):
        self.config=dict(config)
        self.port=str(config.get('scanner_com','COM5'))
        self.baudrate=int(config.get('scanner_baudrate',115200))

    @property
    def is_connected(self):
        return bool(self._serial is not None and self._serial.is_open and not self._stop.is_set())

    def connect(self):
        with self._lock:
            if self.is_connected:return True
            if not self.disconnect():return False
            self.last_error=''
            try:
                self._serial=serial.Serial(port=self.port,baudrate=self.baudrate,
                    bytesize=int(self.config.get('scanner_data_bits',8)),
                    parity={'None':'N','Even':'E','Odd':'O'}[self.config.get('scanner_parity','None')],
                    stopbits=int(self.config.get('scanner_stop_bits',1)),
                    timeout=.05,write_timeout=.3,xonxoff=False,rtscts=False,dsrdtr=False)
                self._stop.clear();self.clear_input()
                self._thread=threading.Thread(target=self._receive,name='infoscan-rx',daemon=True)
                self._thread.start();return True
            except Exception as exc:
                self.last_error=str(exc);self._stop.set()
                if self._serial is not None:self._serial.close()
                self._serial=None;return False

    def _receive(self):
        try:
            while not self._stop.is_set():
                if self._clear_pending.is_set():
                    self._stop.wait(.001);continue
                with self._rx_lock:
                    data=self._serial.read(min(max(self._serial.in_waiting,1),4096))
                    if data:
                        try:self.received.put_nowait(data)
                        except queue.Full:raise RuntimeError('扫码器接收缓存溢出，请重新连接')
        except Exception as exc:
            if not self._stop.is_set():self.last_error=str(exc)
        finally:
            self._stop.set()
            if self._serial is not None:self._serial.close()

    def disconnect(self):
        with self._lock:
            self._stop.set()
            thread=self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(.5)
                if thread.is_alive():
                    self.last_error='扫码接收线程尚未退出';return False
            if self._serial is not None:self._serial.close()
            self._serial=None;self._thread=None
            self._drain();return True

    def _drain(self):
        while True:
            try:self.received.get_nowait()
            except queue.Empty:break

    def clear_input(self):
        self._clear_pending.set()
        try:
            with self._rx_lock:
                self._drain()
                if self.is_connected:self._serial.reset_input_buffer()
        finally:self._clear_pending.clear()

    def write(self,data):
        with self._lock:
            if not self.is_connected:raise RuntimeError('扫码器串口未打开')
            count=self._serial.write(data)
            if count!=len(data):raise RuntimeError('扫码器指令未完整发送')
            return count
