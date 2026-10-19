from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QGroupBox, QDoubleSpinBox,
                               QGridLayout, QScrollArea, QSizePolicy, QLayout)
from PySide6.QtCore import Qt, QTimer
from ui.device_task import DeviceTask
from core.numeric_validation import finite_reading
from core.instrument_control import control_power, connect_instrument

class HVSourceTab(QWidget):
    def __init__(self, device_manager=None):
        super().__init__()
        self.mgr = device_manager
        self._polling = False
        self._pending_command = None
        self._connection_snapshot = None
        self.operation = DeviceTask(self)
        self.operation.finished.connect(self._operation_finished)
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0,0,0,0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(14)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.scroll_area.setWidget(content)
        root_layout.addWidget(self.scroll_area)

        # 1. 网络配置
        config_group = QGroupBox("NGI N3618 高压源配置 (TCP/IP)")
        config_layout = QHBoxLayout()
        config_layout.addWidget(QLabel("IP 地址:"))
        self.edit_ip = QLineEdit("192.168.1.190")
        self.edit_ip.setReadOnly(True)
        config_layout.addWidget(self.edit_ip)

        config_layout.addWidget(QLabel("端口:"))
        self.edit_port = QLineEdit("7000")
        self.edit_port.setReadOnly(True)

        self.edit_port.setFixedWidth(60)
        config_layout.addWidget(self.edit_port)

        self.btn_connect = QPushButton("连接设备")

        self.btn_connect.setStyleSheet("background-color: #007BFF;")
        self.btn_connect.clicked.connect(self.connect_hv)
        config_layout.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.setStyleSheet("background-color: #6C757D;")
        self.btn_disconnect.clicked.connect(self.disconnect_hv)
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
        ctrl_group = QGroupBox("高压输出控制")
        ctrl_layout = QGridLayout()
        ctrl_layout.setContentsMargins(18,24,18,18)
        ctrl_layout.setHorizontalSpacing(12)
        ctrl_layout.setVerticalSpacing(12)
        ctrl_layout.setColumnStretch(1,1)
        ctrl_layout.setColumnStretch(2,1)
        ctrl_layout.setSizeConstraint(QLayout.SetMinimumSize)

        ctrl_layout.addWidget(QLabel("设定电压 (V):"), 0, 0)
        self.spin_volt = QDoubleSpinBox()
        self.spin_volt.setRange(0, 1200)
        self.spin_volt.setDecimals(2)
        self.spin_volt.setValue(0.00)
        ctrl_layout.addWidget(self.spin_volt, 0, 1)


        self.btn_set_volt = QPushButton("设置电压")
        self.btn_set_volt.clicked.connect(self.set_hv_volt)
        ctrl_layout.addWidget(self.btn_set_volt, 0, 2)

        ctrl_layout.addWidget(QLabel("设定电流 (A):"), 1, 0)
        self.spin_curr = QDoubleSpinBox()
        self.spin_curr.setRange(0, 50)
        self.spin_curr.setDecimals(3)
        self.spin_curr.setValue(1.000)
        ctrl_layout.addWidget(self.spin_curr, 1, 1)

        self.btn_set_curr = QPushButton("设置电流")
        self.btn_set_curr.clicked.connect(self.set_hv_curr)
        ctrl_layout.addWidget(self.btn_set_curr, 1, 2)

        self.btn_on = QPushButton("🔥 开启高压输出")
        self.btn_on.setFixedHeight(40)
        self.btn_on.setStyleSheet("background-color: #C82333; font-weight: bold;")
        self.btn_on.clicked.connect(lambda: self.control_output(True))
        ctrl_layout.addWidget(self.btn_on, 2, 0, 1, 2)

        self.btn_off = QPushButton("关闭输出")
        self.btn_off.setFixedHeight(40)
        self.btn_off.setStyleSheet("background-color: #6C757D;")
        self.btn_off.clicked.connect(lambda: self.control_output(False))
        ctrl_layout.addWidget(self.btn_off, 2, 2)
        self.spin_confirm_timeout = QDoubleSpinBox()
        self.spin_confirm_timeout.setRange(.1, 120)
        self.spin_confirm_timeout.setSuffix(' s')
        self.spin_confirm_timeout.setValue(5)
        ctrl_layout.addWidget(QLabel('控制确认超时:'), 3, 0)
        ctrl_layout.addWidget(self.spin_confirm_timeout, 3, 1)
        self.spin_confirm_tolerance = QDoubleSpinBox()
        self.spin_confirm_tolerance.setDecimals(3)
        self.spin_confirm_tolerance.setRange(0, 1200)
        self.spin_confirm_tolerance.setValue(1)
        self.spin_confirm_tolerance.setSuffix(' V')
        ctrl_layout.addWidget(QLabel('开启/带载设压实测容差:'), 4, 0)
        ctrl_layout.addWidget(self.spin_confirm_tolerance, 4, 1)
        self.lbl_output = QLabel('输出状态：未知')
        ctrl_layout.addWidget(self.lbl_output, 5, 0, 1, 3)
        self.lbl_confirmation = QLabel('等待控制确认')
        self.lbl_confirmation.setWordWrap(True)
        ctrl_layout.addWidget(self.lbl_confirmation, 6, 0, 1, 3)
        self.lbl_control_stats = QLabel('本次运行：暂无高压控制记录')
        self.lbl_control_stats.setWordWrap(True)
        ctrl_layout.addWidget(self.lbl_control_stats, 7, 0, 1, 3)
        note = QLabel('关闭确认依据输出状态回读，不表示端子已放电到安全电压；设流确认的是限流设定值。')
        note.setWordWrap(True)
        ctrl_layout.addWidget(note, 8, 0, 1, 3)
        for control in (self.spin_volt,self.spin_curr,self.spin_confirm_timeout,self.spin_confirm_tolerance):
            control.setMinimumHeight(44)
        for button in (self.btn_set_volt,self.btn_set_curr,self.btn_on,self.btn_off):
            button.setFixedHeight(44)
        for row in range(5):ctrl_layout.setRowMinimumHeight(row,44)
        for label in (self.lbl_output,self.lbl_confirmation,self.lbl_control_stats,note):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)


        ctrl_group.setLayout(ctrl_layout)
        layout.addWidget(ctrl_group)

        # 3. 实时回读
        read_group = QGroupBox("实时回读数据")
        read_layout = QGridLayout()

        read_layout.addWidget(QLabel("实时电压 (V):"), 0, 0)
        self.lbl_volt = QLabel("—")
        self.lbl_volt.setStyleSheet("font-size: 32px; color: #00E5FF; font-weight: bold;")
        read_layout.addWidget(self.lbl_volt, 0, 1)

        read_layout.addWidget(QLabel("实时电流 (A):"), 1, 0)
        self.lbl_curr = QLabel("—")
        self.lbl_curr.setStyleSheet("font-size: 32px; color: #76FF03; font-weight: bold;")
        read_layout.addWidget(self.lbl_curr, 1, 1)

        read_group.setLayout(read_layout)
        layout.addWidget(read_group)

        layout.addStretch()

        # 定时器用于回读数据
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_readings)
        self.timer.setInterval(1000)
        self.connection_timer = QTimer(self)
        self.connection_timer.timeout.connect(lambda:self.refresh_connection_state() if self.isVisible() else None)
        self.connection_timer.start(1000)
        self.refresh_connection_state()

    def refresh_connection_state(self):
        if self.operation.isRunning() and not self._polling: return
        connected = bool(self.mgr and self.mgr.hv_source and self.mgr.hv_source.is_connected)
        driver=getattr(self.mgr,'hv_source',None)
        snapshot=(id(driver),connected)
        if snapshot != self._connection_snapshot:
            self._connection_snapshot=snapshot
            self.lbl_status.setText("●  已连接" if connected else "●  未连接")
            color = "#22C55E" if connected else "#FF4D5A"
            self.lbl_status.setStyleSheet(f"color:{color}; border:1px solid {color}; border-radius:6px; padding:7px 14px; font-weight:bold;")
            if driver:
                self.edit_ip.setText(driver.ip)
                self.edit_port.setText(str(driver.port))
            self.btn_connect.setEnabled(not connected)
            self.btn_disconnect.setEnabled(connected)
            if not connected:
                self.lbl_output.setText('输出状态：未知')
                self.lbl_volt.setText('—')
                self.lbl_curr.setText('—')
        # 无论设备从何处连接成功（启动自动初始化或本页手动连接），都按实际连接状态启停回读定时器
        if connected and self.isVisible() and not self.timer.isActive():
            self.timer.start()
        elif not connected and self.timer.isActive():
            self.timer.stop()

    def run_device(self, label, action, callback=None, polling=False):
        if self.operation.isRunning():
            if not polling and self._polling and self._pending_command is None:
                self._pending_command=(label,action,callback)
            return
        self._polling=polling
        if not polling:
            for button in self.findChildren(QPushButton): button.setEnabled(False)
        def completed(ok,value):
            if ok:
                value, details = value
                if details:
                    self.show_confirmation(details)
            elif not polling:
                self.lbl_confirmation.setText('控制失败：' + str(value))
                self.lbl_confirmation.setStyleSheet('color:#FF4D5A;')
            if callback: callback(ok,value)
            else: self._notify_status(label + ("成功" if ok and value is not False else "失败："+str(value)))
        def perform():
            driver = getattr(self.mgr, 'hv_source', None)
            previous = getattr(driver, 'last_control', None)
            try:
                value = action()
            except Exception:
                details = getattr(driver, 'last_control', None)
                if isinstance(details, dict) and details and details is not previous:
                    return False, dict(details)
                raise
            details = getattr(driver, 'last_control', None)
            return value, dict(details) if isinstance(details, dict) and details and details is not previous else None
        self.operation.run(perform,completed)

    def show_confirmation(self, details):
        ok = details.get('success') is True
        self.lbl_confirmation.setText(
            f"{'确认成功' if ok else '确认失败'} | 请求 {details.get('requested')} | "
            f"回读 {details.get('actual')} | 耗时 {details.get('elapsed', 0):.2f}s\n{details.get('reason', '')}")
        self.lbl_confirmation.setStyleSheet(f"color:{'#22C55E' if ok else '#FF4D5A'};")
        actual = details.get('actual', {})
        if 'output' in actual:
            self.lbl_output.setText('输出状态：开启（已回读）' if actual['output'] else '输出状态：关闭（已回读）')
        elif details.get('action') == 'output_control':
            self.lbl_output.setText('输出状态：未知')
        stats = details.get('statistics', {})
        if stats:
            total = stats['total']
            self.lbl_control_stats.setText(
                f"本次运行 {total} 次 | 首次确认 {stats['first_pass']} | 等待后确认 {stats['recovered']} | "
                f"失败 {stats['failed']} | 确认成功率 {(stats['first_pass']+stats['recovered'])/total:.1%}")

    def _configured_action(self, driver, action, value):
        timeout = self.spin_confirm_timeout.value()
        tolerance = self.spin_confirm_tolerance.value()
        def configured():
            return control_power(driver, action, value, confirmation=(timeout, tolerance),
                cancelled=lambda: self.operation.closing or getattr(self, '_closing', False))
        return configured

    def connect_hv(self):
        if self.mgr: self.run_device("高压源连接", lambda: connect_instrument(self.mgr.hv_source))

    def disconnect_hv(self):
        if self.mgr: self.run_device("高压源断开",self.mgr.hv_source.disconnect)

    def set_hv_volt(self):
        if self.mgr:
            driver,value=self.mgr.hv_source,self.spin_volt.value()
            self.run_device("高压源设压",self._configured_action(driver,'set_voltage',value))

    def set_hv_curr(self):
        if self.mgr:
            driver,value=self.mgr.hv_source,self.spin_curr.value()
            self.run_device("高压源设流",self._configured_action(driver,'set_current',value))

    def control_output(self,state):
        if self.mgr:
            driver=self.mgr.hv_source
            self.run_device("高压源输出控制",self._configured_action(driver,'output_control',state))

    def _operation_finished(self):
        polling,self._polling=self._polling,False
        if not polling:
            for button in self.findChildren(QPushButton):button.setEnabled(True)
            self.btn_connect.setEnabled(not bool(self.mgr and self.mgr.hv_source.is_connected))
            self.btn_disconnect.setEnabled(bool(self.mgr and self.mgr.hv_source.is_connected))
        self.refresh_connection_state()
        pending,self._pending_command=self._pending_command,None
        if pending:self.run_device(*pending)

    def update_readings(self):
        if not self.isVisible() or self.operation.isRunning(): return
        if self.mgr and self.mgr.hv_source and self.mgr.hv_source.is_connected:
            driver=self.mgr.hv_source
            def completed(ok,value):
                if driver is not self.mgr.hv_source:return
                if not ok:
                    self.lbl_volt.setText('—');self.lbl_curr.setText('—')
                    self.lbl_output.setText('输出状态：未知')
                    return
                v,c,state=value
                text = ('输出状态：开启（已回读）' if state else '输出状态：关闭（已回读）') if isinstance(state, bool) else '输出状态：未知'
                if self.lbl_output.text() != text: self.lbl_output.setText(text)
                for reading,label,digits in ((v,self.lbl_volt,2),(c,self.lbl_curr,3)):
                    try:
                        reading = finite_reading(reading)
                        if reading < 0: raise ValueError('Invalid measurement')
                        text = f'{reading:.{digits}f}'
                    except (ValueError,TypeError):
                        text = '—'
                    if label.text() != text: label.setText(text)
            self.run_device("Read",lambda:(driver.measure_voltage(),driver.measure_current(),driver.read_output_state()),completed,polling=True)

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,'timer'):self.refresh_connection_state()

    def hideEvent(self,event):
        if hasattr(self,'timer'):self.timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self._pending_command=None
        self.timer.stop();self.connection_timer.stop();self.operation.shutdown()

    def _notify_status(self, msg):
        p = self.parent()
        while p:
            if hasattr(p, "show_status"):
                p.show_status(msg)
                break
            p = p.parent()
