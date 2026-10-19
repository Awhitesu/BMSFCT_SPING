from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton,
                              QComboBox, QSpinBox, QDoubleSpinBox, QGroupBox,
                              QPlainTextEdit, QScrollArea)
from ui.tabs.multimeter_tab import MeterTask
from ui.tabs.hardware_config_tab import HardwareConfigTab
from core.instrument_control import control_power, connect_instrument, instrument_transaction


class IT6333BTab(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.task = None
        self.buttons = []
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(16)
        connection = QGridLayout()
        connection.setSpacing(14)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.addItems([name for name, _ in HardwareConfigTab.get_available_serial_ports()])
        self.port.setEditText(manager.it6333b.port)
        self.baud = QSpinBox()
        self.baud.setRange(300, 115200)
        self.baud.setValue(manager.it6333b.baudrate)
        connection.addWidget(QLabel('串口'), 0, 0)
        connection.addWidget(self.port, 0, 1)
        connection.addWidget(QLabel('波特率（与仪表设置一致）'), 0, 2)
        connection.addWidget(self.baud, 0, 3)
        self.button(connection, '连接', self.connect_device, 1, 0)
        self.button(connection, '断开', lambda: self.run('断开', lambda d: d.disconnect()), 1, 1)
        self.button(connection, '保存配置', self.save_config, 1, 2)
        self.button(connection, '刷新串口', self.refresh_ports, 1, 3)
        body.addLayout(connection)
        self.status = QLabel('未连接')
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.channels = {}
        for ch in range(1, 4):
            box = QGroupBox(f'CH{ch} · {60 if ch < 3 else 5}V / 3A')
            grid = QGridLayout(box)
            grid.setContentsMargins(16, 30, 16, 16)
            grid.setSpacing(14)
            voltage, current = QDoubleSpinBox(), QDoubleSpinBox()
            voltage.setRange(0, 60 if ch < 3 else 5)
            current.setRange(0, 3)
            voltage.setDecimals(3)
            current.setDecimals(3)
            voltage.setSuffix(' V')
            current.setSuffix(' A')
            grid.addWidget(QLabel('设定电压'), 0, 0)
            grid.addWidget(voltage, 0, 1)
            grid.addWidget(QLabel('电流限值'), 0, 2)
            grid.addWidget(current, 0, 3)
            reading = QLabel('实测电压：—　实测电流：—　输出：—')
            reading.setWordWrap(True)
            self.channels[ch] = voltage, current, reading
            self.button(grid, '应用电压 / 电流', lambda _, c=ch: self.apply(c), 1, 0)
            self.button(grid, '开启输出', lambda _, c=ch: self.output(c, True), 1, 1)
            self.button(grid, '关闭输出', lambda _, c=ch: self.output(c, False), 1, 2)
            self.button(grid, '回读', lambda _, c=ch: self.read(c), 1, 3)
            grid.addWidget(reading, 2, 0, 1, 4)
            body.addWidget(box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setMinimumHeight(160)
        body.addWidget(self.log)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        for control in content.findChildren(QDoubleSpinBox) + content.findChildren(QSpinBox) + content.findChildren(QComboBox):
            control.setMinimumHeight(42)

    def button(self, grid, text, callback, row, col):
        button = QPushButton(text)
        button.setMinimumHeight(42)
        button.clicked.connect(callback)
        grid.addWidget(button, row, col)
        self.buttons.append(button)

    def run(self, label, action, channel=None):
        if self.task and self.task.isRunning():
            return
        driver = self.manager.it6333b
        for button in self.buttons:
            button.setEnabled(False)
        self.task = MeterTask(lambda: action(driver))
        self.task.result.connect(lambda ok, result: self.result(label, ok, result, channel))
        self.task.finished.connect(lambda: [b.setEnabled(True) for b in self.buttons])
        self.task.start()

    def result(self, label, ok, result, channel):
        driver = self.manager.it6333b
        control_result = isinstance(result, bool)
        if result is False:
            ok = False
            result = driver.last_error or '控制未确认'
        self.status.setText(f'已连接 · {driver.idn}' if driver.is_connected else '未连接')
        self.log.appendPlainText(f'[{datetime.now():%H:%M:%S}] {label}: {result if ok else "失败 · " + str(result)}')
        if control_result and driver.last_control:
            self.log.appendPlainText(f'控制确认：{driver.last_control}')
        if channel and ok:
            v, a, on = result
            self.channels[channel][2].setText(f'实测电压：{v:.4f} V　实测电流：{a:.4f} A　输出：{"开启" if on else "关闭"}')
        elif channel:
            self.channels[channel][2].setText('回读失败，当前测量无效')

    def connect_device(self):
        port, baud = self.port.currentText().strip(), self.baud.value()
        def connect(driver):
            with instrument_transaction(driver):
                driver.port, driver.baudrate = port, baud
                connect_instrument(driver)
                return driver.idn
        self.run('连接', connect)

    def apply(self, channel):
        v, a, _ = self.channels[channel]
        voltage, current = v.value(), a.value()
        def configure(driver):
            with instrument_transaction(driver):
                control_power(driver, 'set_current', current, channel=channel)
                return control_power(driver, 'set_voltage', voltage, channel=channel)
        self.run(f'CH{channel}设定参数', configure)

    def output(self, channel, state):
        self.run(f'CH{channel}输出{"开启" if state else "关闭"}',
                 lambda d: control_power(d, 'output_control', state, channel=channel))

    def read(self, channel):
        self.run(f'CH{channel}回读', lambda d: d.read_channel(channel), channel)

    def refresh_ports(self):
        selected = self.port.currentText()
        self.port.clear()
        self.port.addItems([name for name, _ in HardwareConfigTab.get_available_serial_ports()])
        self.port.setEditText(selected)

    def save_config(self):
        cfg = dict(self.manager.db_manager.load_sys_config() or {})
        cfg.update(it6333b_com=self.port.currentText().strip(), it6333b_baudrate=self.baud.value(), it6333b_enabled=True)
        ok = self.manager.db_manager.save_sys_config(cfg)
        self.status.setText('配置已保存并启用，点击连接应用' if ok else '配置保存失败')
        if ok:
            self.manager.enabled_devices['it6333b'] = True
            parent = self.parent()
            while parent:
                if hasattr(parent, 'tab_hardware'):
                    parent.tab_hardware.load_config()
                    break
                parent = parent.parent()

    def shutdown(self):
        if self.task:
            self.task.wait()
