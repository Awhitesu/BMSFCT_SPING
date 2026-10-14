from ui.device_task import DeviceTask
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QGroupBox, QDoubleSpinBox, 
                               QGridLayout, QComboBox)
from PySide6.QtCore import Qt, QTimer

class MainboardPowerTab(QWidget):
    """
    IT6722A 被测物供电电源控制页（RS232/SCPI）
    """
    def __init__(self, device_manager=None):
        super().__init__()
        self.mgr = device_manager
        self.conn_worker = None
        self.ctrl_worker = None
        self.read_worker = None
        self._reading_in_progress = False
        self.operation=DeviceTask(self)
        self.operation.finished.connect(self._operation_finished)
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 1. 串口配置
        config_group = QGroupBox("IT6722A 被测物供电电源配置 (RS232)")
        config_layout = QHBoxLayout()
        config_layout.addWidget(QLabel("串口号:"))
        self.combo_com = QComboBox()
        self.combo_com.setEditable(True)
        configured_port = "COM1"
        if self.mgr and getattr(self.mgr, "dut_power", None):
            configured_port = str(self.mgr.dut_power.port)
        config_layout.addWidget(self.combo_com)
        self.refresh_serial_ports(configured_port)
        self.combo_com.setEnabled(False)

        self.btn_refresh_com = QPushButton("刷新")
        self.btn_refresh_com.clicked.connect(lambda: self.refresh_serial_ports())
        self.btn_refresh_com.setVisible(False)
        config_layout.addWidget(self.btn_refresh_com)
        
        config_layout.addWidget(QLabel("波特率:"))
        self.edit_baudrate = QLineEdit("9600")
        if self.mgr and getattr(self.mgr, "dut_power", None):
            self.edit_baudrate.setText(str(self.mgr.dut_power.baudrate))
        self.edit_baudrate.setFixedWidth(80)
        self.edit_baudrate.setReadOnly(True)
        config_layout.addWidget(self.edit_baudrate)
        
        self.btn_connect = QPushButton("连接设备")
        self.btn_connect.setStyleSheet("background-color: #007BFF;")
        self.btn_connect.clicked.connect(self.connect_device)
        config_layout.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.setStyleSheet("background-color: #6C757D;")
        self.btn_disconnect.clicked.connect(self.disconnect_device)
        self.btn_disconnect.setEnabled(False)
        self.btn_connect.setVisible(False)
        self.btn_disconnect.setVisible(False)
        config_layout.addWidget(self.btn_disconnect)
        config_layout.addStretch()
        self.lbl_status = QLabel()
        self.lbl_status.setMinimumWidth(140)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        config_layout.addWidget(self.lbl_status)
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
        # 2. 控制面板
        ctrl_group = QGroupBox("电源输出控制")
        ctrl_layout = QGridLayout()
        
        ctrl_layout.addWidget(QLabel("设定电压 (V):"), 0, 0)
        self.spin_volt = QDoubleSpinBox()
        self.spin_volt.setRange(0, 80)
        self.spin_volt.setDecimals(2)
        self.spin_volt.setValue(0.00)
        ctrl_layout.addWidget(self.spin_volt, 0, 1)
        
        self.btn_set_volt = QPushButton("设置电压")
        self.btn_set_volt.clicked.connect(self.set_volt)
        ctrl_layout.addWidget(self.btn_set_volt, 0, 2)
        
        ctrl_layout.addWidget(QLabel("设定电流 (A):"), 1, 0)
        self.spin_curr = QDoubleSpinBox()
        self.spin_curr.setRange(0, 20)
        self.spin_curr.setDecimals(2)
        self.spin_curr.setValue(1.00)
        ctrl_layout.addWidget(self.spin_curr, 1, 1)
        
        self.btn_set_curr = QPushButton("设置电流")
        self.btn_set_curr.clicked.connect(self.set_curr)
        ctrl_layout.addWidget(self.btn_set_curr, 1, 2)
        
        self.btn_on = QPushButton("开启电源输出")
        self.btn_on.setFixedHeight(40)
        self.btn_on.setStyleSheet("background-color: #28A745; font-weight: bold;")
        self.btn_on.clicked.connect(lambda: self.control_output(True))
        ctrl_layout.addWidget(self.btn_on, 2, 0, 1, 2)
        
        self.btn_off = QPushButton("关闭输出")
        self.btn_off.setFixedHeight(40)
        self.btn_off.setStyleSheet("background-color: #6C757D;")
        self.btn_off.clicked.connect(lambda: self.control_output(False))
        ctrl_layout.addWidget(self.btn_off, 2, 2)
        
        ctrl_group.setLayout(ctrl_layout)
        layout.addWidget(ctrl_group)
        
        # 3. 实时回读
        read_group = QGroupBox("实时监控数据")
        read_layout = QGridLayout()
        read_layout.addWidget(QLabel("实时电压 (V):"), 0, 0)
        self.lbl_volt = QLabel("0.00")
        self.lbl_volt.setStyleSheet("font-size: 32px; color: #00E5FF; font-weight: bold;")
        read_layout.addWidget(self.lbl_volt, 0, 1)
        read_layout.addWidget(QLabel("实时电流 (A):"), 1, 0)
        self.lbl_curr = QLabel("0.00")
        self.lbl_curr.setStyleSheet("font-size: 32px; color: #76FF03; font-weight: bold;")
        read_layout.addWidget(self.lbl_curr, 1, 1)
        read_group.setLayout(read_layout)
        layout.addWidget(read_group)
        
        layout.addStretch()
        self.timer = QTimer()
        self.timer.timeout.connect(self.start_reading)
        self.timer.setInterval(1000)
        self.connection_timer = QTimer(self)
        self.connection_timer.timeout.connect(lambda:self.refresh_connection_state() if self.isVisible() else None)
        self.connection_timer.start(1000)
        self.refresh_connection_state()

    def refresh_connection_state(self):
        if self.operation.isRunning():return
        connected = bool(self.mgr and self.mgr.dut_power and self.mgr.dut_power.is_connected)
        self.lbl_status.setText("●  已连接" if connected else "●  未连接")
        color = "#22C55E" if connected else "#FF4D5A"
        self.lbl_status.setStyleSheet(f"color:{color}; border:1px solid {color}; border-radius:6px; padding:7px 14px; font-weight:bold;")
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        # 无论设备从何处连接成功（启动自动初始化或本页手动连接），都按实际连接状态启停回读定时器
        if connected and not self.timer.isActive():
            self.timer.start()
        elif not connected and self.timer.isActive():
            self.timer.stop()

    def connect_device(self):
        if not self.mgr or self.operation.isRunning(): return
        self.mgr.dut_power.port = self.combo_com.currentData() or self.combo_com.currentText().split(" — ", 1)[0].strip()
        try: self.mgr.dut_power.baudrate = int(self.edit_baudrate.text())
        except: self.mgr.dut_power.baudrate = 9600
        
        self.btn_connect.setText("⏳ 正在连接...")
        self.btn_connect.setEnabled(False)
        self.run_device(self.mgr.dut_power.connect,lambda ok,value:self.on_connect_finished(ok and value))

    def refresh_serial_ports(self, selected=None):
        if selected is None:
            selected = self.combo_com.currentData() or self.combo_com.currentText().split(" — ", 1)[0].strip()
        ports = []
        try:
            import serial.tools.list_ports
            ports = [(p.device, p.description or p.device) for p in serial.tools.list_ports.comports()]
        except Exception:
            pass
        if selected and selected not in [port for port, _desc in ports]:
            ports.append((selected, "当前配置"))
        if not ports:
            ports = [(f"COM{i}", "未检测，手动选择") for i in range(1, 17)]
        self.combo_com.blockSignals(True)
        self.combo_com.clear()
        for port, description in ports:
            self.combo_com.addItem(port if description == port else f"{port} — {description}", port)
        index = self.combo_com.findData(selected)
        if index >= 0:
            self.combo_com.setCurrentIndex(index)
        self.combo_com.blockSignals(False)

    def on_connect_finished(self, success):
        if success:
            self.btn_connect.setText("已连接")
            self.btn_connect.setStyleSheet("background-color: #28A745;")
            self.btn_disconnect.setEnabled(True)
            self.timer.start()
        else:
            self.btn_connect.setText("连接失败")
            self.btn_connect.setStyleSheet("background-color: #C82333;")
            self.btn_connect.setEnabled(True)
        self.refresh_connection_state()

    def run_device(self,action,callback):
        if self.operation.isRunning():return
        for button in self.findChildren(QPushButton):button.setEnabled(False)
        self.operation.run(action,callback)

    def _operation_finished(self):
        for button in self.findChildren(QPushButton):button.setEnabled(True)
        self.refresh_connection_state()

    def disconnect_device(self):
        if self.mgr:
            self.run_device(self.mgr.dut_power.disconnect,lambda *_:None)

    def start_reading(self):
        if not self.isVisible() or self.operation.isRunning():return
        if self.mgr and self.mgr.dut_power.is_connected:
            driver=self.mgr.dut_power
            def completed(ok,value):
                if driver is self.mgr.dut_power:
                    self.on_read_finished(*(value if ok else (-1,-1)))
            self.run_device(lambda:(driver.measure_voltage(),driver.measure_current()),completed)

    def shutdown(self):
        self.timer.stop();self.connection_timer.stop();self.operation.shutdown()

    def on_read_finished(self, v, c):
        self._reading_in_progress = False
        if v >= 0: 
            self.lbl_volt.setText(f"{v:.2f}")
        else:
            self.lbl_volt.setText("---")
            
        if c >= 0: 
            self.lbl_curr.setText(f"{c:.2f}")
        else:
            self.lbl_curr.setText("---")

    def set_volt(self):
        self._run_control("set_voltage", self.spin_volt.value(), self.btn_set_volt, "设置电压")

    def set_curr(self):
        self._run_control("set_current", self.spin_curr.value(), self.btn_set_curr, "设置电流")

    def control_output(self, state):
        btn = self.btn_on if state else self.btn_off
        self._run_control("output_control", state, btn, "开启电源输出" if state else "关闭输出")

    def _run_control(self, action, value, btn, original_text):
        if not self.mgr or not self.mgr.dut_power.is_connected or self.operation.isRunning(): return
        btn.setText("⏳ 执行中...")
        btn.setEnabled(False)
        driver=self.mgr.dut_power
        self.run_device(lambda:getattr(driver,action)(value),lambda ok,result:self.on_control_finished(ok and result,btn,original_text))

    def on_control_finished(self, success, btn, original_text):
        btn.setEnabled(True)
        if success:
            btn.setText("✅ 成功")
            btn.setStyleSheet("background-color: #28A745; color: white;")
        else:
            btn.setText("❌ 失败")
            btn.setStyleSheet("background-color: #C82333; color: white;")
        QTimer.singleShot(2000, lambda: self._reset_btn(btn, original_text))

    def _reset_btn(self, btn, text):
        btn.setText(text)
        if "开启" in text: btn.setStyleSheet("background-color: #28A745; font-weight: bold;")
        elif "关闭" in text: btn.setStyleSheet("background-color: #6C757D;")
        else: btn.setStyleSheet("")
