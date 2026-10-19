from devices.rn_board import shared_board
import ipaddress
from copy import deepcopy
from PySide6.QtCore import Qt,QTimer
from core.board_control import control_board
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QTableWidget,
    QTableWidgetItem,QLineEdit,QSpinBox,QComboBox,QLabel,QPushButton,QHeaderView,QMessageBox,
    QGridLayout,QScrollArea,QGroupBox)
from devices.rn_board import board_settings
from ui.device_task import DeviceTask


class BoardDebugTab(QWidget):
    def __init__(self,manager,kind=None):
        super().__init__()
        self.manager=manager
        self.kind=kind
        self.task=DeviceTask(self)
        self.task.finished.connect(self.finish)
        self.polling=False
        self.pending_action=None
        self.closing=False
        self.relay_buttons=[]
        self._relay_states=None
        self.boards={}
        self.channel_values={}
        self.channel_widgets=[]
        self.settings=[s for s in board_settings(manager.db_manager.load_sys_config() or {}) if kind is None or s['kind']==kind]
        self._loaded_settings = deepcopy(self.settings)
        layout=QVBoxLayout(self)
        self.tabs=QTabWidget()
        layout.addWidget(self.tabs)
        page=QWidget(); body=QVBoxLayout(page)
        tip=QLabel('共14块板卡。每块单独填写IP；启用的板卡不能重复IP/端口。电阻板设置上限1.5MΩ；绝缘板按标签选择5MΩ或30MΩ型号。')
        tip.setWordWrap(True); body.addWidget(tip)
        if kind:
            name=self.settings[0]['name'].rsplit(' ',1)[0]
            tip.setText(f'{name}：共{len(self.settings)}块。分别配置IP，选择本类板卡进行调试。'+
                        ('每块6通道，设置范围0–1,500,000Ω，10Ω步进。' if kind=='resistor' else
                         '单通道，按每块板标签选择5MΩ或30MΩ，1kΩ步进；协议不能自动区分量程。' if kind=='insulation' else
                         f'每块{self.settings[0]["channels"]}路继电器，支持单路和批量控制、状态回读。'))
        self.config=QTableWidget(len(self.settings),7)
        self.config.setStyleSheet('QTableWidget::item { padding: 0px; }')
        self.config.setHorizontalHeaderLabels(['启用','板卡','IP','端口','Unit ID','通道数','电阻协议/量程'])
        self.config.verticalHeader().setVisible(False)
        self.config.verticalHeader().setDefaultSectionSize(54)
        self.config.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.config.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.fields=[]
        for row,s in enumerate(self.settings):
            item=QTableWidgetItem(); item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if s['enabled'] else Qt.Unchecked); self.config.setItem(row,0,item)
            for col,value in ((1,s['name']),(5,s['channels'])):
                item=QTableWidgetItem(str(value)); item.setFlags(item.flags()&~Qt.ItemIsEditable); self.config.setItem(row,col,item)
            ip=QLineEdit(s['ip']); port=QSpinBox(); port.setRange(1,65535); port.setValue(s['port'])
            ip.setMinimumWidth(150)
            unit=QSpinBox(); unit.setRange(0,255); unit.setValue(s['unit'])
            profile=QComboBox()
            if s['kind']=='insulation':
                for text,value in [('请选择板卡型号',0),('绝缘卡5MΩ',5000000),('绝缘卡30MΩ',30000000)]: profile.addItem(text,value)
            elif s['kind']=='resistor': profile.addItem('6通道 / 0–1.5MΩ / 10Ω步进',1500000)
            else: profile.addItem('线圈协议',0)
            index=profile.findData(s['limit']); profile.setCurrentIndex(max(0,index))
            for col,control in ((2,ip),(3,port),(4,unit),(6,profile)):
                control.setMinimumHeight(40); self.config.setCellWidget(row,col,control)
            self.fields.append((ip,port,unit,profile))
        body.addWidget(self.config,1)
        self.save_button=QPushButton('保存并应用板卡配置'); self.save_button.setMinimumHeight(42)
        self.save_button.clicked.connect(self.save); body.addWidget(self.save_button)
        self.tabs.addTab(page,f'连接配置（{len(self.settings)}块）')
        run=QWidget(); body=QVBoxLayout(run)
        row=QHBoxLayout(); self.select=QComboBox(); self.select.setMinimumHeight(42); row.addWidget(self.select,1)
        self.buttons=[]
        self.button(row,'连接',self.connect_board); self.button(row,'断开',self.disconnect_board)
        body.addLayout(row)
        self.status=QLabel(); self.status.setWordWrap(True); body.addWidget(self.status)
        self.select.currentIndexChanged.connect(self.refresh)
        row=QHBoxLayout(); self.relay=QSpinBox(); self.relay.setPrefix('K'); row.addWidget(self.relay)
        self.button(row,'吸合',lambda:self.relay_action(True)); self.button(row,'释放',lambda:self.relay_action(False))
        self.button(row,'全部吸合',lambda:self.all_action(True)); self.button(row,'全部释放',lambda:self.all_action(False))
        body.addLayout(row); self.relay_controls=list(self.buttons[2:])
        if kind in ('resistor','insulation'):
            for i in range(row.count()): row.itemAt(i).widget().hide()
        row=QHBoxLayout(); self.resistance=QSpinBox(); self.resistance.setRange(0,30000000)
        self.resistance.setSingleStep(1000); self.resistance.setSuffix(' Ω'); row.addWidget(self.resistance)
        self.res_button=self.button(row,'设置阻值',self.res_action)
        self.output_controls=[]
        if kind=='insulation':
            self.output_controls=[self.button(row,'开启输出',lambda:self.output_action(True)),
                                  self.button(row,'关闭输出',lambda:self.output_action(False))]
        body.addLayout(row)
        if kind and kind not in ('resistor','insulation'):
            self.resistance.hide(); self.res_button.hide()
        label=QLabel('绝缘板按1kΩ步进。设置非0阻值会立即开启输出；设置0Ω或关闭输出使K19隔离输出。回读是设定值及固件输出状态，不是万用表实测值。')
        label.setWordWrap(True); body.addWidget(label)
        if kind and kind not in ('resistor','insulation'): label.hide()
        if kind=='resistor':
            self.resistance.hide(); self.res_button.hide()
            label.setText('各板各通道独立设置。0Ω为有效阻值；禁用通道约为1.67MΩ，并非物理开路。回读是设定值，不是实测阻值。')
        elif kind and kind!='insulation':
            label.setText('回读为板卡保存的控制状态，不是触点实际反馈。'); label.show()
        self.channel_area=QScrollArea(); self.channel_area.setWidgetResizable(True)
        self.channel_panel=QWidget(); self.channel_grid=QGridLayout(self.channel_panel)
        self.channel_area.setWidget(self.channel_panel)
        if kind and kind!='insulation': body.addWidget(self.channel_area,2)
        else: self.channel_area.hide()
        if kind and kind not in ('resistor','insulation'):
            self.relay.hide()
            for button in self.relay_controls[:2]: button.hide()
        row=QHBoxLayout(); self.button(row,'回读状态',self.read); self.button(row,'读取设备信息',self.info); body.addLayout(row)
        self.results=QTableWidget(0,2); self.results.setHorizontalHeaderLabels(['通道/参数','状态/值'])
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers); body.addWidget(self.results,1)
        if kind in ('relay','hv','hsd','dmm'):
            self.results.hide()
            self.buttons[-2].hide()
        self.tabs.addTab(run,'板卡控制与回读')
        self.rebuild()
        self.poll_timer=QTimer(self)
        self.poll_timer.timeout.connect(self.poll_relays)
        if kind in ('relay','hv','hsd','dmm'):
            self.poll_timer.setInterval(500)
            self.tabs.setCurrentIndex(1)

    def button(self,row,text,callback):
        button=QPushButton(text); button.setMinimumHeight(42); button.clicked.connect(callback)
        row.addWidget(button); self.buttons.append(button); return button

    def reload_saved_config(self):
        settings = [s for s in board_settings(self.manager.db_manager.load_sys_config() or {})
                    if self.kind is None or s['kind'] == self.kind]
        if settings == self._loaded_settings:
            return
        self.settings = settings
        self._loaded_settings = deepcopy(settings)
        self.boards.clear()
        for row, (s, fields) in enumerate(zip(settings, self.fields)):
            ip, port, unit, profile = fields
            self.config.item(row,0).setCheckState(Qt.Checked if s['enabled'] else Qt.Unchecked)
            ip.setText(s['ip']); port.setValue(s['port']); unit.setValue(s['unit'])
            profile.setCurrentIndex(max(0, profile.findData(s['limit'])))
            sequence = self.config.cellWidget(row,1)
            if s['kind'] == 'resistor' and sequence is not None:
                sequence.setValue(s['number'])
        self.rebuild()

    def rebuild(self):
        selected=self.select.currentData(); self.select.blockSignals(True); self.select.clear()
        for setting in self.settings:
            if setting['enabled']:
                self.boards[setting['key']]=shared_board(self.manager,setting)
                self.select.addItem(setting['name'],setting['key'])
        index=self.select.findData(selected); self.select.setCurrentIndex(max(0,index)); self.select.blockSignals(False)
        self.refresh()

    def build_channels(self,board):
        while self.channel_grid.count():
            item=self.channel_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.channel_widgets=[]
        self.relay_buttons=[]
        self._relay_states=None
        if not board or self.kind=='insulation': return
        key=board.setting['key']
        for channel in range(1,board.setting['channels']+1):
            group=QGroupBox(f'CH{channel}'); grid=QGridLayout(group)
            state=QLabel('未回读'); grid.addWidget(state,0,0,1,2)
            if self.kind=='resistor':
                value=QSpinBox(); value.setRange(0,board.setting['limit']); value.setSingleStep(10); value.setSuffix(' Ω')
                value.setValue(self.channel_values.get((key,channel),0))
                value.valueChanged.connect(lambda v,k=key,c=channel:self.channel_values.__setitem__((k,c),v))
                grid.addWidget(value,1,0,1,2)
                apply=QPushButton('设置阻值'); read=QPushButton('回读')
                apply.clicked.connect(lambda checked=False,c=channel,v=value:self.res_channel_action(c,v.value()))
                read.clicked.connect(lambda checked=False,c=channel:self.read_channel(c))
            else:
                group.setTitle('')
                state.hide()
                apply=QPushButton(f'K{channel:02d}\n未回读'); read=QPushButton(); read.hide()
                apply.setProperty('multilineTile',True)
                apply.setCheckable(True); apply.setMinimumHeight(72)
                apply.clicked.connect(lambda checked=False,c=channel:self.run(f'K{c}控制',lambda b:control_board(b,'relay',channel=c,state=checked)))
                self.relay_buttons.append(apply)
            for button in (apply,read): button.setMinimumHeight(40)
            grid.addWidget(apply,2,0,1,2 if self.kind!='resistor' else 1); grid.addWidget(read,2,1)
            columns=3 if self.kind=='resistor' else 6
            self.channel_grid.addWidget(group,(channel-1)//columns,(channel-1)%columns)
            self.channel_widgets.append((state,apply,read))

    def current(self): return self.boards.get(self.select.currentData())

    def refresh(self):
        board=self.current(); self.results.setRowCount(0)
        cancelled_pending=bool(self.pending_action and (self.pending_action[2] is not board
                               or self.pending_action[3]!=board.setting))
        if cancelled_pending:self.pending_action=None
        if not board:
            self.status.setText('请在板卡配置页填写IP并启用设备。')
        else:
            s=board.setting; self.relay.setRange(1,s['channels']); self.resistance.setMaximum(s['limit'])
            self.resistance.setSingleStep(10 if s['kind']=='resistor' else 1000)
            self.status.setText(f'{s["name"]} · {board.ip}:{board.port} · '+('已连接' if board.is_connected else '未连接'))
        if cancelled_pending:
            self.status.setText(self.status.text()+'；已取消前一板卡的排队操作')
        self.build_channels(board)
        for button in self.buttons: button.setEnabled(bool(board))
        resistor=board and board.setting['kind'] in ('resistor','insulation')
        for button in self.relay_controls: button.setEnabled(bool(board) and not resistor)
        self.res_button.setEnabled(bool(resistor and board.setting['limit']))
        self.relay.setEnabled(bool(board) and not resistor); self.resistance.setEnabled(bool(resistor and board.setting['limit']))
        self.update_controls()

    def update_controls(self,busy=False):
        board=self.current()
        for state,on,off in self.channel_widgets:
            on.setEnabled(bool(board and board.is_connected and not busy))
            off.setEnabled(on.isEnabled())
        if board:
            for button in self.output_controls: button.setEnabled(bool(board.is_connected and board.setting['limit'] and not busy))
        if self.relay_buttons and (not board or not board.is_connected):
            self.show_relay_states(None)

    def run(self,label,action,poll=False,expected_setting=None):
        board=self.current()
        if self.closing or not board: return
        setting=dict(board.setting) if expected_setting is None else dict(expected_setting)
        if self.task and self.task.isRunning():
            if not poll and self.polling:
                self.pending_action=(label,action,board,setting)
                self.update_controls(True)
            return
        self.polling=poll
        if not poll:
            for button in self.buttons: button.setEnabled(False)
            self.update_controls(True)
            self.select.setEnabled(False); self.save_button.setEnabled(False)
        def invoke():
            # The shared object can be reconfigured in place by another view.
            # Check the clicked target under the same lock as the actual I/O.
            with board._lock:
                if board.setting!=setting:
                    raise RuntimeError('板卡连接配置已变化，请重新确认后操作')
                return action(board)
        self.task.run(invoke,lambda ok,value:self.result(label,ok,value) if not poll or board is self.current() else None)

    def finish(self):
        self.polling=False
        self.select.setEnabled(True); self.save_button.setEnabled(True)
        board=self.current(); resistor=board and board.setting['kind'] in ('resistor','insulation')
        for b in self.buttons: b.setEnabled(bool(board))
        for b in self.relay_controls: b.setEnabled(bool(board) and not resistor)
        self.res_button.setEnabled(bool(resistor and board.setting['limit']))
        self.update_controls()
        if self.pending_action and not self.closing:
            label,action,target,setting=self.pending_action; self.pending_action=None
            if target is self.current() and target.setting==setting:
                self.run(label,action,expected_setting=setting)
            else:
                self.status.setText('已取消排队操作：板卡选择或连接配置已改变')

    def poll_relays(self):
        if not self.isVisible() or self.tabs.currentIndex()!=1: return
        board=self.current()
        if board and board.is_connected and not self.pending_action:
            self.run('自动回读（500ms）',lambda b:b.read_coils(),poll=True)

    def show_relay_states(self,states):
        snapshot=tuple(states) if states is not None else tuple(None for _ in self.relay_buttons)
        if snapshot==self._relay_states and all(button.isChecked()==bool(value) for button,value in zip(self.relay_buttons,snapshot)): return
        for i,button in enumerate(self.relay_buttons):
            value=snapshot[i] if i<len(snapshot) else None
            if self._relay_states is not None and i<len(self._relay_states) and self._relay_states[i]==value and button.isChecked()==bool(value): continue
            known=value is not None
            enabled=bool(value) if known else False
            button.blockSignals(True); button.setChecked(enabled); button.blockSignals(False)
            button.setText(f'K{i+1:02d}\n'+('ON · 已吸合' if enabled else 'OFF · 已释放' if known else '未知 · 未连接'))
            button.setStyleSheet('QPushButton { background-color: '+('#2563eb' if enabled else '#1e293b')+'; color: #f1f5f9; border: 1px solid #64748b; border-radius: 5px; }')
        self._relay_states=snapshot

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self, 'poll_timer') and not self.task.isRunning():
            self.reload_saved_config()
        if hasattr(self,'poll_timer') and self.kind in ('relay','hv','hsd','dmm'):
            self.poll_timer.start()

    def hideEvent(self,event):
        if hasattr(self,'poll_timer'): self.poll_timer.stop()
        super().hideEvent(event)

    def result(self,label,ok,value):
        if self.polling:
            if ok and isinstance(value,list): self.show_relay_states(value)
            elif not ok:
                self.show_relay_states(None)
                self.status.setText('回读失败：'+str(value))
            return
        if value is None and ok: value='完成'
        self.status.setText(f'{self.select.currentText()} · {label}: {value if ok else "失败 · "+str(value)}')
        if ok and isinstance(value,dict) and 'resistances' in value:
            values=value['resistances']; self.results.setRowCount(len(values))
            for row,actual in enumerate(values):
                self.channel_widgets[row][0].setText(f'设定值回读：{actual:,} Ω')
                self.results.setItem(row,0,QTableWidgetItem(f'CH{row+1}'))
                self.results.setItem(row,1,QTableWidgetItem(f'{actual:,} Ω'))
        elif ok and isinstance(value,dict) and 'channel' in value:
            self.channel_widgets[value['channel']-1][0].setText(f'设定值回读：{value["resistance"]:,} Ω')
        elif ok and isinstance(value,dict) and 'output' in value:
            self.results.setRowCount(2)
            for row,(name,text) in enumerate([('CH1设定值',f'{value["resistance"]:,} Ω'),('CH1输出','开启' if value['output'] else '已隔离/断开')]):
                self.results.setItem(row,0,QTableWidgetItem(name)); self.results.setItem(row,1,QTableWidgetItem(text))
        if ok and isinstance(value,list):
            if value and isinstance(value[0],bool):
                self.show_relay_states(value)
                for (state,on,off),enabled in zip(self.channel_widgets,value):
                    state.setText('已吸合' if enabled else '已释放')
            self.results.setRowCount(len(value))
            for row,item in enumerate(value):
                name=f'K{row+1}' if isinstance(item,bool) else f'0x{0x100+row:04X}'
                self.results.setItem(row,0,QTableWidgetItem(name))
                self.results.setItem(row,1,QTableWidgetItem(('吸合' if item else '释放') if isinstance(item,bool) else f'0x{item:04X}'))

    def connect_board(self):
        def action(b):
            if not b.connect(): raise ConnectionError(b.last_error)
            return b.info
        self.run('连接/身份校验',action)

    def disconnect_board(self): self.run('断开',lambda b:b.disconnect())
    def relay_action(self,state):
        ch=self.relay.value(); self.run('单路继电器',lambda b:control_board(b,'relay',channel=ch,state=state))
    def all_action(self,state): self.run('批量继电器',lambda b:control_board(b,'all_relays',state=state))
    def res_action(self):
        value=self.resistance.value()
        def action(b):
            control_board(b,'resistance',resistance=value)
            return b.read_insulation() if b.setting['kind']=='insulation' else b.read_resistance()
        self.run('设定/回读阻值Ω',action)
    def res_channel_action(self,channel,value):
        self.run(f'CH{channel}设定/回读',lambda b:{'channel':channel,'resistance':control_board(b,'resistance',channel=channel,resistance=value)})
    def read_channel(self,channel):
        self.run(f'CH{channel}回读',lambda b:{'channel':channel,'resistance':b.read_resistance(channel)})
    def output_action(self,enabled):
        value=self.resistance.value(); self.run('开启输出' if enabled else '关闭输出',lambda b:control_board(b,'insulation',state=enabled,resistance=value))
    def read(self): self.run('回读',lambda b:b.read_resistances() if b.setting['kind']=='resistor' else b.read_insulation() if b.setting['kind']=='insulation' else b.read_coils())
    def info(self): self.run('设备信息',lambda b:b.read_registers(0x100,5))

    def save(self):
        if self.task and self.task.isRunning(): return
        cfg=dict(self.manager.db_manager.load_sys_config() or {}); saved=dict(cfg.get('rn_boards', {})); addresses=set()
        current = [s for s in board_settings(cfg) if self.kind is None or s['kind'] == self.kind]
        if current != self._loaded_settings:
            self.reload_saved_config()
            QMessageBox.warning(self, '配置已更新', '硬件管理中心或其他页面已修改板卡配置，已刷新显示，请核对后再保存。')
            return
        for s in board_settings(cfg):
            if self.kind and s['kind']!=self.kind and s['enabled']:
                addresses.add((s['ip'],s['port']))
        used_numbers=set()
        try:
            for row,(s,fields) in enumerate(zip(self.settings,self.fields)):
                ip,port,unit,profile=fields; enabled=self.config.item(row,0).checkState()==Qt.Checked
                address=(ip.text().strip(),port.value())
                if enabled:
                    ipaddress.IPv4Address(address[0])
                    if address in addresses: raise ValueError('已启用板卡IP/端口重复')
                    addresses.add(address)
                saved[s['key']]=dict(saved.get(s['key'], {}), ip=address[0],port=port.value(),unit=unit.value(),enabled=enabled,limit=profile.currentData())
                if s['kind']=='resistor':
                    sequence=self.config.cellWidget(row,1)
                    number=sequence.value() if sequence else s['number']
                    if enabled and number in used_numbers:raise ValueError('已启用电阻板的序号不能重复')
                    if enabled:used_numbers.add(number)
                    saved[s['key']]['number']=number
        except ValueError as exc:
            QMessageBox.warning(self,'配置错误',str(exc)); return
        cfg['rn_boards']=saved
        if not self.manager.db_manager.save_sys_config(cfg):
            QMessageBox.warning(self,'保存失败','板卡配置未保存'); return
        self._loaded_settings = [s for s in board_settings(cfg) if self.kind is None or s['kind'] == self.kind]
        boards=list(self.boards.values())
        self.save_button.setEnabled(False);self.select.setEnabled(False)
        def apply():
            for board in boards:board.disconnect()
        def completed(ok,value):
            if not ok:self.status.setText(str(value));return
            self.boards.clear()
            self.settings=[s for s in board_settings(cfg) if self.kind is None or s['kind']==self.kind]
            self.rebuild();self.tabs.setCurrentIndex(1)
        self.task.run(apply,completed)

    def shutdown(self):
        self.closing=True; self.poll_timer.stop(); self.pending_action=None
        self.task.shutdown()
        # Shared connections belong to DeviceManager, not this view.
