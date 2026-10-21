from ui.device_task import DeviceTask
from core.simulator_control import control_simulator, read_simulator
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QGroupBox, QDoubleSpinBox, QGridLayout, QSpinBox, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QScrollArea, QMessageBox, QSizePolicy
from PySide6.QtCore import Qt, QThread, Signal, QTimer,QSignalBlocker

class ReadAllWorker(QThread):
    """
    后台回读线程，避免阻塞 UI
    """
    progress = Signal(int, float) # ch, voltage
    diagnostic = Signal(int, str)
    def __init__(self, channels):
        super().__init__()
        self.channels = channels
    def run(self):
        try:
            from concurrent.futures import ThreadPoolExecutor
            grouped={}
            for global_ch,sim,local_ch in self.channels:
                grouped.setdefault(id(sim),(sim,[]))[1].append((global_ch,local_ch))
            def acquire(entry):
                sim,channels=entry
                try:
                    if callable(getattr(type(sim),'read_stable_snapshot',None)):
                        values=sim.read_stable_snapshot([local for _,local in channels],cancelled=self.isInterruptionRequested)
                    else:
                        values=sim.read_voltage_snapshot([local for _,local in channels])
                    if len(values)!=len(channels):raise ValueError('回读通道不完整')
                    error=''
                except Exception as exc:
                    values=[(-1.0,None,None)]*len(channels)
                    error=str(exc)
                return sim,channels,values,error
            with ThreadPoolExecutor(max_workers=max(1,len(grouped))) as pool:
                snapshots=list(pool.map(acquire,grouped.values()))
            for sim,channels,values,error in snapshots:
              for (global_ch,local_ch),(voltage,setting,output) in zip(channels,values):
                self.progress.emit(global_ch,voltage)
                try:
                    text=f'CH{global_ch} / 第{getattr(sim,"sequence_number",0)}台 CH{local_ch}：设定{setting}V，输出{output}，实测{voltage}V'
                    if setting is None or output is None or voltage<0:
                        text+='；诊断读取失败'
                    elif output is False:
                        text+='；输出关闭'
                    if voltage>=0 and setting is not None and output is True and abs(voltage-setting)>0.05:
                        text+='；实测偏差超过0.05V，请检查负载、限流及设备保护状态'
                    if error:text+=': '+error
                    self.diagnostic.emit(global_ch,text)
                except Exception as exc:
                    self.diagnostic.emit(global_ch,f'诊断读取失败：{exc}')
        finally:
            pass  # Use QThread.finished after the thread actually exits.

class SimulatorTab(QWidget):
    def __init__(self, device_manager=None):
        super().__init__()
        self.mgr = device_manager
        self.worker = None
        self.read_all_worker = None
        self.batch_worker=None
        self.operation=DeviceTask(self)
        self.operation.finished.connect(self._operation_finished)
        self.voltage_warnings={}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        run = QWidget()
        run_layout = QVBoxLayout(run)
        run_layout.setSpacing(16)
        run_layout.setContentsMargins(16, 16, 16, 16)
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        self.lbl_status = QLabel()
        run_layout.addWidget(self.lbl_summary)
        run_layout.addWidget(self.lbl_status)
        self.btn_settings = QPushButton("配置电池模拟器…")
        self.btn_settings.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        run_layout.addWidget(self.btn_settings)
        self.control_buttons = []

        def button(text, callback, grid, row, col, span=1):
            widget = QPushButton(text)
            widget.setMinimumHeight(42)
            widget.clicked.connect(callback)
            grid.addWidget(widget, row, col, 1, span)
            self.control_buttons.append(widget)
            return widget

        def group(title):
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setContentsMargins(16, 30, 16, 16)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(14)
            run_layout.addWidget(box)
            return grid

        single = group("单通道控制")
        single.addWidget(QLabel("全局通道"), 0, 0)
        self.spin_ch = QComboBox()
        self.spin_ch.setMinimumHeight(42)
        single.addWidget(self.spin_ch, 0, 1, 1, 2)
        single.addWidget(QLabel("电压 (V)"), 1, 0)
        self.spin_volt = QDoubleSpinBox()
        self.spin_volt.setRange(0, 15)
        self.spin_volt.setDecimals(3)
        self.spin_volt.setValue(3.7)
        self.spin_volt.setMinimumHeight(42)
        single.addWidget(self.spin_volt, 1, 1)
        self.btn_set_volt = button("设定电压", self.set_sim_volt, single, 1, 2)
        self.btn_on = button("开启输出", lambda: self.control_output(True), single, 2, 0)
        self.btn_off = button("关闭输出", lambda: self.control_output(False), single, 2, 1)
        self.btn_read = button("回读电压", self.read_sim_volt, single, 2, 2)

        batch = group("整套设备控制 · 所有已启用的实际通道")
        batch.addWidget(QLabel("电压 (V)"), 0, 0)
        self.spin_global_volt = QDoubleSpinBox()
        self.spin_global_volt.setRange(0, 15)
        self.spin_global_volt.setDecimals(3)
        self.spin_global_volt.setValue(3.8)
        self.spin_global_volt.setMinimumHeight(42)
        batch.addWidget(self.spin_global_volt, 0, 1)
        self.btn_global_set = button("全部设压", self.global_broadcast_volt, batch, 0, 2)
        self.btn_global_on = button("全部开启", lambda: self.global_broadcast_output(True), batch, 1, 0)
        self.btn_global_off = button("全部关闭", lambda: self.global_broadcast_output(False), batch, 1, 1)
        self.btn_one_key = button("设压并全部开启", self.one_key_global_action, batch, 1, 2)
        self.btn_all_on = self.btn_global_on
        self.btn_all_off = self.btn_global_off
        self.btn_read_all = button("回读全部通道电压", self.read_all_channels, batch, 2, 0, 3)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["全局通道 / 设备通道", "实测电压 (V)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(240)
        run_layout.addWidget(self.table, 1)
        self.lbl_result = QLabel("待操作")
        self.lbl_result.setWordWrap(True)
        run_layout.addWidget(self.lbl_result)
        for label in (self.lbl_result,self.lbl_summary,self.lbl_status):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            label.setWordWrap(True)
        run.setMinimumWidth(0)
        scroll = QScrollArea()
        self.run_scroll=scroll
        scroll.setWidgetResizable(True)
        scroll.setWidget(run)
        self.tabs.addTab(scroll, "运行控制")
        self._init_settings()
        self._init_current_settings()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_net_info)
        self.status_timer.start(1000)
        self.update_net_info()

    def _init_current_settings(self):
        page=QWidget(); layout=QVBoxLayout(page)
        tip=QLabel('这里设置电流量程和输出限流。高量程/低量程/自动量程对应HIGH/LOW/AUTO；限流用A输入。设备具体支持的电流上限以型号为准，写入后回读确认。设置参数不会自动开启输出。')
        tip.setWordWrap(True); layout.addWidget(tip)
        self.spin_param_ch=QComboBox(); self.spin_param_ch.setMinimumHeight(42)
        layout.addWidget(QLabel('单通道选择（全局通道）')); layout.addWidget(self.spin_param_ch)
        for batch,title in [(False,'单通道量程与限流'),(True,'整套设备 · 全部已启用通道')]:
            box=QGroupBox(title); grid=QGridLayout(box); grid.setContentsMargins(16,30,16,16); grid.setSpacing(14)
            combo=QComboBox()
            for label,value in [('高量程','HIGH'),('低量程','LOW'),('自动量程','AUTO')]: combo.addItem(label,value)
            current=QDoubleSpinBox(); current.setRange(0,5); current.setDecimals(7); current.setSingleStep(.01); current.setSuffix(' A'); current.setValue(.1)
            combo.setToolTip('切换量程会短暂关闭并恢复原先开启的通道。切换LOW前，请先将限流设为不超过0.001 A（1 mA）。')
            current.setToolTip('单位A：0.0005 A = 0.5 mA。LOW最大1 mA；已开启通道修改参数后会确认实际电压稳定。')
            combo.currentIndexChanged.connect(lambda _,c=combo,w=current:w.setSingleStep(.0001 if c.currentData()=='LOW' else .01))
            combo.setMinimumHeight(42); current.setMinimumHeight(42)
            grid.addWidget(QLabel('电流量程'),0,0);grid.addWidget(combo,0,1)
            grid.addWidget(QLabel('输出限流'),1,0);grid.addWidget(current,1,1)
            for row,label,parameter,control in [(0,'设置量程','range',combo),(1,'设置限流','current',current)]:
                button=QPushButton(('全部' if batch else '')+label); button.setMinimumHeight(42)
                button.clicked.connect(lambda checked=False,b=batch,p=parameter,w=control:self.set_current_parameter(b,p,w.currentData() if p=='range' else w.value()))
                grid.addWidget(button,row,2); self.control_buttons.append(button)
            if not batch:
                button=QPushButton('回读量程和限流');button.setMinimumHeight(42);button.clicked.connect(self.read_current_parameters)
                grid.addWidget(button,2,0,1,3);self.control_buttons.append(button)
            layout.addWidget(box)
        self.lbl_current_result=QLabel('参数尚未读取');self.lbl_current_result.setWordWrap(True);layout.addWidget(self.lbl_current_result)
        layout.addStretch()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(page)
        self.tabs.addTab(page,'量程与限流')

    def set_current_parameter(self,batch,parameter,value):
        if not self.mgr:return
        channels=self.mgr.simulator_channels() if batch else []
        global_ch=self.spin_param_ch.currentData()
        if not batch:
            sim,ch=self.mgr._get_sim_and_ch(global_ch)
            if sim:channels=[(global_ch,sim,ch)]
        def action():
            from devices.simulator_batch import run_simulator_batch
            def apply(sim,chs):
                return control_simulator(sim,parameter,chs,value)
            return run_simulator_batch(channels,apply)
        def completed(ok,result):
            success=ok and isinstance(result,dict) and result['ok']
            errors='；'.join(str(row) for row in result['results'] if not row['ok']) if ok and isinstance(result,dict) else str(result)
            text=('全部通道' if batch else f'CH{global_ch}')+('量程' if parameter=='range' else '限流')+f'设置为{value}'+(' A' if parameter=='current' else '')+('，回读确认成功' if success else '，失败：'+errors)
            self.lbl_current_result.setText(text[:240]);self.lbl_current_result.setToolTip(text)
            self._notify_status(text)
        self.run_device(parameter,action,completed)

    def read_current_parameters(self):
        if not self.mgr:return
        global_ch=self.spin_param_ch.currentData();sim,ch=self.mgr._get_sim_and_ch(global_ch)
        def completed(ok,result):
            text=f'CH{global_ch}：量程{result["range"]}，输出限流{result["current"]:.4f} A' if ok and result else f'CH{global_ch}参数读取失败：{result}'
            self.lbl_current_result.setText(text)
        if sim:self.run_device("Read parameters",lambda:sim.read_source_settings(ch),completed)

    def _init_settings(self):
        from devices.simulator_config import simulator_settings
        page = QWidget()
        layout = QVBoxLayout(page)
        tip = QLabel("最多6台，每台1–24通道。已启用设备按设备序号排序，按配置的通道数量连续编号；未启用设备不占编号。保存并应用后生效。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        cfg = self.mgr.db_manager.load_sys_config() or {} if self.mgr and self.mgr.db_manager else {}
        settings = simulator_settings(cfg)
        self.settings_table = QTableWidget(6, 7)
        self.settings_table.setHorizontalHeaderLabels(["启用", "配置", "IP地址", "端口", "设备序号", "通道数量", "有效全局通道"])
        self.settings_table.verticalHeader().setVisible(False)
        self.settings_table.verticalHeader().setDefaultSectionSize(58)
        self.settings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.settings_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.settings_controls = []
        for row, setting in enumerate(settings):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if setting['enabled'] else Qt.Unchecked)
            self.settings_table.setItem(row, 0, check)
            name = QTableWidgetItem(str(row + 1))
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self.settings_table.setItem(row, 1, name)
            ip = QLineEdit(setting['ip'])
            port, number, count = QSpinBox(), QSpinBox(), QSpinBox()
            for control, maximum, value in ((port, 65535, setting['port']), (number, 6, setting['number']), (count, 24, setting['channels'])):
                control.setRange(1, maximum)
                control.setValue(value)
                control.setMinimumHeight(42)
            ip.setMinimumHeight(42)
            for col, control in enumerate((ip, port, number, count), 2):
                self.settings_table.setCellWidget(row, col, control)
            self.settings_controls.append((ip, port, number, count))
            number.valueChanged.connect(self._update_ranges)
            count.valueChanged.connect(self._update_ranges)
        self._update_ranges()
        self.settings_table.itemChanged.connect(lambda item: self._update_ranges() if item.column() == 0 else None)
        layout.addWidget(self.settings_table, 1)
        self.btn_save_settings = QPushButton("保存并应用模拟器配置")
        self.btn_save_settings.setMinimumHeight(44)
        self.btn_save_settings.clicked.connect(self.save_simulator_settings)
        layout.addWidget(self.btn_save_settings)
        self.tabs.addTab(page, "设备设置（最多6台）")

    def _update_ranges(self):
        from devices.simulator_config import channel_routes
        ranges = {}
        error = ''
        try:
            routes = channel_routes((number.value(), count.value(), row)
                                    for row, (_, _, number, count) in enumerate(self.settings_controls)
                                    if self.settings_table.item(row, 0).checkState() == Qt.Checked)
            for global_ch, row, _ in routes:
                ranges.setdefault(row, []).append(global_ch)
        except ValueError as exc:
            error = str(exc)
        with QSignalBlocker(self.settings_table):
            for row in range(len(self.settings_controls)):
                enabled = self.settings_table.item(row, 0).checkState() == Qt.Checked
                channels = ranges.get(row, [])
                text = '未启用' if not enabled else ('配置无效' if error else f'{channels[0]}–{channels[-1]}')
                item = QTableWidgetItem(text)
                item.setToolTip(error if enabled else '')
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.settings_table.setItem(row, 6, item)

    def save_simulator_settings(self):
        if self.operation.isRunning():return
        if self.batch_worker and self.batch_worker.isRunning(): return
        from devices.simulator_config import simulator_settings
        if not self.mgr or not self.mgr.db_manager:
            QMessageBox.warning(self, "无法保存", "未连接配置管理器。")
            return
        if self.read_all_worker and not self.read_all_worker.wait(0):
            QMessageBox.warning(self, "正在回读", "请等待回读完成后保存配置。")
            return
        cfg = dict(self.mgr.db_manager.load_sys_config() or {})
        for row, (ip, port, number, count) in enumerate(self.settings_controls):
            key = f"sim{row+1}"
            cfg.update({f"{key}_enabled": self.settings_table.item(row, 0).checkState() == Qt.Checked,
                        f"{key}_ip": ip.text().strip(), f"{key}_port": port.value(),
                        f"{key}_number": number.value(), f"{key}_channels": count.value()})
        try:
            simulator_settings(cfg)
            import ipaddress
            for row, (ip, _, _, _) in enumerate(self.settings_controls):
                if cfg[f"sim{row+1}_enabled"]:
                    ipaddress.IPv4Address(ip.text().strip())
        except ValueError as exc:
            QMessageBox.warning(self, "配置错误", str(exc))
            return
        if not self.mgr.db_manager.save_sys_config(cfg):
            QMessageBox.warning(self, "保存失败", "模拟器配置未保存。")
            return
        previous=list(self.mgr.simulators)
        def apply():
            from devices.ngi_83624 import NGI83624
            for sim in previous:sim.disconnect()
            result=[]
            for setting in simulator_settings(cfg):
                sim=NGI83624(setting['ip'],setting['port'],setting['channels'])
                sim.sequence_number=setting['number']
                result.append(sim)
            return result
        def completed(ok,value):
            if not ok:self._notify_status(str(value));return
            self.mgr.simulators=value
            for setting in simulator_settings(cfg):
                self.mgr.enabled_devices[f"sim{setting['slot']}"]=setting['enabled']
            parent=self.parent()
            while parent:
                hardware=getattr(parent,'tab_hardware',None)
                if hardware:
                    hardware.load_config();break
                parent=parent.parent()
            self.tabs.setCurrentIndex(0)
            self._notify_status("模拟器配置已保存并应用")
        self.run_device("Apply configuration",apply,completed)

    def update_net_info(self, index=0):
        if self.operation.isRunning():return
        if not self.isVisible() and hasattr(self,'_channel_signature'): return
        if self.batch_worker and self.batch_worker.isRunning(): return
        if not self.mgr:
            return
        if self.read_all_worker and not self.read_all_worker.wait(0):
            return
        channels = self.mgr.simulator_channels()
        signature = [(ch, id(sim), local) for ch, sim, local in channels]
        if getattr(self, '_channel_signature', None) != signature:
            selected = self.spin_ch.currentData()
            selected_param=self.spin_param_ch.currentData()
            with QSignalBlocker(self.spin_ch), QSignalBlocker(self.spin_param_ch):
                self._channel_signature = signature
                self.spin_ch.clear()
                self.spin_param_ch.clear()
                self.table.setRowCount(len(channels))
                self._rows = {}
                for row, (ch, sim, local) in enumerate(channels):
                    self._rows[ch] = row
                    self.spin_ch.addItem(f"CH{ch}（第{sim.sequence_number}台 / CH{local}）", ch)
                    self.spin_param_ch.addItem(f"CH{ch}（第{sim.sequence_number}台 / CH{local}）", ch)
                    self.table.setItem(row, 0, QTableWidgetItem(f"CH{ch} / 第{sim.sequence_number}台 CH{local}"))
                    self.table.setItem(row, 1, QTableWidgetItem("—"))
                idx = self.spin_ch.findData(selected)
                if idx >= 0:
                    self.spin_ch.setCurrentIndex(idx)
                idx=self.spin_param_ch.findData(selected_param)
                if idx>=0:self.spin_param_ch.setCurrentIndex(idx)
        devices = {id(sim): sim for _, sim, _ in channels}.values()
        count = len(devices)
        connected = sum(bool(sim.is_connected) for sim in devices)
        self.lbl_summary.setText(f"整套设备：{count}台模拟器，共{len(channels)}个有效通道")
        self.lbl_status.setText(f"已连接 {connected}/{count} 台")
        for button in self.control_buttons:
            button.setEnabled(bool(channels))

    def run_device(self,label,action,callback=None):
        if self.operation.isRunning() or (self.batch_worker and self.batch_worker.isRunning()) or (self.read_all_worker and not self.read_all_worker.wait(0)): return
        for button in self.control_buttons:button.setEnabled(False)
        self.btn_save_settings.setEnabled(False)
        def completed(ok,value):
            if callback:callback(ok,value)
            else:self._notify_status(label+("成功" if ok and value is not False else "失败："+str(value)))
        self.operation.run(action,completed)

    def _operation_finished(self):
        self.btn_save_settings.setEnabled(True)
        self.update_net_info()

    def set_sim_volt(self):
        if self.mgr:
            ch,volt=self.spin_ch.currentData(),self.spin_volt.value()
            sim,local=self.mgr._get_sim_and_ch(ch)
            if sim:self.run_device(f"CH{ch}设压",lambda:control_simulator(sim,'voltage',[local],volt))

    def control_output(self,state):
        if self.mgr:
            ch=self.spin_ch.currentData();sim,local=self.mgr._get_sim_and_ch(ch)
            if sim:self.run_device(f"CH{ch}输出控制",lambda:control_simulator(sim,'output',[local],state))

    def read_sim_volt(self):
        if self.mgr:
            ch=self.spin_ch.currentData();sim,local=self.mgr._get_sim_and_ch(ch)
            def completed(ok,value):
                if ok and value>=0 and ch in self._rows:
                    self.on_read_all_progress(ch,value)
                else:self._notify_status(str(value))
            if sim:self.run_device(f"CH{ch}电压回读",lambda:read_simulator(sim,'voltage',local),completed)

    def read_all_channels(self):
        if self.operation.isRunning():return
        """异步回读物理设备通道 1。"""
        if not self.mgr: return
        if self.batch_worker and self.batch_worker.isRunning(): return
        if self.read_all_worker and not self.read_all_worker.wait(0): return
        channels = self.mgr.simulator_channels()
        if not channels:
            self._notify_status("请先配置并启用模拟器。")
            return
        self.btn_read_all.setText("正在读取整套设备...")
        self.voltage_warnings={}
        self.btn_read_all.setEnabled(False)
        self.read_all_worker = ReadAllWorker(channels)
        self.read_all_worker.progress.connect(self.on_read_all_progress)
        self.read_all_worker.diagnostic.connect(self.on_channel_diagnostic)
        self.read_all_worker.finished.connect(self.on_read_all_finished)
        self.read_all_worker.start()

    def on_read_all_progress(self, ch, v):
        import math
        if ch not in self._rows:return
        row=self._rows[ch]
        item=self.table.item(row,1)
        if item is None:
            item=QTableWidgetItem();self.table.setItem(row,1,item)
        text=f'{v:.4f}' if math.isfinite(v) and v>=0 else 'Error'
        if item.text()!=text:item.setText(text)
        if item.data(Qt.UserRole) is True:
            from PySide6.QtGui import QBrush
            item.setBackground(QBrush());item.setData(Qt.UserRole,False)

    def on_channel_diagnostic(self,ch,text):
        if ch not in self._rows:return
        item=self.table.item(self._rows[ch],1)
        if item: item.setToolTip(text)
        if item and '输出关闭' in text: item.setText(item.text()+'（输出关闭）')
        if '偏差超过' in text or '诊断读取失败' in text:
            self.voltage_warnings[ch]=text
            if item:
                from PySide6.QtGui import QColor
                item.setBackground(QColor('#78350f'))
                item.setData(Qt.UserRole,True)
                item.setText(item.text()+'（异常）')
        print(f'[Simulator] {text}')

    def on_read_all_finished(self):
        self.btn_read_all.setText("回读全部通道电压")
        self.btn_read_all.setEnabled(True)
        self._notify_status("整套设备所有通道刷新完成"+('；异常通道：'+','.join(f'CH{ch}' for ch in self.voltage_warnings) if self.voltage_warnings else ''))

    def batch_control(self, state):
        if self.mgr:
            res = self._all_channels(lambda sim, ch: control_simulator(sim,'output',[ch],state))
            self._notify_status(f"整套设备批量{'开启' if state else '关闭'} {'成功' if res else '失败'}")

    def global_broadcast_volt(self):
        voltage=self.spin_global_volt.value()
        self.fast_batch(lambda sim,chs:control_simulator(sim,'voltage',chs,voltage,batch=True))

    def global_broadcast_output(self,state):
        self.fast_batch(lambda sim,chs:control_simulator(sim,'output',chs,state,batch=True),read_after=state,output_closed=not state)

    def one_key_global_action(self):
        voltage=self.spin_global_volt.value()
        self.fast_batch(lambda sim,chs:control_simulator(sim,'voltage_on',chs,voltage,batch=True),read_after=True)

    def _all_channels(self, action):
        self.batch_error=''
        if not self.mgr:
            return False
        if self.read_all_worker and not self.read_all_worker.wait(0):
            self.batch_error='：正在回读，请等待完成后操作'
            return False
        channels = self.mgr.simulator_channels()
        results=[]; failures=[]
        for global_ch,sim,ch in channels:
            try:
                ok=bool(action(sim,ch))
                error=getattr(sim,'last_error','')
            except Exception as exc:
                ok=False; error=str(exc)
            results.append(ok)
            if not ok:
                failures.append(f'CH{global_ch}/第{getattr(sim,"sequence_number",0)}台CH{ch} ({error or "命令或回读失败"})')
        if failures: self.batch_error='：'+'；'.join(failures)
        return bool(results) and all(results)

    def fast_batch(self,action,read_after=False,output_closed=False):
        if self.operation.isRunning():return
        if not self.mgr or (self.batch_worker and self.batch_worker.isRunning()): return
        if self.read_all_worker and not self.read_all_worker.wait(0):
            self._notify_status('正在回读，请完成后操作');return
        from devices.simulator_batch import run_simulator_batch
        from ui.tabs.multimeter_tab import MeterTask
        channels=self.mgr.simulator_channels()
        if not channels:
            self._notify_status('请先启用模拟器');return
        position=self.run_scroll.verticalScrollBar().value()
        # Disabling an entire tab moves keyboard focus and scrolls its next child into view.
        # Keep navigation/scrolling active; only lock commands and configuration saving.
        from PySide6.QtWidgets import QApplication
        if QApplication.focusWidget() in self.control_buttons:
            self.run_scroll.setFocus(Qt.OtherFocusReason)
        for button in self.control_buttons:button.setEnabled(False)
        self.btn_save_settings.setEnabled(False)
        self.run_scroll.verticalScrollBar().setValue(position)
        QTimer.singleShot(0,lambda:self.run_scroll.verticalScrollBar().setValue(position))
        self._notify_status('正在并行批量设置并回读确认…')
        self._batch_result_ok=False
        self.batch_worker=MeterTask(lambda:run_simulator_batch(channels,action))
        def result(ok,value):
            if not ok:self._notify_status('批量操作失败：'+str(value));return
            self._batch_result_ok=value['ok']
            errors=['全局'+','.join(f'CH{ch}' for ch in row['channels'])+'：'+row['error'] for row in value['results'] if not row['ok']]
            if output_closed:
                for row in value['results']:
                    if row['ok']:
                        for ch in row['channels']:
                            item=QTableWidgetItem('输出已关闭（未重新测量）')
                            item.setToolTip('已回读确认输出关闭；端子残余电压需另行测量。')
                            self.table.setItem(self._rows[ch],1,item)
            self._notify_status(('批量设置及校验成功' if value['ok'] else '批量操作失败')+f'，耗时{value["elapsed"]:.3f}s'+('；'+'；'.join(errors) if errors else ''))
        def finished():
            self.btn_save_settings.setEnabled(True);self.update_net_info()
            if read_after and self._batch_result_ok:self.read_all_channels()
        self.batch_worker.result.connect(result);self.batch_worker.finished.connect(finished);self.batch_worker.start()

    def shutdown(self):
        self.status_timer.stop()
        self.operation.shutdown()
        for worker in (self.batch_worker,self.read_all_worker):
            if worker:worker.wait()

    def _notify_status(self, msg):
        self.lbl_result.setText(msg if len(msg)<=240 else msg[:240]+'…（完整信息见悬停提示）')
        self.lbl_result.setToolTip(msg)
        import datetime
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [Simulator] {msg}")

        p = self.parent()
        while p:
            if hasattr(p, "show_status"):
                p.show_status(msg)
                break
            p = p.parent()
