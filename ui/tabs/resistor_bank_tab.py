import ipaddress
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QGroupBox,
    QLabel,QPushButton,QSpinBox,QCheckBox,QScrollArea,QSizePolicy,QMessageBox)
from ui.tabs.board_debug_tab import BoardDebugTab
from devices.rn_board import RNBoard,board_settings
from devices.resistor_bank import channel_routes,operate_channels


class ResistorBankTab(BoardDebugTab):
    def __init__(self,manager):
        self.bank_ready=False
        super().__init__(manager,'resistor')
        self.config.horizontalHeaderItem(1).setText('排序序号（1–6）')
        for row,setting in enumerate(self.settings):
            sequence=QSpinBox();sequence.setRange(1,6);sequence.setValue(setting['number'])
            sequence.setMinimumHeight(40)
            sequence.setToolTip(f"{setting['key']}：序号决定全局通道，每块固定占6通道")
            self.config.setCellWidget(row,1,sequence)
        old=self.tabs.widget(1);self.tabs.removeTab(1);old.deleteLater()
        page=QWidget();body=QVBoxLayout(page)
        self.bank_buttons=[];self.bank_widgets={};self.bank_columns=0
        row=QHBoxLayout()
        self.bank_status=QLabel();self.bank_status.setWordWrap(True);row.addWidget(self.bank_status,1)
        self.add_button(row,'连接并回读整套设备',lambda:self.run_bank('read',self.active_channels()))
        self.add_button(row,'断开整套设备',lambda:self.run_bank('disconnect',self.active_channels()))
        body.addLayout(row)
        row=QHBoxLayout()
        row.addWidget(QLabel('批量阻值：'))
        self.bank_resistance=QSpinBox();self.bank_resistance.setRange(0,1500000)
        self.bank_resistance.setSingleStep(10);self.bank_resistance.setSuffix(' Ω');row.addWidget(self.bank_resistance,1)
        self.add_button(row,'设置勾选通道',lambda:self.run_bank('set',self.selected_channels(),self.bank_resistance.value()))
        self.add_button(row,'设置全部通道',lambda:self.run_bank('set',self.active_channels(),self.bank_resistance.value()))
        body.addLayout(row)
        row=QHBoxLayout()
        self.add_button(row,'全选',lambda:self.select_channels(True))
        self.add_button(row,'取消勾选',lambda:self.select_channels(False))
        self.add_button(row,'回读勾选通道',lambda:self.run_bank('read',self.selected_channels()))
        self.add_button(row,'回读全部通道',lambda:self.run_bank('read',self.active_channels()))
        body.addLayout(row)
        tip=QLabel('序号1→CH1–6，序号2→CH7–12，依次到CH36。0–1.5MΩ、10Ω步进；回读为设定值，不是实测阻值。')
        tip.setWordWrap(True);body.addWidget(tip)
        self.bank_scroll=QScrollArea();self.bank_scroll.setWidgetResizable(True)
        self.bank_scroll.viewport().installEventFilter(self)
        panel=QWidget();self.bank_grid=QGridLayout(panel);self.bank_grid.setSpacing(12)
        self.bank_grid.setAlignment(Qt.AlignTop);self.bank_scroll.setWidget(panel);body.addWidget(self.bank_scroll,1)
        self.tabs.addTab(page,'整套电阻控制（CH1–36）')
        self.bank_ready=True;self.rebuild();self.tabs.setCurrentIndex(1)

    def add_button(self,row,text,callback):
        button=QPushButton(text);button.setMinimumHeight(40);button.clicked.connect(callback)
        row.addWidget(button);self.bank_buttons.append(button);return button

    def rebuild(self):
        if not self.bank_ready:return super().rebuild()
        for setting in self.settings:
            if setting['enabled'] and setting['key'] not in self.boards:
                self.boards[setting['key']]=RNBoard(setting)
        self.routes=channel_routes(self.boards)
        while self.bank_grid.count():
            item=self.bank_grid.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.bank_widgets={}
        for channel in range(1,37):
            number,local=divmod(channel-1,6)
            group=QGroupBox(f'CH{channel} · {number+1}号板 CH{local+1}')
            group.setMinimumWidth(220);group.setFixedHeight(190)
            group.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            grid=QGridLayout(group);check=QCheckBox('勾选批量控制');check.setEnabled(channel in self.routes)
            grid.addWidget(check,0,0,1,2)
            value=QSpinBox();value.setRange(0,1500000);value.setSingleStep(10);value.setSuffix(' Ω')
            route=self.routes.get(channel)
            if route:value.setValue(self.channel_values.get((route[0].setting['key'],route[1]),0))
            value.valueChanged.connect(lambda v,ch=channel:self.remember_value(ch,v))
            grid.addWidget(value,1,0,1,2)
            state=QLabel('未回读' if route else '未配置/未启用');grid.addWidget(state,2,0,1,2)
            apply=QPushButton('设置');read=QPushButton('回读')
            apply.clicked.connect(lambda checked=False,ch=channel,v=value:self.run_bank('set',[ch],v.value()))
            read.clicked.connect(lambda checked=False,ch=channel:self.run_bank('read',[ch]))
            grid.addWidget(apply,3,0);grid.addWidget(read,3,1)
            self.bank_widgets[channel]=(group,check,value,state,apply,read)
            self.bank_grid.addWidget(group,(channel-1)//3,(channel-1)%3)
        self.reflow_bank();self.finish()
        self.bank_status.setText(f'整套设备：{len(self.boards)}块已启用电阻板，{len(self.routes)}个有效通道')

    def remember_value(self,channel,value):
        if channel in self.routes:
            board,local=self.routes[channel];self.channel_values[board.setting['key'],local]=value

    def save(self):
        if not self.bank_ready or self.task.isRunning():return
        cfg=dict(self.manager.db_manager.load_sys_config() or {})
        saved=dict(cfg.get('rn_boards',{}));numbers=set()
        addresses={(s['ip'],s['port']) for s in board_settings(cfg) if s['enabled'] and s['kind']!='resistor'}
        try:
            for row,(setting,fields) in enumerate(zip(self.settings,self.fields)):
                ip,port,unit,profile=fields
                enabled=self.config.item(row,0).checkState()==Qt.Checked
                number=self.config.cellWidget(row,1).value();address=(ip.text().strip(),port.value())
                if enabled:
                    ipaddress.IPv4Address(address[0])
                    if number in numbers:raise ValueError('已启用电阻板的序号不能重复')
                    if address in addresses:raise ValueError('已启用板卡的IP/端口重复')
                    numbers.add(number);addresses.add(address)
                saved[setting['key']]=dict(ip=address[0],port=port.value(),unit=unit.value(),
                    enabled=enabled,limit=profile.currentData(),number=number)
        except ValueError as exc:
            QMessageBox.warning(self,'配置错误',str(exc));return
        cfg['rn_boards']=saved
        if not self.manager.db_manager.save_sys_config(cfg):
            QMessageBox.warning(self,'保存失败','板卡配置未保存');return
        boards=list(self.boards.values());self.set_bank_busy(True)
        def apply():
            for board in boards:board.disconnect()
        def completed(ok,value):
            if not ok:self.bank_status.setText('配置已保存，但断开旧连接失败：'+str(value));return
            self.boards.clear()
            self.settings=[s for s in board_settings(cfg) if s['kind']=='resistor']
            self.rebuild();self.tabs.setCurrentIndex(1)
        self.task.run(apply,completed)

    def active_channels(self):return sorted(self.routes)
    def selected_channels(self):return [ch for ch,widgets in self.bank_widgets.items() if widgets[1].isChecked() and ch in self.routes]
    def select_channels(self,state):
        for channel,widgets in self.bank_widgets.items():widgets[1].setChecked(state and channel in self.routes)

    def set_bank_busy(self,busy):
        self.save_button.setEnabled(not busy);self.config.setEnabled(not busy)
        for button in self.bank_buttons:button.setEnabled(not busy and bool(self.routes))
        self.bank_resistance.setEnabled(not busy)
        for channel,(_,check,value,_,apply,read) in self.bank_widgets.items():
            for control in (check,value,apply,read):control.setEnabled(not busy and channel in self.routes)

    def finish(self):
        if not self.bank_ready:return super().finish()
        self.set_bank_busy(False)

    def run_bank(self,operation,channels,value=0):
        if self.closing or self.task.isRunning():return
        if not channels:self.bank_status.setText('没有勾选有效通道或没有已启用设备');return
        self.set_bank_busy(True);self.bank_status.setText('正在执行…')
        def completed(ok,result):
            if not ok:self.bank_status.setText('失败：'+str(result));return
            for channel,actual in result['values'].items():
                self.bank_widgets[channel][3].setText(f'回读：{actual:,} Ω')
            for channel,error in result['errors'].items():
                self.bank_widgets[channel][3].setText('失败：'+error)
            if operation=='disconnect':
                for widgets in self.bank_widgets.values():widgets[3].setText('未连接')
            failures='；'.join(f'CH{ch}：{error}' for ch,error in result['errors'].items())
            self.bank_status.setText(f'{"完成" if result["ok"] else "部分失败"} · 耗时{result["elapsed"]:.2f}s'+(' · '+failures if failures else ''))
        self.task.run(lambda:operate_channels(self.boards,channels,operation,value),completed)

    def eventFilter(self,watched,event):
        if self.bank_ready and watched is self.bank_scroll.viewport() and event.type()==QEvent.Resize:self.reflow_bank()
        return super().eventFilter(watched,event)

    def reflow_bank(self):
        columns=max(1,(self.bank_scroll.viewport().width()-30)//232)
        for column in range(max(self.bank_columns,self.bank_grid.columnCount())):
            self.bank_grid.setColumnStretch(column,0);self.bank_grid.setColumnMinimumWidth(column,0)
        for position,widgets in enumerate(self.bank_widgets.values()):
            self.bank_grid.removeWidget(widgets[0]);self.bank_grid.addWidget(widgets[0],position//columns,position%columns)
        for column in range(columns):self.bank_grid.setColumnStretch(column,1)
        self.bank_columns=columns
