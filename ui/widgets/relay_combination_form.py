from PySide6.QtCore import Qt, Signal
import sys
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                              QFrame, QCheckBox, QLabel, QPushButton, QButtonGroup)


class RelayCombinationForm(QWidget):
    mixed_requested = Signal()
    def __init__(self, channels, parent=None):
        super().__init__(parent)
        self.channels = channels
        self.cards = {}
        self.legacy_state = None
        self.missing = set()
        self.setObjectName('relayCombinationForm')
        self.checks = []
        self.setStyleSheet('''
            QWidget { font-size:12px; }
            QWidget#relayCombinationForm { background:#0F172A; }
            QLabel { color:#94A3B8; }
            QFrame[relayCard="true"] { background:#182337; border:1px solid #334155; border-radius:5px; }
            QCheckBox { color:white; spacing:4px; }
            QCheckBox::indicator { width:15px; height:15px; border:1px solid white; border-radius:3px; background:#182337; }
            QCheckBox::indicator:checked { background:#182337; image:url(RELAY_TICK); }
            QPushButton { min-width:0px; min-height:0px; padding:2px 5px; background:#243246; color:white; border:1px solid #475569; border-radius:3px; }
            QPushButton:checked { color:black; font-weight:bold; }
            QPushButton[relayState="on"]:checked { background:#22C55E; }
            QPushButton[relayState="off"]:checked { background:#EF4444; }
            QPushButton:disabled { background:#293447; color:#778397; }
        ''')
        resource_root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
        tick = (resource_root/'resources/checkbox_green_tick.svg').as_posix()
        self.setStyleSheet(self.styleSheet().replace('RELAY_TICK', '"'+tick+'"'))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        tip = QLabel('勾选点位后设置ON/OFF；灰色未启用点位保持原状态。执行时先OFF、再ON。')
        tip.setWordWrap(True)
        root.addWidget(tip)
        grid = QGridLayout()
        grid.setSpacing(5)
        root.addLayout(grid)
        for position, channel in enumerate(channels):
            index = channel['index']
            card = QFrame(); card.setProperty('relayCard', True)
            box = QVBoxLayout(card); box.setContentsMargins(6, 4, 6, 4); box.setSpacing(3)
            check = QCheckBox('启用'); check.setProperty('coil_index', index)
            self.checks.append(check); box.addWidget(check)
            label = QLabel(channel['do'] + (' ' + channel['name'] if channel['name'] else ''))
            label.setWordWrap(True); label.setToolTip(label.text()); box.addWidget(label)
            buttons = QHBoxLayout(); buttons.setSpacing(3)
            group = QButtonGroup(card); group.setExclusive(True)
            on = QPushButton('ON'); off = QPushButton('OFF')
            for button, state in ((on, 'on'), (off, 'off')):
                button.setCheckable(True); button.setProperty('relayState', state)
                button.setFixedHeight(23); button.setEnabled(False)
                group.addButton(button); buttons.addWidget(button)
                button.pressed.connect(self.mixed_requested.emit)
                button.toggled.connect(self.refresh)
            box.addLayout(buttons)
            self.cards[index] = (check, on, off, label, group)
            check.toggled.connect(self.refresh)
            grid.addWidget(card, position // 6, position % 6)
        for column in range(6):grid.setColumnStretch(column, 1)
        footer = QHBoxLayout()
        self.summary = QLabel(); footer.addWidget(self.summary, 1)
        for title, checked in (('全部启用', True), ('全部取消', False)):
            button = QPushButton(title)
            button.clicked.connect(lambda _=False, checked=checked: self.select_all(checked))
            footer.addWidget(button)
        root.addLayout(footer)
        root.addStretch()
        self.refresh()

    def select_all(self, checked):
        if not checked:self.missing.clear()
        for check in self.checks:check.setChecked(checked)

    def refresh(self, *_):
        count = on_count = off_count = 0
        for check, on, off, label, group in self.cards.values():
            enabled = check.isChecked()
            if self.legacy_state is not None:
                for button, state in ((on, True), (off, False)):
                    button.blockSignals(True)
                    button.setChecked(enabled and self.legacy_state == state)
                    button.blockSignals(False)
            on.setEnabled(enabled)
            off.setEnabled(enabled)
            label.setStyleSheet('color:#F1F5F9;' if enabled else 'color:#778397;')
            count += enabled
            on_count += enabled and on.isChecked()
            off_count += enabled and off.isChecked()
        self.summary.setText(f'启用 {count}/{len(self.cards)} · ON {on_count} · OFF {off_count} · 待设置 {count-on_count-off_count}')

    def set_legacy(self, state):
        self.legacy_state = state
        self.refresh()

    def config(self):
        if self.missing:
            raise ValueError('原工步含已移除点位，请点击全部取消后重新配置，或恢复导入表')
        rows = []
        for index, (check, on, off, _, _) in self.cards.items():
            if not check.isChecked():continue
            if not on.isChecked() and not off.isChecked():
                raise ValueError(f'请为DO{index:o}选择ON或OFF')
            rows.append(dict(index=index, on=on.isChecked()))
        from core.relay_combination import validate
        return validate(rows)

    def load(self, rows):
        missing = {row['index'] for row in rows} - self.cards.keys()
        self.missing = missing
        if missing:
            message='原工步包含导入表不存在的点位：' + ', '.join(f'DO{i:o}' for i in sorted(missing))
            self.summary.setText(message)
            raise ValueError(message)
        for check, on, off, _, group in self.cards.values():
            check.setChecked(False)
            group.setExclusive(False); on.setChecked(False); off.setChecked(False); group.setExclusive(True)
        for row in rows:
            check, on, off, _, _ = self.cards[row['index']]
            check.setChecked(True)
            (on if row['on'] else off).setChecked(True)
        self.refresh()
