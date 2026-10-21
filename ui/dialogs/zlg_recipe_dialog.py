"""Recipe-owned configuration for one four-channel CANFDNET-400U."""

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
    QSpinBox, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialogButtonBox, QMessageBox, QLabel)


from core.zlg_recipe_config import NOMINAL, DATA, validate_zlg_config


class ZlgRecipeDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ZLG CANFDNET-400U · 整机配置')
        self.resize(850, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.ip = QLineEdit(config.get('ip', '192.168.0.177'))
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(config.get('port', 8000))
        self._legacy_channel = config.get('send_channel', 1)
        form.addRow('设备IP（TCP服务器）', self.ip)
        form.addRow('TCP端口', self.port)
        layout.addLayout(form)
        self.table = QTableWidget(4, 5)
        self.table.setHorizontalHeaderLabels(['物理通道', '使能', '仲裁波特率(bit/s)', '数据波特率(bit/s)', '120Ω终端电阻'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.controls = []
        for index in range(4):
            row = config.get('channels', [{}]*4)[index]
            item = QTableWidgetItem(f'CH{index+1}')
            from PySide6.QtCore import Qt
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(index, 0, item)
            enable, termination = QCheckBox('启用'), QCheckBox('启用')
            enable.setChecked(row.get('enabled', True)); termination.setChecked(row.get('termination', False))
            nominal, data = QComboBox(), QComboBox()
            nominal.addItems([str(v) for v in NOMINAL]); data.addItems([str(v) for v in DATA])
            nominal.setCurrentText(str(row.get('nominal', 500000))); data.setCurrentText(str(row.get('data', 2000000)))
            controls = (enable, nominal, data, termination)
            for column, widget in enumerate(controls, 1): self.table.setCellWidget(index, column, widget)
            self.table.setRowHeight(index, max(58, max(w.sizeHint().height() for w in controls)+12))
            self.controls.append(controls)
        layout.addWidget(self.table, 1)
        tip = QLabel('参数保存到当前配方，开始测试时应用四通道配置。发送、UDS及DBC读取的物理通道在各工步中设置；SDK目录使用硬件管理中心设置。保存配方不连接设备。')
        tip.setWordWrap(True); layout.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_config(self):
        return validate_zlg_config(dict(ip=self.ip.text().strip(), port=self.port.value(),
            send_channel=self._legacy_channel,
            channels=[dict(enabled=e.isChecked(), nominal=int(n.currentText()),
                data=int(d.currentText()), termination=t.isChecked()) for e,n,d,t in self.controls]))

    def accept(self):
        try: self.get_config()
        except (ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, '参数错误', str(exc)); return
        super().accept()
