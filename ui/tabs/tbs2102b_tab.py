import csv
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit,
                              QComboBox, QDoubleSpinBox, QPushButton, QTabWidget,
                              QScrollArea, QPlainTextEdit, QFileDialog)
from ui.tabs.multimeter_tab import MeterTask


class WavePlot(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
        self.setMinimumHeight(260)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#101827'))
        if not self.points:
            painter.setPen(QColor('#aab7c8'))
            painter.drawText(self.rect(), Qt.AlignCenter, '点击读取波形；采集中会自动暂停并恢复')
            return
        xmin, xmax = self.points[0][0], self.points[-1][0]
        ymin, ymax = min(p[1] for p in self.points), max(p[1] for p in self.points)
        painter.setPen(QColor('#68b5ef'))
        step = max(1, len(self.points)//max(1, self.width()*2))
        poly = QPolygonF([QPointF(16+(x-xmin)/max(xmax-xmin, 1e-30)*(self.width()-32),
                                 20+(ymax-y)/max(ymax-ymin, 1e-30)*(self.height()-60))
                          for x, y in self.points[::step]])
        painter.drawPolyline(poly)
        painter.drawText(16, self.height()-10, f'{xmin:.5g}–{xmax:.5g} s   {ymin:.5g}–{ymax:.5g} V')


class TBS2102BTab(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.task = None
        self.buttons = []
        layout = QVBoxLayout(self)
        self.ip = QLineEdit(manager.tbs2102b.ip)
        self.ip.setPlaceholderText('泰克 TBS2102B IP地址（LAN / VXI-11）')
        layout.addWidget(self.ip)
        connection = QGridLayout()
        self.button(connection, '连接', self.connect_device, 0, 0)
        self.button(connection, '断开', lambda: self.run(lambda d: d.disconnect()), 0, 1)
        self.button(connection, '保存IP', self.save_config, 0, 2)
        layout.addLayout(connection)
        self.status = QLabel('未连接')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.channel_fields = {}
        for ch in (1, 2):
            grid = self.page(f'CH{ch}通道')
            fields = self.channel_fields[ch] = {
                'enabled': self.choice(grid, '通道显示', [('开启', 'ON'), ('关闭', 'OFF')], 0),
                'coupling': self.choice(grid, '耦合：DC保留直流；AC过滤直流', [(x,x) for x in ('DC','AC')], 1),
                'probe': self.number(grid, '探头倍率：必须与探头拨档一致，否则读数不准', 1, 1000, 10, 2),
                'scale': self.number(grid, '垂直刻度 V/div：信号占屏幕4–6格较易观察', .001, 100, 1, 3),
                'position': self.number(grid, '垂直位置 div：移动波形显示位置', -5, 5, 0, 4),
                'band': self.choice(grid, '带宽限制：20MHz可减少高频噪声', [('全带宽','FULL'),('20MHz','TWENTY')], 5),
                'invert': self.choice(grid, '反相：翻转波形极性', [('关闭','OFF'),('开启','ON')], 6)}
            self.button(grid, '应用通道设置', lambda _, c=ch: self.apply_channel(c), 7, 0)
        grid = self.page('时基与采集')
        self.timescale = self.number(grid, '时基 s/div：周期信号建议屏幕显示2–5个周期', 2e-9, 100, .001, 0)
        self.acqmode = self.choice(grid, '采集模式', [('采样','SAMPLE'),('峰值检测：短毛刺','PEAKDETECT'),('平均：重复信号降噪','AVERAGE')], 1)
        self.average = self.choice(grid, '平均次数（仅平均模式生效）', [(str(x),str(x)) for x in (2,4,8,16,32,64,128,256,512)], 2)
        self.button(grid, '应用时基与采集', self.apply_acquisition, 3, 0)
        self.button(grid, '连续运行', lambda: self.run(lambda d: d.apply(['ACQ:STOPAFTER RUNSTOP','ACQ:STATE ON'])), 4, 0)
        self.button(grid, '停止', lambda: self.run(lambda d: d.apply(['ACQ:STATE OFF'])), 4, 1)
        self.button(grid, '单次捕获（等待触发）', lambda: self.run(lambda d: d.apply(['ACQ:STATE OFF','ACQ:STOPAFTER SEQUENCE','ACQ:STATE ON'])), 5, 0)
        self.button(grid, '自动设置', lambda: self.run(lambda d: d.apply(['AUTOSET EXECUTE'])), 5, 1)
        self.button(grid, '查询触发状态', lambda: self.run(lambda d: d.command('TRIG:STATE?')), 6, 0)
        grid = self.page('触发设置与说明')
        self.kind = self.choice(grid, '触发类型', [('边沿：上升/下降越过电平','EDGE'),('脉宽：查找过短/过长脉冲','WIDTH'),('欠幅：跨过下阈值但未达到上阈值','RUNT')], 0)
        self.source = self.choice(grid, '触发源：选择实际接入信号的通道', [(x,x) for x in ('CH1','CH2','LINE')], 1)
        self.mode = self.choice(grid, '触发模式：Auto无事件也刷新；Normal只在事件发生时刷新', [('自动','AUTO'),('正常','NORMAL')], 2)
        self.level = self.number(grid, '触发电平 V：通常设在信号高低电平中间', -1000, 1000, 1.65, 3)
        self.slope = self.choice(grid, '边沿方向', [('上升沿','RISE'),('下降沿','FALL')], 4)
        self.coupling = self.choice(grid, '触发耦合：与通道耦合不同，仅影响触发判定', [('直流','DC'),('高频抑制','HFREJ'),('低频抑制','LFREJ'),('噪声抑制','NOISEREJ')], 5)
        self.holdoff = self.number(grid, '释抑时间 s：一次触发后忽略后续事件；重复脉冲串可增加此值', 20e-9, 8, 20e-9, 6)
        self.polarity = self.choice(grid, '脉冲极性', [('正脉冲','POSITIVE'),('负脉冲','NEGATIVE')], 7)
        self.when = self.choice(grid, '脉宽条件：等于/不等于按仪表容差判定', [('小于','LESSTHAN'),('大于','MORETHAN'),('等于','EQUAL'),('不等于','UNEQUAL'),('任意欠幅事件','OCCURS')], 8)
        self.width = self.number(grid, '脉宽 s：例如10μs填写0.00001', 1e-9, 10, .00001, 9)
        self.lower = self.number(grid, '欠幅下阈值 V', -1000, 1000, .8, 10)
        self.upper = self.number(grid, '欠幅上阈值 V：必须大于下阈值', -1000, 1000, 2, 11)
        self.button(grid, '应用触发设置', self.apply_trigger, 12, 0)
        self.button(grid, '回读触发全部设置', self.read_trigger_settings, 12, 1)
        self.button(grid, '触发电平自动50%', lambda: self.run(lambda d: d.apply(['TRIG:A SETLEVEL'])), 13, 0)
        self.button(grid, '强制触发', lambda: self.run(lambda d: d.apply(['TRIG FORCE'])), 13, 1)
        self.preset = self.choice(grid, '常用预设（先填入，再应用）', [('3.3V数字上升沿','digital'),('电源上电单次捕获','power'),('查找短毛刺','glitch')], 14)
        self.button(grid, '填入预设', self.fill_preset, 15, 0)
        self.trigger_help = QLabel()
        self.trigger_help.setWordWrap(True)
        grid.addWidget(self.trigger_help, 16, 0, 1, 2)
        self.trigger_readback=QLabel('尚未回读：输入值是请求值，实际生效值以示波器回读为准。')
        self.trigger_readback.setWordWrap(True)
        self.trigger_readback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(self.trigger_readback,17,0,1,2)
        self.last_trigger_request=None
        self.kind.currentIndexChanged.connect(self.update_trigger_fields)
        self.update_trigger_fields()
        grid = self.page('波形与测量')
        self.wave_channel = self.choice(grid, '波形来源', [('CH1','CH1'),('CH2','CH2')], 0)
        self.button(grid, '读取波形（自动暂停并恢复）', self.read_waveform, 1, 0)
        self.button(grid, '导出波形CSV', self.export_waveform, 1, 1)
        self.plot = WavePlot()
        grid.addWidget(self.plot, 2, 0, 1, 2)
        self.measure_type = self.choice(grid, '即时测量（来源同上）', [('频率','FREQUENCY'),('周期','PERIOD'),('峰峰值','PK2PK'),('均值','MEAN'),('RMS','RMS'),('上升时间','RISE'),('下降时间','FALL')], 3)
        self.button(grid, '读取测量值', self.measure, 4, 0)
        grid = self.page('SCPI日志与帮助')
        self.scpi = QLineEdit('*IDN?')
        grid.addWidget(self.scpi, 0, 0, 1, 2)
        self.button(grid, '发送指令', self.send_scpi, 1, 0)
        self.button(grid, '查询仪表事件', lambda: self.run(lambda d: d.command('ALLEv?')), 1, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(240)
        grid.addWidget(self.log, 2, 0, 1, 2)
        help_label = QLabel('入门：①探头倍率匹配→②选通道与V/div→③设时基→④选触发类型/来源/阈值→⑤应用→⑥运行或单次捕获。\n'
                            '单次捕获无波形：检查来源、电平、方向，并确认真实事件已发生。Auto适合找信号；Normal适合稳定触发；单次适合上电或故障。\n'
                            '输入端为接地参考；测浮地/高压节点应使用适当差分探头。波形数据是示波器已采集的记录，不是实时连续流。')
        help_label.setWordWrap(True)
        grid.addWidget(help_label, 3, 0, 1, 2)

    def page(self, title):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(16,16,16,16)
        grid.setSpacing(14)
        grid.setColumnStretch(1,1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll,title)
        return grid

    def choice(self, grid, label, options, row):
        widget = QComboBox()
        for text,value in options:
            widget.addItem(text,value)
        self.field(grid,label,widget,row)
        return widget

    def number(self, grid, label, minimum, maximum, value, row):
        widget = QDoubleSpinBox()
        widget.setDecimals(12)
        widget.setRange(minimum,maximum)
        widget.setValue(value)
        self.field(grid,label,widget,row)
        return widget

    def field(self, grid, label, widget, row):
        text=QLabel(label)
        text.setWordWrap(True)
        grid.addWidget(text,row,0)
        widget.setMinimumHeight(42)
        grid.addWidget(widget,row,1)

    def button(self, grid, label, callback, row, col):
        widget=QPushButton(label)
        widget.setMinimumHeight(42)
        widget.clicked.connect(callback)
        grid.addWidget(widget,row,col)
        self.buttons.append(widget)

    def run(self, action, waveform=False):
        if self.task and self.task.isRunning():
            return
        driver=self.manager.tbs2102b
        for button in self.buttons:
            button.setEnabled(False)
        self.task=MeterTask(lambda: action(driver))
        self.task.result.connect(lambda ok,result: self.result(ok,result,waveform))
        self.task.finished.connect(lambda: [b.setEnabled(True) for b in self.buttons])
        self.task.start()

    def result(self, ok, result, waveform):
        driver=self.manager.tbs2102b
        self.status.setText(('已连接 · '+driver.identity if driver.is_connected else '未连接') + (' · 操作成功' if ok else ' · '+str(result)))
        if ok and isinstance(result,dict) and result.get('type')=='trigger_readback':
            requested=result['requested'];actual=result['actual']
            lines=[f"{result['source']} · 示波器触发电平回读"]
            different=False
            names={'level':'触发电平','lower':'下阈值','upper':'上阈值'}
            for name,value in actual.items():
                if name in requested:
                    delta=value-requested[name]
                    different=different or abs(delta)>1e-9
                    lines.append(f'{names[name]}：请求 {requested[name]:.6f} V → 实际 {value:.6f} V；偏差 {delta*1000:+.3f} mV')
                else:
                    lines.append(f'{names[name]}：实际 {value:.6f} V（未记录本次请求值）')
            if not actual:lines.append('LINE 触发源不设置通道电压阈值。')
            if different:lines.append('请求值与实际值不同：请检查垂直刻度/位置、触发范围和仪器电平步进。')
            self.trigger_readback.setText('\n'.join(lines))
            if different:self.status.setText('已连接 · 设置已发送，实际电平与请求值不同，请查看回读结果')
            result='\n'.join(lines)+'\n全部触发设置：'+result['all_settings']
        if ok and waveform:
            self.plot.points=result
            self.plot.update()
            result=f'波形已读取：{len(result)}点；{result[0][0]:.6g}～{result[-1][0]:.6g} s；原采集状态已恢复'
            self.status.setText(result)
        self.log.appendPlainText(str(result))

    def connect_device(self):
        ip=self.ip.text().strip()
        def connect(d):
            d.ip=ip
            if not d.connect():
                raise ConnectionError(d.last_error)
            return d.identity
        self.run(connect)

    def save_config(self):
        cfg=dict(self.manager.db_manager.load_sys_config() or {})
        cfg.update(tbs2102b_ip=self.ip.text().strip(),tbs2102b_enabled=True)
        ok=self.manager.db_manager.save_sys_config(cfg)
        if ok:
            self.manager.enabled_devices['tbs2102b']=True
        self.status.setText('IP已保存，点击连接应用' if ok else '保存失败')

    def apply_channel(self,ch):
        f=self.channel_fields[ch]
        commands=[f'SELECT:CH{ch} {f["enabled"].currentData()}',f'CH{ch}:COUPLING {f["coupling"].currentData()}',
                  f'CH{ch}:PROBE:GAIN {1/f["probe"].value():.12g}',f'CH{ch}:SCALE {f["scale"].value():.12g}',
                  f'CH{ch}:POSITION {f["position"].value():.12g}',f'CH{ch}:BANDWIDTH {f["band"].currentData()}',
                  f'CH{ch}:INVERT {f["invert"].currentData()}']
        self.run(lambda d:d.apply(commands))

    def apply_acquisition(self):
        commands=[f'HORIZONTAL:SCALE {self.timescale.value():.12g}',f'ACQ:MODE {self.acqmode.currentData()}',f'ACQ:NUMAVG {self.average.currentData()}']
        self.run(lambda d:d.apply(commands))

    def update_trigger_fields(self):
        kind=self.kind.currentData()
        for widget in (self.slope,self.coupling): widget.setEnabled(kind=='EDGE')
        for widget in (self.polarity,self.when,self.width): widget.setEnabled(kind!='EDGE')
        for widget in (self.lower,self.upper): widget.setEnabled(kind=='RUNT')
        self.level.setEnabled(kind!='RUNT')
        self.trigger_help.setText({'EDGE':'最常用：当信号跨过指定电平时触发；上升沿适合上电、数字信号由低变高。',
                                  'WIDTH':'测量信号停留在阈值一侧的时间。例如小于10μs正脉冲可用于查找短毛刺。',
                                  'RUNT':'用于找幅度不足的脉冲。设置上下两个阈值；OCCURS不限制欠幅持续时间。'}[kind])

    def apply_trigger(self):
        if self.task and self.task.isRunning():
            return
        kind=self.kind.currentData()
        if kind=='WIDTH' and self.when.currentData()=='OCCURS':
            self.status.setText('OCCURS仅适用于欠幅触发')
            return
        args=(kind,self.source.currentData(),self.mode.currentData(),self.level.value(),self.slope.currentData(),self.coupling.currentData(),self.holdoff.value(),self.polarity.currentData(),self.when.currentData(),self.width.value(),self.lower.value(),self.upper.value())
        self.last_trigger_request=(kind,args[1],{'lower':args[10],'upper':args[11]} if kind=='RUNT' else {'level':args[3]})
        self.run(lambda d:d.trigger(*args))

    def read_trigger_settings(self):
        kind,source=self.kind.currentData(),self.source.currentData()
        requested={}
        if self.last_trigger_request and self.last_trigger_request[:2]==(kind,source):
            requested=dict(self.last_trigger_request[2])
        self.run(lambda d:d.read_trigger_settings(kind,source,requested))

    def fill_preset(self):
        preset=self.preset.currentData()
        self.kind.setCurrentIndex(1 if preset=='glitch' else 0)
        self.source.setCurrentIndex(0)
        self.mode.setCurrentIndex(1 if preset!='digital' else 0)
        self.level.setValue(1.65)
        self.slope.setCurrentIndex(0)
        self.coupling.setCurrentIndex(0)
        self.holdoff.setValue(20e-9)
        self.polarity.setCurrentIndex(0)
        self.when.setCurrentIndex(0)
        self.width.setValue(10e-6)
        self.status.setText('预设已填入，请根据实际电平调整后应用；上电场景还需在采集页点击单次捕获。')

    def read_waveform(self):
        channel=self.wave_channel.currentData()
        self.run(lambda d:d.waveform(channel),waveform=True)

    def export_waveform(self):
        if not self.plot.points:
            self.status.setText('请先读取波形')
            return
        path,_=QFileDialog.getSaveFileName(self,'导出波形','','CSV (*.csv)')
        if path:
            with open(path,'w',newline='',encoding='utf-8-sig') as stream:
                writer=csv.writer(stream)
                writer.writerow(['time_s','voltage_V'])
                writer.writerows(self.plot.points)

    def measure(self):
        source,kind=self.wave_channel.currentData(),self.measure_type.currentData()
        def action(d):
            d.apply([f'MEASUREMENT:IMMED:SOURCE1 {source}',f'MEASUREMENT:IMMED:TYPE {kind}'])
            value=d.command('MEASUREMENT:IMMED:VALUE?')
            if abs(float(value))>=9e37: raise ValueError('测量无效，请检查通道、采集状态及信号')
            return value+' '+d.command('MEASUREMENT:IMMED:UNITS?')
        self.run(action)

    def send_scpi(self):
        command=self.scpi.text()
        self.run(lambda d:d.command(command))

    def shutdown(self):
        if self.task and self.task.isRunning(): self.task.wait()
