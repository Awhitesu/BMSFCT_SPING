from devices.rn_board import shared_board
import ipaddress
from copy import deepcopy
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QGroupBox,
    QLabel,QPushButton,QSpinBox,QCheckBox,QScrollArea,QSizePolicy,QMessageBox,QComboBox,QTableWidgetItem,QHeaderView)
from ui.tabs.board_debug_tab import BoardDebugTab
from devices.rn_board import board_settings
from devices.resistor_bank import channel_routes,operate_channels


class ResistorBankTab(BoardDebugTab):
    def __init__(self,manager):
        self.bank_ready=False
        super().__init__(manager,'resistor')
        self.config.setColumnCount(9)
        self.config.setHorizontalHeaderLabels(['启用','设备序号','IP地址','端口','Unit ID','通道数量','协议/量程','有效全局通道','配置'])
        self.tabs.setTabText(0,'设备设置（最多6张）')
        self.save_button.setText('保存并应用电阻卡配置')
        self.tabs.widget(0).layout().itemAt(0).widget().setText('最多6张，每张固定6通道。已启用卡按设备序号排序，连续编号；未启用卡不占号。修改后保存并应用生效。')
        for row,setting in enumerate(self.settings):
            sequence=QSpinBox();sequence.setRange(1,6);sequence.setValue(setting['number'])
            sequence.setMinimumHeight(40)
            sequence.setToolTip(f"{setting['key']}：序号决定全局通道，每块固定占6通道")
            self.config.setCellWidget(row,1,sequence)
            self.config.setItem(row,7,QTableWidgetItem())
            self.config.setItem(row,8,QTableWidgetItem(str(row+1)))
            for column in (7,8):self.config.item(row,column).setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable)
            sequence.valueChanged.connect(self.refresh_channel_ranges)
        self.config.itemChanged.connect(lambda item:self.refresh_channel_ranges() if item.column()==0 else None)
        header=self.config.horizontalHeader()
        for position,logical in enumerate((0,8,2,3,1,5,7,4,6)):
            header.moveSection(header.visualIndex(logical),position)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2,QHeaderView.Stretch)
        self.config.setColumnHidden(4,True);self.config.setColumnHidden(6,True)
        advanced=QPushButton('高级参数（Unit ID / 协议）');advanced.setCheckable(True)
        advanced.toggled.connect(lambda show:[self.config.setColumnHidden(c,not show) for c in (4,6)])
        self.tabs.widget(0).layout().insertWidget(1,advanced)
        self.refresh_channel_ranges()
        old=self.tabs.widget(1);self.tabs.removeTab(1);old.deleteLater()
        page=QWidget();body=QVBoxLayout(page)
        self.bank_buttons=[];self.bank_widgets={};self.bank_columns=0
        row=QHBoxLayout()
        self.bank_status=QLabel();self.bank_status.setWordWrap(True);row.addWidget(self.bank_status,1)
        self.add_button(row,'连接并回读整套设备',lambda:self.run_bank('read',self.active_channels()))
        self.add_button(row,'断开整套设备',lambda:self.run_bank('disconnect',self.active_channels()))
        body.addLayout(row)
        selector_row=QHBoxLayout();selector_row.addWidget(QLabel('全局通道：'))
        self.bank_selection=QComboBox();self.bank_selection.setEditable(True)
        self.bank_selection.addItems(['所有','所有奇数','所有偶数'])
        self.bank_selection.setToolTip('填写7、1,7,13、7-12，或选择所有/奇数/偶数；按硬件排序序号映射CH1～36')
        selector_row.addWidget(self.bank_selection,1)
        self.add_button(selector_row,'按编号勾选',self.select_by_number)
        body.addLayout(selector_row)
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
        tip=QLabel('按已启用卡的设备序号连续编号，未启用卡不占号；范围见设备设置。0–1.5MΩ、10Ω步进；回读为设定值，不是实测阻值。')
        tip.setWordWrap(True);body.addWidget(tip)
        self.bank_scroll=QScrollArea();self.bank_scroll.setWidgetResizable(True)
        self.bank_scroll.viewport().installEventFilter(self)
        panel=QWidget();self.bank_grid=QGridLayout(panel);self.bank_grid.setSpacing(12)
        self.bank_grid.setAlignment(Qt.AlignTop);self.bank_scroll.setWidget(panel);body.addWidget(self.bank_scroll,1)
        self.tabs.addTab(page,'整套电阻控制（CH1–36）')
        self.bank_ready=True;self.rebuild();self.tabs.setCurrentIndex(1)

    def refresh_channel_ranges(self,*_):
        rows=[row for row in range(len(self.settings)) if self.config.item(row,0).checkState()==Qt.Checked]
        numbers=[self.config.cellWidget(row,1).value() for row in rows]
        for row in range(len(self.settings)):
            self.config.item(row,7).setText('未启用')
        if len(numbers)!=len(set(numbers)):
            for row in rows:self.config.item(row,7).setText('序号重复')
            return
        for offset,row in enumerate(sorted(rows,key=lambda r:self.config.cellWidget(r,1).value())):
            self.config.item(row,7).setText(f'{offset*6+1}–{offset*6+6}')

    def add_button(self,row,text,callback):
        button=QPushButton(text);button.setMinimumHeight(40);button.clicked.connect(callback)
        row.addWidget(button);self.bank_buttons.append(button);return button

    def rebuild(self):
        if not self.bank_ready:return super().rebuild()
        for setting in self.settings:
            if setting['enabled'] and setting['key'] not in self.boards:
                self.boards[setting['key']]=shared_board(self.manager,setting)
        self.routes=channel_routes(self.boards)
        while self.bank_grid.count():
            item=self.bank_grid.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.bank_widgets={}
        for channel in range(1,37):
            route=self.routes.get(channel)
            group=QGroupBox(f'CH{channel} · 序号{route[0].setting["number"]} CH{route[1]}' if route else f'CH{channel} · 未启用')
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
        current = [s for s in board_settings(cfg) if s['kind'] == 'resistor']
        if current != self._loaded_settings:
            self.reload_saved_config()
            QMessageBox.warning(self, '配置已更新', '板卡配置已在其他页面修改，已刷新显示，请核对后再保存。')
            return
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
                saved[setting['key']]=dict(saved.get(setting['key'], {}), ip=address[0],port=port.value(),unit=unit.value(),
                    enabled=enabled,limit=profile.currentData(),number=number)
        except ValueError as exc:
            QMessageBox.warning(self,'配置错误',str(exc));return
        cfg['rn_boards']=saved
        if not self.manager.db_manager.save_sys_config(cfg):
            QMessageBox.warning(self,'保存失败','板卡配置未保存');return
        self._loaded_settings = deepcopy([s for s in board_settings(cfg) if s['kind'] == 'resistor'])
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

    def select_by_number(self):
        from devices.resistor_bank import select_channels
        try:channels=set(select_channels(self.bank_selection.currentText(),self.routes))
        except ValueError as exc:self.bank_status.setText(str(exc));return
        for channel,widgets in self.bank_widgets.items():widgets[1].setChecked(channel in channels)
        self.bank_status.setText('已勾选全局通道：'+', '.join(map(str,sorted(channels))))

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
