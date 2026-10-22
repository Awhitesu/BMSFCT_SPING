from ui.device_task import DeviceTask
import time
import queue
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout, QLabel, QLineEdit, QComboBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QGridLayout, QPlainTextEdit, QTabWidget, QCheckBox, QScrollArea, QLayout, QFileDialog


class UDSRequestWorker(QThread):
    result = Signal(object)

    def __init__(self, client, sid, payload, security=None):
        super().__init__()
        self.client = client
        self.sid = sid
        self.payload = payload
        self.security = security

    def run(self):
        try:
            if self.security is not None:
                from core.seedkey import unlock
                self.result.emit(unlock(self.client,self.security,cancelled=self.isInterruptionRequested))
            else:
                self.result.emit(self.client.request(self.sid, self.payload))
        except Exception as exc:
            self.result.emit(exc)


class PCANFDTab(QWidget):
    def __init__(self, device_manager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self.operation=DeviceTask(self)
        self._monitor_ids = None
        self._monitor_skipped = 0
        self._init_ui()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_transmit_channels)
        self.timer.timeout.connect(self.update_monitor)
        self.timer.start(50)

    def _init_ui(self):
        outer = QVBoxLayout(self)
        self.config_scroll = QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setFrameShape(QScrollArea.NoFrame)
        self.config_scroll.setMinimumHeight(160)
        config_content = QWidget()
        layout = QVBoxLayout(config_content)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.config_scroll.setWidget(config_content)

        connection = QGroupBox("CAN设备连接（PCAN / ZLG CANFDNET-400U）")
        row = QHBoxLayout(connection)
        self.device_type = QComboBox()
        self.device_type.addItems(['PCAN USB', 'ZLG CANFDNET-400U'])
        self.device_type.setCurrentIndex(1 if self.driver.channel.startswith('ZLG_CAN') else 0)
        row.addWidget(QLabel('设备')); row.addWidget(self.device_type)
        self.channel = QComboBox()
        self.channel.setEditable(False)
        self.refresh_pcan_channels(getattr(self.driver, "channel", "PCAN_USBBUS1"))
        self.nominal = QComboBox()
        self.nominal.addItems(["125000", "250000", "500000", "1000000"])
        self.nominal.setCurrentText(str(getattr(self.driver, "nominal_bitrate", 500000)))
        self.data_rate = QComboBox()
        self.data_rate.addItems(["500000", "1000000", "2000000", "4000000", "5000000", "8000000"])
        self.data_rate.setCurrentText(str(getattr(self.driver, "data_bitrate", 2000000)))
        self.pcan_channel_label = QLabel('PCAN通道')
        row.addWidget(self.pcan_channel_label); row.addWidget(self.channel)
        self.refresh_usb = QPushButton("刷新USB")
        self.refresh_usb.clicked.connect(lambda: self.refresh_pcan_channels())
        row.addWidget(self.refresh_usb)
        row.addWidget(QLabel("仲裁波特率")); row.addWidget(self.nominal)
        row.addWidget(QLabel("数据波特率")); row.addWidget(self.data_rate)
        self.btn_connect = QPushButton("连接")
        self.btn_connect.clicked.connect(self.connect_device)
        btn_disconnect = QPushButton("断开")
        btn_disconnect.clicked.connect(self.disconnect_device)
        self.status = QLabel("离线")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(130)
        self.status.setAlignment(Qt.AlignCenter)
        layout.addWidget(connection)
        self.zlg_connection = QGroupBox("ZLG以太网参数（设备需设为TCP服务器）")
        net_layout = QVBoxLayout(self.zlg_connection)
        net = QHBoxLayout()
        net_layout.addLayout(net)
        self.zlg_ip = QLineEdit(str(getattr(self.driver, 'zlg_ip', '192.168.0.177')))
        self.zlg_port = QSpinBox()
        self.zlg_port.setRange(1, 65535)
        self.zlg_port.setValue(int(getattr(self.driver, 'zlg_port', 8000)))
        self.zlg_sdk = QLineEdit(str(getattr(self.driver, 'zlg_sdk_path', '')))
        self.zlg_sdk.setReadOnly(True)
        self.zlg_sdk.setToolTip('通过"浏览…"选择 zlgcan.dll 所在目录（绝对路径）。')
        btn_zlg_sdk = QPushButton('浏览…')
        btn_zlg_sdk.setFixedWidth(64)
        btn_zlg_sdk.clicked.connect(self._browse_zlg_sdk)
        net.addWidget(QLabel('设备IP')); net.addWidget(self.zlg_ip)
        net.addWidget(QLabel('TCP端口')); net.addWidget(self.zlg_port)
        net.addWidget(QLabel('官方SDK目录')); net.addWidget(self.zlg_sdk, 2); net.addWidget(btn_zlg_sdk)
        self.zlg_channels = QTableWidget(4, 5)
        self.zlg_channels.setHorizontalHeaderLabels(['物理通道', '使能', '仲裁波特率', '数据波特率', '120Ω终端电阻'])
        self.zlg_channels.verticalHeader().setVisible(False)
        self.zlg_channels.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.zlg_channels.setAlternatingRowColors(True)
        self.zlg_channel_controls = []
        for index in range(4):
            label = QTableWidgetItem(f'CH{index + 1}')
            label.setFlags(label.flags() & ~Qt.ItemIsEditable)
            self.zlg_channels.setItem(index, 0, label)
            enable, termination = QCheckBox('启用'), QCheckBox('启用')
            enable.setChecked(True)
            nominal, data = QComboBox(), QComboBox()
            nominal.setEditable(True)
            data.setEditable(True)
            nominal.lineEdit().setValidator(QIntValidator(40000, 1000000, nominal))
            data.lineEdit().setValidator(QIntValidator(40000, 5000000, data))
            nominal.setToolTip('可选择或输入仲裁波特率（bit/s）。非标准速率以设备支持为准。')
            data.setToolTip('可选择或输入数据域波特率（bit/s，最高5Mbps）。不等同于自定义采样点/位时序。')
            nominal.addItems(['50000', '100000', '125000', '250000', '500000', '800000', '1000000'])
            nominal.setCurrentText('500000')
            data.addItems(['100000', '125000', '250000', '500000', '800000', '1000000', '2000000', '4000000', '5000000'])
            data.setCurrentText('2000000')
            for col, widget in enumerate((enable, nominal, data, termination), 1):
                self.zlg_channels.setCellWidget(index, col, widget)
            enable.toggled.connect(nominal.setEnabled)
            enable.toggled.connect(data.setEnabled)
            enable.toggled.connect(termination.setEnabled)
            self.zlg_channel_controls.append((enable, nominal, data, termination))
        net_layout.addWidget(self.zlg_channels)
        QTimer.singleShot(0, self._fit_zlg_channel_table)
        self.zlg_persist = QCheckBox('断电保存')
        self.zlg_apply = QPushButton('应用四通道配置')
        self.zlg_apply.clicked.connect(self.apply_zlg_parameters)
        options = QHBoxLayout()
        options.addWidget(self.zlg_persist)
        options.addStretch()
        options.addWidget(self.zlg_apply)
        net_layout.addLayout(options)
        layout.addWidget(self.zlg_connection)
        self.can_connection_tip = QLabel()
        self.can_connection_tip.setWordWrap(True)
        layout.addWidget(self.can_connection_tip)
        self.channel.currentIndexChanged.connect(self._update_connection_controls)
        self.device_type.currentIndexChanged.connect(self._update_connection_controls)

        layout = outer
        common = QHBoxLayout()
        common.addWidget(QLabel('CAN连接状态'))
        common.addWidget(self.status, 1)
        common.addWidget(self.btn_connect)
        common.addWidget(btn_disconnect)
        outer.addLayout(common)
        self.sub_tabs = QTabWidget()
        self.sub_tabs.addTab(self.config_scroll, '设备配置')

        # --- 子页 1: 📡 报文交互监控 (独占大屏高度) ---
        tab_monitor = QWidget()
        monitor_layout = QVBoxLayout(tab_monitor)
        monitor_layout.setContentsMargins(4, 6, 4, 4)

        bar_layout = QHBoxLayout()
        self.btn_clear_monitor = QPushButton("🧹 清空报文")
        self.btn_clear_monitor.clicked.connect(self.clear_monitor)
        self.chk_auto_scroll = QPushButton("⏸ 暂停滚动")
        self.chk_auto_scroll.setCheckable(True)
        self.chk_auto_scroll.toggled.connect(self._toggle_auto_scroll)
        self.lbl_msg_count = QLabel("共 0 条报文")
        self.lbl_msg_count.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 8px;")
        self.monitor_id_filter = QLineEdit()
        self.monitor_id_filter.setMinimumWidth(260)
        self.monitor_id_filter.setPlaceholderText("留空监控全部，例如：0x7E8, 0x7F8, 123")
        self.monitor_id_filter.textChanged.connect(self._update_monitor_id_filter)
        self.lbl_filter_state = QLabel("全部ID")
        self.lbl_filter_state.setStyleSheet("color: #94a3b8; font-size: 12px;")
        bar_layout.addWidget(self.btn_clear_monitor)
        bar_layout.addWidget(self.chk_auto_scroll)
        bar_layout.addWidget(QLabel("监控ID"))
        bar_layout.addWidget(self.monitor_id_filter)
        bar_layout.addWidget(self.lbl_filter_state)
        bar_layout.addWidget(self.lbl_msg_count)
        bar_layout.addStretch()
        monitor_layout.addLayout(bar_layout)

        self.monitor = QTableWidget(0, 8)
        self.monitor.setHorizontalHeaderLabels(["时间", "CAN卡", "物理通道", "方向", "ID", "类型", "DLC", "数据"])
        self.monitor.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        for col in (0, 1, 2, 3, 4, 5, 6):
            self.monitor.horizontalHeader().setSectionResizeMode(col, QHeaderView.Interactive)
            self.monitor.setColumnWidth(col, (115, 150, 105, 60, 90, 80, 55)[col])
        monitor_layout.addWidget(self.monitor, 1)
        self.sub_tabs.addTab(tab_monitor, "📡 报文交互监控")
        from ui.widgets.dbc_forms import DBCMonitorTab
        self.dbc_monitor = DBCMonitorTab(self.device_manager)
        self.sub_tabs.addTab(self.dbc_monitor, "DBC信号监控")

        # --- 子页 2: 📤 报文发送 ---
        tab_send = QWidget()
        send_layout = QVBoxLayout(tab_send)
        send_group = QGroupBox("CAN / CAN-FD 报文发送")
        form = QFormLayout(send_group)
        self.send_channel = QComboBox()
        self.send_channel.setToolTip('ZLG手动发送与UDS共用此物理通道；切换无需重连，不修改配方工步。PCAN使用连接区所选USB通道。')
        self.can_id = QLineEdit("0x7F0")
        self.frame_type = QComboBox()
        self.frame_type.addItems(["经典 CAN", "CAN-FD", "CAN-FD + BRS"])
        self.dlc = QSpinBox(); self.dlc.setRange(0, 15); self.dlc.setValue(8)
        self.data = QLineEdit("00 00 00 00 00 00 00 00")
        send_btn = QPushButton("发送报文")
        send_btn.clicked.connect(self.send_message)
        form.addRow('发送物理通道', self.send_channel)
        form.addRow("CAN ID", self.can_id)
        form.addRow("帧类型", self.frame_type)
        form.addRow("DLC", self.dlc)
        form.addRow("数据", self.data)
        form.addRow("", send_btn)
        send_layout.addWidget(send_group)
        send_layout.addStretch()
        self.sub_tabs.addTab(tab_send, "📤 报文发送")

        # --- 子页 3: 🩺 UDS 诊断（ISO-TP / ISO 14229）---
        tab_uds = QWidget()
        uds_page_layout = QVBoxLayout(tab_uds)
        uds_group = QGroupBox("UDS 诊断配置与响应")
        uds_layout = QGridLayout(uds_group)
        self.uds_tx_id = QLineEdit("0x7E0")
        self.uds_rx_id = QLineEdit("0x7E8")
        self.uds_frame_type = QComboBox()
        self.uds_frame_type.addItems(["经典 CAN", "CAN-FD", "CAN-FD + BRS"])
        self.uds_service = QComboBox()
        self.uds_service.addItems([
            "0x10 诊断会话控制", "0x11 ECU复位", "0x14 清除故障信息",
            "0x19 读取DTC信息", "0x22 按DID读取数据", "0x23 按地址读取内存",
            "0x24 读取DID缩放信息", "0x27 安全访问", "0x28 通信控制",
            "0x2A 周期读取DID", "0x2C 动态定义DID", "0x2E 按DID写入数据",
            "0x2F DID输入输出控制", "0x31 例程控制", "0x34 请求下载",
            "0x35 请求上传", "0x36 数据传输", "0x37 请求退出传输",
            "0x38 文件传输", "0x3D 按地址写内存", "0x3E Tester Present",
            "0x83 访问时序参数", "0x84 安全数据传输", "0x85 DTC设置控制",
            "0x86 事件响应", "0x87 链路控制", "自定义SID",
        ])
        self.uds_service.currentTextChanged.connect(self._update_uds_sid)
        self.uds_sid = QLineEdit("0x10")
        self.uds_params = QLineEdit("03")
        self.uds_params.setPlaceholderText("仅填写SID之后的数据，例如扩展会话填 03")
        self.uds_timeout = QSpinBox(); self.uds_timeout.setRange(1, 60); self.uds_timeout.setValue(2)
        self.uds_send_btn = QPushButton("发送 UDS 请求")
        self.uds_send_btn.clicked.connect(self.send_uds_request)
        self.uds_result = QPlainTextEdit(); self.uds_result.setReadOnly(True); self.uds_result.setMaximumBlockCount(1000)
        self.uds_channel = QComboBox()
        self.uds_channel.setToolTip(self.send_channel.toolTip())
        uds_layout.addWidget(QLabel("请求ID"), 0, 0); uds_layout.addWidget(self.uds_tx_id, 0, 1)
        uds_layout.addWidget(QLabel("响应ID"), 0, 2); uds_layout.addWidget(self.uds_rx_id, 0, 3)
        uds_layout.addWidget(QLabel("帧类型"), 0, 4); uds_layout.addWidget(self.uds_frame_type, 0, 5)
        uds_layout.addWidget(QLabel("UDS服务"), 1, 0); uds_layout.addWidget(self.uds_service, 1, 1, 1, 2)
        uds_layout.addWidget(QLabel("SID"), 1, 3); uds_layout.addWidget(self.uds_sid, 1, 4)
        uds_layout.addWidget(QLabel("超时(s)"), 1, 5); uds_layout.addWidget(self.uds_timeout, 1, 6)
        uds_layout.addWidget(QLabel("服务参数(hex)"), 2, 0); uds_layout.addWidget(self.uds_params, 2, 1, 1, 5)
        uds_layout.addWidget(self.uds_send_btn, 2, 6)
        uds_layout.addWidget(QLabel("响应日志"), 3, 0)
        uds_layout.addWidget(QLabel('UDS物理通道'), 3, 2)
        uds_layout.addWidget(self.uds_channel, 3, 3, 1, 2)
        uds_layout.addWidget(self.uds_result, 4, 0, 1, 7)
        uds_layout.setRowStretch(4, 1)
        self.uds_detail_tabs = QTabWidget()
        self.uds_detail_tabs.addTab(uds_group, '请求与响应')
        uds_page_layout.addWidget(self.uds_detail_tabs, 1)
        security_page = QWidget()
        security_layout = QVBoxLayout(security_page)
        from ui.widgets.security_access_form import SecurityAccessForm
        self.uds_security = SecurityAccessForm()
        security_layout.addWidget(self.uds_security)
        self.uds_unlock_btn = QPushButton('执行 DLL 安全解锁（0x27）')
        self.uds_unlock_btn.clicked.connect(self.unlock_uds_security)
        security_layout.addWidget(self.uds_unlock_btn)
        security_layout.addStretch()
        self.uds_detail_tabs.addTab(security_page, '安全访问 DLL')
        self.sub_tabs.addTab(tab_uds, "🩺 UDS 诊断")

        self.send_channel.currentTextChanged.connect(self.uds_channel.setCurrentText)
        self.uds_channel.currentTextChanged.connect(self.send_channel.setCurrentText)
        self._transmit_signature = None
        self._update_connection_controls()

        layout.addWidget(self.sub_tabs, 1)

    def refresh_pcan_channels(self, selected=None):
        if selected is None:
            selected = self.channel.currentData() or self.channel.currentText().strip()
        from core.can_choices import CAN_CHANNELS
        channels = [channel for channel in CAN_CHANNELS if channel.startswith('PCAN_')]
        self.channel.blockSignals(True)
        self.channel.clear()
        for value in channels:
            self.channel.addItem(value, value)
        index = self.channel.findData(selected)
        if index >= 0:
            self.channel.setCurrentIndex(index)
        self.channel.blockSignals(False)

    @property
    def driver(self):
        return self.device_manager.pcan

    def run_device(self,action,callback):
        if self.operation.isRunning() or (getattr(self,'uds_worker',None) and not self.uds_worker.wait(0)):return
        self.operation.run(action,callback)

    def _browse_zlg_sdk(self):
        current = self.zlg_sdk.text().strip()
        start = current if current and Path(current).is_dir() else ''
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择 ZLG 官方SDK目录中的 zlgcan.dll', start,
            'zlgcan.dll (zlgcan.dll);;动态链接库 (*.dll)')
        if filename:
            self.zlg_sdk.setText(str(Path(filename).resolve().parent))

    def connect_device(self):
        driver=self.driver
        if self.device_type.currentIndex()==1:
            channel=driver.channel if driver.channel.startswith('ZLG_CAN') else 'ZLG_CAN1'
        else:
            channel=self.channel.currentText().strip()
        try:nominal,data=int(self.nominal.currentText()),int(self.data_rate.currentText())
        except ValueError:return
        ip,port,sdk=self.zlg_ip.text().strip(),self.zlg_port.value(),self.zlg_sdk.text().strip()
        def action():
            if channel.startswith('ZLG_CAN'):
                if not ip or not sdk:raise ValueError('ZLG IP / SDK required')
                driver.zlg_ip,driver.zlg_port,driver.zlg_sdk_path=ip,port,sdk
            driver.configure(channel=channel,nominal_bitrate=nominal,data_bitrate=data,fd_enabled=True)
            if not driver.connect():raise ConnectionError(driver.last_error)
            return True
        self.run_device(action,self.operation_result)

    def operation_result(self,ok,value):
        if not ok or value is False:
            QMessageBox.warning(self,"CAN",str(value))
        self._update_transmit_channels()
        self.update_monitor()

    def shutdown(self):
        self.timer.stop()
        self.operation.shutdown()
        if getattr(self,'uds_worker',None):
            self.uds_worker.requestInterruption()
            self.uds_worker.wait()

    def _update_connection_controls(self, *_args):
        zlg = self.device_type.currentIndex()==1
        self.zlg_connection.setVisible(zlg)
        self.pcan_channel_label.setVisible(not zlg)
        self.channel.setVisible(not zlg)
        self.refresh_usb.setVisible(not zlg)
        self.nominal.setEnabled(not zlg)
        self.data_rate.setEnabled(not zlg)
        self.can_connection_tip.setText(
            'ZLG连接整台设备，CH1–CH4独立配置；发送通道在“报文发送”或“UDS诊断”页选择。表格为待设置值，点击“应用四通道配置”才生效。设备TCP转发需包含四通道。'
            if zlg else 'PCAN使用所选USB通道和波特率。连接参数与自动化测试共享，测试执行期间请勿切换连接。')
        self._update_transmit_channels()

    def _update_transmit_channels(self):
        driver=self.driver
        connected=bool(driver.is_connected)
        zlg=driver.channel.startswith('ZLG_CAN') if connected else self.device_type.currentIndex()==1
        signature=(zlg,driver.channel if connected else (None if zlg else self.channel.currentText()))
        if signature==self._transmit_signature:return
        previous=self.send_channel.currentText()
        if zlg:
            channels=[f'ZLG_CAN{i}' for i in range(1,5)]
            # 配方切换或重新连接到另一默认通道时，采用新连接的初始值。
            changed_connection=(self._transmit_signature is not None and self._transmit_signature[1] is not None
                                and self._transmit_signature[1]!=signature[1])
            selected=driver.channel if changed_connection and driver.channel in channels else previous
            if selected not in channels:selected=driver.channel if driver.channel in channels else channels[0]
        else:
            selected=driver.channel if connected else self.channel.currentText()
            channels=[selected]
        for combo in (self.send_channel,self.uds_channel):
            combo.blockSignals(True)
            combo.clear();combo.addItems(channels);combo.setCurrentText(selected)
            combo.setEnabled(zlg)
            combo.blockSignals(False)
        self._transmit_signature=signature

    def _selected_debug_driver(self):
        self._update_transmit_channels()
        driver=self.driver
        if driver.channel.startswith('ZLG_CAN'):
            from devices.can_channel_view import CANChannelView
            channel=self.send_channel.currentText()
            if not channel.startswith('ZLG_CAN'):raise ValueError('请先连接ZLG设备并选择发送物理通道')
            return CANChannelView(driver,int(channel.removeprefix('ZLG_CAN')))
        return driver

    def apply_zlg_parameters(self):
        if self.operation.isRunning():
            return
        if self.device_type.currentIndex()!=1 or not self.driver.is_connected or not self.driver.channel.startswith('ZLG_CAN'):
            QMessageBox.warning(self, '未连接', '请先连接ZLG设备。')
            return
        if getattr(self, 'uds_worker', None) and not self.uds_worker.wait(0):
            QMessageBox.warning(self, '正在执行', '请等待UDS请求结束后再设置。')
            return
        try:
            for index, (enable, nominal, data, _) in enumerate(self.zlg_channel_controls):
                if not nominal.lineEdit().hasAcceptableInput() or not data.lineEdit().hasAcceptableInput():
                    raise ValueError(f'CH{index + 1}波特率无效：仲裁40000–1000000，数据40000–5000000 bit/s。')
            settings = [dict(enabled=enable.isChecked(), nominal=int(nominal.currentText()),
                             data=int(data.currentText()), termination=termination.isChecked())
                        for enable, nominal, data, termination in self.zlg_channel_controls]
            driver, bus = self.driver, self.driver.bus
            persist = self.zlg_persist.isChecked()
            def apply():
                with driver.lock:
                    if not driver.is_connected or driver.bus is not bus:
                        raise ConnectionError('CAN连接已改变，请重新连接后应用配置')
                    bus.configure_channels(settings, persist)
                return True
            def completed(ok, value):
                self.zlg_apply.setEnabled(True)
                if ok and value is True:
                    QMessageBox.information(self, '已应用', 'CH1–CH4配置请求已成功应用。')
                else:
                    QMessageBox.critical(self, '设置失败', str(value))
            self.zlg_apply.setEnabled(False)
            if not self.operation.run(apply, completed):
                self.zlg_apply.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, '设置失败', str(exc))

    def _fit_zlg_channel_table(self):
        table = self.zlg_channels
        table.ensurePolished()
        heights = []
        for index, controls in enumerate(self.zlg_channel_controls):
            for widget in controls:
                widget.ensurePolished()
            height = max(40, max(widget.sizeHint().height() for widget in controls) + 12)
            table.setRowHeight(index, height)
            heights.append(height)
        table.setFixedHeight(sum(heights) + table.horizontalHeader().height() + 2 * table.frameWidth() + table.horizontalScrollBar().sizeHint().height())
        table.setMinimumWidth(600)
        self.zlg_connection.setMinimumHeight(self.zlg_connection.sizeHint().height())

    def showEvent(self, event):
        super().showEvent(event)
        self._update_transmit_channels()
        QTimer.singleShot(0, self._fit_zlg_channel_table)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def disconnect_device(self):
        self.run_device(self.driver.disconnect,self.operation_result)

    def send_message(self):
        try:
            can_id = int(self.can_id.text().strip(), 0)
            payload = bytes.fromhex(self.data.text().replace(",", " "))
            driver=self._selected_debug_driver()
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", f"CAN ID、数据或发送通道配置错误：{exc}")
            return
        frame,dlc=self.frame_type.currentIndex(),self.dlc.value()
        self.run_device(lambda:driver.send_can_message(0,can_id,frame,dlc,payload),self.operation_result)

    def _update_uds_sid(self, text):
        if text.startswith("0x"):
            self.uds_sid.setText(text.split()[0])

    def send_uds_request(self):
        if self.operation.isRunning() or (getattr(self,'uds_worker',None) and not self.uds_worker.wait(0)):return
        if not self.driver.is_connected:
            QMessageBox.warning(self, "UDS诊断", "请先连接CAN设备")
            return
        try:
            tx_id = int(self.uds_tx_id.text().strip(), 0)
            rx_id = int(self.uds_rx_id.text().strip(), 0)
            sid = int(self.uds_sid.text().strip(), 0)
            params = bytes.fromhex(self.uds_params.text().replace(",", " ")) if self.uds_params.text().strip() else b""
            frame_index = self.uds_frame_type.currentIndex()
            timeout = float(self.uds_timeout.value())
            client = self._selected_debug_driver().create_uds_client(
                tx_id=tx_id, rx_id=rx_id, can_fd=frame_index>0,
                brs=frame_index==2, timeout=timeout, p2_star_timeout=max(5.0, timeout))
        except ValueError as exc:
            QMessageBox.warning(self, "UDS参数错误", str(exc))
            return
        self.uds_result.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] TX UDS 0x{sid:02X}: {params.hex(' ').upper()}"
        )
        self.uds_send_btn.setEnabled(False)
        self.uds_unlock_btn.setEnabled(False)
        self.uds_worker = UDSRequestWorker(client, sid, params)
        self.uds_worker.result.connect(self._on_uds_finished)
        self.uds_worker.start()

    def unlock_uds_security(self):
        if self.operation.isRunning() or (getattr(self,'uds_worker',None) and not self.uds_worker.wait(0)):
            return
        if not self.driver.is_connected:
            QMessageBox.warning(self, 'UDS安全访问', '请先连接配方使用的CAN设备')
            return
        try:
            config = self.uds_security.config()
            if config is None:
                raise ValueError('请勾选并配置安全访问DLL')
            tx_id=int(self.uds_tx_id.text().strip(),0);rx_id=int(self.uds_rx_id.text().strip(),0)
            frame=self.uds_frame_type.currentIndex();timeout=float(self.uds_timeout.value())
            client=self._selected_debug_driver().create_uds_client(tx_id=tx_id,rx_id=rx_id,can_fd=frame>0,brs=frame==2,
                timeout=timeout,p2_star_timeout=max(5.0,timeout))
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, '安全访问配置错误', str(exc))
            return
        self.uds_result.appendPlainText(f'开始DLL安全解锁：Seed子功能=0x{config["level"]:02X}；仅ECU正响应才表示解锁成功')
        self.uds_send_btn.setEnabled(False);self.uds_unlock_btn.setEnabled(False)
        self.uds_detail_tabs.setCurrentIndex(0)
        self.uds_worker=UDSRequestWorker(client,0x27,b'',security=config)
        self.uds_worker.result.connect(self._on_uds_finished)
        self.uds_worker.start()

    def _on_uds_finished(self, result):
        self.uds_send_btn.setEnabled(True)
        self.uds_unlock_btn.setEnabled(True)
        if isinstance(result, Exception):
            self.uds_result.appendPlainText(f"RX ERROR: {result}")
            return
        raw = result.raw.hex(" ").upper() if result.raw else "--"
        if result.positive:
            self.uds_result.appendPlainText(f"RX POSITIVE: {raw}")
        elif result.nrc is not None:
            self.uds_result.appendPlainText(
                f"RX NEGATIVE: {raw} | NRC=0x{result.nrc:02X} {result.nrc_name}"
            )
        else:
            self.uds_result.appendPlainText(f"RX ERROR: {result.error or raw}")

    def clear_monitor(self):
        self.monitor.setRowCount(0)
        if hasattr(self, "lbl_msg_count"):
            self.lbl_msg_count.setText("共 0 条报文")

    def _update_monitor_id_filter(self, text):
        """解析显示过滤器；留空表示显示全部，支持十六进制和十进制ID。"""
        normalized = str(text).replace("，", ",").replace(";", ",").replace("；", ",")
        tokens = [item.strip() for item in normalized.split(",") if item.strip()]
        if not tokens:
            self._monitor_ids = None
            self.monitor_id_filter.setStyleSheet("")
            self.lbl_filter_state.setText("全部ID")
            self.clear_monitor()
            return
        try:
            values = {int(token, 0) for token in tokens}
            if any(can_id < 0 or can_id > 0x1FFFFFFF for can_id in values):
                raise ValueError
        except ValueError:
            self._monitor_ids = set()
            self.monitor_id_filter.setStyleSheet("border: 1px solid #FF4D5A;")
            self.lbl_filter_state.setText("ID格式错误")
            return
        self._monitor_ids = values
        self.monitor_id_filter.setStyleSheet("")
        self.lbl_filter_state.setText(f"已选择 {len(values)} 个ID")
        self.clear_monitor()

    def _monitor_message_matches(self, msg):
        return self._monitor_ids is None or int(msg.get("can_id", -1)) in self._monitor_ids

    def _toggle_auto_scroll(self, checked):
        if checked:
            self.chk_auto_scroll.setText("▶ 继续滚动")
        else:
            self.chk_auto_scroll.setText("⏸ 暂停滚动")

    def update_monitor(self):
        if not self.isVisible(): return
        connected = bool(self.driver.is_connected)
        self.status.setText(("● ZLG已连接 · CH1–CH4监听" if self.driver.channel.startswith('ZLG_CAN') else "● 已连接") if connected else "● 未连接")
        color = "#22C55E" if connected else "#FF4D5A"
        self.status.setStyleSheet(f"color:{color}; border:1px solid {color}; border-radius:6px; padding:7px 14px; font-weight:bold;")
        # Drain a bounded snapshot and render the latest matching frames.
        # Hidden pages and busy buses must not replay seconds of stale data.
        pending = deque(maxlen=500)
        for _ in range(min(10000, self.driver.msg_queue.qsize())):
            try:
                msg = self.driver.msg_queue.get_nowait()
            except queue.Empty:
                break
            if not self._monitor_message_matches(msg):
                continue
            if len(pending) == pending.maxlen:
                self._monitor_skipped += 1
            pending.append(msg)
        displayed = len(pending)
        self.monitor.setUpdatesEnabled(False)
        try:
            self._append_monitor_frames(pending)
        finally:
            self.monitor.setUpdatesEnabled(True)
        if hasattr(self, "lbl_msg_count"):
            skipped = self._monitor_skipped + getattr(self.driver, 'monitor_dropped', 0)
            self.lbl_msg_count.setText(f"共 {self.monitor.rowCount()} 条报文 · 显示跳过 {skipped} 帧")
        if displayed and not (hasattr(self, "chk_auto_scroll") and self.chk_auto_scroll.isChecked()):
            self.monitor.scrollToBottom()

    def _append_monitor_frames(self, pending):
        excess = max(0, self.monitor.rowCount() + len(pending) - 2000)
        if excess:
            self.monitor.model().removeRows(0, excess)
        first = self.monitor.rowCount()
        self.monitor.setRowCount(first + len(pending))
        for row, msg in enumerate(pending, first):
            type_name = ["CAN", "FD", "FD+BRS"][min(int(msg.get("can_type", 0)), 2)]
            stamp = msg.get('timestamp_rel', time.time())
            values = [
                time.strftime("%H:%M:%S", time.localtime(stamp)) + f'.{int(stamp * 1000) % 1000:03d}',
                msg.get('device', '--'), str(msg.get('physical_channel', '--')), msg.get("direction", ""),
                f"0x{msg.get('can_id', 0):X}", type_name, str(msg.get("dlc", 0)),
                bytes(msg.get("data", b"")).hex(" ").upper(),
            ]
            for col, value in enumerate(values):
                self.monitor.setItem(row, col, QTableWidgetItem(value))
