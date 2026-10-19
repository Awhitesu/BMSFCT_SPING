"""Infoscan raw serial barcode input; all serial I/O is event driven."""
from PySide6.QtCore import QObject, QTimer, Signal, QIODevice, Qt
from PySide6.QtSerialPort import QSerialPort
from PySide6.QtWidgets import QGroupBox,QFormLayout,QLineEdit,QComboBox,QCheckBox,QPushButton,QLabel,QHBoxLayout,QWidget


class _ManagedPort:
    def __init__(self,provider):self.provider=provider
    def isOpen(self):return self.provider().is_connected
    def clear(self,*args):self.provider().clear_input()
    def write(self,data):return self.provider().write(data)
    def close(self):return self.provider().disconnect()


class SerialScanner(QObject):
    barcode=Signal(str)
    status=Signal(str)
    raw_received=Signal(bytes)

    def __init__(self,parent=None,driver_provider=None):
        super().__init__(parent)
        self.driver_provider=driver_provider
        self._config={}
        from ui.device_task import DeviceTask
        self.connection_task=DeviceTask(self)
        if driver_provider:
            self.port=_ManagedPort(driver_provider)
            self._driver=driver_provider()
            self._connected=self.port.isOpen()
            self.receive_timer=QTimer(self);self.receive_timer.setInterval(30)
            self.receive_timer.timeout.connect(self._drain_received);self.receive_timer.start()
        else:
            self.port=QSerialPort(self)
            self.port.setReadBufferSize(8192)
            self.port.readyRead.connect(self._read)
            self.port.errorOccurred.connect(self._error)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(150)
        self.timer.timeout.connect(self._finish)
        self.retrigger_timer=QTimer(self);self.retrigger_timer.setSingleShot(True)
        self.retrigger_timer.timeout.connect(lambda:self._retrigger_continuous(self._retrigger_owner))
        self.trigger_on_timer=QTimer(self);self.trigger_on_timer.setSingleShot(True)
        self.trigger_on_timer.timeout.connect(self._send_continuous_ton)
        self.trigger_watchdog=QTimer(self);self.trigger_watchdog.setSingleShot(True)
        self.trigger_watchdog.timeout.connect(self._schedule_continuous)
        self.buffer=bytearray();self.overflow=False;self.config={};self.waiting=False;self.owner=None

    @property
    def config(self):return self.driver_provider().config if self.driver_provider else self._config

    @config.setter
    def config(self,value):self._config=dict(value)

    def _drain_received(self):
        import queue
        driver=self.driver_provider();connected=driver.is_connected
        if driver is not self._driver or connected!=self._connected:
            self.waiting=False;self.owner=None;self.reset()
            self._driver=driver;self._connected=connected
            self.status.emit('串口已连接（扫码未验证）' if connected else '串口未连接：'+driver.last_error)
        for _ in range(128):
            try:data=driver.received.get_nowait()
            except queue.Empty:break
            self.raw_received.emit(data);self.feed(data)

    def configure(self,config):
        if self.driver_provider:
            if self.connection_task.isRunning():return
            self.end_scan()
            driver=self.driver_provider()
            def apply():
                if not driver.disconnect():raise RuntimeError(driver.last_error)
                driver.update_config(config)
                if config.get('scanner_enabled',False) and not driver.connect():raise RuntimeError(driver.last_error)
                return driver.is_connected
            def completed(ok,value):
                self.status.emit(('串口已连接（扫码未验证）' if value else '未打开') if ok else '连接失败：'+str(value))
            self.connection_task.run(apply,completed)
            return
        self.close()
        self.config=dict(config)
        if not config.get('scanner_enabled',False):return
        self.port.setPortName(config.get('scanner_com','COM5'))
        self.port.setBaudRate(int(config.get('scanner_baudrate',115200)))
        self.port.setDataBits(QSerialPort.DataBits(int(config.get('scanner_data_bits',8))))
        self.port.setParity({'None':QSerialPort.NoParity,'Even':QSerialPort.EvenParity,'Odd':QSerialPort.OddParity}[config.get('scanner_parity','None')])
        self.port.setStopBits(QSerialPort.StopBits(int(config.get('scanner_stop_bits',1))))
        self.port.setFlowControl(QSerialPort.NoFlowControl)
        if not self.port.open(QIODevice.ReadWrite):
            self.status.emit('串口打开失败：'+self.port.errorString());return
        self.port.setDataTerminalReady(True)
        self.status.emit('串口已打开，尚未验证扫码；请点击开始后扫码')

    def begin_scan(self,owner=None,continuous=False):
        if self.waiting or self.connection_task.isRunning():return False
        if self.driver_provider:
            self._driver=self.driver_provider();self._connected=self.port.isOpen()
        self.reset();self.waiting=True;self.owner=owner;self.continuous=continuous
        if self.port.isOpen():
            try:
                self.port.clear(QSerialPort.Input)
                if self.config.get('scanner_trigger',True):
                    self.port.write(b'TON')
                    if continuous:self.trigger_watchdog.start(3000)
            except Exception as exc:
                self.waiting=False;self.owner=None;self.status.emit('触发扫码失败：'+str(exc));return False
        return True

    def end_scan(self,owner=None):
        if owner is not None and self.owner is not owner:return
        active=self.waiting or self.owner is not None
        self.waiting=False;self.owner=None;self.reset()
        if active and self.port.isOpen() and self.config.get('scanner_trigger',True):
            try:self.port.write(b'TOFF')
            except Exception as exc:self.status.emit('停止扫码失败：'+str(exc))

    def reset(self):
        self.retrigger_timer.stop();self.trigger_on_timer.stop();self.trigger_watchdog.stop()
        self._clear_frame()

    def _clear_frame(self):
        self.timer.stop();self.buffer.clear();self.overflow=False

    def close(self):
        self.end_scan()
        if self.driver_provider:
            self.receive_timer.stop();self.status.emit('扫码接收已停止');return
        self.port.close();self.status.emit('未打开')

    def _error(self,error):
        if error in (QSerialPort.ResourceError,QSerialPort.ReadError):
            reason=self.port.errorString()
            self.waiting=False;self.owner=None;self.reset()
            self.port.blockSignals(True)
            try:self.port.close()
            finally:self.port.blockSignals(False)
            self.status.emit('串口接收失败：'+reason)

    def _read(self):
        data=bytes(self.port.readAll());self.raw_received.emit(data);self.feed(data)

    def feed(self,data):
        if not self.waiting:return
        for byte in data:
            if byte in (10,13):
                self._finish()
            elif self.waiting:
                if len(self.buffer)>=4096:self.overflow=True
                elif not self.overflow:self.buffer.append(byte)
        if self.buffer and self.waiting:
            self.timer.start(150 if self.config.get('scanner_framing','idle')=='idle' else 3000)

    def _finish(self):
        if not self.buffer and not self.overflow:return
        data=bytes(self.buffer);overflow=self.overflow;self._clear_frame()
        try:self._process_frame(data,overflow)
        finally:self._schedule_continuous()

    def _process_frame(self,data,overflow):
        if overflow:
            self.status.emit('扫码数据超过4096字节，已丢弃，请重新扫码');return
        # In delimiter mode, silence is a timeout, never an implicit barcode end.
        if self.sender() is self.timer and self.config.get('scanner_framing','idle')!='idle':
            self.status.emit('未收到回车/换行结束符，已丢弃不完整条码');return
        try:barcode=data.decode(self.config.get('scanner_encoding','ascii')).strip()
        except UnicodeDecodeError:
            self.status.emit('条码解码失败，请检查编码及串口参数');return
        if not barcode or any(ord(c)<32 or ord(c)==127 for c in barcode):
            self.status.emit('收到控制字符或非条码数据，已忽略');return
        if barcode.upper() in ('NR','NOREAD','NO READ'):
            self.status.emit('扫码器未识别条码，请重新扫码');return
        self.waiting=getattr(self,'continuous',False)
        self.status.emit('已收到条码（仍需通过配方及MES校验）')
        self.barcode.emit(barcode)
    def _schedule_continuous(self):
        if self.waiting and getattr(self,'continuous',False) and self.config.get('scanner_trigger',True) and self.port.isOpen():
            self.trigger_watchdog.stop()
            if self.retrigger_timer.isActive() or self.trigger_on_timer.isActive():return
            self._retrigger_owner=self.owner
            self.retrigger_timer.start(0)

    def _retrigger_continuous(self,owner):
        if self.owner is not owner or not self.waiting or not self.port.isOpen():return
        try:
            self.port.write(b'TOFF')
            # Give the device a real stop edge; separate writes can otherwise
            # arrive as one TOFFTON chunk and fail to arm the next decode.
            self.trigger_on_timer.start(100)
        except Exception as exc:
            self.waiting=False;self.status.emit('继续扫码失败：'+str(exc))

    def _send_continuous_ton(self):
        if not self.waiting or self.owner is not self._retrigger_owner or not self.port.isOpen():return
        try:
            self._clear_frame()
            self.port.write(b'TON');self.trigger_watchdog.start(3000)
        except Exception as exc:
            self.waiting=False;self.status.emit('继续扫码失败：'+str(exc))


class ScannerPanel(QGroupBox):
    settings_saved=Signal(dict)
    def __init__(self,db,parent=None,reader=None):
        super().__init__('infoscan FV31-2110 串口扫码器',parent)
        self.db=db;self.can_edit=lambda:True;self.reader=reader or SerialScanner(self)
        form=QFormLayout(self)
        self.enabled=QCheckBox('启用串口扫码器（保存并连接后生效）')
        self.com=QLineEdit('COM5');self.baud=QComboBox();self.baud.addItems(['115200','9600','19200','38400','57600']);self.baud.setEditable(True)
        self.bits=QComboBox();self.bits.addItems(['8','7'])
        self.parity=QComboBox();self.parity.addItems(['None','Even','Odd'])
        self.stops=QComboBox();self.stops.addItems(['1','2'])
        self.encoding=QComboBox();self.encoding.addItems(['ascii','utf-8','gb18030'])
        self.framing=QComboBox();self.framing.addItem('回车/换行或150ms无新数据（无后缀兼容）','idle');self.framing.addItem('必须回车/换行（推荐设备配置后使用）','delimiter')
        self.trigger=QCheckBox('扫码窗口打开时发送TON，关闭时发送TOFF');self.trigger.setChecked(True)
        form.addRow(self.enabled)
        row=QWidget();layout=QHBoxLayout(row);layout.setContentsMargins(0,0,0,0)
        for label,widget in [('串口',self.com),('波特率',self.baud),('数据位',self.bits),('校验',self.parity),('停止位',self.stops)]:layout.addWidget(QLabel(label));layout.addWidget(widget)
        form.addRow(row);form.addRow('条码编码',self.encoding);form.addRow('条码结束方式',self.framing);form.addRow(self.trigger)
        hint=QLabel('115200 / 8N1 为厂家示例参数，并非实机回读。建议用Infostepper配置条码后缀为回车/换行，再选择必须结束符。仅在扫码窗口接收条码，其他时间不启动测试。');hint.setWordWrap(True);form.addRow(hint)
        self.save=QPushButton('保存并连接扫码器');self.save.clicked.connect(self.apply)
        self.state=QLabel('串口已连接（扫码未验证）' if self.reader.port.isOpen() else '未打开');self.state.setWordWrap(True);self.state.setTextFormat(Qt.PlainText)
        self.reader.status.connect(self.state.setText);form.addRow(self.save);form.addRow(self.state)
        cfg=db.load_sys_config() or {}
        self.reload_config()
        if not self.reader.driver_provider:QTimer.singleShot(0,lambda:self.reader.configure(cfg))

    def reload_config(self):
        cfg=self.db.load_sys_config() or {}
        self.enabled.setChecked(bool(cfg.get('scanner_enabled',False)));self.com.setText(cfg.get('scanner_com','COM5'))
        for widget,key,default in [(self.baud,'scanner_baudrate',115200),(self.bits,'scanner_data_bits',8),(self.parity,'scanner_parity','None'),(self.stops,'scanner_stop_bits',1),(self.encoding,'scanner_encoding','ascii')]:widget.setCurrentText(str(cfg.get(key,default)))
        self.framing.setCurrentIndex(max(0,self.framing.findData(cfg.get('scanner_framing','idle'))));self.trigger.setChecked(bool(cfg.get('scanner_trigger',True)))

    def apply(self):
        try:
            if not self.can_edit():raise ValueError('测试或准备中不能更改扫码器连接')
            baud=int(self.baud.currentText())
            if not 300<=baud<=2000000:raise ValueError('波特率必须在300–2000000之间')
            com=self.com.text().strip().upper()
            import re
            if not re.fullmatch(r'COM[1-9][0-9]*',com):raise ValueError('请输入有效串口号，例如COM5')
            cfg=dict(scanner_enabled=self.enabled.isChecked(),scanner_com=com,scanner_baudrate=baud,scanner_data_bits=int(self.bits.currentText()),scanner_parity=self.parity.currentText(),scanner_stop_bits=int(self.stops.currentText()),scanner_encoding=self.encoding.currentText(),scanner_framing=self.framing.currentData(),scanner_trigger=self.trigger.isChecked())
            if not self.db.save_sys_config(cfg):raise ValueError('扫码器配置保存失败')
            self.settings_saved.emit(cfg)
            self.reader.configure(cfg)
        except Exception as exc:self.state.setText(str(exc))


class ScannerDebugTab(QWidget):
    def __init__(self,panel,parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout,QPlainTextEdit
        self.reader=panel.reader;self.can_edit=panel.can_edit;self.active=False
        self.panel=panel
        layout=QVBoxLayout(self)
        layout.addWidget(panel)
        hint=QLabel('与硬件管理中心共用串口；连接只打开串口，点击触发才发送扫码指令。调试结果不会启动生产测试。');hint.setWordWrap(True);layout.addWidget(hint)
        self.state=panel.state
        self.result=QLineEdit();self.result.setReadOnly(True);layout.addWidget(self.result)
        row=QHBoxLayout();layout.addLayout(row)
        self.start_button=QPushButton('触发一次扫码');self.start_button.clicked.connect(self.start);row.addWidget(self.start_button)
        stop=QPushButton('停止扫码');stop.clicked.connect(self.stop);row.addWidget(stop)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(200);layout.addWidget(self.log)
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(self.stop)
        self.reader.raw_received.connect(self.raw)
        self.reader.barcode.connect(self.received)

    def start(self):
        if not self.can_edit():self.state.setText('生产测试或准备中，不能调试扫码器');return
        if not self.reader.port.isOpen():self.state.setText('请先连接扫码器');return
        if not self.reader.begin_scan(self):self.state.setText('已有扫码任务，请先结束该任务');return
        self.active=True;self.result.clear();self.state.setText('等待扫码（10秒）');self.timeout.start(10000)

    def stop(self):
        self.timeout.stop()
        if self.active:self.reader.end_scan(self)
        self.active=False

    def received(self,barcode):
        if not self.active:return
        self.result.setText(barcode);self.log.appendPlainText('条码：'+barcode);self.stop()

    def raw(self,data):
        if self.active:self.log.appendPlainText('RX HEX：'+data.hex(' ').upper())

    def hideEvent(self,event):
        self.stop();super().hideEvent(event)

    def showEvent(self,event):
        self.panel.reload_config()
        self.state.setText('串口已连接（扫码未验证）' if self.reader.port.isOpen() else '未打开')
        super().showEvent(event)
