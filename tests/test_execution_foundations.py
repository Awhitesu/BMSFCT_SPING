import json
import sqlite3
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, TestEngine as Engine, TestStep as Step, StepType
from core.recipe_identity import ensure_step_ids
from core.device_steps import encode, decode, PREFIX
from data.db_manager import DBManager
from devices.rn_board import shared_board

app = QApplication.instance() or QApplication([])


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.005)
    assert predicate()


def test_stop_returns_before_blocked_action_and_cleanup_runs_after_action(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()
    threads = []
    manager = SimpleNamespace(safe_shutdown_outputs=lambda *a, **k: (
        threads.append(threading.get_ident()), cleaned.set()))
    engine = Engine(manager)
    recipe = [{'name': 'test', 'sub_steps': [{'type': 'set', 'params': ''}]}]

    def action(*args):
        threads.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return True, 'PASS'

    monkeypatch.setattr(ChannelWorker, "_execute_sub_step_logic", action)
    assert engine.start_channel_test(1, recipe)
    try:
        pump_until(entered.is_set)
        begin = time.monotonic()
        engine.stop_test(1)
        assert time.monotonic() - begin < .1
        assert not cleaned.is_set()
        assert not engine.start_channel_test(1, recipe)
        assert not engine.start_channel_test(2, recipe)
        release.set()
        pump_until(lambda: not engine.workers)
        assert cleaned.is_set()
        assert threads[0] == threads[1] != threading.get_ident()
    finally:
        release.set()
        engine.stop_all()
        pump_until(lambda: not engine.workers)


def test_duplicate_names_preserve_separate_results_and_ids():
    data = [{'name': 'same'}, {'name': 'same'}]
    ensure_step_ids(data)
    ids = [item['step_id'] for item in data]
    data[0]['name'] = 'renamed'
    ensure_step_ids(data)
    assert ids == [item['step_id'] for item in data]
    steps = [Step('same', StepType.CUSTOM), Step('same', StepType.CUSTOM)]
    worker = ChannelWorker(1, steps)
    worker._record_step_result(1, 0, 'same', True, 1.2)
    worker._record_step_result(1, 1, 'same', False, 3.4)
    assert worker.step_measured_values[(0, 'same')] == 1.2
    assert worker.step_measured_values[(1, 'same')] == 3.4
    assert len(worker.results_by_id) == 2


def test_stable_action_decode_ignores_changed_display_text():
    text = encode('easy320', 'connect', {})
    payload = json.loads(text[len(PREFIX):])
    payload.update({key: 'renamed' for key in payload if key not in (
        'schema_version', 'device_id', 'action_id', 'values')})
    device, action, values = decode(PREFIX + json.dumps(payload))
    assert device == 'easy320' and action.key == 'connect'


def test_board_consumers_share_connection_and_lock():
    manager = SimpleNamespace()
    setting = dict(key='relay1', ip='127.0.0.1', port=502)
    first = shared_board(manager, setting)
    second = shared_board(manager, setting)
    assert first is second and first._lock is second._lock
    changed = dict(setting, ip='127.0.0.2')
    assert shared_board(manager, changed) is first
    assert second.ip == '127.0.0.2'


def test_stop_cleanup_exception_still_retires_thread():
    worker = ChannelWorker(1, [])
    completed = []
    worker.cleanup_finished.connect(lambda: completed.append(True))
    def fail():
        raise RuntimeError('offline')
    worker._shutdown_outputs = fail
    worker.stop()
    worker.stop()
    assert completed == [True]


def test_resistor_global_mapping_uses_configured_sequence():
    from devices.manager import DeviceManager
    from unittest.mock import Mock
    cfg = {'rn_boards': {'resistor1': {'enabled': True, 'ip': '127.0.0.1', 'number': 2},
                         'resistor2': {'enabled': True, 'ip': '127.0.0.2', 'number': 1}}}
    db = Mock()
    db.load_sys_config.return_value = cfg
    manager = DeviceManager(db)
    routes = manager.resistor_routes()
    assert routes[1][0] is manager.get_rn_board('resistor2')
    assert routes[7][0] is manager.get_rn_board('resistor1')
    assert routes[7][1] == 1


def test_database_result_id_migration_and_recipe_roundtrip(tmp_path):
    db = DBManager(str(tmp_path / 'test.db'))
    try:
        recipe = {'items': [{'name': 'same', 'sub_steps': [{'name': 'sub'}]}]}
        assert db.save_recipe_json('test', recipe)
        loaded = db.load_recipe_json('test')
        assert loaded['items'][0]['step_id'] == recipe['items'][0]['step_id']
        tid = db.start_new_test(1, 'shelf', 'SN', 'recipe')
        db.log_item_result(tid, 'same', 0, 2, 1, 'PASS',
                           execution_id='run1', step_id='step1', attempt=2)
        db.log_step_event(tid, dict(execution_id='run1', step_id='sub1',
                          parent_step_id='step1', attempt=1, status='PASS', value=1.2))
        db.finish_test(tid, 'PASS')
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT execution_id,step_id,attempt FROM test_items_results').fetchone() == ('run1', 'step1', 2)
            assert conn.execute('SELECT step_id,parent_step_id FROM test_step_events').fetchone() == ('sub1', 'step1')
    finally:
        db.close()
