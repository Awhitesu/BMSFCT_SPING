from ui.device_task import DeviceTask
from core.simulator_control import control_simulator
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QDoubleSpinBox, QGridLayout, QComboBox, QCheckBox,
                               QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
                               QAbstractItemView, QLayout, QFrame, QScrollArea)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSignalBlocker
from ui.widgets.eliding_label import ElidingLabel
import math


def _current_reading(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('电流数据缺失或类型无效')
    value = float(value)
    if not math.isfinite(value) or abs(value) >= 9e37:
        raise ValueError('电流数据无效或超量程')
    return value

class ReadAllWorker(QThread):
    """
    后台回读线程，避免阻塞 UI
    """
    progress = Signal(int, float) # global channel, measured value
    diagnostic = Signal(int, str)
    read_failed = Signal(int, str)
    def __init__(self, channels, quantity='voltage'):
        super().__init__()
        if quantity not in ('voltage', 'current'):raise ValueError('回读类型无效')
        self.channels = channels
        self.quantity = quantity
    def run(self):
        if self.quantity == 'current':
            self._read_currents()
            return
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

    def _read_currents(self):
        from concurrent.futures import ThreadPoolExecutor
        grouped = {}
        for global_ch, sim, local in self.channels:
            grouped.setdefault(id(sim), (sim, []))[1].append((global_ch, local))
        def acquire(entry):
            sim, channels = entry
            results = []
            for global_ch, local in channels:
                try:
                    if self.isInterruptionRequested():raise RuntimeError('电流回读已停止')
                    value = _current_reading(sim.read_current_sample(local, cancelled=self.isInterruptionRequested))
                    results.append((global_ch, local, value, ''))
                except Exception as exc:
                    results.append((global_ch, local, None, str(exc)))
            return sim, results
        with ThreadPoolExecutor(max_workers=max(1, len(grouped))) as pool:
            snapshots = list(pool.map(acquire, grouped.values()))
        for sim, results in snapshots:
            for global_ch, local, value, error in results:
                context = f'CH{global_ch} / 第{getattr(sim,"sequence_number",0)}台 CH{local}'
                if value is None:
                    text = context+'：电流读取失败：'+error
                    self.read_failed.emit(global_ch, text)
                else:
                    text = context+f'：实测电流 {value:.9g} mA'
                    self.progress.emit(global_ch, value)
                self.diagnostic.emit(global_ch, text)

class SimulatorTab(QWidget):
    def __init__(self, device_manager=None):
        super().__init__()
        self.mgr = device_manager
        self.read_all_worker = None
        self.operation = DeviceTask(self)
        self.operation.finished.connect(self._operation_finished)
        self.voltage_warnings = {}
        self.current_warnings = {}
        self._routes = []
        self._rows = {}
        self._scope_checks = {}
        self._pending_read = None
        self._init_ui()

    def _init_ui(self):
        columns = QHBoxLayout(self)
        columns.setSizeConstraint(QLayout.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        columns.setContentsMargins(8, 8, 8, 8)
        columns.setSpacing(10)

        def separator(shape=QFrame.HLine):
            line = QFrame()
            line.setFrameShape(shape)
            line.setStyleSheet('QFrame { background-color: #334155; border: none; }')
            if shape == QFrame.VLine:
                line.setFixedWidth(1)
            else:
                line.setFixedHeight(1)
            return line

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['通道 / 全局映射', '实测电压 (V)', '实测电流 (mA)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setMinimumSectionSize(24)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setMinimumSize(240, 120)
        columns.addWidget(self.table, 1)
        columns.addWidget(separator(QFrame.VLine))

        self.control_scroll = QScrollArea()
        self.control_scroll.setWidgetResizable(True)
        self.control_scroll.setFrameShape(QFrame.NoFrame)
        self.control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.control_scroll.setFixedWidth(320)
        self.control_panel = QFrame()
        self.control_panel.setObjectName('simulatorControls')
        self.control_panel.setStyleSheet('QFrame#simulatorControls { background-color: #151f30; border: 1px solid #334155; border-radius: 6px; }')
        layout = QVBoxLayout(self.control_panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.control_scroll.setWidget(self.control_panel)
        columns.addWidget(self.control_scroll)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(QLabel('模拟电池'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(160)
        self.device_combo.currentIndexChanged.connect(self._render_scope)
        header.addWidget(self.device_combo, 1)
        layout.addLayout(header)
        status_row = QHBoxLayout()
        self.lbl_status = QLabel('未启用设备')
        status_row.addWidget(self.lbl_status)
        status_row.addStretch()
        self.lbl_selection = QLabel('已选 0 路')
        status_row.addWidget(self.lbl_selection)
        layout.addLayout(status_row)
        self.lbl_summary = ElidingLabel('')
        self.lbl_summary.setMinimumWidth(0)
        self.lbl_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.lbl_summary)
        layout.addWidget(separator())

        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel('通道选择'))
        selection_row.addStretch()
        self.chk_all = QCheckBox('全选')
        self.chk_all.toggled.connect(self._select_all_channels)
        selection_row.addWidget(self.chk_all)
        layout.addLayout(selection_row)

        self.channel_grid = QGridLayout()
        self.channel_grid.setContentsMargins(0, 0, 0, 0)
        self.channel_grid.setHorizontalSpacing(6)
        self.channel_grid.setVerticalSpacing(2)
        self.channel_checks = {}
        for local in range(1, 25):
            check = QCheckBox(f'CH{local:02d}')
            check.setFixedHeight(26)
            check.setStyleSheet('QCheckBox { padding: 0px; spacing: 3px; }')
            check.toggled.connect(self._selection_changed)
            self.channel_checks[local] = check
            self.channel_grid.addWidget(check, (local-1)//4, (local-1)%4, Qt.AlignLeft)
        for column in range(4):
            self.channel_grid.setColumnStretch(column, 1)
        layout.addLayout(self.channel_grid)
        layout.addWidget(separator())

        self.control_buttons = []
        def button(text, callback, row):
            widget = QPushButton(text)
            widget.setStyleSheet('QPushButton { min-height: 0px; padding: 5px 12px; }')
            widget.setFixedHeight(34)
            widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            widget.clicked.connect(callback)
            row.addWidget(widget)
            self.control_buttons.append(widget)
            return widget
        def spin(maximum, value, decimals, width):
            widget = QDoubleSpinBox()
            widget.setDecimals(decimals)
            widget.setRange(0, maximum)
            widget.setValue(value)
            widget.setFixedWidth(width)
            widget.setStyleSheet('QDoubleSpinBox { min-height: 0px; padding: 3px 6px; }')
            widget.setFixedHeight(34)
            return widget

        def parameter_group(title):
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(title)
            label.setFixedWidth(52)
            row.addWidget(label)
            layout.addLayout(row)
            return row

        voltage_row = parameter_group('电压(V)')
        self.spin_volt = spin(15, 3.8, 3, 128)
        voltage_row.addWidget(self.spin_volt)
        self.btn_set_volt = button('设定电压', self.set_sim_volt, voltage_row)
        voltage_row.addStretch()
        layout.addWidget(separator())
        range_row = parameter_group('量程')
        self.range_combo = QComboBox()
        for label, value in [('0–1000 mA', 'HIGH'), ('0–1 mA', 'LOW'), ('AUTO', 'AUTO')]:
            self.range_combo.addItem(label, value)
        self.range_combo.setFixedWidth(128)
        self.range_combo.setStyleSheet('QComboBox { min-height: 0px; padding: 3px 6px; }')
        self.range_combo.setFixedHeight(34)
        range_row.addWidget(self.range_combo)
        self.btn_set_range = button('设置量程', lambda: self.set_current_parameter('range', self.range_combo.currentData()), range_row)
        range_row.addStretch()
        layout.addWidget(separator())
        current_row = parameter_group('限流(A)')
        self.current_limit = spin(1, .1, 7, 128)
        self.current_limit.setSingleStep(.01)
        current_row.addWidget(self.current_limit)
        self.btn_set_current = button('设置限流', lambda: self.set_current_parameter('current', self.current_limit.value()), current_row)
        current_row.addStretch()
        self.range_combo.currentIndexChanged.connect(self._update_current_limit_control)
        self.range_combo.setToolTip('LOW先自动将勾选通道限流设为1mA并确认，再切量程；HIGH/AUTO不主动改限流。不联动输出或电压。手册要求换档后再次ON生效。')
        layout.addWidget(separator())

        layout.addWidget(QLabel('输出控制'))
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.btn_on = button('开启输出', lambda: self.control_output(True), action_row)
        self.btn_off = button('关闭输出', lambda: self.control_output(False), action_row)
        self.btn_one_key = button('设压并开启', self.one_key_global_action, action_row)
        action_row.addStretch()
        layout.addLayout(action_row)
        layout.addWidget(separator())

        layout.addWidget(QLabel('数据回读'))
        read_row = QHBoxLayout()
        read_row.setSpacing(6)
        self.btn_read = button('回读电压', self.read_all_channels, read_row)
        self.btn_read_current = button('回读电流', self.read_all_currents, read_row)
        self.btn_read_parameters = button('回读参数', self.read_current_parameters, read_row)
        self.btn_read_parameters.setToolTip('回读勾选通道的量程与输出限流')
        read_row.addStretch()
        layout.addLayout(read_row)
        layout.addWidget(separator())
        layout.addStretch()
        self.lbl_result = ElidingLabel('待操作；控制与回读仅作用于当前范围内的勾选通道')
        self.lbl_result.setMinimumWidth(0)
        self.lbl_result.setFixedHeight(26)
        layout.addWidget(self.lbl_result)
        self._update_current_limit_control()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self.update_net_info)
        self.status_timer.start()
        self.update_net_info(force=True)

    def _io_busy(self):
        return self.operation.isRunning() or (self.read_all_worker is not None and not self.read_all_worker.wait(0))

    def _scope_routes(self):
        key = self.device_combo.currentData()
        return [route for route in self._routes if key == 'all' or id(route[1]) == key]

    def selected_channels(self):
        return [route for route in self._scope_routes() if self.channel_checks[route[2]].isChecked()]

    def _render_scope(self, *_):
        scope = self._scope_routes()
        key = self.device_combo.currentData()
        selected = self._scope_checks.get(key, set())
        for local, check in self.channel_checks.items():
            routes = [route for route in scope if route[2] == local]
            with QSignalBlocker(check):
                check.setChecked(bool(routes) and local in selected)
            check.setEnabled(bool(routes) and not self._io_busy())
            check.setToolTip('、'.join(f'第{sim.sequence_number}台 CH{ch} → 全局CH{global_ch}' for global_ch, sim, ch in routes) or '当前范围未配置此通道')
        visible = {global_ch for global_ch, _, _ in scope}
        for global_ch, row in self._rows.items():
            self.table.setRowHidden(row, global_ch not in visible)
        self.table.scrollToTop()
        summary = ('整体模式：勾选各台同号通道' if key == 'all' else '单台模式：只控制当前设备')
        self.lbl_summary.setText(summary)
        self.lbl_summary.setToolTip(summary)
        self._selection_changed()

    def _selection_changed(self, *_):
        scope = self._scope_routes()
        available = {local for _, _, local in scope}
        selected = {local for local, check in self.channel_checks.items() if check.isChecked() and local in available}
        self._scope_checks[self.device_combo.currentData()] = selected
        with QSignalBlocker(self.chk_all):
            self.chk_all.setChecked(bool(available) and selected == available)
        count = sum(local in selected for _, _, local in scope)
        self.lbl_selection.setText(f'已选 {count}/{len(scope)} 路')
        self._update_controls()

    def _select_all_channels(self, checked):
        available = {local for _, _, local in self._scope_routes()}
        for local, check in self.channel_checks.items():
            with QSignalBlocker(check):
                check.setChecked(bool(checked) and local in available)
        self._selection_changed()

    def _update_controls(self):
        busy = self._io_busy()
        scope = self._scope_routes()
        available = {local for _, _, local in scope}
        devices = {id(sim): sim for _, sim, _ in scope}.values()
        self.lbl_status.setText(f'已连接 {sum(bool(sim.is_connected) for sim in devices)}/{len(devices)} 台')
        self.device_combo.setEnabled(bool(self._routes) and not busy)
        self.chk_all.setEnabled(bool(available) and not busy)
        for local, check in self.channel_checks.items():
            check.setEnabled(local in available and not busy)
        enabled = bool(self.selected_channels()) and not busy
        for button in self.control_buttons:
            button.setEnabled(enabled)
        self.spin_volt.setEnabled(not busy)
        self.range_combo.setEnabled(not busy)
        self._update_current_limit_control()

    def _update_current_limit_control(self, *_):
        low = self.range_combo.currentData() == 'LOW'
        self.current_limit.setRange(.001 if low else 0, .001 if low else 1)
        self.current_limit.setEnabled(not low and not self._io_busy())
        self.current_limit.setToolTip('LOW固定0.001 A（1 mA）' if low else '输出限流范围：0–1 A；AUTO自动选择测量量程，仍需设置限流')

    def refresh_configuration(self):
        self.update_net_info(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_configuration()

    def update_net_info(self, *_args, force=False):
        if self._io_busy() or (not force and not self.isVisible() and hasattr(self, '_channel_signature')):
            return
        routes = self.mgr.simulator_channels() if self.mgr else []
        signature = [(ch, id(sim), local, sim.sequence_number, getattr(sim, 'ip', ''), getattr(sim, 'port', '')) for ch, sim, local in routes]
        if getattr(self, '_channel_signature', None) != signature:
            key = self.device_combo.currentData()
            self._channel_signature = signature
            self._routes = list(routes)
            devices = {id(sim): sim for _, sim, _ in routes}
            self._scope_checks = {k: v for k, v in self._scope_checks.items() if k == 'all' or k in devices}
            with QSignalBlocker(self.device_combo):
                self.device_combo.clear()
                for identity, sim in devices.items():
                    count = sum(driver is sim for _, driver, _ in routes)
                    self.device_combo.addItem(f'模拟电池{sim.sequence_number}（{count}路）', identity)
                    self.device_combo.setItemData(self.device_combo.count()-1, f'{getattr(sim,"ip","")}:{getattr(sim,"port","")}', Qt.ToolTipRole)
                if devices:
                    self.device_combo.addItem(f'整体模拟电池（{len(devices)}台/{len(routes)}路）', 'all')
                index = self.device_combo.findData(key)
                self.device_combo.setCurrentIndex(index if index >= 0 else 0)
            self.table.setRowCount(len(routes))
            self._rows = {}
            self.voltage_warnings.clear()
            self.current_warnings.clear()
            for row, (global_ch, sim, local) in enumerate(routes):
                self._rows[global_ch] = row
                item = QTableWidgetItem(f'CH{local} / 第{sim.sequence_number}台 / 全局CH{global_ch}')
                item.setToolTip(item.text())
                self.table.setItem(row, 0, item)
                self.table.setItem(row, 1, QTableWidgetItem('—'))
                self.table.setItem(row, 2, QTableWidgetItem('—'))
            self._render_scope()
        self._update_controls()

    def run_device(self, label, action, callback=None):
        if self._io_busy():
            return False
        def completed(ok, value):
            if callback:
                callback(ok, value)
            else:
                self._notify_status(label + ('成功' if ok and value is not False else '失败：'+str(value)))
        accepted = self.operation.run(action, completed)
        self._update_controls()
        return accepted

    def _operation_finished(self):
        self.refresh_configuration()
        if self._pending_read is not None:
            channels, self._pending_read = self._pending_read, None
            self._start_read_all('voltage', channels)

    def _control_selected(self, action, value=None, read_after=False):
        if self._io_busy():
            return
        channels = list(self.selected_channels())
        if not channels:
            self._notify_status('请先勾选需要控制的通道')
            return
        from devices.simulator_batch import run_simulator_batch
        def completed(ok, result):
            if not ok:
                self._notify_status('操作失败：'+str(result))
                return
            errors = ['全局'+','.join(f'CH{ch}' for ch in row['channels'])+'：'+row['error'] for row in result['results'] if not row['ok']]
            if action == 'output' and value is False:
                for row in result['results']:
                    if row['ok']:
                        for ch in row['channels']:
                            item = QTableWidgetItem('输出已关闭（未重新测量）')
                            item.setToolTip('已回读确认输出关闭；端子残余电压需另行测量。')
                            if ch in self._rows:
                                self.table.setItem(self._rows[ch], 1, item)
            self._notify_status(('勾选通道设置及校验成功' if result['ok'] else '勾选通道操作失败')+f'，{len(channels)}路，耗时{result["elapsed"]:.3f}s'+('；'+'；'.join(errors) if errors else ''))
            if read_after and result['ok']:
                self._pending_read = channels
        self._notify_status(f'正在设置并回读确认 {len(channels)} 路勾选通道…')
        self.run_device(action, lambda: run_simulator_batch(channels, lambda sim, chs: control_simulator(sim, action, chs, value, batch=True)), completed)

    def set_sim_volt(self):
        self._control_selected('voltage', self.spin_volt.value())

    def control_output(self, state):
        self._control_selected('output', state, read_after=state)

    def one_key_global_action(self):
        self._control_selected('voltage_on', self.spin_volt.value(), read_after=True)

    def set_current_parameter(self, parameter, value):
        self._control_selected(parameter, value)

    def read_current_parameters(self):
        if self._io_busy():
            return
        channels = list(self.selected_channels())
        if not channels:
            self._notify_status('请先勾选需要读取的通道')
            return
        from devices.simulator_batch import run_simulator_batch
        values = {}
        mapping = {(id(sim), local): global_ch for global_ch, sim, local in channels}
        def read(sim, local_channels):
            for local in local_channels:
                result = sim.read_source_settings(local)
                if result is None:
                    raise RuntimeError(sim.last_error or '量程和限流回读失败')
                values[mapping[id(sim), local]] = result
            return True
        def completed(ok, result):
            if not ok:
                self._notify_status('参数回读失败：'+str(result))
                return
            details = []
            for ch, settings in sorted(values.items()):
                text = f'全局CH{ch}：量程{settings["range"]}，限流{settings["current"]*1000:g} mA'
                details.append(text)
                if ch in self._rows:
                    self.table.item(self._rows[ch], 0).setToolTip(text)
            errors = [row['error'] for row in result['results'] if not row['ok']]
            self._notify_status(('参数回读完成：' if result['ok'] else '参数回读部分失败：')+'；'.join(details+errors))
        self.run_device('参数回读', lambda: run_simulator_batch(channels, read), completed)

    def read_all_channels(self):
        self._start_read_all('voltage')

    def read_all_currents(self):
        self._start_read_all('current')

    def _start_read_all(self, quantity, channels=None):
        if self._io_busy():
            return
        channels = list(self.selected_channels() if channels is None else channels)
        if not channels:
            self._notify_status('请先勾选需要读取的通道')
            return
        warnings = self.current_warnings if quantity == 'current' else self.voltage_warnings
        for ch, _, _ in channels:
            warnings.pop(ch, None)
            if quantity == 'current' and ch in self._rows:
                self.table.setItem(self._rows[ch], 2, QTableWidgetItem('—'))
        if self.read_all_worker is not None:
            self.read_all_worker.deleteLater()
        self.read_all_worker = ReadAllWorker(channels, quantity)
        if quantity == 'current':
            self.read_all_worker.progress.connect(self.on_current_read_progress)
            self.read_all_worker.read_failed.connect(self.on_current_read_error)
            self.read_all_worker.diagnostic.connect(self.on_current_diagnostic)
        else:
            self.read_all_worker.progress.connect(self.on_read_all_progress)
            self.read_all_worker.diagnostic.connect(self.on_channel_diagnostic)
        self.read_all_worker.finished.connect(self.on_read_all_finished)
        self._notify_status(f'正在回读勾选的 {len(channels)} 路'+('电流…' if quantity == 'current' else '电压…'))
        self.read_all_worker.start()
        self._update_controls()

    def on_current_read_progress(self, ch, value):
        if ch not in self._rows:
            return
        try:
            value = _current_reading(value)
        except (TypeError, ValueError) as exc:
            self.on_current_read_error(ch, str(exc))
            return
        item = self.table.item(self._rows[ch], 2)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(self._rows[ch], 2, item)
        item.setText(f'{value:.9g}')
        item.setToolTip(f'全局CH{ch}：实测电流 {value:.9g} mA')
        from PySide6.QtGui import QBrush
        item.setBackground(QBrush())
        self.current_warnings.pop(ch, None)

    def on_current_read_error(self, ch, error):
        if ch not in self._rows:
            return
        from PySide6.QtGui import QColor
        item = QTableWidgetItem('Error')
        item.setToolTip(error)
        item.setBackground(QColor('#78350f'))
        self.table.setItem(self._rows[ch], 2, item)
        self.current_warnings[ch] = error

    def on_current_diagnostic(self, ch, text):
        if ch in self._rows:
            self.table.item(self._rows[ch], 2).setToolTip(text)
        print(f'[Simulator] {text}')

    def on_read_all_progress(self, ch, value):
        if ch not in self._rows:
            return
        item = self.table.item(self._rows[ch], 1)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(self._rows[ch], 1, item)
        text = f'{value:.4f}' if math.isfinite(value) and value >= 0 else 'Error'
        if item.text() != text:
            item.setText(text)
        if item.data(Qt.UserRole) is True:
            from PySide6.QtGui import QBrush
            item.setBackground(QBrush())
            item.setData(Qt.UserRole, False)

    def on_channel_diagnostic(self, ch, text):
        if ch not in self._rows:
            return
        item = self.table.item(self._rows[ch], 1)
        if item:
            item.setToolTip(text)
        if item and '输出关闭' in text:
            item.setText(item.text()+'（输出关闭）')
        if '偏差超过' in text or '诊断读取失败' in text:
            self.voltage_warnings[ch] = text
            if item:
                from PySide6.QtGui import QColor
                item.setBackground(QColor('#78350f'))
                item.setData(Qt.UserRole, True)
                item.setText(item.text()+'（异常）')
        print(f'[Simulator] {text}')

    def on_read_all_finished(self):
        if self.read_all_worker is not None and not self.read_all_worker.wait(0):
            QTimer.singleShot(10, self.on_read_all_finished)
            return
        worker = self.read_all_worker
        if worker is None:
            return
        current = worker.quantity == 'current'
        warnings = self.current_warnings if current else self.voltage_warnings
        failed = [ch for ch, _, _ in worker.channels if ch in warnings]
        self._notify_status(f'勾选的 {len(worker.channels)} 路'+('电流' if current else '电压')+'回读完成'+('；异常通道：'+','.join(f'CH{ch}' for ch in failed) if failed else ''))
        self.refresh_configuration()

    def shutdown(self):
        self.status_timer.stop()
        self.operation.shutdown()
        if self.read_all_worker:
            self.read_all_worker.requestInterruption()
            self.read_all_worker.wait()

    def _notify_status(self, msg):
        self.lbl_result.setText(msg)
        self.lbl_result.setToolTip(msg)
        import datetime
        print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S.%f}] [Simulator] {msg}')
        parent = self.parent()
        while parent:
            if hasattr(parent, 'show_status'):
                parent.show_status(msg)
                break
            parent = parent.parent()
