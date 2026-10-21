"""Current debug reads share hardware routes and preserve signed native mA values."""
import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from devices.manager import DeviceManager
from devices.ngi_83624 import NGI83624
from ui.tabs.simulator_tab import SimulatorTab, ReadAllWorker

app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic()+4
    while not predicate():
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.002)
    app.processEvents()


@pytest.fixture
def page():
    db = Mock()
    db.load_sys_config.return_value = dict(sim1_number=2, sim1_channels=2,
                                          sim2_enabled=True, sim2_number=1, sim2_channels=1)
    manager = DeviceManager(db)
    for sim in manager.simulators:
        sim.read_stable = Mock(return_value=-.0002)
        sim.read_current_sample = Mock(return_value=-.0002)
        for method in ('output_control', 'output_controls', 'set_voltage', 'set_voltages', 'set_range', 'set_current_limit'):
            setattr(sim, method, Mock(return_value=True))
    widget = SimulatorTab(manager)
    widget.status_timer.stop()
    yield widget, manager, db
    widget.shutdown()
    widget.close()


def assert_no_output_writes(manager):
    for sim in manager.simulators:
        for method in ('output_control', 'output_controls', 'set_voltage', 'set_voltages', 'set_range', 'set_current_limit'):
            getattr(sim, method).assert_not_called()


def test_single_button_uses_global_channel_mapping_and_displays_signed_ma(page):
    widget, manager, db = page
    assert widget.table.columnCount() == 3
    assert widget.table.horizontalHeaderItem(2).text() == '实测电流 (mA)'
    widget.spin_ch.setCurrentIndex(widget.spin_ch.findData(2))
    widget.on_read_all_progress(2, 2.7)
    widget.btn_read_current.click()
    pump(lambda:not widget.operation.isRunning())
    assert manager.simulators[0].read_stable.call_args.args[:2] == (1, 'current')
    manager.simulators[1].read_stable.assert_not_called()
    assert float(widget.table.item(1, 2).text()) == -.0002
    assert float(widget.lbl_read_current.text()) == -.0002
    assert 'mA' in widget.table.item(1, 2).toolTip()
    assert widget.table.item(1, 1).text() == '2.7000'
    assert_no_output_writes(manager)
    db.save_sys_config.assert_not_called()


def test_all_current_button_handles_partial_failure_without_losing_other_channels(page):
    widget, manager, _ = page
    manager.simulators[1].read_current_sample.return_value = -1.0
    def samples(channel, **_):
        if channel == 2:raise TimeoutError('current query timeout')
        return .0000001
    manager.simulators[0].read_current_sample.side_effect = samples
    for ch in (1, 2, 3):widget.on_read_all_progress(ch, 2.5+ch/10)
    voltage_text = [widget.table.item(row, 1).text() for row in range(3)]
    widget.btn_read_all_current.click()
    pump(lambda:widget.read_all_worker.wait(0) and widget.btn_read_all_current.isEnabled())
    assert float(widget.table.item(0, 2).text()) == -1.0
    assert float(widget.table.item(1, 2).text()) == .0000001
    assert widget.table.item(2, 2).text() == 'Error'
    assert 'current query timeout' in widget.table.item(2, 2).toolTip()
    assert set(widget.current_warnings) == {3}
    assert '异常通道：CH3' in widget.lbl_result.text()
    assert [widget.table.item(row, 1).text() for row in range(3)] == voltage_text
    assert [call.args[0] for call in manager.simulators[0].read_current_sample.call_args_list] == [1, 2]
    manager.simulators[2].read_current_sample.assert_not_called()
    assert_no_output_writes(manager)
    manager.simulators[0].read_current_sample.side_effect = None
    manager.simulators[0].read_current_sample.return_value = 4.5
    widget.btn_read_all_current.click()
    pump(lambda:widget.read_all_worker.wait(0) and widget.btn_read_all_current.isEnabled())
    assert widget.table.item(2, 2).text() == '4.5' and not widget.current_warnings


def test_failed_single_read_replaces_stale_value_and_keeps_the_error(page):
    widget, manager, _ = page
    widget.on_current_read_progress(1, 5)
    manager.simulators[1].read_stable.side_effect = TimeoutError('single current timeout')
    widget.btn_read_current.click()
    pump(lambda:not widget.operation.isRunning())
    assert widget.lbl_read_current.text() == widget.table.item(0, 2).text() == 'Error'
    assert 'single current timeout' in widget.lbl_read_current.toolTip()
    assert 'single current timeout' in widget.lbl_result.text()
    assert_no_output_writes(manager)


def test_single_read_is_background_and_selection_change_cannot_mislabel_the_result(page):
    widget, manager, _ = page
    entered, release = threading.Event(), threading.Event()
    def read(*_):
        entered.set()
        assert release.wait(3)
        return -2.5
    manager.simulators[1].read_stable.side_effect = read
    try:
        started = time.monotonic()
        widget.btn_read_current.click()
        assert time.monotonic()-started < .15
        assert entered.wait(1)
        assert not widget.btn_read_current.isEnabled()
        widget.btn_read_current.click()
        widget.spin_ch.setCurrentIndex(widget.spin_ch.findData(2))
        release.set()
        pump(lambda:not widget.operation.isRunning())
        assert widget.lbl_read_current.text() == '—'
        assert widget.table.item(0, 2).text() == '-2.5'
        assert manager.simulators[1].read_stable.call_count == 1
    finally:release.set()


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), 9.9e37])
def test_bulk_invalid_sample_is_reported_as_failure_not_a_numeric_reading(value):
    driver = NGI83624('unused')
    driver.read_current_sample = Mock(return_value=value)
    worker = ReadAllWorker([(25, driver, 1)], quantity='current')
    values, errors = [], []
    worker.progress.connect(lambda ch, val:values.append((ch, val)))
    worker.read_failed.connect(lambda ch, text:errors.append((ch, text)))
    worker.run()
    assert not values and len(errors) == 1
    assert errors[0][0] == 25 and '电流读取失败' in errors[0][1]


def test_bulk_reads_devices_in_parallel_and_keeps_local_channels():
    one, two = NGI83624('unused', max_channels=2), NGI83624('unused', max_channels=2)
    barrier = threading.Barrier(2)
    def acquire(channel, **_):
        barrier.wait(2)
        return -float(channel)
    one.read_current_sample = Mock(side_effect=acquire)
    two.read_current_sample = Mock(side_effect=acquire)
    worker = ReadAllWorker([(5, one, 1), (77, two, 2)], quantity='current')
    values = []
    worker.progress.connect(lambda ch, val:values.append((ch, val)))
    worker.run()
    assert values == [(5, -1.), (77, -2.)]


def test_voltage_read_after_current_preserves_current_column(page):
    widget, manager, _ = page
    widget.on_current_read_progress(1, -.0123)
    manager.simulators[1].read_stable.return_value = 3.25
    widget.btn_read.click()
    pump(lambda:not widget.operation.isRunning())
    assert widget.table.item(0, 1).text() == '3.2500'
    assert widget.table.item(0, 2).text() == '-0.0123'
