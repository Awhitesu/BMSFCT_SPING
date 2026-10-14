from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit,
                              QSpinBox, QDoubleSpinBox, QPushButton, QComboBox,
                              QTabWidget, QPlainTextEdit, QScrollArea)
from ui.tabs.multimeter_tab import MeterTask
from devices.rigol_dg2052 import DG2052


class DG2052Tab(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.task = None
        self.buttons = []
        self.channels = {}
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.ip = QLineEdit(manager.dg2052.ip)
        self.ip.setPlaceholderText('DG2052设备IP')
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(manager.dg2052.port)
        grid.addWidget(QLabel('设备IP'), 0, 0)
        grid.addWidget(self.ip, 0, 1)
        grid.addWidget(QLabel('TCP端口'), 0, 2)
        grid.addWidget(self.port, 0, 3)
        self.connect_mode=QComboBox()
        for label,value in [('自动（VISA优先）','AUTO'),('VISA / VXI-11（inst0）','VISA'),('TCP Socket（端口连接）','SOCKET')]:self.connect_mode.addItem(label,value)
        self.connect_mode.setCurrentIndex(max(0,self.connect_mode.findData(manager.dg2052.connect_mode)))
        grid.addWidget(QLabel('连接方式'),2,0);grid.addWidget(self.connect_mode,2,1,1,3)
        self.connect_mode.currentIndexChanged.connect(lambda:self.port.setEnabled(self.connect_mode.currentData()!='VISA'))
        self.port.setEnabled(self.connect_mode.currentData()!='VISA')
        self.button(grid, '连接', self.connect_device, 1, 0)
        self.button(grid, '断开', lambda: self.run('断开', lambda d: d.disconnect()), 1, 1)
        self.button(grid, '保存配置', self.save_config, 1, 2)
        layout.addLayout(grid)
        self.status = QLabel('未连接')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        for ch in (1, 2):
            page = QWidget()
            body = QVBoxLayout(page)
            grid = QGridLayout()
            grid.setSpacing(16)
            waveform, load = QComboBox(), QComboBox()
            waveform.addItems(DG2052.WAVEFORMS)
            load.addItem('高阻 HighZ', 'INF')
            load.addItem('50 Ω', '50')
            frequency, amplitude, offset = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
            frequency.setDecimals(6)
            frequency.setRange(0.000001, 50000000)
            frequency.setValue(1000)
            amplitude.setDecimals(4)
            amplitude.setRange(0.001, 20)
            amplitude.setValue(1)
            offset.setDecimals(4)
            offset.setRange(-10, 10)
            for row, (label, control) in enumerate((('波形', waveform), ('频率 (Hz)', frequency), ('幅值 (Vpp)', amplitude), ('偏置 (V)', offset), ('负载设置', load))):
                grid.addWidget(QLabel(label), row, 0)
                grid.addWidget(control, row, 1)
            self.button(grid, '应用参数', lambda _, c=ch: self.apply(c), 5, 0)
            self.button(grid, '回读参数', lambda _, c=ch: self.run(f'CH{c}回读', lambda d: d.read_channel(c), c), 5, 1)
            self.button(grid, '开启输出', lambda _, c=ch: self.run(f'CH{c}开启', lambda d: d.output_control(c, True), c), 6, 0)
            self.button(grid, '关闭输出', lambda _, c=ch: self.run(f'CH{c}关闭', lambda d: d.output_control(c, False), c), 6, 1)
            reading = QLabel('待回读')
            reading.setWordWrap(True)
            body.addLayout(grid)
            body.addWidget(reading)
            body.addWidget(QLabel('幅值使用Vpp；负载设置应与实际连接一致。应用参数失败时查看日志，部分参数可能已生效。'))
            body.addStretch()
            self.channels[ch] = waveform, frequency, amplitude, offset, load, reading
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            tabs.addTab(scroll, f'CH{ch}通道')
        debug = QWidget()
        body = QVBoxLayout(debug)
        self.scpi = QLineEdit('*IDN?')
        body.addWidget(self.scpi)
        grid = QGridLayout()
        self.button(grid, '发送SCPI', self.send_scpi, 0, 0)
        self.button(grid, '查询仪表错误', lambda: self.run('错误查询', lambda d: d.query(':SYST:ERR?')), 0, 1)
        body.addLayout(grid)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        body.addWidget(self.log, 1)
        tabs.addTab(debug, 'SCPI调试与日志')
        for control in self.findChildren(QLineEdit) + self.findChildren(QSpinBox) + self.findChildren(QDoubleSpinBox) + self.findChildren(QComboBox):
            control.setMinimumHeight(42)

    def button(self, grid, text, callback, row, col):
        button = QPushButton(text)
        button.setMinimumHeight(42)
        button.clicked.connect(callback)
        grid.addWidget(button, row, col)
        self.buttons.append(button)

    def run(self, label, action, ch=None):
        if self.task and self.task.isRunning():
            return
        driver = self.manager.dg2052
        for button in self.buttons:
            button.setEnabled(False)
        self.task = MeterTask(lambda: action(driver))
        self.task.result.connect(lambda ok, result: self.result(label, ok, result, ch))
        self.task.finished.connect(lambda: [b.setEnabled(True) for b in self.buttons])
        self.task.start()

    def result(self, label, ok, result, ch):
        driver = self.manager.dg2052
        self.status.setText(f'已连接 · {driver.identity} · {driver.transport}' if driver.is_connected else ('连接/操作失败：'+str(result) if not ok else '未连接'))
        self.log.appendPlainText(f'{label}: {result if ok else "失败 · " + str(result)}')
        if ch:
            self.channels[ch][-1].setText('　'.join(f'{k}: {v}' for k, v in result.items()) if ok else '操作失败，请查看日志并回读当前参数')

    def connect_device(self):
        ip, port,mode = self.ip.text().strip(), self.port.value(),self.connect_mode.currentData()
        def connect(d):
            d.ip, d.port = ip, port
            d.connect_mode=mode
            if not d.connect():
                raise ConnectionError(d.last_error)
            return d.identity
        self.run('连接', connect)

    def apply(self, ch):
        wave, freq, amp, offset, load, _ = self.channels[ch]
        args = (ch, wave.currentText(), freq.value(), amp.value(), offset.value(), load.currentData())
        self.run(f'CH{ch}应用', lambda d: d.configure(*args), ch)

    def send_scpi(self):
        command = self.scpi.text()
        self.run(command, lambda d: d.command(command))

    def save_config(self):
        cfg = dict(self.manager.db_manager.load_sys_config() or {})
        cfg.update(dg2052_ip=self.ip.text().strip(), dg2052_port=self.port.value(), dg2052_enabled=True,dg2052_connect_mode=self.connect_mode.currentData())
        ok = self.manager.db_manager.save_sys_config(cfg)
        self.status.setText('配置已保存，点击连接应用' if ok else '保存失败')
        if ok:
            self.manager.enabled_devices['dg2052'] = True
            parent = self.parent()
            while parent:
                if hasattr(parent, 'tab_hardware'):
                    parent.tab_hardware.load_config()
                    break
                parent = parent.parent()

    def shutdown(self):
        if self.task and self.task.isRunning():
            self.task.wait()
