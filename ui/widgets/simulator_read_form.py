"""Batch current read controls for the battery simulator."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from core.device_steps import ACTIONS
from core.simulator_current_batch import selected_channels
from ui.widgets.device_step_form import DeviceStepForm


class SimulatorReadForm(QWidget):
    def __init__(self, manager=None):
        super().__init__()
        self.manager = manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fields = DeviceStepForm()
        self.fields.configure('simulator', next(spec for spec in ACTIONS['simulator'] if spec.key == 'current_batch'))
        layout.addWidget(self.fields)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.fields.controls['selection'].currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def values(self):
        return self.fields.values()

    def load(self, values):
        self.fields.configure('simulator', self.fields.spec, values)
        self.fields.controls['selection'].currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def update_preview(self):
        if self.manager is None:
            self.preview.setText('运行时按硬件管理中心已保存并应用的启用设备及实际通道数读取。结果单位 mA，变量名为“电流-全局通道号”。')
            return
        try:
            routes = selected_channels(self.manager, self.fields.controls['selection'].currentData())
        except (ValueError, AttributeError) as exc:
            self.preview.setText('无法预览通道：' + str(exc))
            return
        channels = [str(channel) for channel, _, _ in routes]
        if not channels:
            self.preview.setText('当前没有符合选择的已启用通道，请在硬件管理中心保存并应用模拟器配置。')
            self.preview.setToolTip('')
            return
        summary = '、'.join(channels if len(channels) <= 12 else channels[:10] + ['…', channels[-1]])
        self.preview.setText(f'当前配置：读取 {len(channels)} 个通道（{summary}）。\n'
                             f'写入变量：电流-{channels[0]} 至 电流-{channels[-1]} 中所选通道的键；单位 mA。')
        self.preview.setToolTip('\n'.join(f'CH{channel} → 设备{sim.sequence_number}/CH{local} → 电流-{channel}'
                                         for channel, sim, local in routes))

    def showEvent(self, event):
        super().showEvent(event)
        self.update_preview()
