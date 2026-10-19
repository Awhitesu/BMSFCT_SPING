from datetime import datetime
from PySide6.QtCore import QObject, QCoreApplication, QThread, Signal, Slot, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit,
                              QSpinBox, QPushButton, QComboBox, QCheckBox,
                              QPlainTextEdit, QTabWidget, QScrollArea)
from devices.keysight_34461a import Keysight34461A


class _MeterTaskLifetime(QObject):
    """Retain one-shot threads and their page closures until native exit."""
    def __init__(self):
        super().__init__(QCoreApplication.instance())
        self.tasks = set()
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.retire)

    @Slot()
    def retire(self):
        for task in tuple(self.tasks):
            if not task.isRunning() and task.wait(0):
                # Final page references are released here on the GUI thread.
                task.action = None
                self.tasks.remove(task)
        if not self.tasks:
            self.timer.stop()


class MeterTask(QThread):
    result = Signal(bool, object)

    def __init__(self, action):
        super().__init__()
        self.action = action
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError('MeterTask must be created on the GUI thread')
        if not hasattr(app, '_meter_task_lifetime'):
            app._meter_task_lifetime = _MeterTaskLifetime()
        self._lifetime = app._meter_task_lifetime
        self.finished.connect(self._lifetime.retire)

    def start(self, priority=QThread.InheritPriority):
        self._lifetime.tasks.add(self)
        self._lifetime.timer.start()
        super().start(priority)

    def run(self):
        try:
            self.result.emit(True, self.action())
        except Exception as exc:
            self.result.emit(False, str(exc))


class MultimeterTab(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._fallback_driver = Keysight34461A()
        self._ui_driver=getattr(manager,'dmm34461',self._fallback_driver)
        self.task = None
        self.controls = []
        cfg = manager.db_manager.load_sys_config() or {}
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        page = QWidget()
        body = QVBoxLayout(page)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self.ip = QLineEdit(cfg.get('dmm34461_ip', ''))
        self.ip.setPlaceholderText('输入34461A仪表IP地址')
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cfg.get('dmm34461_port', 5025)))
        grid.addWidget(QLabel('设备IP'), 0, 0)
        grid.addWidget(self.ip, 0, 1)
        grid.addWidget(QLabel('端口'), 0, 2)
        grid.addWidget(self.port, 0, 3)
        self._button(grid, '连接', self.connect_meter, 1, 0)
        self._button(grid, '断开', self.disconnect_meter, 1, 1)
        self._button(grid, '保存连接配置', self.save_config, 1, 2)
        body.addLayout(grid)
        self.status = QLabel('未连接 · LAN / SCPI')
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.value = QLabel('—')
        self.value.setStyleSheet('font-size:32px; font-weight:bold; padding:16px;')
        body.addWidget(self.value)
        measure = QGridLayout()
        measure.setVerticalSpacing(14)
        measure.setHorizontalSpacing(14)
        self.function = QComboBox()
        self.function.addItems(Keysight34461A.FUNCTIONS)
        self.range = QLineEdit()
        self.range.setPlaceholderText('留空 = 自动量程；手动量程按 V / A / Ω / Hz / s 输入')
        measure.addWidget(QLabel('测量功能'), 0, 0)
        measure.addWidget(self.function, 0, 1)
        measure.addWidget(QLabel('量程'), 1, 0)
        measure.addWidget(self.range, 1, 1)
        self._button(measure, '单次测量', self.measure, 2, 0)
        self.continuous = QCheckBox('连续读取')
        measure.addWidget(self.continuous, 2, 1)
        self.interval = QSpinBox()
        self.interval.setRange(200, 60000)
        self.interval.setValue(1000)
        self.interval.setSuffix(' ms')
        measure.addWidget(QLabel('读取间隔'), 3, 0)
        measure.addWidget(self.interval, 3, 1)
        body.addLayout(measure)
        body.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, '连接与测量')
        debug = QWidget()
        debug_layout = QVBoxLayout(debug)
        self.scpi = QLineEdit('*IDN?')
        debug_layout.addWidget(QLabel('发送单行SCPI指令；含 ? 的指令自动读取响应。'))
        debug_layout.addWidget(self.scpi)
        buttons = QGridLayout()
        self._button(buttons, '发送指令', self.send_scpi, 0, 0)
        self._button(buttons, '查询设备信息', lambda: self.run('*IDN?', lambda: self.driver.query('*IDN?')), 0, 1)
        self._button(buttons, '查询仪表错误', lambda: self.run('SYST:ERR?', lambda: self.driver.query('SYST:ERR?')), 0, 2)
        debug_layout.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        debug_layout.addWidget(self.log, 1)
        self.tabs.addTab(debug, 'SCPI调试与日志')
        for control in self.findChildren(QLineEdit) + self.findChildren(QComboBox) + self.findChildren(QSpinBox):
            control.setMinimumHeight(42)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.continuous.toggled.connect(lambda _: self.timer.setInterval(self.interval.value()))
        self.interval.valueChanged.connect(self.timer.setInterval)

    def _button(self, grid, text, callback, row, col):
        button = QPushButton(text)
        button.setMinimumHeight(42)
        button.clicked.connect(callback)
        grid.addWidget(button, row, col)
        self.controls.append(button)

    @property
    def driver(self):
        return getattr(self.manager,'dmm34461',self._fallback_driver)

    def _sync_driver_config(self):
        driver=self.driver
        if driver is not self._ui_driver and not (self.task and self.task.isRunning()):
            self._ui_driver=driver
            self.ip.setText(driver.ip);self.port.setValue(driver.port)
            self.continuous.setChecked(False)
            self.status.setText(f'已连接 · {driver.identity}' if driver.is_connected else '未连接')

    def run(self, label, action, measurement=False):
        if self.task and self.task.isRunning():
            return
        for button in self.controls:
            button.setEnabled(False)
        self.task = MeterTask(action)
        self.task.result.connect(lambda ok, result: self.on_result(label, ok, result, measurement))
        self.task.finished.connect(lambda: [button.setEnabled(True) for button in self.controls])
        self.task.start()

    def on_result(self, label, ok, result, measurement):
        self.status.setText(f'已连接 · {self.driver.identity}' if self.driver.is_connected else '未连接')
        if ok and measurement:
            number, unit = result
            self.value.setText(f'{number:.9g} {unit}')
        if not ok:
            self.continuous.setChecked(False)
            if measurement:
                self.value.setText('测量失败')
        self.log.appendPlainText(f'[{datetime.now():%H:%M:%S}] {label}: {result if ok else "失败 · " + str(result)}')

    def connect_meter(self):
        self.continuous.setChecked(False)
        ip, port = self.ip.text().strip(), self.port.value()
        def connect():
            self.driver.ip, self.driver.port = ip, port
            if not self.driver.connect():
                raise ConnectionError(self.driver.last_error)
            return self.driver.identity
        self.run('连接', connect)

    def disconnect_meter(self):
        self.continuous.setChecked(False)
        self.run('断开', lambda: self.driver.disconnect())

    def save_config(self):
        cfg = dict(self.manager.db_manager.load_sys_config() or {})
        cfg.update(dmm34461_ip=self.ip.text().strip(), dmm34461_port=self.port.value(),dmm34461_enabled=True)
        ok = self.manager.db_manager.save_sys_config(cfg)
        self.log.appendPlainText('连接配置已保存' if ok else '连接配置保存失败')
        self.status.setText('连接配置已保存' if ok else '连接配置保存失败')
        if ok:
            self.manager.enabled_devices['dmm34461']=True
            parent=self.parent()
            while parent:
                if hasattr(parent,'tab_hardware'):
                    parent.tab_hardware.load_config();break
                parent=parent.parent()

    def measure(self):
        function, range_value = self.function.currentText(), self.range.text().strip() or 'AUTO'
        self.run('测量', lambda: self.driver.measure(function, range_value), measurement=True)

    def poll(self):
        if not self.isVisible(): return
        self._sync_driver_config()
        if self.continuous.isChecked() and self.driver.is_connected and not (self.task and self.task.isRunning()):
            self.measure()

    def send_scpi(self):
        self.continuous.setChecked(False)
        command = self.scpi.text()
        self.run(command, lambda: self.driver.command(command))

    def shutdown(self):
        self.timer.stop()
        if self.task:
            self.task.wait()
        self.driver.disconnect()
