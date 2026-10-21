"""Shared temperature/humidity driver, with nonblocking debug reads."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                              QLabel, QPushButton, QCheckBox, QSpinBox,
                              QPlainTextEdit, QGroupBox)
from ui.device_task import DeviceTask


class EnvironmentTab(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._ui_driver = manager.rs_ws_eth6
        self._last_displayed = None
        self.task = DeviceTask(self)
        self.task.finished.connect(self._update_controls)
        body = QVBoxLayout(self)
        body.setContentsMargins(14, 12, 14, 12)
        self.address = QLabel()
        self.address.setWordWrap(True)
        body.addWidget(self.address)
        self.tip = QLabel()
        self.tip.setWordWrap(True)
        body.addWidget(self.tip)
        row = QHBoxLayout()
        self.connect_button = QPushButton('连接')
        self.read_button = QPushButton('读取温湿度')
        self.disconnect_button = QPushButton('断开')
        self.connect_button.clicked.connect(self.connect_sensor)
        self.read_button.clicked.connect(self.read_sensor)
        self.disconnect_button.clicked.connect(self.disconnect_sensor)
        for button in (self.connect_button, self.read_button, self.disconnect_button):
            row.addWidget(button)
        row.addStretch()
        body.addLayout(row)
        readings = QGroupBox('当前测量值')
        grid = QGridLayout(readings)
        grid.addWidget(QLabel('温度'), 0, 0)
        grid.addWidget(QLabel('相对湿度'), 0, 1)
        self.temperature = QLabel('— ℃')
        self.humidity = QLabel('— %RH')
        for col, label in enumerate((self.temperature, self.humidity)):
            label.setStyleSheet('font-size:28px; font-weight:bold; padding:12px 0;')
            grid.addWidget(label, 1, col)
            grid.setColumnStretch(col, 1)
        self.details = QLabel('尚未读取')
        self.details.setWordWrap(True)
        grid.addWidget(self.details, 2, 0, 1, 2)
        body.addWidget(readings)
        options = QHBoxLayout()
        self.continuous = QCheckBox('连续读取')
        self.continuous.toggled.connect(self._continuous_changed)
        options.addWidget(self.continuous)
        options.addWidget(QLabel('读取周期'))
        self.interval = QSpinBox()
        self.interval.setRange(1000, 60000)
        self.interval.setValue(1000)
        self.interval.setSuffix(' ms')
        options.addWidget(self.interval)
        options.addStretch()
        body.addLayout(options)
        self.status = QLabel()
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setPlaceholderText('读取记录与通信错误')
        self.log.setMinimumHeight(90)
        self.log.setMaximumHeight(200)
        body.addWidget(self.log)
        body.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval.value())
        self.timer.timeout.connect(self.poll)
        self.interval.valueChanged.connect(self.timer.setInterval)
        self.timer.start()
        self._sync_driver()

    @property
    def driver(self):
        return self.manager.rs_ws_eth6

    def _clear_values(self, text='尚未读取'):
        self._last_displayed = None
        self.temperature.setText('— ℃')
        self.humidity.setText('— %RH')
        self.details.setText(text)

    def _sync_driver(self):
        driver = self.driver
        if driver is not self._ui_driver:
            self._ui_driver = driver
            self.continuous.setChecked(False)
            self._clear_values('配置已更新，请重新读取')
        upload = driver.mode == 'upload'
        self.address.setText(f'RS-WS-ETH-6 · {driver.ip or "未配置 IP"} · '
                             + (f'本机监听 TCP {driver.port} · 节点 {driver.unit}' if upload else
                                f'端口 {driver.port} · 站号 {driver.unit}'))
        self.tip.setText('① 在硬件管理中心配置　② 启动接收　③ 查看上传的温湿度\n'
                         '传感器服务器目标地址需填写本机 IP，目标端口与本机监听端口一致。' if upload else
                         '① 在硬件管理中心配置　② 连接　③ 单次读取或连续读取\n'
                         'Modbus 传感器需设为 TCP Server；端口填写设备的本地端口。')
        self.connect_button.setText('启动接收' if upload else '连接')
        self.disconnect_button.setText('停止接收' if upload else '断开')
        self.continuous.setText('自动刷新上传数据' if upload else '连续读取')
        self._update_controls()

    def _update_controls(self):
        enabled = self.manager.is_enabled('rs_ws_eth6')
        idle = not self.task.isRunning() and not self.task.closing
        connected = self.driver.is_connected
        listening = getattr(self.driver, 'is_listening', False)
        self.connect_button.setEnabled(idle and enabled)
        self.read_button.setEnabled(idle and enabled and (connected or listening))
        self.disconnect_button.setEnabled(idle and (connected or listening))
        self.continuous.setEnabled(enabled and (connected or listening))
        if self.driver.mode == 'upload':
            state = '已收到数据' if connected else ('数据超时' if self.driver.data_age is not None else '监听中，等待上传') if listening else '未启动接收'
        else:
            state = '已连接' if connected else '未连接'
        self.status.setText('未启用，请在硬件管理中心勾选启用并保存' if not enabled else
                            state +
                            (f' · {self.driver.last_error}' if self.driver.last_error else ''))

    def _display_reading(self, result, label):
        self.temperature.setText(f'{result.temperature:.1f} ℃')
        self.humidity.setText(f'{result.humidity:.1f} %RH')
        extra = (f'终端 {result.terminal_id} / 节点 {result.node}' if result.terminal_id else
                 f'响应 {result.elapsed_ms:.1f} ms')
        text = (f'采集时间 {result.captured_at:%Y-%m-%d %H:%M:%S} · {extra} · '
                f'原始值 {result.registers[0]:04X} {result.registers[1]:04X}')
        self.details.setText(text)
        if result.captured_at != self._last_displayed:
            self.log.appendPlainText(f'{label}：{result.temperature:.1f} ℃ / {result.humidity:.1f} %RH · {text}')
            self._last_displayed = result.captured_at

    def _run(self, action, label):
        if self.task.closing or self.task.isRunning():
            return
        self._sync_driver()
        driver = self.driver
        if not self.manager.is_enabled('rs_ws_eth6') and label != '断开':
            return
        def completed(ok, result):
            # Configuration may have replaced the shared driver during a read.
            if driver is not self.driver:
                self._sync_driver()
                return
            if ok and result is not None:
                self._display_reading(result, label)
            elif ok and getattr(driver, 'is_listening', False):
                self._clear_values('监听已启动，等待传感器上传')
                self.continuous.setChecked(True)
                self.log.appendPlainText('本机监听已启动，尚未收到温湿度数据')
            else:
                self.continuous.setChecked(False)
                self._clear_values('已断开' if ok else '读取失败，当前值无效')
                self.log.appendPlainText(f'{label}：' + ('已断开' if ok else str(result)))
            self._update_controls()
        if self.task.run(lambda: action(driver), completed):
            self._update_controls()

    def connect_sensor(self):
        self.continuous.setChecked(False)
        def connect(driver):
            if not driver.connect():
                raise ConnectionError(driver.last_error)
            return driver.last_reading
        self._run(connect, '连接并读取')

    def read_sensor(self):
        self._run(lambda driver: driver.read_environment(), '读取')

    def disconnect_sensor(self):
        self.continuous.setChecked(False)
        self._run(lambda driver: driver.disconnect(), '断开')

    def _continuous_changed(self, checked):
        if checked:
            if self.driver.mode == 'upload':
                self.poll()
            else:
                self.read_sensor()

    def poll(self):
        if not self.isVisible() or self.task.closing:
            return
        self._sync_driver()
        if self.driver.mode == 'upload':
            reading = self.driver.last_reading
            if reading is None:
                self._clear_values('尚未收到有效数据，或数据已超时')
            elif self.continuous.isChecked():
                self._display_reading(reading, '上传')
            return
        if not self.driver.is_connected:
            self.continuous.setChecked(False)
            if self.driver.last_reading is None:
                self._clear_values('尚未连接或连接已断开')
        elif self.continuous.isChecked():
            self.read_sensor()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_driver()

    def hideEvent(self, event):
        self.continuous.setChecked(False)
        super().hideEvent(event)

    def shutdown(self):
        self.timer.stop()
        self.task.shutdown()
