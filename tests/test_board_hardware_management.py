from copy import deepcopy
from unittest.mock import Mock, patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from devices.manager import DeviceManager
from devices.rn_board import board_settings
from core.hardware_status import configuration_snapshot
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.board_debug_tab import BoardDebugTab
from ui.tabs.resistor_bank_tab import ResistorBankTab

app = QApplication.instance() or QApplication([])


@pytest.fixture
def pages():
    cfg = {'rn_boards': {'insulation1': dict(enabled=True, ip='192.0.2.1', port=502, unit=3, limit=30000000),
                         'resistor1': dict(enabled=True, ip='192.0.2.2', port=502, unit=1, number=4, limit=1500000)}}
    db = Mock()
    db.load_sys_config.side_effect = lambda: deepcopy(cfg)
    def save(value):
        cfg.clear(); cfg.update(deepcopy(value)); return True
    db.save_sys_config.side_effect = save
    manager = DeviceManager(db)
    page = HardwareConfigTab(db); page.status_timer.stop()
    yield page, manager, cfg
    page.shutdown(); page.close()


def row(page, key):
    return next(i for i,s in enumerate(page.DEVICE_SPECS) if s[0] == key)


def test_board_rows_shared_save_preserves_range_and_sequence(pages):
    page, manager, cfg = pages
    settings = board_settings(cfg)
    assert len([s for s in page.DEVICE_SPECS if s[2] == 'Modbus TCP 板卡']) == 14
    for s in settings:
        assert page.table.item(row(page,s['key']),0).checkState() == (Qt.Checked if s['enabled'] else Qt.Unchecked)
    i = row(page,'insulation1')
    page.table.item(i,3).setText('192.0.2.19')
    page.table.item(i,4).setText('1502')
    page.table.item(i,5).setText('7')
    page.table.item(row(page,'resistor1'),0).setCheckState(Qt.Unchecked)
    with patch.object(QMessageBox,'information'):
        page.save_config()
    assert cfg['rn_boards']['insulation1']['ip'] == '192.0.2.19'
    assert cfg['rn_boards']['insulation1']['port'] == 1502
    assert cfg['rn_boards']['insulation1']['unit'] == 7
    assert cfg['rn_boards']['insulation1']['limit'] == 30000000
    assert cfg['rn_boards']['resistor1']['number'] == 4
    assert cfg['rn_boards']['resistor1']['enabled'] is False
    assert 'insulation1_enabled' not in cfg
    manager.update_config()
    debug = BoardDebugTab(manager,'insulation')
    try:
        assert debug.fields[0][0].text() == '192.0.2.19'
        assert debug.fields[0][2].value() == 7
    finally:
        debug.shutdown(); debug.close()


def test_pages_reload_external_board_changes_without_touching_other_drafts(pages):
    page, manager, cfg = pages
    debug = BoardDebugTab(manager,'insulation')
    try:
        cfg['rn_boards']['insulation1']['ip'] = '192.0.2.27'
        debug.reload_saved_config()
        assert debug.fields[0][0].text() == '192.0.2.27'
        hvrow = row(page,'hv')
        page.table.item(hvrow,3).setText('192.0.2.99')
        page.show(); app.processEvents()
        assert page.table.item(row(page,'insulation1'),3).text() == '192.0.2.27'
        assert page.table.item(hvrow,3).text() == '192.0.2.99'
    finally:
        debug.shutdown(); debug.close()


@pytest.mark.parametrize('column,value', [(4,'0'),(5,'256'),(3,'invalid')])
def test_invalid_board_connection_is_not_saved(pages,column,value):
    page, _, cfg = pages
    before = deepcopy(cfg)
    page.table.item(row(page,'insulation1'),column).setText(value)
    with patch.object(QMessageBox,'warning') as warning:
        page.save_config()
        warning.assert_called_once()
    assert cfg == before


def test_board_applied_state_and_disabled_init(pages):
    page, manager, cfg = pages
    snapshot = configuration_snapshot(manager)
    assert snapshot['insulation1']['baud'] == 3
    assert snapshot['resistor1']['number'] == 4
    page._status_snapshot = (manager.get_all_device_status(), snapshot)
    page._render_hardware_status()
    assert page.table.item(row(page,'insulation1'),6).text() == '离线'
    page.table.item(row(page,'insulation1'),5).setText('9')
    assert page.table.item(row(page,'insulation1'),6).text() == '修改未应用'
    page.table.item(row(page,'insulation1'),0).setCheckState(Qt.Unchecked)
    assert page.table.item(row(page,'insulation1'),6).text() == '停用未应用'
    manager.enabled_devices = {s[0]:False for s in page.DEVICE_SPECS}
    cfg['rn_boards']['insulation1']['enabled'] = False
    cfg['rn_boards']['resistor1']['enabled'] = False
    with patch.object(manager,'get_rn_board') as get:
        assert manager.init_all_devices()
        get.assert_not_called()


def test_resistor_sequence_reload_and_stale_save_protection(pages):
    page, manager, cfg = pages
    bank = ResistorBankTab(manager)
    try:
        assert bank.active_channels() == list(range(19,25))
        cfg['rn_boards']['resistor1']['number'] = 2
        bank.reload_saved_config()
        assert bank.config.cellWidget(0,1).value() == 2
        assert bank.active_channels() == list(range(7,13))
        cfg['rn_boards']['resistor1']['enabled'] = False
        with patch.object(QMessageBox,'warning') as warning:
            bank.save()
            warning.assert_called_once()
        assert not bank.active_channels()
        assert cfg['rn_boards']['resistor1']['enabled'] is False
        before = deepcopy(cfg)
        with patch.object(QMessageBox,'warning') as warning:
            page.save_config()
            warning.assert_called_once()
        assert cfg == before
    finally:
        bank.shutdown(); bank.close()


def test_duplicate_board_addresses_rejected(pages):
    page, _, cfg = pages
    before = deepcopy(cfg)
    page.table.item(row(page,'resistor1'),3).setText('192.0.2.1')
    with patch.object(QMessageBox,'warning') as warning:
        page.save_config()
        warning.assert_called_once()
    assert cfg == before
