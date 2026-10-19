"""Ordered UDS extraction records; dragging alone never overwrites a saved segment."""
from copy import deepcopy
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
                              QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                              QAbstractItemView, QSizePolicy)
from core.uds_parser import parse_uds_response_data
from ui.widgets.compact_step_layout import compact_table, adaptive_columns


class UDSSegmentsWidget(QWidget):
    changed = Signal()
    selected_rule = Signal(object)
    append_requested = Signal()
    replace_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rules = []
        self.raw = b''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox('多段组合（右键追加分段）')
        self.enabled.setChecked(True)
        layout.addWidget(self.enabled)
        self.panel = QWidget()
        body = QVBoxLayout(self.panel)
        body.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.count = QLabel('已记录 0 段')
        tools.addWidget(self.count)
        self.separator = QLineEdit()
        self.separator.setPlaceholderText('拼接分隔符（留空直接拼接）')
        tools.addWidget(self.separator, 1)
        body.addLayout(tools)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['起始字节', '长度', '格式', '本段输出预览'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setToolTip('点击记录载入上方参数，修改后点击“修改选中段”。右键追加不会覆盖已有记录。')
        adaptive_columns(self.table, [1.2, .7, 1, 2.5])
        compact_table(self.table, max_rows=4)
        body.addWidget(self.table)
        buttons = QHBoxLayout()
        for title, callback in [('追加当前段', self.append_requested.emit),
                                ('修改选中段', self.replace_requested.emit),
                                ('删除段', self.remove), ('上移', lambda: self.move(-1)),
                                ('下移', lambda: self.move(1))]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, fn=callback: fn())
            buttons.addWidget(button)
        body.addLayout(buttons)
        self.preview = QLabel('组合输出：尚未记录分段')
        self.preview.setWordWrap(True)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.addWidget(self.preview)
        layout.addWidget(self.panel)
        self.enabled.toggled.connect(self.panel.setVisible)
        self.enabled.toggled.connect(self.changed)
        self.separator.textChanged.connect(self._changed)
        self.table.cellClicked.connect(self._select)

    def _select(self, row, column=0):
        if 0 <= row < len(self.rules):
            self.selected_rule.emit(deepcopy(self.rules[row]))

    def append(self, rule):
        if len(self.rules) >= 128:
            raise ValueError('最多支持128段')
        self.rules.append(deepcopy(rule))
        self._changed()
        self.table.selectRow(len(self.rules)-1)

    def replace(self, rule):
        row = self.table.currentRow()
        if not 0 <= row < len(self.rules):
            raise ValueError('请先选择需要修改的分段')
        self.rules[row] = deepcopy(rule)
        self._changed()
        self.table.selectRow(row)

    def remove(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules.pop(row)
            self._changed()

    def move(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if 0 <= row < len(self.rules) and 0 <= target < len(self.rules):
            self.rules[row], self.rules[target] = self.rules[target], self.rules[row]
            self._changed()
            self.table.selectRow(target)

    def load(self, rule):
        self.rules = deepcopy(rule.get('segments', []))
        self.separator.blockSignals(True)
        self.separator.setText(rule.get('separator', ''))
        self.separator.blockSignals(False)
        self._changed()

    def config(self, var_name=''):
        return dict(mode='COMBINE', segments=deepcopy(self.rules),
                    separator=self.separator.text(), var_name=var_name)

    def _changed(self, *_):
        self.refresh()
        self.changed.emit()

    def refresh(self, raw=None):
        if raw is not None:
            self.raw = raw
        self.count.setText(f'已记录 {len(self.rules)} 段')
        self.table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            result = parse_uds_response_data(self.raw, rule) if self.raw else {}
            value = str(result.get('value', '等待响应样本'))
            if rule['mode'] == 'HEX':
                value = value.replace(' ', '')
            if result and not result.get('success'):
                value = result.get('error', '解析失败')
            for col, text in enumerate((str(rule['offset']), str(rule['length']), rule['mode'], value)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 0:
                    item.setBackground(QColor(['#2563EB', '#9333EA', '#0F766E', '#B45309', '#BE185D', '#475569'][row % 6]))
                    item.setForeground(QColor('white'))
                self.table.setItem(row, col, item)
        if self.rules and self.raw:
            result = parse_uds_response_data(self.raw, self.config())
            text = str(result.get('value', result.get('error', '')))
        else:
            text = '等待响应样本' if self.rules else '尚未记录分段'
        self.preview.setText('组合输出：' + text)
        self.preview.setToolTip(text)
