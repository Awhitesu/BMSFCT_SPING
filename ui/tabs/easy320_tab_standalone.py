import sys

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel, QGroupBox, QLineEdit, QScrollArea, QMessageBox, QSpinBox, QFileDialog, QSizePolicy
from PySide6.QtCore import Qt, QTimer,QEvent

# 导入之前提取的独立驱动

from ui.device_task import DeviceTask
from core.easy320_channels import default_channels,read_channel_excel,configured_channels

class Easy320StandaloneTab(QWidget):
    def __init__(self, driver, manager=None):
        super().__init__()
        self.setWindowTitle("Easy320 继电器控制测试")
        self.resize(1000, 600)

        # 使用传入的驱动实例
        self.manager = manager
        self._driver = driver
        self.task = DeviceTask(self)
        self.task.finished.connect(self._task_finished)
        self.pending_action = None
        self.closing = False
        self.relay_buttons = []
        self.channels=default_channels()
        self.channel_config_error=''
        try:self.channels=configured_channels(manager)
        except (ValueError,KeyError,TypeError) as exc:
            self.channels=[];self.channel_config_error=f'已保存通道配置无效：{exc}，请重新导入Excel'
        self.start_test_signal_val = 0

        self._init_ui()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)

        # 定时同步共享驱动状态：启动自检已连接时也能立即显示。
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda:self.sync_status() if self.isVisible() else None)
        self.timer.start(1000)
        QTimer.singleShot(0, self.sync_status)

        # 500ms 定时读取 PLC 地址 1 开始测试信号位
        self.signal_timer = QTimer(self)
        self.signal_timer.timeout.connect(self.poll_start_test_signal)
        self.signal_timer.start(500)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # --- 1. 顶部通讯配置 ---
        group_comm = QGroupBox("Easy320 继电器控制 (Modbus TCP)")
        comm_layout = QHBoxLayout()

        comm_layout.addWidget(QLabel("IP 地址:"))
        self.edit_ip = QLineEdit(self.easy320.ip)
        self.edit_ip.setReadOnly(True)
        self.edit_ip.setFixedWidth(150)
        comm_layout.addWidget(self.edit_ip)

        comm_layout.addWidget(QLabel("端口:"))
        self.edit_port = QLineEdit(str(self.easy320.port))
        self.edit_port.setReadOnly(True)
        self.edit_port.setFixedWidth(60)
        comm_layout.addWidget(self.edit_port)

        self.btn_connect = QPushButton("连接设备")
        self.btn_connect.setMinimumSize(110, 34)
        self.btn_connect.setStyleSheet("background-color: #007bff; color: white; font-weight: bold;")
        self.btn_connect.clicked.connect(self.connect_device)

        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.setMinimumSize(110, 34)
        self.btn_disconnect.clicked.connect(self.disconnect_device)

        comm_layout.addWidget(self.btn_connect)
        comm_layout.addWidget(self.btn_disconnect)
        comm_layout.addStretch()

        self.lbl_status = QLabel("状态: 未连接")
        self.lbl_status.setMinimumWidth(150)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        comm_layout.addWidget(self.lbl_status)

        group_comm.setLayout(comm_layout)
        main_layout.addWidget(group_comm)
        self._refresh_connection_state()
        imports=QHBoxLayout()
        self.btn_import=QPushButton('导入通道Excel')
        self.btn_import.clicked.connect(self.import_channels)
        imports.addWidget(self.btn_import)
        self.lbl_channels=QLabel('')
        self.lbl_channels.setWordWrap(True)
        imports.addWidget(self.lbl_channels,1)
        main_layout.addLayout(imports)

        # --- 2. 继电器控制面板 ---
        scroll = QScrollArea()
        self.relay_scroll=scroll
        scroll.viewport().installEventFilter(self)
        scroll.setWidgetResizable(True)
        main_layout.addWidget(scroll)

        panel_widget = QWidget()
        scroll.setWidget(panel_widget)
        panel_layout = QVBoxLayout(panel_widget)

        group_relays = QGroupBox("继电器状态控制")
        self.relay_group=group_relays
        group_relays.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Maximum)
        grid_layout = QGridLayout()
        grid_layout.setSpacing(10)
        self.relay_grid=grid_layout
        self.relay_columns=0

        self.rebuild_channels()

        group_relays.setLayout(grid_layout)
        panel_layout.addWidget(group_relays)

        # --- 3. 批量操作 ---
        batch_layout = QHBoxLayout()
        btn_all_on = QPushButton("全部开启")
        btn_all_on.clicked.connect(lambda: self.batch_control(True))
        btn_all_off = QPushButton("全部关闭")
        btn_all_off.clicked.connect(lambda: self.batch_control(False))
        batch_layout.addWidget(btn_all_on)
        batch_layout.addWidget(btn_all_off)

        batch_layout.addSpacing(25)
        self.lbl_trigger_signal = QLabel("🚩 开始测试信号: 0")
        self.lbl_trigger_signal.setStyleSheet(
            "font-size: 13px; font-weight: bold; padding: 6px 14px; "
            "background-color: #1e1e2e; color: #00ffcc; border: 1px solid #00cc99; border-radius: 4px;"
        )
        batch_layout.addWidget(self.lbl_trigger_signal)

        batch_layout.addStretch()
        panel_layout.addLayout(batch_layout)

        # --- 4. PLC 保持寄存器读写调试 ---
        group_registers = QGroupBox("PLC 寄存器读写调试 (保持寄存器 FC03 / FC06)")
        reg_layout = QHBoxLayout(group_registers)
        reg_layout.setContentsMargins(12, 12, 12, 12)
        reg_layout.setSpacing(10)

        reg_layout.addWidget(QLabel("寄存器地址:"))
        self.spin_reg_addr = QSpinBox()
        self.spin_reg_addr.setRange(0, 65535)
        self.spin_reg_addr.setValue(0)
        self.spin_reg_addr.setFixedWidth(80)
        reg_layout.addWidget(self.spin_reg_addr)

        reg_layout.addWidget(QLabel("写入数据:"))
        self.spin_reg_val = QSpinBox()
        self.spin_reg_val.setRange(0, 65535)
        self.spin_reg_val.setValue(5)
        self.spin_reg_val.setFixedWidth(80)
        reg_layout.addWidget(self.spin_reg_val)

        btn_write_reg = QPushButton("✍️ 写入单个寄存器")
        btn_write_reg.setStyleSheet("background-color: #007bff; color: white; font-weight: bold; padding: 5px 12px;")
        btn_write_reg.clicked.connect(self.on_write_register_clicked)
        reg_layout.addWidget(btn_write_reg)

        btn_read_reg = QPushButton("📖 读取单个寄存器")
        btn_read_reg.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 5px 12px;")
        btn_read_reg.clicked.connect(self.on_read_register_clicked)
        reg_layout.addWidget(btn_read_reg)

        reg_layout.addWidget(QLabel("读取结果:"))
        self.edit_reg_read_val = QLineEdit()
        self.edit_reg_read_val.setReadOnly(True)
        self.edit_reg_read_val.setPlaceholderText("未读取")
        self.edit_reg_read_val.setFixedWidth(130)
        reg_layout.addWidget(self.edit_reg_read_val)

        self.lbl_reg_op_status = QLabel("就绪")
        self.lbl_reg_op_status.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 8px;")
        reg_layout.addWidget(self.lbl_reg_op_status)

        reg_layout.addStretch()
        panel_layout.addWidget(group_registers)

        panel_layout.addStretch()

    def rebuild_channels(self):
        for button in self.relay_buttons:
            self.relay_grid.removeWidget(button);button.deleteLater()
        self.relay_buttons=[]
        for position,channel in enumerate(self.channels):
            button=QPushButton();button.setCheckable(True)
            button.setProperty('multilineTile',True)
            button.setMinimumWidth(150)
            button.setFixedHeight(72)
            button.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            button.clicked.connect(lambda checked,row=position:self.on_relay_clicked(row,checked))
            self.relay_grid.addWidget(button,position//6,position%6)
            self.relay_buttons.append(button)
            self._update_btn_style(position,None)
        self.reflow_channels()
        self.lbl_channels.setText(f'已配置 {len(self.channels)} 个输出；DO编号为八进制，批量操作仅控制这些通道。')
        if self.channel_config_error:self.lbl_channels.setText(self.channel_config_error)

    def eventFilter(self,watched,event):
        if (hasattr(self,'relay_grid') and watched is self.relay_scroll.viewport()
                and event.type()==QEvent.Resize):
            self.reflow_channels()
        return super().eventFilter(watched,event)

    def reflow_channels(self):
        if not hasattr(self,'relay_grid'):return
        # Reserve panel/group margins and the vertical scrollbar. Fixed button
        # height prevents many rows being squeezed together on a small page.
        available=max(150,self.relay_scroll.viewport().width()-60)
        columns=max(1,available//(150+self.relay_grid.horizontalSpacing()))
        columns=min(columns,max(1,len(self.relay_buttons)))
        for column in range(max(self.relay_columns,self.relay_grid.columnCount())):
            self.relay_grid.setColumnStretch(column,0)
            self.relay_grid.setColumnMinimumWidth(column,0)
        for position,button in enumerate(self.relay_buttons):
            self.relay_grid.removeWidget(button)
            self.relay_grid.addWidget(button,position//columns,position%columns)
        for column in range(columns):self.relay_grid.setColumnStretch(column,1)
        self.relay_columns=columns

    def import_channels(self):
        if self.closing:return
        path,_=QFileDialog.getOpenFileName(self,'导入EASY320通道','','Excel (*.xlsx *.xlsm)')
        if path:self.import_channel_file(path)

    def import_channel_file(self,path):
        if self.pending_action is not None:
            QMessageBox.information(self,'正在执行','已有操作等待执行，请稍后导入');return False
        def completed(ok,value):
            if not ok:
                QMessageBox.warning(self,'导入失败',str(value));return
            if self.manager and self.manager.db_manager:
                cfg=dict(self.manager.db_manager.load_sys_config() or {})
                cfg['easy320_channels']=value
                if not self.manager.db_manager.save_sys_config(cfg):
                    QMessageBox.warning(self,'保存失败','通道配置未保存，原配置保留');return
            self.channels=value
            self.channel_config_error=''
            self.rebuild_channels()
        self.run_task(lambda:read_channel_excel(path),completed)
        return True

    def connect_device(self):
        self.lbl_status.setText("状态: 正在连接...")
        driver = self.easy320
        self.run_task(driver.connect, lambda ok, value: self._finalize_connect(ok and value))

    def _finalize_connect(self, success):
        self._refresh_connection_state()
        if success:
            self.sync_status()
        else:
            QMessageBox.critical(self, "错误", "连接设备失败，请检查网络设置。")

    def disconnect_device(self):
        driver = self.easy320
        self.run_task(driver.disconnect, lambda *_: self._refresh_connection_state())

    @property
    def easy320(self):
        return self.manager.easy320 if self.manager else self._driver

    def run_task(self, action, callback, polling=False):
        if self.closing: return
        if self.task and self.task.isRunning():
            if not polling and self.pending_action is None:
                self.pending_action = (action, callback)
            return
        if not polling:
            for btn in self.findChildren(QPushButton): btn.setEnabled(False)
        self.task.run(action,callback)

    def _task_finished(self):
        for btn in self.findChildren(QPushButton): btn.setEnabled(True)
        self._refresh_connection_state()
        pending, self.pending_action = self.pending_action, None
        if pending and not self.closing:
            self.run_task(*pending)

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        self.signal_timer.stop()
        self.pending_action = None
        self.task.shutdown()

    def _refresh_connection_state(self):
        self.edit_ip.setText(self.easy320.ip)
        self.edit_port.setText(str(self.easy320.port))
        connected = bool(self.easy320 and getattr(self.easy320, "is_connected", False))
        self.lbl_status.setText("●  已连接" if connected else "●  未连接")
        color = "#22C55E" if connected else "#FF4D5A"
        background = "#123524" if connected else "#351820"
        self.lbl_status.setStyleSheet(
            f"color: {color}; background-color: {background}; border: 1px solid {color}; "
            "border-radius: 6px; padding: 7px 14px; font-weight: bold;"
        )
        busy = bool(self.task and self.task.isRunning()) or self.pending_action is not None
        self.btn_connect.setEnabled(not connected and not busy)
        self.btn_disconnect.setEnabled(connected and not busy)

    def on_relay_clicked(self, index, checked):
        if not self.easy320.is_connected:
            QMessageBox.warning(self, "提醒", "请先连接设备")
            self.relay_buttons[index].setChecked(not checked)
            return

        driver=self.easy320
        def completed(ok, value):
            state=checked if ok and value else not checked
            self.relay_buttons[index].setChecked(state)
            self._update_btn_style(index,state)
            if not (ok and value): QMessageBox.warning(self,"失败",f"控制继电器 {index+1} 失败")
        address=self.channels[index]['index']
        self.run_task(lambda:driver.write_relay(address,checked),completed)

    def _update_btn_style(self, index, state):
        btn = self.relay_buttons[index]
        channel=self.channels[index]
        text=channel['do']
        if channel['name']:text+='\n'+channel['name']
        btn.setToolTip(f"{channel['do']} {channel['name']}".strip())
        if btn.text()!=text:btn.setText(text)
        style=('background-color: #245c42; color: white; font-weight: bold;' if state is True else
               'background-color: #243247; color: #eef2f7; border: 1px solid #53647a;')
        if btn.styleSheet()!=style:btn.setStyleSheet(style)

    def batch_control(self, state):
        if not self.easy320.is_connected: return
        driver=self.easy320
        indices=[row['index'] for row in self.channels]
        def completed(ok,value):
            if not (ok and value):QMessageBox.warning(self,'控制失败','部分通道未确认成功，请查看回读状态')
            self.sync_status()
        self.run_task(lambda:driver.control_selected_relays(indices,state),completed)

    def sync_status(self):
        self._refresh_connection_state()
        if not self.isVisible() or not self.easy320.is_connected: return
        driver=self.easy320
        def completed(ok,value):
            if ok and driver is self.easy320 and value:
                for i,s in enumerate(value):
                    if i<len(self.relay_buttons):
                        self.relay_buttons[i].setChecked(s)
                        self._update_btn_style(i,s)
        indices=[row['index'] for row in self.channels]
        self.run_task(lambda:driver.read_selected_relays(indices),completed,polling=True)

    def poll_start_test_signal(self):
        """500ms 定时拉取 PLC 地址 1 开始测试信号位"""
        if not self.isVisible() or not self.easy320.is_connected:
            self._update_trigger_signal_ui(-1)
            return

        driver=self.easy320
        def completed(ok,value):
            if driver is self.easy320:
                self._update_trigger_signal_ui(value[0] if ok and value else -1)
        self.run_task(lambda:driver.read_register(address=1,count=1),completed,polling=True)

    def _update_trigger_signal_ui(self, val: int):
        val_display = val if val >= 0 else 0
        self.start_test_signal_val = val_display
        self.lbl_trigger_signal.setText(f"🚩 开始测试信号: {val_display}")
        if val_display == 1:
            self.lbl_trigger_signal.setStyleSheet(
                "font-size: 13px; font-weight: bold; padding: 6px 14px; "
                "background-color: #28a745; color: #ffffff; border: 1px solid #1e7e34; border-radius: 4px;"
            )
        elif val_display == 2:
            self.lbl_trigger_signal.setStyleSheet(
                "font-size: 13px; font-weight: bold; padding: 6px 14px; "
                "background-color: #007bff; color: #ffffff; border: 1px solid #0056b3; border-radius: 4px;"
            )
        else:
            self.lbl_trigger_signal.setStyleSheet(
                "font-size: 13px; font-weight: bold; padding: 6px 14px; "
                "background-color: #1e1e2e; color: #00ffcc; border: 1px solid #00cc99; border-radius: 4px;"
            )

    def on_write_register_clicked(self):
        if not self.easy320.is_connected: return
        driver=self.easy320
        addr,val=self.spin_reg_addr.value(),self.spin_reg_val.value()
        self.lbl_reg_op_status.setText("正在写入…")
        def completed(ok,value):
            self.lbl_reg_op_status.setText("写入成功（已校验）" if ok and value else "写入失败："+str(value))
        self.run_task(lambda:driver.write_register(addr,val,verify=True),completed)

    def on_read_register_clicked(self):
        if not self.easy320.is_connected: return
        driver=self.easy320
        addr=self.spin_reg_addr.value()
        self.lbl_reg_op_status.setText("正在读取…")
        def completed(ok,value):
            if ok and value:
                self.edit_reg_read_val.setText(f"{value[0]} (0x{value[0]:04X})")
                self.lbl_reg_op_status.setText("读取成功")
            else:
                self.lbl_reg_op_status.setText("读取失败："+str(value))
        self.run_task(lambda:driver.read_register(address=addr,count=1),completed)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Easy320StandaloneTab()
    window.show()
    sys.exit(app.exec())
