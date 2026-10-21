import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from devices.manager import DeviceManager
from ui.tabs.simulator_tab import SimulatorTab
from ui.tabs.hardware_config_tab import HardwareConfigTab

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
assert widget.tabs.count() == 3
assert widget.settings_table.rowCount() == 6
assert not widget.btn_set_volt.isEnabled()
hardware = HardwareConfigTab(db)
hardware.status_timer.stop()
rows = {spec[0]: row for row, spec in enumerate(hardware.DEVICE_SPECS)}
hardware.table.item(rows['sim1'], 0).setCheckState(Qt.Checked)
hardware.table.cellWidget(rows['sim1'], 8).setValue(24)
hardware.table.item(rows['sim2'], 0).setCheckState(Qt.Checked)
hardware.table.cellWidget(rows['sim2'], 8).setValue(12)
with patch('ui.tabs.hardware_config_tab.QMessageBox.information'):
    hardware.save_config()
manager.update_config()
widget.refresh_configuration()
assert widget.table.rowCount() == 36
assert widget.spin_ch.itemData(24) == 25
assert cfg['sim2_channels'] == 12
with patch('ui.tabs.hardware_config_tab.QMessageBox.warning') as warning:
    hardware.table.cellWidget(rows['sim2'], 7).setValue(1)
    hardware.save_config()
    assert warning.called
assert manager.simulators[1].sequence_number == 2
app.processEvents()
assert widget.spin_ch.geometry().bottom() < widget.spin_volt.geometry().top()
assert widget.spin_volt.geometry().bottom() < widget.btn_off.geometry().top()
assert widget.table.columnCount() == 3
assert widget.btn_read_current.geometry().top() > widget.btn_read.geometry().bottom()
assert widget.btn_read_all.geometry().right() < widget.btn_read_all_current.geometry().left()
Path('scratch').mkdir(exist_ok=True)
widget.grab().save('scratch/simulator_layout_preview.png')
widget.tabs.setCurrentIndex(1)
app.processEvents()
widget.grab().save('scratch/simulator_hardware_configuration_preview.png')
widget.status_timer.stop()
hardware.shutdown()
hardware.close()
widget.shutdown()
widget.close()
print('Hardware-owned configuration, duplicate validation, channel sync and read-only layout passed')
