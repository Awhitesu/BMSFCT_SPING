from ui.device_task import DeviceTask
from devices.simulator_config import simulator_settings
from ui.tabs.multimeter_tab import MeterTask

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QSpinBox
)


class HardwareConfigTab(QWidget):
    """设备使能、通讯参数与实时连接状态的统一配置页。"""

    DEVICE_SPECS = [
        ("pcan", "USB PCAN-FD", "USB CAN-FD", "pcan_channel", "PCAN_USBBUS1", "pcan_nominal_bitrate", "500000", "pcan_data_bitrate", "2000000"),
        ("zlg", "ZLG CANFDNET-400U 网络参数", "TCP", "zlg_ip", "192.168.0.177", "zlg_port", "8000", None, ""),
        ("zlg_sdk", "ZLG 官方SDK目录", "目录", "zlg_sdk_path", "zlgcan(20260806)/zlgcan_x64", None, "", None, ""),
        ("hv", "NGI 高压源", "TCP", "hv_ip", "192.168.1.190", "hv_port", "7000", None, ""),
        ("dut_pwr", "IT6722A 被测物供电电源", "RS232", "dut_pwr_com", "COM1", None, "", "dut_pwr_baudrate", "9600"),
        ("it6333b", "IT6333B 三通道电源", "RS232", "it6333b_com", "", None, "", "it6333b_baudrate", "9600"),
        ("dg2052", "DG2052 双通道信号发生器", "TCP", "dg2052_ip", "", "dg2052_port", "5555", None, ""),
        ("dmm34461", "34461A 万用表", "TCP / SCPI", "dmm34461_ip", "", "dmm34461_port", "5025", None, ""),
        ("tbs2102b", "泰克 TBS2102B 双通道示波器", "LAN VXI-11", "tbs2102b_ip", "", None, "", None, ""),
        *[(f"sim{i}", f"电池模拟器配置 {i}", "TCP", f"sim{i}_ip", f"192.168.1.{209+i}", f"sim{i}_port", "7000", None, "") for i in range(1, 7)],
        ("easy320", "Easy320 PLC", "Modbus TCP", "easy320_ip", "192.168.1.88", "easy320_port", "502", None, ""),
        ("opteeq", "力捷丰 SG400 下载器", "TCP", "opteeq_ip", "192.168.0.100", "opteeq_port", "10023", None, ""),
        ("e2_rfp", "瑞萨E2下载器 (rfp-cli)", "EXE", "e2_rfp_cli_path", "", None, "", None, ""),
        ("ca550", "CA550 校准源", "串口", "ca550_com", "COM1", None, "", "ca550_baudrate", "9600"),
    ]

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.device_manager = None
        self.operation=None
        self.interface_task=DeviceTask(self)
        self._serial_ports=[]
        self._usb_channels=[f"PCAN_USBBUS{i}" for i in range(1,17)]+[f"ZLG_CAN{i}" for i in range(1,5)]
        self._init_ui()
        self.load_config()
        self.table.itemChanged.connect(lambda _item: self._apply_row_visibility())
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1500)
        self.status_timer.timeout.connect(lambda:self.refresh_hardware_status() if self.isVisible() else None)
        self.status_timer.start()

    @staticmethod
    def get_available_serial_ports():
        """检索物理串口和 USB 转串口，返回 (端口, 描述) 列表。"""
        try:
            import serial.tools.list_ports
            return [(p.device, p.description or p.device) for p in serial.tools.list_ports.comports()]
        except Exception:
            return []

    @staticmethod
    def get_available_usb_channels():
        """枚举 PCAN USB 通道；驱动未安装时仍提供标准通道名称。"""
        channels = []
        try:
            import can
            for item in can.detect_available_configs(interfaces=["pcan"]):
                channel = str(item.get("channel", "")).strip()
                if channel and channel not in channels:
                    channels.append(channel)
        except Exception:
            pass
        return (channels or [f"PCAN_USBBUS{i}" for i in range(1, 17)]) + [f"ZLG_CAN{i}" for i in range(1, 5)]

    @staticmethod
    def _is_serial_protocol(protocol):
        return protocol.upper() == "RS232" or "串口" in protocol

    @staticmethod
    def _is_usb_protocol(protocol):
        return "USB" in protocol.upper()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("硬件设备配置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #FFFFFF;")
        title_row.addWidget(title)
        title_row.addStretch()

        self.btn_refresh_ports = QPushButton("🔄 刷新USB/串口")
        self.btn_refresh_ports.setToolTip("重新扫描USB设备、物理串口和USB转串口")
        self.btn_refresh_ports.clicked.connect(self.refresh_interface_lists)
        title_row.addWidget(self.btn_refresh_ports)

        self.chk_show_all = QCheckBox("显示未启用设备")
        self.chk_show_all.toggled.connect(self._apply_row_visibility)
        title_row.addWidget(self.chk_show_all)
        layout.addLayout(title_row)

        tip = QLabel("USB和串口设备使用可编辑下拉选择；TCP设备直接填写IP，不显示接口下拉。")
        tip.setStyleSheet("color: #AEB4C0;")
        layout.addWidget(tip)

        self.table = QTableWidget(len(self.DEVICE_SPECS), 9)
        self.table.setHorizontalHeaderLabels(["启用", "设备名称", "通讯方式", "IP / USB / 串口", "端口 / 仲裁波特率", "波特率 / 数据波特率", "连接状态", "模拟器序号", "实际通道数"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for col in (4, 5, 6, 7, 8):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.btn_save = QPushButton("保存设备配置")
        self.btn_save.setMinimumHeight(42)
        self.btn_save.clicked.connect(self.save_config)
        buttons.addWidget(self.btn_save)

        self.btn_init = QPushButton("初始化已启用设备")
        self.btn_init.setMinimumHeight(42)
        self.btn_init.setStyleSheet("background-color: #28A745; font-weight: bold;")
        self.btn_init.clicked.connect(self.init_enabled_devices)
        buttons.addWidget(self.btn_init)

        btn_disconnect = QPushButton("断开所有连接")
        self.btn_disconnect=btn_disconnect
        btn_disconnect.setMinimumHeight(42)
        btn_disconnect.clicked.connect(self.disconnect_all)
        buttons.addWidget(btn_disconnect)
        layout.addLayout(buttons)
        self.lbl_operation=QLabel('待操作');self.lbl_operation.setWordWrap(True);layout.addWidget(self.lbl_operation)

    def _readonly_item(self, text):
        item = QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def load_config(self):
        blocker=QSignalBlocker(self.table)
        cfg = self.db_manager.load_sys_config() or {}
        available_ports = self._serial_ports
        usb_channels = self._usb_channels
        for row, spec in enumerate(self.DEVICE_SPECS):
            key, name, protocol, address_key, address_default, port_key, port_default, baud_key, baud_default = spec
            enabled = bool(cfg.get(f"{key}_enabled", False if key in ("opteeq", "e2_rfp", "it6333b", "dg2052", "tbs2102b", "sim2", "sim3", "sim4", "sim5", "sim6") else True))
            if key=='dmm34461':enabled=bool(cfg.get('dmm34461_enabled',bool(cfg.get('dmm34461_ip',''))))
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            enabled_item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self.table.setItem(row, 0, enabled_item)
            self.table.setItem(row, 1, self._readonly_item(name))
            self.table.setItem(row, 2, self._readonly_item(protocol))

            cur_addr = str(cfg.get(address_key, address_default))

            if self._is_serial_protocol(protocol) or self._is_usb_protocol(protocol):
                cb = QComboBox()
                cb.setEditable(True)
                cb.setProperty("embeddedCell", True)
                cb.setMinimumWidth(190)
                if self._is_usb_protocol(protocol):
                    port_options = [(channel, channel) for channel in usb_channels]
                else:
                    port_options = list(available_ports)
                known_values = [value for value, _desc in port_options]
                if cur_addr and cur_addr not in known_values:
                    port_options.append((cur_addr, cur_addr))
                if not port_options and self._is_serial_protocol(protocol):
                    port_options = [(f"COM{i}", f"COM{i}") for i in range(1, 17)]
                for value, description in port_options:
                    label = value if description == value else f"{value} — {description}"
                    cb.addItem(label, value)
                
                # 监听选择变更：选中时编辑框仅显示纯净端口名（如 COM1, COM3, PCAN_USBBUS1）
                cb.currentIndexChanged.connect(
                    lambda idx, c=cb: c.setEditText(c.itemData(idx) or c.currentText().split(" — ", 1)[0].strip()) if idx >= 0 else None
                )

                index = cb.findData(cur_addr)
                if index >= 0:
                    cb.setCurrentIndex(index)
                cb.setEditText(cur_addr)
                self.table.setCellWidget(row, 3, cb)
            else:
                self.table.removeCellWidget(row, 3)
                self.table.setItem(row, 3, QTableWidgetItem(cur_addr))

            self.table.setItem(row, 4, QTableWidgetItem(str(cfg.get(port_key, port_default))) if port_key else self._readonly_item("—"))
            self.table.setItem(row, 5, QTableWidgetItem(str(cfg.get(baud_key, baud_default))) if baud_key else self._readonly_item("—"))
            self.table.setItem(row, 6, self._readonly_item("未初始化" if enabled else "未启用"))
            for col, suffix, maximum, default in ((7, 'number', 6, int(key[-1]) if key.startswith('sim') else 1), (8, 'channels', 24, 1 if key == 'sim1' else 24)):
                if key.startswith('sim'):
                    spin = QSpinBox()
                    spin.setRange(1, maximum)
                    spin.setValue(int(cfg.get(f"{key}_{suffix}", default)))
                    self.table.setCellWidget(row, col, spin)
                else:
                    self.table.setItem(row, col, self._readonly_item('—'))
        self._apply_row_visibility()

    def refresh_interface_lists(self):
        if self.interface_task.isRunning():return
        self.btn_refresh_ports.setEnabled(False)
        def completed(ok,value):
            self.btn_refresh_ports.setEnabled(True)
            if not ok:
                self.lbl_operation.setText(str(value));return
            self._serial_ports,self._usb_channels=value
            self._apply_interface_lists(*value)
        self.interface_task.run(lambda:(self.get_available_serial_ports(),self.get_available_usb_channels()),completed)

    def _apply_interface_lists(self,serial_ports,usb_channels):
        current_values = {}
        for row, spec in enumerate(self.DEVICE_SPECS):
            protocol = spec[2]
            if not (self._is_serial_protocol(protocol) or self._is_usb_protocol(protocol)):
                continue
            combo = self.table.cellWidget(row, 3)
            if isinstance(combo, QComboBox):
                current_values[row] = combo.currentData() or combo.currentText().split(" — ", 1)[0].strip()

        for row, spec in enumerate(self.DEVICE_SPECS):
            protocol = spec[2]
            combo = self.table.cellWidget(row, 3)
            if not isinstance(combo, QComboBox):
                continue
            selected = current_values.get(row, "")
            options = ([(x, x) for x in usb_channels] if self._is_usb_protocol(protocol)
                       else list(serial_ports))
            if selected and selected not in [x[0] for x in options]:
                options.append((selected, selected))
            combo.blockSignals(True)
            combo.clear()
            for value, description in options:
                combo.addItem(value if description == value else f"{value} — {description}", value)
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.setEditText(selected)
            combo.blockSignals(False)

    def _apply_row_visibility(self):
        show_all = self.chk_show_all.isChecked()
        for row in range(self.table.rowCount()):
            enabled = self.table.item(row, 0).checkState() == Qt.Checked
            hidden = not show_all and not enabled
            if self.table.isRowHidden(row) != hidden:
                self.table.setRowHidden(row, hidden)

    def set_device_manager(self, manager):
        self.device_manager = manager
        self.refresh_hardware_status()

    def save_config(self, initialize=False):
        if self.operation and self.operation.isRunning(): return
        cfg = self.db_manager.load_sys_config() or {}
        try:
            for row, spec in enumerate(self.DEVICE_SPECS):
                key, _, _, address_key, _, port_key, _, baud_key, _ = spec
                cfg[f"{key}_enabled"] = self.table.item(row, 0).checkState() == Qt.Checked
                if key.startswith('sim'):
                    cfg[f"{key}_number"] = self.table.cellWidget(row, 7).value()
                    cfg[f"{key}_channels"] = self.table.cellWidget(row, 8).value()


                cell_widget = self.table.cellWidget(row, 3)
                if isinstance(cell_widget, QComboBox):
                    raw_val = cell_widget.currentData() or cell_widget.currentText()
                    raw_str = str(raw_val).strip()
                    address_val = raw_str.split(" — ", 1)[0].strip() if " — " in raw_str else raw_str
                else:
                    item = self.table.item(row, 3)
                    address_val = item.text().strip() if item else ""
                cfg[address_key] = address_val

                if port_key:
                    cfg[port_key] = int(self.table.item(row, 4).text().strip())
                if baud_key:
                    cfg[baud_key] = int(self.table.item(row, 5).text().strip())
            simulator_settings(cfg)
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        if not self.db_manager.save_sys_config(cfg):
            QMessageBox.critical(self, "保存失败", "设备配置保存失败，请检查文件权限。")
            return
        if self.device_manager:
            manager=self.device_manager
            def apply():
                manager.disconnect_all()
                manager.update_config()
                if initialize:
                    manager.init_all_devices(logger=print)
                    states=manager.get_all_device_status()
                    offline=[s['name'] for s in states if s['status']=='离线']
                    return '初始化完成；未连接：'+ '、'.join(offline) if offline else '初始化完成，已启用设备已连接。'
                return '配置已保存并应用，可初始化已启用设备。'
            self.run_operation('正在应用设备配置…',apply)
            return
        self._apply_row_visibility()
        self.refresh_hardware_status()
        QMessageBox.information(self, "保存成功", "配置已保存。下次启动只检测已启用设备。")

    def refresh_hardware_status(self):
        if self.operation and self.operation.isRunning(): return
        blocker = QSignalBlocker(self.table)
        status_by_key = {}
        if self.device_manager:
            status_by_key = {item.get("key", item["name"]): item for item in self.device_manager.get_all_device_status()}
        for row, spec in enumerate(self.DEVICE_SPECS):
            key, name = spec[0], spec[1]
            enabled = self.table.item(row, 0).checkState() == Qt.Checked
            status_item = self.table.item(row, 6)
            if not enabled:
                status_item.setText("未启用")
                status_item.setForeground(QColor("#8A8F9C"))
                continue
            info = status_by_key.get(key)
            if key in ('zlg', 'zlg_sdk') and self.device_manager:
                driver = self.device_manager.pcan
                item = self.table.item(row, 3)
                actual = getattr(driver, 'zlg_ip' if key == 'zlg' else 'zlg_sdk_path', '')
                port_matches = key != 'zlg' or self.table.item(row, 4).text().strip() == str(getattr(driver, 'zlg_port', ''))
                matches = item.text().strip().replace('\\', '/') == str(actual).replace('\\', '/') and port_matches
                info = {'status': '配置已应用' if matches else '修改未应用', 'color': '#8A8F9C' if matches else '#FFC107'}
            if info:
                if status_item.text() != info["status"]:
                    status_item.setText(info["status"])
                    status_item.setForeground(QColor(info["color"]))
            else:
                status_item.setText("待应用配置")
                status_item.setForeground(QColor("#FFC107"))
        self._apply_row_visibility()

    def init_enabled_devices(self):
        if not self.device_manager:
            return
        self.save_config(initialize=True)

    def _finish_init(self):
        self.btn_init.setEnabled(True)
        self.btn_init.setText("初始化已启用设备")
        self.refresh_hardware_status()

    def disconnect_all(self):
        if self.device_manager:
            self.run_operation('正在断开连接…',self.device_manager.disconnect_all)

    def run_operation(self,text,action):
        if self.operation and self.operation.isRunning(): return
        self.lbl_operation.setText(text)
        for button in (self.btn_save,self.btn_init,self.btn_disconnect):button.setEnabled(False)
        self.table.setEnabled(False)
        self.operation=MeterTask(action)
        def result(ok,value):
            self.lbl_operation.setText((str(value) if value is not None else '操作完成') if ok else '操作失败：'+str(value))
        def finished():
            self.table.setEnabled(True)
            for button in (self.btn_save,self.btn_init,self.btn_disconnect):button.setEnabled(True)
            self._apply_row_visibility();self.refresh_hardware_status()
        self.operation.result.connect(result);self.operation.finished.connect(finished);self.operation.start()

    def shutdown(self):
        self.status_timer.stop()
        self.interface_task.shutdown()
        if self.operation and self.operation.isRunning():self.operation.wait()
