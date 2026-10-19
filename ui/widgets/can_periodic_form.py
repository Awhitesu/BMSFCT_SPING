"""Editor for paired periodic CAN start/stop steps."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QSpinBox,QComboBox,QHeaderView
from core.can_periodic import validate,encode


class CANPeriodicForm(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.operation='start'
        layout=QVBoxLayout(self);form=QFormLayout()
        self.name=QLineEdit();self.name.setPlaceholderText('例如 BMS唤醒；启动和停止必须填写同一个名称')
        form.addRow('报文列表名称',self.name);layout.addLayout(form)
        self.tip=QLabel();self.tip.setWordWrap(True);layout.addWidget(self.tip)
        self.frames=QTableWidget(0,4);self.frames.setHorizontalHeaderLabels(['ID（HEX）','数据（HEX）','发送周期（ms）','CAN类型'])
        self.frames.setStyleSheet('QTableWidget QSpinBox, QTableWidget QComboBox { min-height: 0px; padding: 2px 6px; margin: 0px; }')
        from ui.widgets.compact_step_layout import adaptive_columns
        adaptive_columns(self.frames, [1.2, 3, 1.5, 1.5])
        self.frames.verticalHeader().setMinimumSectionSize(48)
        self.frames.verticalHeader().setDefaultSectionSize(56)
        layout.addWidget(self.frames)
        self.buttons=QWidget();row=QHBoxLayout(self.buttons);row.setContentsMargins(0,0,0,0)
        add=QPushButton('添加报文');remove=QPushButton('删除选中报文');row.addWidget(add);row.addWidget(remove);row.addStretch()
        add.clicked.connect(lambda:self.add_frame());remove.clicked.connect(self.remove_rows);layout.addWidget(self.buttons)
        self.add_frame();self.set_operation('start')
        from ui.widgets.compact_step_layout import compact_table
        compact_table(self.frames)
        layout.addStretch()

    def set_operation(self,operation):
        self.operation=operation;start=operation=='start';self.frames.setVisible(start);self.buttons.setVisible(start)
        self.tip.setText('每条报文按自己的周期持续发送，直到同名停止工步。列表启动后立即发送一轮。ID大于7FF自动使用扩展帧。CAN FD数据长度应为0～8、12、16、20、24、32、48或64字节。'
                         if start else '停止前面已经启动的同名报文列表；没有启动、名称不一致或已停止时，禁止执行。')

    def add_frame(self,c=None):
        if self.frames.rowCount()>=100:return
        c=c or {};r=self.frames.rowCount();self.frames.insertRow(r)
        self.frames.setItem(r,0,QTableWidgetItem(f"{c['id']:X}" if 'id' in c else ''))
        self.frames.setItem(r,1,QTableWidgetItem(c.get('data','')))
        period=QSpinBox();period.setRange(10,3600000);period.setValue(c.get('period_ms',100));self.frames.setCellWidget(r,2,period)
        kind=QComboBox();kind.addItems(['Classic','CAN FD','CAN FD + BRS']);kind.setCurrentIndex(c.get('type',0));self.frames.setCellWidget(r,3,kind)
        self.frames.setRowHeight(r,max(56,period.sizeHint().height()+8,kind.sizeHint().height()+8))

    def remove_rows(self):
        for r in sorted({i.row() for i in self.frames.selectedIndexes()},reverse=True):self.frames.removeRow(r)

    def config(self):
        c=dict(name=self.name.text().strip(),operation=self.operation)
        if self.operation=='start':
            rows=[]
            for r in range(self.frames.rowCount()):
                try:ident=int(self.frames.item(r,0).text().strip(),16)
                except ValueError:raise ValueError(f'第{r+1}条请填写有效的报文ID（HEX）')
                rows.append(dict(id=ident,data=self.frames.item(r,1).text().strip(),period_ms=self.frames.cellWidget(r,2).value(),type=self.frames.cellWidget(r,3).currentIndex()))
            c['frames']=rows
        return validate(c)

    def params(self):return encode(self.config())

    def load(self,c):
        self.name.setText(c['name']);self.set_operation(c['operation']);self.frames.setRowCount(0)
        for frame in c.get('frames',[]):self.add_frame(frame)
