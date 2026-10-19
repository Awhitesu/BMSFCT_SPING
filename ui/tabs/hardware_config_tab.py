from ui.device_task import DeviceTask
from core.hardware_status import configuration_snapshot, configuration_matches
from devices.simulator_config import simulator_settings
from devices.rn_board import board_settings
import ipaddress
from copy import deepcopy
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
        ("ca550", "CA550 校准源", "串口", "ca550_com", "", None, "", "ca550_baudrate", "9600"),
        ("scanner", "infoscan FV31-2110 扫码器", "串口", "scanner_com", "COM5", None, "", "scanner_baudrate", "115200"),
        *[(s['key'], s['name'], 'Modbus TCP 板卡', 'ip', '', 'port', '502', 'unit', '1')
          for s in board_settings({})],
    ]

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.device_manager = None
        self.operation=None
        self.interface_task=DeviceTask(self)
        self.status_task=DeviceTask(self)
        self._status_revision=0
        self._status_snapshot=None
        self._status_error=''
        self._serial_ports=[]
        from core.can_choices import CAN_CHANNELS
        self._usb_channels=list(CAN_CHANNELS)
        self._init_ui()
        self.load_config()
        self.table.itemChanged.connect(self._configuration_edited)
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
        from core.can_choices import CAN_CHANNELS
        return list(CAN_CHANNELS)

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
        self.table.setHorizontalHeaderLabels(["启用", "设备名称", "通讯方式", "IP / USB / 串口", "端口 / 仲裁波特率", "波特率 / 数据波特率 / Unit ID", "连接状态", "设备序号", "实际通道数"])
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
        with QSignalBlocker(self.table):
            cfg = self.db_manager.load_sys_config() or {}
            available_ports = self._serial_ports
            usb_channels = self._usb_channels
            for row, spec in enumerate(self.DEVICE_SPECS):
                key, name, protocol, address_key, address_default, port_key, port_default, baud_key, baud_default = spec
                if protocol == 'Modbus TCP 板卡':
                    self._load_board_row(row, next(s for s in board_settings(cfg) if s['key'] == key))
                    continue
                enabled = bool(cfg.get(f"{key}_enabled", False if key in ("scanner", "opteeq", "e2_rfp", "it6333b", "dg2052", "tbs2102b", "sim2", "sim3", "sim4", "sim5", "sim6") else True))
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
                    if cur_addr and cur_addr not in known_values and not self._is_usb_protocol(protocol):
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
                    if self._is_usb_protocol(protocol):
                        cb.setEditable(False)
                        cb.setCurrentIndex(cb.findData(cur_addr))
                    cb.editTextChanged.connect(self._configuration_edited)
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
                        spin.valueChanged.connect(self._configuration_edited)
                        self.table.setCellWidget(row, col, spin)
                    else:
                        self.table.setItem(row, col, self._readonly_item('—'))
            self._apply_row_visibility()
            self._loaded_boards = deepcopy(cfg.get('rn_boards', {}))

    def _load_board_row(self, row, setting):
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if setting['enabled'] else Qt.Unchecked)
        self.table.setItem(row, 0, item)
        for col, value in ((1, setting['name']), (2, 'Modbus TCP 板卡'),
                           (6, '未初始化' if setting['enabled'] else '未启用'), (8, setting['channels'])):
            self.table.setItem(row, col, self._readonly_item(value))
        for col, value in ((3, setting['ip']), (4, setting['port']), (5, setting['unit'])):
            self.table.setItem(row, col, QTableWidgetItem(str(value)))
        if setting['kind'] == 'resistor':
            spin = QSpinBox(); spin.setRange(1, 6); spin.setValue(setting['number'])
            spin.valueChanged.connect(self._configuration_edited)
            self.table.setCellWidget(row, 7, spin)
        else:
            self.table.setItem(row, 7, self._readonly_item(setting['number']))
        self.table.item(row, 1).setToolTip('与板卡调试共用配置；量程和输出控制在对应调试页设置。')

    def showEvent(self, event):
        super().showEvent(event)
        if not hasattr(self, '_loaded_boards') or (self.operation and self.operation.isRunning()):
            return
        cfg = self.db_manager.load_sys_config() or {}
        if cfg.get('rn_boards', {}) != self._loaded_boards:
            with QSignalBlocker(self.table):
                settings = {s['key']: s for s in board_settings(cfg)}
                for row, spec in enumerate(self.DEVICE_SPECS):
                    if spec[2] == 'Modbus TCP 板卡':
                        self._load_board_row(row, settings[spec[0]])
                self._loaded_boards = deepcopy(cfg.get('rn_boards', {}))
            self._render_hardware_status()

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
                current_values[row] = self._address_text(row)

        for row, spec in enumerate(self.DEVICE_SPECS):
            protocol = spec[2]
            combo = self.table.cellWidget(row, 3)
            if not isinstance(combo, QComboBox):
                continue
            selected = current_values.get(row, "")
            options = ([(x, x) for x in self.get_available_usb_channels()] if self._is_usb_protocol(protocol)
                       else list(serial_ports))
            if selected and selected not in [x[0] for x in options] and not self._is_usb_protocol(protocol):
                options.append((selected, selected))
            combo.blockSignals(True)
            combo.clear()
            for value, description in options:
                combo.addItem(value if description == value else f"{value} — {description}", value)
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
            if combo.isEditable():
                combo.setEditText(selected)
            combo.blockSignals(False)
        self._render_hardware_status()

    def _apply_row_visibility(self):
        show_all = self.chk_show_all.isChecked()
        for row in range(self.table.rowCount()):
            enabled = self.table.item(row, 0).checkState() == Qt.Checked
            hidden = not show_all and not enabled
            if self.table.isRowHidden(row) != hidden:
                self.table.setRowHidden(row, hidden)

    def set_device_manager(self, manager):
        self._status_revision += 1
        self._status_snapshot=None
        self.device_manager = manager
        self.refresh_hardware_status()

    def _address_text(self, row):
        combo = self.table.cellWidget(row, 3)
        if isinstance(combo, QComboBox):
            return combo.currentText().split(' — ', 1)[0].strip()
        item = self.table.item(row, 3)
        return item.text().strip() if item else ''

    def _configuration_edited(self, *_args):
        self._apply_row_visibility()
        self._render_hardware_status()

    def _row_configuration(self, row):
        spec=self.DEVICE_SPECS[row]
        result=dict(address=self._address_text(row),
                    enabled=self.table.item(row,0).checkState()==Qt.Checked)
        for field,col,key in (('port',4,spec[5]),('baud',5,spec[7])):
            if key:result[field]=self.table.item(row,col).text().strip()
        if spec[0].startswith('sim'):
            result['number']=self.table.cellWidget(row,7).value()
            result['channels']=self.table.cellWidget(row,8).value()
        if spec[2] == 'Modbus TCP 板卡' and spec[0].startswith('resistor'):
            result['number'] = self.table.cellWidget(row,7).value()
        return result

    def save_config(self, initialize=False):
        if self.operation and self.operation.isRunning(): return
        reader=getattr(self,'scanner_reader',None)
        if reader and reader.connection_task.isRunning():
            QMessageBox.warning(self,'扫码器正在连接','请等待扫码器连接操作完成后再修改设备配置。');return
        cfg = self.db_manager.load_sys_config() or {}
        try:
            saved_boards = deepcopy(cfg.get('rn_boards', {}))
            if saved_boards != self._loaded_boards:
                raise ValueError('板卡配置已在其他页面修改，请切回本页刷新后再保存，避免覆盖新配置。')
            for row, spec in enumerate(self.DEVICE_SPECS):
                key, _, _, address_key, _, port_key, _, baud_key, _ = spec
                if spec[2] == 'Modbus TCP 板卡':
                    value = dict(saved_boards.get(key, {}))
                    value.update(enabled=self.table.item(row,0).checkState()==Qt.Checked,
                                 ip=self._address_text(row), port=int(self.table.item(row,4).text()),
                                 unit=int(self.table.item(row,5).text()))
                    if not 1 <= value['port'] <= 65535 or not 0 <= value['unit'] <= 255:
                        raise ValueError(f'{spec[1]}：端口应为1–65535，Unit ID应为0–255')
                    if value['enabled']:
                        ipaddress.IPv4Address(value['ip'])
                    if key.startswith('resistor'):
                        value['number'] = self.table.cellWidget(row,7).value()
                    saved_boards[key] = value
                    continue
                cfg[f"{key}_enabled"] = self.table.item(row, 0).checkState() == Qt.Checked
                if key.startswith('sim'):
                    cfg[f"{key}_number"] = self.table.cellWidget(row, 7).value()
                    cfg[f"{key}_channels"] = self.table.cellWidget(row, 8).value()


                cfg[address_key] = self._address_text(row)
                if self._is_usb_protocol(spec[2]):
                    from core.can_choices import CAN_CHANNELS
                    if cfg[address_key] not in CAN_CHANNELS:
                        raise ValueError('CAN卡仅支持PCAN_USBBUS1或ZLG CANFDNET-400U的CH1–CH4，请重新选择。')

                if port_key:
                    cfg[port_key] = int(self.table.item(row, 4).text().strip())
                if baud_key:
                    cfg[baud_key] = int(self.table.item(row, 5).text().strip())
            simulator_settings(cfg)
            cfg['rn_boards'] = saved_boards
            addresses, numbers = set(), set()
            for setting in board_settings(cfg):
                if not setting['enabled']:
                    continue
                address = (setting['ip'], setting['port'])
                if address in addresses:
                    raise ValueError('已启用板卡 IP/端口重复')
                addresses.add(address)
                if setting['kind'] == 'resistor':
                    if setting['number'] in numbers:
                        raise ValueError('已启用电阻板序号不能重复')
                    numbers.add(setting['number'])
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return

        if not self.db_manager.save_sys_config(cfg):
            QMessageBox.critical(self, "保存失败", "设备配置保存失败，请检查文件权限。")
            return
        self._loaded_boards = deepcopy(saved_boards)
        if self.device_manager:
            manager=self.device_manager
            def apply():
                if manager.safe_shutdown_outputs(check_offline=False) is False:
                    raise RuntimeError('旧设备输出未确认关闭，未切换连接配置：' + '; '.join(manager.last_shutdown_errors))
                warnings = list(getattr(manager, 'last_shutdown_unverified', []))
                if manager.disconnect_all() is False:
                    raise RuntimeError('配置已保存，但旧设备断开失败，未应用：' + '; '.join(manager.last_disconnect_errors))
                manager.update_config()
                if initialize:
                    initialized = manager.init_all_devices(logger=print)
                    states=manager.get_all_device_status()
                    offline=[s['name'] for s in states if s['status']=='离线']
                    if initialized is False:
                        raise RuntimeError('设备初始化未完成：' + ('、'.join(offline) or '初始化已取消或未确认成功'))
                    unverified = [s['name'] for s in states if s.get('connection_verified') is False]
                    result = '初始化完成；未连接：'+ '、'.join(offline) if offline else '初始化完成。'
                    if unverified:
                        result += '；USB已识别，目标连接未验证：' + '、'.join(unverified)
                else:
                    result = '配置已保存并应用，可初始化已启用设备。'
                return result + ('；旧设备输出未确认：' + '; '.join(warnings) if warnings else '')
            self.run_operation('正在应用设备配置…',apply)
            return
        self._apply_row_visibility()
        self.refresh_hardware_status()
        QMessageBox.information(self, "保存成功", "配置已保存。下次启动只检测已启用设备。")

    def refresh_hardware_status(self):
        if self.status_task.closing or (self.operation and self.operation.isRunning()):return
        self._render_hardware_status()
        if not self.device_manager or self.status_task.isRunning():return
        manager,revision=self.device_manager,self._status_revision
        def read():
            e2 = getattr(manager, 'e2_rfp', None)
            if e2 is not None and manager.is_enabled('e2_rfp'):
                e2.refresh_presence()
            states=manager.get_all_device_status()
            return states,configuration_snapshot(manager)
        def completed(ok,value):
            if revision!=self._status_revision or manager is not self.device_manager:
                QTimer.singleShot(0,self.refresh_hardware_status)
                return
            if self.operation and self.operation.isRunning():return
            self._status_error='' if ok else str(value)
            if ok:self._status_snapshot=value
            self._render_hardware_status()
        self.status_task.run(read,completed)

    def _render_hardware_status(self):
        with QSignalBlocker(self.table):
            states,applied=self._status_snapshot or ([],{})
            status_by_key={item.get('key',item['name']):item for item in states}
            for row, spec in enumerate(self.DEVICE_SPECS):
                key = spec[0]
                enabled = self.table.item(row, 0).checkState() == Qt.Checked
                status_item = self.table.item(row, 6)
                if not enabled and applied.get(key,{}).get('enabled') is True:
                    info=dict(status='停用未应用',color='#FFC107')
                elif not enabled:
                    info=dict(status='未启用',color='#8A8F9C')
                elif self._status_error:
                    info=dict(status='状态读取失败',color='#DC3545')
                elif key in applied and not configuration_matches(key,self._row_configuration(row),applied[key]):
                    info=dict(status='修改未应用',color='#FFC107')
                elif key in ('zlg','zlg_sdk') and key in applied:
                    info=dict(status='配置已应用',color='#8A8F9C')
                else:
                    info=status_by_key.get(key) or dict(status='读取中' if self.device_manager and self._status_snapshot is None else '待应用配置',color='#FFC107')
                if status_item.text()!=info['status']:status_item.setText(info['status'])
                color=QColor(info['color'])
                if status_item.foreground().color()!=color:status_item.setForeground(color)
                tooltip=self._status_error if enabled and self._status_error else info.get('detail', '')
                if not tooltip and info['status'] in ('修改未应用','停用未应用'):
                    running=status_by_key.get(key,{}).get('status','未连接')
                    tooltip=f'当前运行状态：{running}。编辑尚未应用，请保存配置；需要连接时再初始化。'
                elif not tooltip and info['status']=='配置已应用':
                    tooltip='参数已加载；CAN连接状态请查看PCAN/ZLG设备行。'
                if status_item.toolTip()!=tooltip:status_item.setToolTip(tooltip)
            self._apply_row_visibility()

    def init_enabled_devices(self):
        if not self.device_manager:
            return
        self.save_config(initialize=True)


    def disconnect_all(self):
        if self.device_manager:
            self.run_operation('正在断开连接…',self.device_manager.disconnect_all)

    def run_operation(self,text,action):
        if self.operation and self.operation.isRunning(): return
        reader=getattr(self,'scanner_reader',None)
        if reader and reader.connection_task.isRunning():
            self.lbl_operation.setText('请等待扫码器连接操作完成');return
        self._status_revision+=1
        self.lbl_operation.setText(text)
        for button in (self.btn_save,self.btn_init,self.btn_disconnect):button.setEnabled(False)
        self.table.setEnabled(False)
        self.operation=MeterTask(action)
        def result(ok,value):
            if value is False:
                ok = False
                value = '; '.join(getattr(self.device_manager, 'last_disconnect_errors', [])) or '操作未确认成功'
            self.lbl_operation.setText((str(value) if value is not None else '操作完成') if ok else '操作失败：'+str(value))
        def finished():
            self.table.setEnabled(True)
            for button in (self.btn_save,self.btn_init,self.btn_disconnect):button.setEnabled(True)
            self._apply_row_visibility();self.refresh_hardware_status()
        self.operation.result.connect(result);self.operation.finished.connect(finished);self.operation.start()

    def shutdown(self):
        self.status_timer.stop()
        self.interface_task.shutdown()
        self.status_task.shutdown()
        if self.operation:self.operation.wait()
