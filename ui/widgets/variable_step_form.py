"""Named variable actions with segment rows and live sample preview."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QLineEdit,QLabel,QPushButton,QTableWidget,QSpinBox,QTreeWidget,QMenu
from core.variable_steps import encode,decode,parse

def catalog(parent):
    sources=[];keys=set();node=parent
    while node is not None:
        tree=getattr(node,'step_tree',None)
        if tree is not None:break
        node=node.parent()
    if node is None:return sources,keys
    for i in range(tree.topLevelItemCount()):
        item=tree.topLevelItem(i)
        for j in range(item.childCount()):
            sub=item.child(j);identity=sub.data(0,Qt.UserRole+20)
            if identity:sources.append((f'{i+1}.{j+1} {sub.text(0)}',identity))
            text=sub.text(2)
            try:
                c=decode(text)
                if c and c['operation']=='write':keys.add(c['key'])
                from core.device_steps import decode as decode_device
                device_step = decode_device(text)
                if device_step and device_step[2].get('variable'):
                    keys.add(device_step[2]['variable'])
                if device_step and device_step[0] == 'simulator' and device_step[1].key == 'current_batch':
                    manager = getattr(node, 'device_manager', None)
                    if manager is not None:
                        from core.simulator_current_batch import selected_channels
                        keys.update(f'电流-{channel}' for channel, _, _ in selected_channels(
                            manager, device_step[2]['selection']))
                import re
                m=re.search(r'WRITE64:([^\s]+)',text)
                if m:
                    keys.add('主板序列号')
                    from core.uds_field_write import decode as legacy
                    key=legacy(m[1]).get('variable')
                    if key:keys.add(key)
            except (ValueError,TypeError):pass
    return sources,keys

class VariableStepForm(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.operation='write';layout=QVBoxLayout(self);form=QFormLayout()
        self.key=QComboBox();self.key.setEditable(True);self.key.addItems(sorted(catalog(parent)[1]));self.key.setCurrentIndex(-1)
        self.source=QComboBox();self.source.addItem('上一个工步输出','last')
        for label,identity in catalog(parent)[0]:self.source.addItem(label,identity)
        form.addRow('变量键名',self.key);form.addRow('来源工步（写入时）',self.source)
        self.input=QComboBox();self.input.addItems(['字符串／原始数值','HEX字节']);form.addRow('输入格式',self.input)
        self.separator=QLineEdit();form.addRow('分段拼接分隔符（可空）',self.separator);layout.addLayout(form)
        tip=QLabel('不添加分段时原值输出。字符和字节位置均从1计数，长度0表示到末尾。HEX字节可解析ASCII、BCD或整数；变量仅在本次测试内有效。');tip.setWordWrap(True);layout.addWidget(tip)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['起始位置','截取长度','输出格式']);self.table.verticalHeader().setDefaultSectionSize(56);self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        row=QHBoxLayout();add=QPushButton('添加解析分段');remove=QPushButton('删除选中分段');row.addWidget(add);row.addWidget(remove);layout.addLayout(row)
        add.clicked.connect(lambda:self.add_segment());remove.clicked.connect(self.remove)
        self.sample=QLineEdit();self.sample.setPlaceholderText('输入样本查看解析结果；不保存到配方')
        from ui.widgets.byte_grid_widget import ByteGridWidget
        self.grid=ByteGridWidget(self)
        layout.addWidget(QLabel('拖选数据 → 右键添加解析段；已有分段保留在下表，可重复选取。B0对应起始位置1。'))
        layout.addWidget(self.grid)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.segment_menu)
        self.grid.range_selected.connect(lambda *_:self.mark_segments())
        moves=QHBoxLayout()
        for label,delta in [('分段上移',-1),('分段下移',1)]:
            button=QPushButton(label);button.clicked.connect(lambda _,d=delta:self.move_segment(d));moves.addWidget(button)
        layout.addLayout(moves)
        self._sample_signature=None
        self.preview=QLabel('原值输出');self.preview.setWordWrap(True);layout.addWidget(self.sample);layout.addWidget(self.preview)
        self.runtime=QLabel('尚无本次调试运行值');self.runtime.setWordWrap(True);layout.addWidget(self.runtime)
        refresh=QPushButton('载入最近调试值');refresh.clicked.connect(self.load_runtime);layout.addWidget(refresh)
        self.key.currentTextChanged.connect(self.load_runtime)
        self.source.currentIndexChanged.connect(self.load_runtime)
        for w in (self.sample,self.separator):w.textChanged.connect(self.update_preview)
        self.input.currentIndexChanged.connect(self.update_preview)
        from ui.widgets.compact_step_layout import compact_table,compact_combos
        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setStretchLastSection(False)
        from ui.widgets.compact_step_layout import adaptive_columns
        adaptive_columns(self.table, [1, 1, 2])
        self.table.setStyleSheet('QTableWidget { color: white; background: #1A1A2E; } QHeaderView::section { color: white; background: #243246; padding: 6px; } QTableWidget QSpinBox,QTableWidget QComboBox { min-height: 0px; padding: 2px 6px; margin: 0px; }')
        compact_table(self.table);compact_combos(self)
        layout.addStretch()

    def set_operation(self,operation):
        self.operation=operation;self.source.setEnabled(operation=='write')
        self.load_runtime()

    def load_runtime(self,*_):
        node=self.parentWidget();engine=None
        while node is not None:
            engine=getattr(node,'test_engine',None)
            if engine is not None:break
            node=node.parent()
        if engine is None or getattr(engine,'workers',{}):
            self.runtime.setText('尚无可用调试值，或测试正在运行；可手工输入预览样本。');return
        if getattr(node,'current_recipe_name',None)!=getattr(engine,'debug_recipe_name',None):
            self.runtime.setText('当前配方没有调试运行值；不会载入其他配方的数据。');return
        if self.operation=='read':
            values=engine.debug_variables
            for key in values:
                if self.key.findText(key)<0:self.key.addItem(key)
            key=self.key.currentText()
            if key not in values:
                self.runtime.setText('本次调试尚未写入键名：'+key);return
            value=values[key]
        else:
            source=self.source.currentData()
            record=engine.debug_last_output if source=='last' else engine.debug_step_outputs.get(source)
            if not record or not record['ok']:
                self.runtime.setText('来源工步尚未成功运行；请先勾选执行来源工步。');return
            value=record['value']
        self.runtime.setText('最近调试原值（仅预览，不保存到配方）：'+repr(value))
        self.sample.setText(bytes(value).hex(' ').upper() if isinstance(value,(bytes,bytearray)) else str(value))

    def add_segment(self,s=None):
        s=s or dict(start=1,length=0,format='原值');r=self.table.rowCount();self.table.insertRow(r)
        for col,key in enumerate(('start','length')):
            w=QSpinBox();w.setRange(1 if key=='start' else 0,65535);w.setValue(s[key]);w.valueChanged.connect(self.update_preview);self.table.setCellWidget(r,col,w)
        w=QComboBox();w.addItems(['原值','ASCII','HEX','BCD','UINT_LE','UINT_BE']);w.setCurrentText(s['format']);w.currentIndexChanged.connect(self.update_preview);self.table.setCellWidget(r,2,w)
        self.table.setRowHeight(r,max(56,w.sizeHint().height()+8));self.update_preview()
        w.setProperty('compact_hex',s.get('compact_hex',False))
        self.update_preview()
        from ui.widgets.compact_step_layout import compact_combos
        compact_combos(self)

    def remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()},reverse=True):self.table.removeRow(r)
        self.update_preview()

    def config(self):
        return dict(operation=self.operation,key=self.key.currentText().strip(),source=self.source.currentData() or 'last',input='hex' if self.input.currentIndex() else 'text',separator=self.separator.text(),
            segments=[dict(dict(start=self.table.cellWidget(r,0).value(),length=self.table.cellWidget(r,1).value(),format=self.table.cellWidget(r,2).currentText()),
                           **({'compact_hex':True} if self.table.cellWidget(r,2).property('compact_hex') else {})) for r in range(self.table.rowCount())])

    def segment_menu(self,pos):
        offset,length,data=self.grid.get_selected_range()
        if not data or not length:return
        menu=QMenu(self.grid)
        formats=['原值','HEX'] if not self.input.currentIndex() else ['HEX','ASCII','BCD','UINT_LE','UINT_BE']
        for kind in formats:
            menu.addAction('添加为 '+kind,lambda _,k=kind:self.append_selection(k))
        menu.exec(self.grid.mapToGlobal(pos))

    def append_selection(self,kind):
        offset,length,data=self.grid.get_selected_range()
        if not data:return
        self.add_segment(dict(start=offset+1,length=length,format=kind,compact_hex=kind=='HEX'))
        self.update_preview()

    def move_segment(self,delta):
        row=self.table.currentRow();target=row+delta
        if row<0 or not 0<=target<self.table.rowCount():return
        segments=self.config()['segments'];segments[row],segments[target]=segments[target],segments[row]
        self.table.setRowCount(0)
        for s in segments:self.add_segment(s)
        self.table.selectRow(target);self.update_preview()

    def mark_segments(self):
        colors=['#2563EB','#9333EA','#0F766E','#B45309','#BE185D','#475569']
        for i,block in enumerate(self.grid._blocks):
            block.update_style();labels=[]
            for n in range(self.table.rowCount()):
                start=self.table.cellWidget(n,0);length=self.table.cellWidget(n,1)
                if not start or not length:continue
                if i>=start.value()-1 and (length.value()==0 or i<start.value()-1+length.value()):labels.append(n)
            if labels:
                if not block.is_selected:
                    color=colors[labels[-1]%len(colors)]
                    block.setStyleSheet(f'QFrame#byte_block {{ background:{color}; border:2px solid white; border-radius:6px; }} QLabel {{ background:transparent; border:none; color:white; }}')
                block.setToolTip('解析段：'+','.join(str(n+1) for n in labels))
            else:block.setToolTip('')

    def params(self):return encode(self.config())

    def load(self,c):
        self.set_operation(c['operation']);self.key.setCurrentText(c['key']);self.input.setCurrentIndex(c.get('input')=='hex');self.separator.setText(c.get('separator',''))
        source=c.get('source','last');index=self.source.findData(source)
        if index<0:self.source.addItem('来源工步已删除：'+source,source);index=self.source.count()-1
        self.source.setCurrentIndex(index);self.table.setRowCount(0)
        for s in c.get('segments',[]):self.add_segment(s)
        self.load_runtime()

    def update_preview(self,*_):
        if hasattr(self,'grid'):
            signature=(self.sample.text(),self.input.currentIndex())
            if signature!=self._sample_signature:
                self._sample_signature=signature
                try:
                    data=bytes.fromhex(signature[0]) if signature[1] else signature[0].encode('ascii')
                    self.grid.set_bytes(data)
                except (ValueError,UnicodeError):self.grid.set_bytes(b'')
            self.mark_segments()
        if not self.sample.text():
            self.preview.setText('未输入预览样本；运行时使用变量实际值。可输入样本检查截取位置和长度。')
            return
        try:self.preview.setText('解析结果：'+str(parse(self.sample.text(),dict(self.config(),key=self.key.currentText() or '预览'))))
        except (ValueError,UnicodeError) as exc:self.preview.setText('填写提示：'+str(exc))
