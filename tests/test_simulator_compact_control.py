"""按设备和勾选通道隔离调试控制，并把页面高度留给通道表。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTabWidget, QScrollArea

from devices.manager import DeviceManager
from ui.tabs.simulator_tab import SimulatorTab


app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


@pytest.fixture
def page():
    db = Mock()
    db.load_sys_config.return_value = dict(sim1_channels=24, sim2_enabled=True, sim2_channels=24,
                                          sim3_enabled=True, sim3_channels=6)
    manager = DeviceManager(db)
    for sim in manager.simulators:
        sim.is_connected = True
        sim.apply_confirmed = Mock(return_value=True)
        sim.read_current_sample = Mock(return_value=.125)
        sim.read_stable_snapshot = Mock(side_effect=lambda channels, **_: [(3.2, 3.2, True) for _ in channels])
        sim.read_source_settings = Mock(return_value=dict(range='AUTO', current=.5))
    widget = SimulatorTab(manager)
    widget.status_timer.stop()
    yield widget, manager, db
    widget.shutdown()
    widget.close()


def choose(widget, key, local_channels):
    widget.device_combo.setCurrentIndex(widget.device_combo.findData(key))
    for local, check in widget.channel_checks.items():
        check.setChecked(local in local_channels)


def test_scope_lists_only_enabled_devices_plus_whole_set_and_starts_unselected(page):
    widget, manager, db = page
    assert widget.device_combo.count() == 4
    assert widget.device_combo.findData(id(manager.simulators[3])) == -1
    assert widget.device_combo.itemData(3) == 'all'
    assert len(widget.channel_checks) == 24
    assert widget.selected_channels() == [] and not widget.btn_on.isEnabled()
    assert not widget.findChildren(QTabWidget)
    assert widget.findChildren(QScrollArea) == [widget.control_scroll]
    widget.set_sim_volt()
    for sim in manager.simulators:
        sim.apply_confirmed.assert_not_called()
    db.save_sys_config.assert_not_called()


@pytest.mark.parametrize('action', ['voltage', 'output', 'range', 'current'])
def test_single_device_control_cannot_reach_unselected_or_other_device_channels(page, action):
    widget, manager, _ = page
    choose(widget, id(manager.simulators[1]), [1, 6, 24])
    if action == 'voltage':
        widget.btn_set_volt.click()
    elif action == 'output':
        widget.btn_off.click()
    elif action == 'range':
        widget.range_combo.setCurrentIndex(widget.range_combo.findData('AUTO'))
        widget.btn_set_range.click()
    else:
        widget.btn_set_current.click()
    pump(lambda: not widget._io_busy() and widget.btn_off.isEnabled())
    call = manager.simulators[1].apply_confirmed.call_args
    assert call.args[:2] == (action, [1, 6, 24])
    for slot in (0, 2, 3, 4, 5):
        manager.simulators[slot].apply_confirmed.assert_not_called()


def test_whole_scope_controls_same_selected_local_numbers_on_each_enabled_device(page):
    widget, manager, _ = page
    choose(widget, 'all', [2, 8, 24])
    assert len(widget.selected_channels()) == 7  # third device only has six channels
    widget.btn_set_volt.click()
    pump(lambda: not widget._io_busy() and widget.btn_off.isEnabled())
    assert manager.simulators[0].apply_confirmed.call_args.args[1] == [2, 8, 24]
    assert manager.simulators[1].apply_confirmed.call_args.args[1] == [2, 8, 24]
    assert manager.simulators[2].apply_confirmed.call_args.args[1] == [2]
    manager.simulators[3].apply_confirmed.assert_not_called()


def test_select_all_boolean_and_device_switch_keep_separate_scopes(page):
    widget, manager, _ = page
    widget.chk_all.setChecked(True)
    assert len(widget.selected_channels()) == 24
    widget.chk_all.setChecked(False)
    assert widget.selected_channels() == []
    choose(widget, id(manager.simulators[2]), [1, 6])
    assert all(not widget.channel_checks[ch].isEnabled() for ch in range(7, 25))
    assert [ch for ch, _, _ in widget.selected_channels()] == [49, 54]
    choose(widget, 'all', [24])
    assert [ch for ch, _, _ in widget.selected_channels()] == [24, 48]
    widget.device_combo.setCurrentIndex(widget.device_combo.findData(id(manager.simulators[2])))
    assert [ch for ch, _, _ in widget.selected_channels()] == [49, 54]
    assert sum(not widget.table.isRowHidden(row) for row in range(widget.table.rowCount())) == 6


def test_readbacks_only_touch_selected_routes_and_keep_other_cells(page):
    widget, manager, _ = page
    choose(widget, id(manager.simulators[1]), [3])
    for ch in widget._rows:
        widget.on_read_all_progress(ch, 2.7)
        widget.on_current_read_progress(ch, .5)
    widget.btn_read_current.click()
    pump(lambda: not widget._io_busy() and widget.btn_read_current.isEnabled())
    manager.simulators[1].read_current_sample.assert_called_once()
    assert manager.simulators[1].read_current_sample.call_args.args == (3,)
    assert widget.table.item(widget._rows[27], 2).text() == '0.125'
    assert widget.table.item(widget._rows[26], 2).text() == '0.5'
    widget.btn_read.click()
    pump(lambda: not widget._io_busy() and widget.btn_read.isEnabled())
    assert manager.simulators[1].read_stable_snapshot.call_args.args == ([3],)
    assert widget.table.item(widget._rows[27], 1).text() == '3.2000'
    assert widget.table.item(widget._rows[26], 1).text() == '2.7000'
    for slot in (0, 2):
        manager.simulators[slot].read_current_sample.assert_not_called()
        manager.simulators[slot].read_stable_snapshot.assert_not_called()


def test_parameter_readback_uses_selection_and_reports_each_channel(page):
    widget, manager, _ = page
    choose(widget, id(manager.simulators[1]), [2, 9])
    widget.btn_read_parameters.click()
    pump(lambda: not widget._io_busy() and widget.btn_read_parameters.isEnabled())
    assert [call.args[0] for call in manager.simulators[1].read_source_settings.call_args_list] == [2, 9]
    assert '500 mA' in widget.table.item(widget._rows[26], 0).toolTip()
    manager.simulators[0].read_source_settings.assert_not_called()


def test_control_captures_scope_and_keeps_ui_alive_while_device_blocks(page):
    widget, manager, _ = page
    choose(widget, id(manager.simulators[1]), [1, 4])
    entered, release = threading.Event(), threading.Event()
    def blocked(*_, **__):
        entered.set()
        assert release.wait(3)
        return True
    manager.simulators[1].apply_confirmed.side_effect = blocked
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    try:
        started = time.monotonic()
        widget.btn_set_volt.click()
        assert time.monotonic() - started < .15
        assert entered.wait(1)
        assert not widget.device_combo.isEnabled() and not widget.chk_all.isEnabled()
        assert not widget.channel_checks[1].isEnabled()
        widget.btn_set_volt.click()
        pump(lambda: len(ticks) >= 3)
        assert manager.simulators[1].apply_confirmed.call_count == 1
        release.set()
        pump(lambda: not widget._io_busy() and widget.btn_set_volt.isEnabled())
        assert manager.simulators[1].apply_confirmed.call_args.args[1] == [1, 4]
    finally:
        release.set()
        timer.stop()


def test_two_columns_keep_table_full_height_and_controls_at_right(page):
    widget, _, _ = page
    widget.resize(1280, 800)
    widget.show()
    app.processEvents()
    assert widget.table.y() == 8 and widget.table.height() >= 780
    assert widget.table.visualItemRect(widget.table.item(23, 0)).bottom() < widget.table.viewport().height()
    assert widget.control_scroll.x() > widget.table.geometry().right()
    assert all(check.mapTo(widget.control_scroll.viewport(), check.rect().bottomRight()).y()
               < widget.control_scroll.viewport().height() for check in widget.channel_checks.values())
    widths = [button.width() for button in widget.control_buttons]
    assert max(widths) <= 150
    widget.resize(800, 650)
    app.processEvents()
    assert widget.width() == 800
    assert widget.table.y() == 8 and widget.table.height() >= 630
    assert widths == [button.width() for button in widget.control_buttons]
    assert all(button.mapTo(widget, button.rect().topRight()).x() < widget.width() for button in widget.control_buttons)
    from PySide6.QtCore import QPoint
    inputs = (widget.spin_volt, widget.range_combo, widget.current_limit)
    settings = (widget.btn_set_volt, widget.btn_set_range, widget.btn_set_current)
    assert all(control.mapTo(widget, QPoint()).y() == button.mapTo(widget, QPoint()).y()
               for control, button in zip(inputs, settings))
    assert len({control.mapTo(widget, QPoint()).x() for control in inputs}) == 1
