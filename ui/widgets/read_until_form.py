from PySide6.QtWidgets import QGroupBox, QFormLayout, QLineEdit, QDoubleSpinBox, QSpinBox, QLabel
from core.read_until import validate


class ReadUntilForm(QGroupBox):
    def __init__(self):
        super().__init__('等待回读进入范围（不勾选为单次读取）')
        self.setCheckable(True); self.setChecked(False)
        form = QFormLayout(self)
        tip = QLabel('上下限包含边界，按回读所选单位填写。立即开始读取，满足连续合格次数就进入下一步；超时或通信失败则失败。最长等待不改变仪器自身通信超时。')
        tip.setWordWrap(True); form.addRow(tip)
        self.lower = QLineEdit(); self.upper = QLineEdit()
        self.lower.setPlaceholderText('必填，例如 11.95'); self.upper.setPlaceholderText('必填，例如 12.05')
        form.addRow('下限：', self.lower); form.addRow('上限：', self.upper)
        self.timeout = QDoubleSpinBox(); self.timeout.setRange(.01,86400); self.timeout.setValue(10); self.timeout.setSuffix(' s')
        self.interval = QDoubleSpinBox(); self.interval.setRange(.01,3600); self.interval.setValue(.1); self.interval.setSuffix(' s')
        self.consecutive = QSpinBox(); self.consecutive.setRange(1,10000)
        form.addRow('最长等待时间：',self.timeout); form.addRow('读取周期（两次读取间隔）：',self.interval); form.addRow('连续合格次数：',self.consecutive)

    def config(self):
        if not self.isChecked() or self.isHidden():
            return None
        return validate(dict(min=float(self.lower.text()),max=float(self.upper.text()),timeout=self.timeout.value(),interval=self.interval.value(),consecutive=self.consecutive.value()))

    def load(self,c):
        self.setChecked(c is not None)
        if c:
            validate(c)
            self.lower.setText(str(c['min'])); self.upper.setText(str(c['max']))
            self.timeout.setValue(c['timeout']); self.interval.setValue(c['interval']); self.consecutive.setValue(c['consecutive'])
