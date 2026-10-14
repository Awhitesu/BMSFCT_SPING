import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from devices.manager import DeviceManager
from ui.tabs.simulator_tab import SimulatorTab

app = QApplication([])
app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
db = Mock()
cfg = {'sim1_enabled': False}
db.load_sys_config.side_effect = lambda: dict(cfg)
db.save_sys_config.side_effect = lambda value: cfg.update(value) or True
manager = DeviceManager(db)
widget = SimulatorTab(manager)
widget.resize(1000, 650)
widget.show()
app.processEvents()
assert widget.tabs.count() == 2
assert widget.settings_table.rowCount() == 6
assert not widget.btn_set_volt.isEnabled()
widget.settings_table.item(0, 0).setCheckState(Qt.Checked)
widget.settings_controls[0][3].setValue(24)
widget.settings_table.item(1, 0).setCheckState(Qt.Checked)
widget.settings_controls[1][3].setValue(12)
widget.save_simulator_settings()
assert widget.table.rowCount() == 36
assert widget.spin_ch.itemData(24) == 25
assert cfg['sim2_channels'] == 12
with patch('ui.tabs.simulator_tab.QMessageBox.warning') as warning:
    widget.settings_controls[1][2].setValue(1)
    widget.save_simulator_settings()
    assert warning.called
assert manager.simulators[1].sequence_number == 2
app.processEvents()
assert widget.spin_ch.geometry().bottom() < widget.spin_volt.geometry().top()
assert widget.spin_volt.geometry().bottom() < widget.btn_off.geometry().top()
widget.grab().save('tests/simulator_layout_preview.png')
widget.status_timer.stop()
print('Configuration, duplicate validation, channel refresh and small-window layout passed')
