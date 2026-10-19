from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox,
    QSpinBox, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QPushButton, QHBoxLayout, QHeaderView, QStyledItemDelegate, QStyle)
from core.can_data_parser import encode_parser, decode_parser, validate_parser, parse_payload, bit_positions


class RangeHighlightDelegate(QStyledItemDelegate):
    """Paint saved extraction independently of focus, selection and theme QSS."""
    def paint(self,painter,option,index):
        if index.data(Qt.UserRole) or option.state & QStyle.State_Selected:
            painter.save()
            painter.fillRect(option.rect,QColor('#087eaf'))
            painter.setPen(QPen(QColor('#67e8f9'),2))
            painter.drawRect(option.rect.adjusted(1,1,-1,-1))
            painter.setPen(QColor('#ffffff'))
            painter.drawText(option.rect.adjusted(3,1,-3,-1),Qt.AlignCenter,str(index.data() or ''))
            painter.restore()
        else:
            super().paint(painter,option,index)


class SelectionTable(QTableWidget):
    """Commit after dragging finishes so rebuilding cannot interrupt selection."""
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.commit_selection()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self.commit_selection()


class BitLayoutTable(SelectionTable):
    """Drag a linear signal in protocol bit order, including byte boundaries."""
    def __init__(self):
        super().__init__()
        self.anchor = None
        self.motorola = False

    def _rank(self, item):
        return item.row()*8+(item.column() if self.motorola else 7-item.column())

    def _select_to(self, item):
        if item is None or self.anchor is None:return
        low,high=sorted((self.anchor,self._rank(item)))
        self.clearSelection()
        for rank in range(low,high+1):
            row,bit=divmod(rank,8)
            self.item(row,bit if self.motorola else 7-bit).setSelected(True)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            item=self.itemAt(event.position().toPoint())
            if item is not None:
                self.anchor=self._rank(item);self._select_to(item);event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.anchor is not None and event.buttons() & Qt.LeftButton:
            self._select_to(self.itemAt(event.position().toPoint()));event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.anchor is not None and event.button()==Qt.LeftButton:
            self._select_to(self.itemAt(event.position().toPoint()))
            self.anchor=None;self.commit_selection();event.accept();return
        super().mouseReleaseEvent(event)


class CanParserForm(QWidget):
    """Manual-first guided extraction; tables and inputs share one configuration."""
    capture_requested = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox('解析返回数据（不勾选时保留原有报文结果）')
        layout.addWidget(self.enabled)
        self.body = QWidget()
        layout.addWidget(self.body)
        form = QFormLayout(self.body)
        self.help = QLabel('① 填写返回长度或粘贴样例 → ② 手输位置或在表中选择 → ③ 按协议填写类型和换算。\nByte 从 0 开始；Byte 1 是第 2 字节。样例仅用于预览，不参与实际测试。\n需要用于上下限判定时，请勾选工步的“结果输出并参与判定”，上下限和单位按换算后的值填写。')
        self.help.setWordWrap(True)
        form.addRow(self.help)
        self.sample = QLineEdit()
        self.sample.setPlaceholderText('例如：01 02 34 00 00 00 00 00（自动识别返回长度）')
        sample_row=QHBoxLayout();sample_row.addWidget(self.sample,1)
        self.capture=QPushButton('在线抓取');self.capture.clicked.connect(self.capture_requested)
        sample_row.addWidget(self.capture);form.addRow('① 返回样例 HEX：',sample_row)
        self.length = self.spin(1, 64, 8)
        form.addRow('期望返回字节数：', self.length)
        self.mode = QComboBox(); self.mode.addItems(['按字节', '按位'])
        form.addRow('② 提取方式：', self.mode)
        self.start = self.spin(0, 63, 0); form.addRow('起始 Byte（从 0）：', self.start)
        self.bit = self.spin(0, 7, 0); form.addRow('起始 Bit（最低位为 0）：', self.bit)
        self.bit_order = QComboBox();self.bit_order.addItems(['Intel（小端，起点为最低位）','Motorola（DBC大端，起点为最高位）'])
        form.addRow('位序：',self.bit_order)
        self.count = self.spin(1, 512, 1); form.addRow('提取数量（字节 / 位）：', self.count)
        self.kind = QComboBox(); self.kind.addItems(['无符号整数', '有符号整数', '浮点数', 'HEX', 'ASCII 文字'])
        form.addRow('③ 数据类型：', self.kind)
        self.endian = QComboBox(); self.endian.addItems(['Motorola / 大端（高字节在前）', 'Intel / 小端（低字节在前）'])
        form.addRow('字节序（按协议）：', self.endian)
        self.scale = QLineEdit('1'); self.offset = QLineEdit('0'); self.unit = QLineEdit()
        self.conversion=QPushButton('数值换算（系数 / 偏移 / 单位）');self.conversion.setCheckable(True)
        form.addRow(self.conversion)
        conversion_body=QWidget();conversion_form=QFormLayout(conversion_body)
        conversion_form.addRow('系数：',self.scale);conversion_form.addRow('偏移：',self.offset);conversion_form.addRow('单位：',self.unit)
        conversion_body.hide();self.conversion.toggled.connect(conversion_body.setVisible);form.addRow(conversion_body)
        self.tabs = QTabWidget()
        self.tabs.setMaximumHeight(450)
        self.bytes_table = SelectionTable(); self.bits_table = BitLayoutTable()
        self.tabs.addTab(self.bytes_table, 'Byte 表'); self.tabs.addTab(self.bits_table, '位布局（CANdb 风格）')
        form.addRow(self.tabs)
        for table in (self.bytes_table, self.bits_table):
            table.setItemDelegate(RangeHighlightDelegate(table))
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.ContiguousSelection)
            table.setMinimumHeight(240)
            table.setMaximumHeight(410)
        self.bits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bits_table.verticalHeader().setDefaultSectionSize(46)
        self.bits_table.setStyleSheet('QTableWidget::item:selected { background: #2878b8; color: white; }')
        self.selection_summary=QLabel('选区：尚未选择');self.selection_summary.setWordWrap(True)
        self.selection_summary.setStyleSheet('color: #67e8f9; font-weight: bold;')
        form.addRow(self.selection_summary)
        self.bytes_table.commit_selection = lambda: self.selected(False)
        self.bits_table.commit_selection = lambda: self.selected(True)
        self.preview = QLabel(); self.preview.setWordWrap(True); form.addRow(self.preview)
        self.enabled.toggled.connect(self.body.setEnabled)
        self.enabled.toggled.connect(self.refresh)
        self.body.setEnabled(False)
        self.sample.textChanged.connect(self.sample_changed)
        for field in (self.length, self.start, self.bit, self.count):
            field.valueChanged.connect(self.refresh)
        for field in (self.mode, self.kind, self.endian, self.bit_order):
            field.currentIndexChanged.connect(self.refresh)
        for field in (self.scale, self.offset, self.unit):
            field.textChanged.connect(self.refresh)
        self.refresh()

    @staticmethod
    def spin(low, high, value):
        widget = QSpinBox(); widget.setRange(low, high); widget.setValue(value)
        return widget

    def config(self):
        c = dict(length=self.length.value(), start=self.start.value(), bit=self.bit.value(),
                 count=self.count.value(), mode=('bytes', 'bits')[self.mode.currentIndex()],
                 type=('unsigned', 'signed', 'float', 'hex', 'ascii')[self.kind.currentIndex()],
                 endian=('big', 'little')[self.endian.currentIndex()], scale=float(self.scale.text()),
                 offset=float(self.offset.text()), unit=self.unit.text().strip(),
                 bit_order=('intel','motorola')[self.bit_order.currentIndex()])
        if c['bit_order']=='intel':c.pop('bit_order')
        validate_parser(c)
        return c

    def encoded(self):
        return encode_parser(self.config()) if self.enabled.isChecked() else None

    def load(self, value):
        c = decode_parser(value)
        self._updating = True
        for key in ('length', 'start', 'bit', 'count'):
            getattr(self, key).setValue(c[key])
        self.mode.setCurrentIndex(('bytes', 'bits').index(c['mode']))
        self.kind.setCurrentIndex(('unsigned', 'signed', 'float', 'hex', 'ascii').index(c['type']))
        self.endian.setCurrentIndex(('big', 'little').index(c['endian']))
        self.bit_order.setCurrentIndex(('intel','motorola').index(c.get('bit_order','intel')))
        self.scale.setText(str(c['scale'])); self.offset.setText(str(c['offset'])); self.unit.setText(c.get('unit', ''))
        self.conversion.setChecked(c['scale']!=1 or c['offset']!=0 or bool(c.get('unit')))
        self._updating = False
        self.enabled.setChecked(True); self.refresh()

    def sample_changed(self):
        try:
            data = bytes.fromhex(self.sample.text())
            if 1 <= len(data) <= 64:
                self.length.setValue(len(data))
        except ValueError:
            pass
        self.refresh()

    def selected(self, bits):
        if self._updating:
            return
        table = self.bits_table if bits else self.bytes_table
        indices = sorted({i.row() * (8 if bits else 1) + (7 - i.column() if bits else 0)
                          for i in table.selectedIndexes()})
        if not indices:
            return
        if bits and self.bit_order.currentIndex() == 1:
            indices.sort(key=lambda position:(position//8,7-position%8))
            expected=[indices[0]]
            for _ in range(len(indices)-1):
                p=expected[-1];expected.append(p+15 if p%8==0 else p-1)
        else:
            expected=list(range(indices[0],indices[-1]+1))
        if indices != expected:
            self.preview.setText('请选择连续位段；跨字节时请直接填写起始 Byte、Bit 和位数。')
            return
        self._updating = True
        self.mode.setCurrentIndex(int(bits))
        self.start.setValue(indices[0] // 8 if bits else indices[0])
        self.bit.setValue(indices[0] % 8 if bits else 0)
        self.count.setValue(len(indices))
        self._updating = False
        self.refresh()

    def refresh(self, *_):
        if self._updating or not self.enabled.isChecked():
            return
        self._updating = True
        try:
            bits = self.mode.currentIndex() == 1
            self.bits_table.motorola=self.bit_order.currentIndex()==1
            form=self.body.layout()
            for field,visible in ((self.bit,bits),(self.bit_order,bits),(self.endian,not bits)):
                field.setVisible(visible);form.labelForField(field).setVisible(visible)
            self.tabs.setCurrentIndex(int(bits))
            data = b''
            sample_error = ''
            try:
                data = bytes.fromhex(self.sample.text())
            except ValueError:
                sample_error = '样例格式错误：请输入两位 HEX 字节，例如 01 02 34。'
            for table, columns in ((self.bytes_table, 1), (self.bits_table, 8)):
                table.clear(); table.setRowCount(self.length.value()); table.setColumnCount(columns)
                table.setHorizontalHeaderLabels(['HEX'] if columns == 1 else [f'bit{i}' for i in range(7, -1, -1)])
                table.setVerticalHeaderLabels([f'B{i}' for i in range(self.length.value())])
                for row in range(self.length.value()):
                    for col in range(columns):
                        text = '--' if row >= len(data) else (f'{data[row]:02X}' if columns == 1 else str((data[row] >> (7-col)) & 1))
                        if columns==8:text+=f'\n[{row*8+7-col}]'
                        item = QTableWidgetItem(text); item.setTextAlignment(Qt.AlignCenter)
                        if columns==8:item.setToolTip(f'Byte {row} · Bit {7-col} · 位编号 {row*8+7-col}')
                        if bits:
                            address = row * 8 + (7-col) if columns == 8 else row * 8
                            positions=bit_positions(dict(start=self.start.value(),bit=self.bit.value(),count=self.count.value(),bit_order=('intel','motorola')[self.bit_order.currentIndex()]))
                            chosen = address in positions if columns == 8 else any(p//8==row for p in positions)
                        else:
                            chosen = self.start.value() <= row < self.start.value()+self.count.value()
                        if chosen:
                            item.setBackground(QColor('#245d85'))
                            item.setForeground(QColor('#ffffff'))
                            if bits and columns==8:
                                if address==positions[0]:item.setText(item.text()+('\nMSB' if self.bits_table.motorola else '\nLSB'))
                                if address==positions[-1] and len(positions)>1:item.setText(item.text()+('\nLSB' if self.bits_table.motorola else '\nMSB'))
                        item.setData(Qt.UserRole,bool(chosen))
                        table.setItem(row, col, item)
                table.horizontalHeader().setStretchLastSection(True)
            c = self.config()
            if bits:
                positions=bit_positions(c)
                self.selection_summary.setText(f"已选 {c['count']} 位：Byte {positions[0]//8} / Bit {positions[0]%8} → Byte {positions[-1]//8} / Bit {positions[-1]%8}（{'Motorola' if c.get('bit_order')=='motorola' else 'Intel'}）；蓝色格为当前选区")
            else:
                self.selection_summary.setText(f"已选 {c['count']} 字节：Byte {c['start']}～Byte {c['start']+c['count']-1}；蓝色格为当前选区")
            detail = (f"Byte {c['start']} 的 bit{c['bit']} 起取 {c['count']} 位；"+('Motorola / DBC：起点为最高位，跨字节按锯齿位序。' if c.get('bit_order')=='motorola' else 'Intel：起点为最低位，位地址递增。') if bits else
                      f"从 Byte {c['start']}（第 {c['start']+1} 字节）取 {c['count']} 字节。")
            detail += '\n最终值 = 原始值 × 系数 + 偏移。请依据协议核对类型、位置及换算。'
            if sample_error:
                raise ValueError(sample_error)
            if self.sample.text().strip():
                raw, value = parse_payload(data, c)
                detail += f"\n原始值：{raw} → 最终值：{value} {c.get('unit', '')}"
                if not bits and c['type'] in ('unsigned', 'signed'):
                    chunk = data[c['start']:c['start']+c['count']]
                    detail += f"\n同一数据的大端值 {int.from_bytes(chunk, 'big', signed=c['type']=='signed')}；小端值 {int.from_bytes(chunk, 'little', signed=c['type']=='signed')}。"
            else:
                detail += '\n尚未填写样例：可保存有效配置，但请用实际返回报文核对。'
            self.preview.setText(detail)
        except (ValueError, TypeError, OverflowError) as exc:
            self.selection_summary.setText('选区配置无效，请检查位置和长度')
            self.preview.setText(f'请修正：{exc}')
        finally:
            self._updating = False
