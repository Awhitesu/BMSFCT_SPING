from copy import deepcopy
from PySide6.QtCore import Qt,QTimer,QAbstractListModel,QModelIndex
from PySide6.QtWidgets import (QWidget,QGroupBox,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QFileDialog,QMessageBox,QComboBox,QDoubleSpinBox,QCheckBox,QLineEdit,
    QTableWidget,QTableWidgetItem,QHeaderView,QSpinBox,QStyledItemDelegate,QStyleOptionViewItem,QToolTip)
from ui.device_task import DeviceTask
from core.dbc_signals import import_dbc,catalog,decode_step,resolve_step


class DBCImportForm(QGroupBox):
    def __init__(self,parent=None):
        super().__init__('配方DBC（选填；没有DBC的项目留空）',parent)
        self.asset=None;self.task=DeviceTask(self);self._revision=0
        layout=QHBoxLayout(self);self.label=QLabel('未导入DBC');self.label.setWordWrap(True);self.label.setTextFormat(Qt.PlainText)
        self.browse=QPushButton('导入DBC…');self.clear=QPushButton('移除DBC')
        layout.addWidget(self.label,1);layout.addWidget(self.browse);layout.addWidget(self.clear)
        self.browse.clicked.connect(self.select);self.clear.clicked.connect(lambda:self.set_asset(None))

    def set_asset(self,asset):
        self._revision+=1;self.asset=deepcopy(asset) if asset else None
        self.label.setText(f'{asset.get("name","DBC")}（随配方保存；选择运行配方后开始监控）' if asset else '未导入DBC；不影响普通CAN及无CAN配方')

    def select(self):
        if self.task.isRunning():return
        path,_=QFileDialog.getOpenFileName(self,'导入配方DBC','','DBC文件 (*.dbc)')
        if not path:return
        revision=self._revision;self.browse.setEnabled(False)
        def done(ok,value):
            self.browse.setEnabled(True)
            if revision!=self._revision:return
            if ok:self.set_asset(value)
            else:QMessageBox.warning(self,'DBC导入失败',str(value))
        self.task.run(lambda:import_dbc(path),done)


class _SignalModel(QAbstractListModel):
    def __init__(self,rows,parent):super().__init__(parent);self.rows=rows
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid() or not 0<=index.row()<len(self.rows):return None
        row=self.rows[index.row()]
        if role in (Qt.DisplayRole,Qt.EditRole,Qt.ToolTipRole):return row['label']
        if role==Qt.UserRole:return row


class DBCStepForm(QWidget):
    def __init__(self,asset,parent=None,*,test_item_name=None):
        super().__init__(parent);self.asset=deepcopy(asset)
        self._test_item_name=test_item_name
        from core.dbc_groups import test_item_prefix
        self._group_prefix=test_item_prefix(test_item_name or '')
        self.task=DeviceTask(self);self._loaded=False;self._pending=None
        form=QFormLayout(self);self.signal=QComboBox();self.signal.setEditable(True)
        self.signal.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.signal.setMinimumContentsLength(30)
        self.signal.setInsertPolicy(QComboBox.NoInsert);self.signal.setMaxVisibleItems(25)
        self.signal.completer().setFilterMode(Qt.MatchContains)
        self.signal.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.info=QLabel('');self.info.setWordWrap(True);self.info.setTextFormat(Qt.PlainText)
        self.timeout=QDoubleSpinBox();self.timeout.setRange(.01,3600);self.timeout.setValue(5);self.timeout.setSuffix(' s')
        self.interval=QDoubleSpinBox();self.interval.setRange(.01,60);self.interval.setValue(.01);self.interval.setSuffix(' s')
        self.max_age=QDoubleSpinBox();self.max_age.setRange(.01,3600);self.max_age.setValue(1);self.max_age.setSuffix(' s')
        self.max_age.setToolTip('采样组只在保存整组快照时检查有效期；保存后不再因时间流逝而失效。')
        self.timeout.setToolTip('采样组等待上限取本组参与信号中最短的超时。')
        self.new_frame=QCheckBox('仅使用本工步开始后的新报文（特殊读取）');self.new_frame.setChecked(False)
        self.new_frame.setToolTip('组外特殊读取使用；起终点范围内统一使用整组快照。')
        self.group=QLineEdit();self.group.setMaxLength(128)
        self.group.setReadOnly(bool(self._group_prefix))
        self.group.setPlaceholderText('自动取测试项前缀' if self._group_prefix else '请输入采样组名称（必填）；起点、终点保持同名')
        self.group.setToolTip('有测试项前缀时自动同步；没有前缀时可手动填写组名。起点、终点必须同名，起点兼终点也必须填写组名。')
        self.group_role=QComboBox()
        for label,value in [('不标记（组内自动归组）',''),('起点','start'),('终点','end'),('起点兼终点','single')]:
            self.group_role.addItem(label,value)
        self.group.textChanged.connect(self._group_changed)
        self.group_role.currentIndexChanged.connect(self._group_role_changed)
        self.group.setEnabled(False)
        self.variable=QLineEdit();self.variable.setPlaceholderText('选填，例如 BMS_SOC；供后续变量读取或公式计算使用')
        tip=QLabel('普通读取使用后台最新有效值。采样组只在首尾设置同名起点、终点，中间DBC工步自动归组；起点等待全组新数据且全部未过期后保存固定快照，再逐项读取判定。组内延时、设置及后台更新均不改变快照；有效期仅在保存时检查。后台解码不发送报文。');tip.setWordWrap(True)
        form.addRow(tip);form.addRow('DBC信号：',self.signal);form.addRow(self.info)
        form.addRow('等待有效信号超时：',self.timeout)
        form.addRow('检查周期：',self.interval);form.addRow('数据有效期：',self.max_age);form.addRow(self.new_frame)
        form.addRow('采样组位置：',self.group_role)
        form.addRow('采样组名称：',self.group)
        form.addRow('保存到变量：',self.variable)
        self.signal.currentIndexChanged.connect(self.show_info);self.show_info()

    def ensure_loaded(self):
        if self._loaded or self.task.isRunning():return
        if not self.asset:
            self._loaded=True;self.info.setText('当前配方未导入可用DBC；请先导入。');return
        self.info.setText('正在后台加载DBC信号…');self.signal.setEnabled(False)
        def done(ok,value):
            self._loaded=True;self.signal.setEnabled(True)
            if not ok:self.info.setText('DBC错误：'+str(value));return
            self.signal.setModel(_SignalModel(value,self.signal))
            self.signal.completer().setFilterMode(Qt.MatchContains)
            self.signal.completer().setCaseSensitivity(Qt.CaseInsensitive)
            self.show_info()
            if self._pending:self._load_config(self._pending)
        self.task.run(lambda:catalog(self.asset),done)

    def show_info(self):
        row=self.signal.currentData()
        if row:self.info.setText(f'CAN ID 0x{row["frame_id"]:X} · {"扩展帧" if row["extended"] else "标准帧"} · 实际值单位 {row["unit"] or "无"} · DBC定义范围 [{row["minimum"]}, {row["maximum"]}]（测试上下限按实际值填写）')

    def config(self):
        if not self._loaded:raise ValueError('DBC信号正在加载，请稍后再确认')
        index=self.signal.findText(self.signal.currentText(),Qt.MatchExactly)
        row=self.signal.itemData(index) if index>=0 else None
        if not row or not self.asset:raise ValueError('请从当前配方DBC中选择有效信号')
        variable=self.variable.text().strip()
        if variable and (not variable.isidentifier() or variable.startswith('__')):raise ValueError('变量名应为有效标识符，例如 BMS_SOC')
        result=dict(row,sha256=self.asset['sha256'],output='value',variable=variable,
                    timeout=self.timeout.value(),interval=self.interval.value(),max_age=self.max_age.value(),new_frame=self.new_frame.isChecked())
        if self.group.text().strip():result['group']=self.group.text().strip()
        role=self.group_role.currentData()
        if role and role!='legacy':
            if not self.group.text().strip() and self._test_item_name is None:raise ValueError('采样组起点/终点必须填写组名')
            # The editor may hold an incomplete marker; recipe save and runtime remain strict.
            result['group']=self.group.text().strip()
            result['group_role']=role
        return result

    def _group_changed(self):
        grouped=bool(self.group.text().strip() or self.group_role.currentData())
        if grouped:self.new_frame.setChecked(False)
        self.new_frame.setEnabled(not grouped)

    def _group_role_changed(self):
        marked=bool(self.group_role.currentData())
        self.group.setEnabled(marked)
        if not marked:self.group.clear()
        elif self._group_prefix:
            self.group.setText(self._group_prefix)
        self._group_changed()

    def load(self,text):
        c=decode_step(text,allow_empty_group=True)
        self._pending=c
        self.ensure_loaded()
        if self._loaded:self._load_config(c)

    def _load_config(self,c):
        try:
            resolved=resolve_step(dict(c,output='value'),self.asset)
            index=self.signal.findText(resolved['label'],Qt.MatchExactly)
            error=''
        except ValueError as exc:index=-1;error=str(exc)
        self.signal.setCurrentIndex(index)
        if index<0:
            self.signal.setCurrentIndex(-1);self.signal.setEditText('原DBC信号已失效，请重新选择')
            self.info.setText(error or '当前DBC中找不到对应信号')
        for widget,key in [(self.timeout,'timeout'),(self.interval,'interval'),(self.max_age,'max_age')]:widget.setValue(c[key])
        self.new_frame.setChecked(bool(c.get('new_frame',True)))
        role=c.get('group_role','')
        if c.get('group') and not role:
            role='legacy'
            if self.group_role.findData(role)<0:self.group_role.addItem('旧版同名分组（保存配方时转换）',role)
        self.group_role.setCurrentIndex(self.group_role.findData(role))
        self.group.setText(c.get('group',''))
        if self._test_item_name is not None:self._group_role_changed()
        self.variable.setText(c.get('variable',''))


class DBCStepDelegate(QStyledItemDelegate):
    """Show readable sampling boundaries while retaining the exact editable parameter text."""
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        text=index.data(Qt.DisplayRole)
        if not isinstance(text,str) or not text.startswith('DBC64:'):return
        try:config=decode_step(text,allow_empty_group=True)
        except (ValueError,TypeError,KeyError):return
        role={'start':'起点','end':'终点','single':'起点兼终点'}.get(config.get('group_role'))
        prefix=f'【{role}】{config.get("group") or "（名称为空）"} · ' if role else ''
        option.text=prefix+f'{config["message"]}.{config["signal"]}'

    def helpEvent(self,event,view,option,index):
        text=index.data(Qt.DisplayRole)
        if isinstance(text,str) and text.startswith('DBC64:'):
            readable=QStyleOptionViewItem(option)
            self.initStyleOption(readable,index)
            QToolTip.showText(event.globalPos(),readable.text,view)
            return True
        return super().helpEvent(event,view,option,index)


class DBCMonitorTab(QWidget):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self._signature=None;self._rows=[]
        layout=QVBoxLayout(self);self.status=QLabel('选择已导入DBC的运行配方后，自动监控。');self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);layout.addWidget(self.status)
        row=QHBoxLayout();layout.addLayout(row)
        self.search=QLineEdit();self.search.setPlaceholderText('按信号名、报文名或ID搜索…');row.addWidget(self.search,1)
        self.channel=QComboBox();self.channel.addItem('未选择设备',None);self.channel.setEnabled(False);row.addWidget(QLabel('物理通道'));row.addWidget(self.channel)
        self.age=QDoubleSpinBox();self.age.setRange(.01,3600);self.age.setValue(1);self.age.setSuffix(' s');row.addWidget(QLabel('显示有效期'));row.addWidget(self.age)
        self.page=QSpinBox();self.page.setRange(1,1);self.page.setPrefix('第 ');self.page.setSuffix(' 页');row.addWidget(self.page)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['报文 / ID','信号','物理值','枚举含义','单位','数据年龄(ms)','状态'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.setWordWrap(False)
        # ResizeToContents remeasures the entire visible model after every
        # replacement. Updating 500 rows then becomes quadratic on page changes.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column,width in enumerate((230,240,110,150,70,125,90)):self.table.setColumnWidth(column,width)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);layout.addWidget(self.table,1)
        self.search.textChanged.connect(self._filter_changed);self.channel.currentIndexChanged.connect(self.rebuild);self.page.valueChanged.connect(self.rebuild)
        self.timer=QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.refresh)

    def rebuild(self):
        service=self.manager.dbc_monitor;asset=service.asset
        text=self.search.text().casefold()
        rows=[r for r in catalog(asset) if text in r['label'].casefold()]
        self._total=len(rows);self.page.blockSignals(True);self.page.setMaximum(max(1,(len(rows)+499)//500));self.page.blockSignals(False)
        start=(self.page.value()-1)*500;self._rows=rows[start:start+500]
        self.table.setUpdatesEnabled(False);self.table.setRowCount(0);self.table.setRowCount(len(self._rows))
        for i,r in enumerate(self._rows):
            for j,value in enumerate([f'{r["message"]} / 0x{r["frame_id"]:X}',r['signal'],'--','--',r['unit'],'--','未收到']):
                item=QTableWidgetItem(value);item.setToolTip(r['label']);self.table.setItem(i,j,item)
        self.table.setUpdatesEnabled(True)

    def refresh(self):
        if not self.isVisible():return
        service=self.manager.dbc_monitor;driver=getattr(self.manager,'pcan',None)
        connected=bool(getattr(driver,'is_connected',False))
        driver_channel=getattr(driver,'channel',None)
        enabled=getattr(getattr(driver,'bus',None),'enabled_channels',set(range(4)))
        enabled=tuple(sorted(enabled)) if isinstance(enabled,(set,list,tuple)) else tuple(range(4))
        asset=service.asset;signature=((asset or {}).get('sha256'),driver_channel,connected,enabled)
        if signature!=self._signature:
            self._signature=signature;self.channel.blockSignals(True);self.channel.clear()
            if connected and driver_channel:
                channels=[f'ZLG_CAN{i+1}' for i in enabled if 0<=i<4] if str(driver_channel).startswith('ZLG_CAN') else [driver_channel]
                for channel in channels:self.channel.addItem(channel,channel)
                index=self.channel.findData(driver_channel)
                if index>=0:self.channel.setCurrentIndex(index)
                if not channels:self.channel.addItem('没有已启用通道',None)
            else:
                self.channel.addItem('未选择设备',None)
            self.channel.setEnabled(connected and bool(driver_channel));self.channel.blockSignals(False);self.rebuild()
        values=service.snapshot() if connected else {};interface=self.channel.currentData()
        self.status.setText(f'配方 {service.context_key or "--"} · {(asset or {}).get("name","未加载DBC")} · 显示{len(self._rows)}/{getattr(self,"_total",0)}个信号（每页500，可搜索或翻页） · '+('CAN已连接' if getattr(driver,'is_connected',False) else 'CAN未连接')+(' · '+service.last_error if service.last_error else ''))
        for i,r in enumerate(self._rows):
            value=values.get((interface,r['frame_id'],r['extended'],r['signal']))
            cells={2:str(value['value']) if value else '--',3:value['choice'] if value else '--',
                   5:str(round(value['age']*1000)) if value else '--',6:('有效' if value['age']<=self.age.value() else '过期') if value else '未收到'}
            for j,text in cells.items():
                item=self.table.item(i,j)
                if item.text()!=text:item.setText(text)

    def _filter_changed(self):
        self.page.blockSignals(True);self.page.setValue(1);self.page.blockSignals(False);self.rebuild()

    def showEvent(self,event):
        self.refresh();self.timer.start();super().showEvent(event)

    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)
