"""Hardware center owns simulator configuration; debug only shows applied parameters."""
from copy import deepcopy
import time
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget

from devices.manager import DeviceManager
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.simulator_tab import SimulatorTab
from ui.widgets.simulator_read_form import SimulatorReadForm

app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic()+4
    while not predicate():
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.002)


@pytest.fixture
def pages():
    cfg = dict(sim1_ip='192.0.2.1', sim1_number=2, sim1_channels=2,
               sim2_ip='192.0.2.2', sim2_enabled=True, sim2_number=1, sim2_channels=3)
    db = Mock()
    db.load_sys_config.side_effect = lambda:deepcopy(cfg)
    db.save_sys_config.side_effect = lambda saved:cfg.update(saved) or True
    manager = DeviceManager(db)
    # Hardware application retains its existing shutdown/disconnect sequence.
    manager.safe_shutdown_outputs = Mock(return_value=True)
    manager.disconnect_all = Mock(return_value=True)
    hardware = HardwareConfigTab(db)
    hardware.status_timer.stop()
    hardware.device_manager = manager
    hardware.refresh_hardware_status = Mock()
    debug = SimulatorTab(manager)
    debug.status_timer.stop()
    hardware.configuration_changed.connect(debug.refresh_configuration)
    yield cfg, db, manager, hardware, debug
    hardware.shutdown()
    debug.shutdown()
    hardware.close()
    debug.close()


def row(hardware, key):
    return next(i for i, spec in enumerate(hardware.DEVICE_SPECS) if spec[0] == key)


def test_debug_is_read_only_and_never_saves_or_replaces_drivers(pages):
    cfg, db, manager, _, debug = pages
    original = deepcopy(cfg)
    devices = list(manager.simulators)
    assert not debug.findChildren(QTabWidget)
    assert not hasattr(debug, 'settings_table')
    assert len(debug.channel_checks) == 24
    assert not hasattr(debug, 'save_simulator_settings')
    debug.refresh_configuration()
    assert cfg == original and manager.simulators == devices
    db.save_sys_config.assert_not_called()
    manager.disconnect_all.assert_not_called()
    manager.safe_shutdown_outputs.assert_not_called()
    assert not hasattr(debug, 'btn_settings') and not hasattr(debug, 'btn_hardware_config')


def test_hardware_save_refreshes_all_debug_fields_routes_and_recipe_preview(pages):
    _, db, manager, hardware, debug = pages
    first, second, third = (row(hardware, f'sim{i}') for i in (1, 2, 3))
    hardware.table.item(first, 3).setText('192.0.2.10')
    hardware.table.item(first, 4).setText('7001')
    hardware.table.cellWidget(first, 7).setValue(3)
    hardware.table.cellWidget(first, 8).setValue(4)
    hardware.table.item(second, 0).setCheckState(Qt.Unchecked)
    hardware.table.item(third, 0).setCheckState(Qt.Checked)
    hardware.table.cellWidget(third, 7).setValue(1)
    hardware.table.cellWidget(third, 8).setValue(3)
    preview = SimulatorReadForm(manager)
    try:
        assert '5 个通道' in preview.preview.text()
        hardware.save_config()
        pump(lambda:hardware.table.isEnabled())
        assert not debug.isVisible()  # The completion signal also updates hidden debug pages.
        assert debug.device_combo.count() == 3  # two enabled devices and the whole set
        index=debug.device_combo.findData(id(manager.simulators[0]))
        assert debug.device_combo.itemText(index) == '模拟电池3（4路）'
        assert debug.device_combo.itemData(index,Qt.ToolTipRole) == '192.0.2.10:7001'
        assert debug.device_combo.findData(id(manager.simulators[1])) == -1
        assert debug.table.rowCount() == 7
        assert [(ch,local) for ch,sim,local in debug._routes if sim is manager.simulators[2]] == [(1,1),(2,2),(3,3)]
        assert manager._get_sim_and_ch(4) == (manager.simulators[0], 1)
        assert manager._get_sim_and_ch(8) == (None, None)
        preview.show()
        app.processEvents()
        assert '7 个通道' in preview.preview.text()
        assert 'CH4 → 设备3/CH1' in preview.preview.toolTip()
        assert db.save_sys_config.call_count == 1
        manager.safe_shutdown_outputs.assert_called_once_with(check_offline=False)
        manager.disconnect_all.assert_called_once()
        manager.simulators[0].connect = Mock()
        debug.refresh_configuration()
        manager.simulators[0].connect.assert_not_called()
    finally:preview.close()


def test_failed_application_keeps_debug_on_actual_old_parameters(pages):
    cfg, _, manager, hardware, debug = pages
    manager.safe_shutdown_outputs.return_value = False
    manager.last_shutdown_errors = ['test shutdown failure']
    hardware.table.item(row(hardware, 'sim1'), 3).setText('192.0.2.99')
    hardware.save_config()
    pump(lambda:hardware.table.isEnabled())
    assert cfg['sim1_ip'] == '192.0.2.99'
    index=debug.device_combo.findData(id(manager.simulators[0]))
    assert debug.device_combo.itemData(index,Qt.ToolTipRole).startswith('192.0.2.1:')
    assert manager.simulators[0].ip == '192.0.2.1'
    assert '操作失败' in hardware.lbl_operation.text()
    manager.disconnect_all.assert_not_called()


def test_in_place_device_number_change_refreshes_channel_labels(pages):
    _, _, manager, _, debug = pages
    manager.enabled_devices['sim2'] = False
    debug.refresh_configuration()
    assert '模拟电池2' in debug.device_combo.currentText()
    manager.simulators[0].sequence_number = 4
    debug.refresh_configuration()
    assert '模拟电池4' in debug.device_combo.currentText()


@pytest.mark.parametrize('column,value', [(3, 'invalid-ip'), (4, '0'), (4, '65536')])
def test_hardware_center_validates_simulator_connection_parameters(pages, column, value):
    _, db, manager, hardware, _ = pages
    hardware.table.item(row(hardware, 'sim1'), column).setText(value)
    with patch('ui.tabs.hardware_config_tab.QMessageBox.warning') as warning:
        hardware.save_config()
    warning.assert_called_once()
    db.save_sys_config.assert_not_called()
    manager.disconnect_all.assert_not_called()
