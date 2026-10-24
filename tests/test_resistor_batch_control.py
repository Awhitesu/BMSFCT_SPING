"""验证电阻板的实际批量载荷、跨板并发及停止边界，使用内存模拟传输。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.board_control import control_board
from devices.manager import DeviceManager
from devices.resistor_bank import channel_routes, execute_selection, operate_channels
from devices.rn_board import RNBoard, board_settings


@pytest.fixture(autouse=True)
def settle_delay(monkeypatch):
    delay = Mock()
    monkeypatch.setattr('devices.rn_board.time', SimpleNamespace(sleep=delay))
    return delay


def board(number, write_hook=None):
    setting = next(s for s in board_settings({}) if s['key'] == f'resistor{number}')
    setting['enabled'] = True
    driver = RNBoard(setting)
    driver.is_connected = True
    driver.values = [number * 1000 + ch * 10 for ch in range(1, 7)]
    driver.requests = []

    def request(function, payload):
        driver.requests.append((function, payload))
        address, count = struct.unpack('>HH', payload[:4])
        if function == 16:
            if write_hook:
                write_hook(driver, payload)
            assert count % 2 == 0 and payload[4] == count * 2
            values = list(struct.unpack('>' + 'I' * (count // 2), payload[5:]))
            driver.values[address // 2:(address + count) // 2] = values
            return payload[:4]
        assert function == 3
        words = [word for value in driver.values for word in (value >> 16, value & 65535)]
        words = words[address:address + count]
        return bytes([len(words) * 2]) + struct.pack('>' + 'H' * len(words), *words)

    driver.request = request
    return driver


def manager_for(cards):
    locks = {}
    return SimpleNamespace(resistor_routes=lambda: channel_routes(cards),
                           device_step_lock=lambda key: locks.setdefault(key, threading.RLock()))


def test_full_board_is_one_write_one_read_and_one_settle(settle_delay):
    driver = board(1)
    assert driver.set_resistances(144100) == {ch: 144100 for ch in range(1, 7)}
    assert driver.requests == [
        (16, struct.pack('>HHB', 0, 12, 24) + struct.pack('>I', 144100) * 6),
        (3, struct.pack('>HH', 0, 12)),
    ]
    settle_delay.assert_called_once_with(.1)


@pytest.mark.parametrize('channels', [[2, 3, 4], [1, 3, 5], [2, 4, 6]])
def test_partial_selection_never_writes_unselected_channels(channels, settle_delay):
    driver = board(1)
    previous = list(driver.values)
    assert driver.set_resistances(10000, reversed(channels)) == {ch: 10000 for ch in channels}
    assert driver.values == [10000 if ch in channels else value for ch, value in enumerate(previous, 1)]
    writes = [payload for function, payload in driver.requests if function == 16]
    assert len(writes) == (1 if channels == [2, 3, 4] else 3)
    assert sum(struct.unpack('>HH', payload[:4])[1] for payload in writes) == len(channels) * 2
    assert driver.requests[-1] == (3, struct.pack('>HH', 0, 12))
    settle_delay.assert_called_once_with(.1)


@pytest.mark.parametrize('value,channels', [
    (11, [1, 2]), (1500010, [1, 2]), (True, [1]), (1000, []),
    (1000, [1, 1]), (1000, [0, 1]), (1000, [1, 7]), (1000, [True]),
])
def test_invalid_batch_cannot_write(value, channels):
    driver = board(1)
    with pytest.raises(ValueError):
        driver.set_resistances(value, channels)
    assert driver.requests == []


def test_write_error_and_readback_mismatch_cannot_pass_or_fall_back():
    driver = board(1)
    driver.request = Mock(return_value=b'bad echo')
    with pytest.raises(ValueError, match='写入响应'):
        driver.set_resistances(1000)
    assert driver.request.call_count == 1
    driver = board(1)
    driver.read_resistances = lambda: {'resistances': [1000] * 5 + [999]}
    with pytest.raises(ValueError, match='CH6'):
        driver.set_resistances(1000)
    assert len(driver.requests) == 1


def test_cancel_between_sparse_groups_stops_later_writes():
    cancelled = threading.Event()
    driver = board(1, lambda *_: cancelled.set())
    previous = list(driver.values)
    with pytest.raises(RuntimeError, match='测试已停止'):
        driver.set_resistances(1000, [1, 3, 5], cancelled=cancelled.is_set)
    assert len(driver.requests) == 1
    assert driver.values == [1000] + previous[1:]


@pytest.mark.parametrize('result', [None, True, {1: 1000}, {1: 1000, 2: True},
                                   {1: 1000, 2: 1000, 3: 1000}])
def test_control_boundary_rejects_unconfirmed_batch(result):
    driver = SimpleNamespace(set_resistances=Mock(return_value=result))
    with pytest.raises(RuntimeError, match='批量阻值设置未确认'):
        control_board(driver, 'resistances', channels=[1, 2], resistance=1000)


def test_explicit_zero_batch_is_allowed_after_cancellation():
    driver = board(1)
    assert control_board(driver, 'resistances', channels=range(1, 7), resistance=0,
                         cancelled=lambda: True) == {ch: 0 for ch in range(1, 7)}
    assert driver.values == [0] * 6


@pytest.mark.parametrize('entry', ['sequence', 'debug'])
def test_all_six_boards_run_concurrently_with_one_write_each(entry):
    barrier = threading.Barrier(6)
    thread_ids = set()
    def simultaneous(*_):
        thread_ids.add(threading.get_ident())
        barrier.wait(timeout=3)
    cards = {str(n): board(n, simultaneous) for n in range(1, 7)}
    if entry == 'sequence':
        values = execute_selection(manager_for(cards), '所有', 144100, False, lambda: False, lambda _: None)
    else:
        result = operate_channels(cards, range(1, 37), 'set', 144100)
        assert result['ok']
        values = result['values']
    assert values == {ch: 144100 for ch in range(1, 37)}
    assert len(thread_ids) == 6
    for driver in cards.values():
        assert [function for function, _ in driver.requests] == [16, 3]


def test_sequence_read_batches_each_board_and_preserves_global_mapping():
    cards = {'a': board(2), 'b': board(1)}
    values = execute_selection(manager_for(cards), '1,3,7,9', 0, True, lambda: False, lambda _: None)
    assert values == {1: 1010, 3: 1030, 7: 2010, 9: 2030}
    for driver in cards.values():
        assert driver.requests == [(3, struct.pack('>HH', 0, 12))]


def test_one_board_failure_does_not_skip_other_boards_or_pass_sequence():
    def fail(*_):
        raise ValueError('Modbus exception 0x04')
    cards = {'a': board(1, fail), 'b': board(2)}
    with pytest.raises(RuntimeError, match='Modbus exception 0x04'):
        execute_selection(manager_for(cards), '所有', 144100, False, lambda: False, lambda _: None)
    assert cards['b'].values == [144100] * 6
    assert [function for function, _ in cards['a'].requests] == [16]


def test_shutdown_runs_all_boards_in_parallel_and_continues_after_failure():
    barrier = threading.Barrier(6)
    def shutdown(driver, *_):
        barrier.wait(timeout=3)
        if driver.setting['number'] == 1:
            raise ConnectionError('board 1 no response')
    manager = DeviceManager.__new__(DeviceManager)
    manager.rn_boards = {str(n): board(n, shutdown) for n in range(1, 7)}
    manager.simulator_channels = lambda: []
    manager.hv_source = manager.ca550 = manager.dg2052 = manager.dut_power = None
    manager.it6333b = SimpleNamespace(is_connected=True, output_control=Mock(return_value=True))
    assert not manager.safe_shutdown_outputs()
    assert 'board 1 no response' in '; '.join(manager.last_shutdown_errors)
    manager.it6333b.output_control.assert_has_calls([((1, False),), ((2, False),), ((3, False),)])
    for n in range(2, 7):
        driver = manager.rn_boards[str(n)]
        assert driver.values == [0] * 6
        assert [function for function, _ in driver.requests] == [16, 3]


def test_stop_keeps_ui_responsive_and_retires_after_parallel_cleanup():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from core.engine import TestEngine as Engine
    from core.device_steps import encode
    from ui.tabs.overview_tab import OverviewTab

    app = QApplication.instance() or QApplication([])
    entered = threading.Barrier(7)
    release = threading.Event()
    def hold_setting(_driver, payload):
        if struct.unpack('>I', payload[5:9])[0] != 0:
            entered.wait(timeout=3)
            assert release.wait(3)
    cards = {str(n): board(n, hold_setting) for n in range(1, 7)}
    manager = DeviceManager.__new__(DeviceManager)
    manager.rn_boards = cards
    routes = manager_for(cards)
    manager.resistor_routes = routes.resistor_routes
    manager.device_step_lock = routes.device_step_lock
    manager.simulator_channels = lambda: []
    manager.hv_source = manager.ca550 = manager.dg2052 = manager.dut_power = None
    manager.it6333b = manager.easy320 = None
    engine = Engine(manager)
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab(engine)
    page.stop_monitor()
    QTreeWidgetItem(page.step_tree, ['1', 'temperature setting', '', '', '', '', 'WAIT'])
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    recipe = [{'name': 'temperature setting', 'sub_steps': [
        {'type': 'set', 'params': encode('resistor', 'resistance_batch',
                                       {'channels': '所有', 'resistance': 144100})}]}]
    def pump_until(predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert predicate()
    try:
        assert engine.start_channel_test(1, recipe)
        entered.wait(timeout=3)
        pump_until(lambda: len(ticks) >= 3)
        start = time.monotonic()
        page.stop_single_channel_test()
        assert time.monotonic() - start < .15
        assert page.lbl_big_status.text() == 'STOPPING'
        assert engine.workers
        before = len(ticks)
        pump_until(lambda: len(ticks) >= before + 3)
        release.set()
        pump_until(lambda: not engine.workers)
        assert page.lbl_big_status.text() == 'STOPPED'
        for driver in cards.values():
            assert driver.values == [0] * 6
            assert sum(function == 16 for function, _ in driver.requests) == 2
    finally:
        release.set()
        timer.stop()
        engine.stop_all()
        pump_until(lambda: not engine.workers)
        page.begin_shutdown()
        for task in (page._prepare_task, page._plc_task, page._recipe_match_task, page._stats_task):
            task.shutdown()
        page.close()
