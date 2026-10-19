from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.engine import ChannelWorker, SubStep, SubStepType


@pytest.mark.parametrize('kind', [SubStepType.SET_INSTRUMENT, SubStepType.READ_INSTRUMENT])
@pytest.mark.parametrize('device', ['Unknown instrument', 'AFE power'])
def test_unhandled_legacy_device_is_failure_instead_of_empty_success(kind, device):
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace())
    worker.is_running = True
    ok, result = worker._execute_sub_step_logic(SubStep(kind,
        {'device': device, 'action': 'set', 'params': '2.5V'}))
    assert ok is False
    assert result and 'FAIL' in result


@pytest.mark.parametrize('channels', ['', '1,invalid', '0', '1.5', '1,,2'])
def test_easy320_validates_whole_channel_list_before_any_control(channels):
    plc = SimpleNamespace(write_relay=Mock(return_value=True))
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(easy320=plc))
    worker.is_running = True
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device': 'Easy320', 'action': '闭合继电器', 'params': channels}))
    assert not ok
    plc.write_relay.assert_not_called()


def test_easy320_valid_legacy_channels_remain_one_based():
    plc = SimpleNamespace(write_relay=Mock(return_value=True))
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(easy320=plc))
    worker.is_running = True
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device': 'Easy320', 'action': '闭合继电器', 'params': '1,2,9'}))
    assert ok
    assert [call.args for call in plc.write_relay.call_args_list] == [(0, True), (1, True), (8, True)]


@pytest.mark.parametrize('value', [float('inf'), float('nan'), True])
def test_legacy_simulator_read_rejects_non_measurement_values(value):
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(measure_voltage=lambda *a, **k: value))
    worker.is_running = True
    ok, result = worker._execute_sub_step_logic(SubStep(SubStepType.READ_INSTRUMENT,
        {'device': 'simulator', 'action': '读取电压', 'params': '电压'}))
    assert not ok
    assert 'FAIL' in result


def test_easy320_cancellation_after_first_control_blocks_later_closures():
    plc = SimpleNamespace(write_relay=Mock())
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace(easy320=plc))
    worker.is_running = True
    plc.write_relay.side_effect = lambda *args: (worker.cancel_event.set(), True)[1]
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device': 'Easy320', 'action': '闭合继电器', 'params': '1,2,9'}))
    assert not ok
    plc.write_relay.assert_called_once_with(0, True)


def test_unhandled_control_cannot_pass_an_entire_non_judged_test_item(tmp_path):
    import sqlite3
    import time
    from PySide6.QtWidgets import QApplication
    from core.engine import TestEngine as Engine
    from data.db_manager import DBManager
    app = QApplication.instance() or QApplication([])
    db = DBManager(str(tmp_path / 'dispatch.db'))
    manager = SimpleNamespace(safe_shutdown_outputs=Mock(return_value=True))
    engine = Engine(manager, db)
    finished = []
    engine.channel_test_finished.connect(lambda _, passed: finished.append(passed))
    try:
        assert engine.start_channel_test(1, [{'name': 'control', 'standard_type': '不判断',
            'sub_steps': [{'type': '设置仪表', 'device': 'Unknown instrument', 'params': '2.5V'}]}])
        deadline = time.monotonic() + 5
        while engine.workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert not engine.workers
        assert finished == [False]
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT result FROM test_main').fetchone()[0] == 'NG'
            assert conn.execute('SELECT result FROM test_items_results').fetchone()[0] == 'NG'
    finally:
        engine.stop_all()
        deadline = time.monotonic() + 5
        while engine.workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        db.close()
