import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from core.engine import TestEngine as Engine, ChannelWorker, SubStep, SubStepType
from core.single_channel_recipe import single_channel_items

app = QApplication.instance() or QApplication([])


def legacy_recipe():
    return [{'name': 'test', 'exec_mode': '顺序执行', 'is_block_start': True,
             'is_block_end': True, 'sub_steps': [
                 {'name': 'barrier', 'type': '同步屏障', 'params': 'legacy string'},
                 {'name': 'lock', 'type': '申请顺序锁'},
                 {'name': 'one', 'type': '设置仪表', 'sync_exec': True, 'seq_exec': True},
                 {'name': 'two', 'type': '设置仪表', 'params': {'sync_exec': True, 'value': 2}},
                 {'name': 'unlock', 'type': '释放顺序锁'}]}]


def test_legacy_adapter_preserves_order_and_original_recipe():
    recipe = legacy_recipe()
    adapted = single_channel_items(recipe)
    assert [s['name'] for s in adapted[0]['sub_steps']] == ['one', 'two']
    assert 'sync_exec' not in adapted[0]['sub_steps'][0]
    assert adapted[0]['sub_steps'][1]['params'] == {'value': 2}
    assert recipe[0]['is_block_start']
    assert len(recipe[0]['sub_steps']) == 5


def test_direct_legacy_control_steps_do_not_wait_or_acquire_resources():
    worker = ChannelWorker(1, [])
    for kind in (SubStepType.BARRIER, SubStepType.ACQUIRE_SEQ_LOCK, SubStepType.RELEASE_SEQ_LOCK):
        assert worker._execute_sub_step_logic(SubStep(kind, {})) == (True, 'OK')
        assert not hasattr(worker, "is_waiting_for_sync")


def test_legacy_recipe_runs_without_barrier_or_injected_locks(monkeypatch):
    ran = []
    def execute(worker, sub, *args):
        ran.append(sub.params['name'])
        assert not sub.params.get('sync_exec')
        assert not sub.params.get('seq_exec')
        return True, 'PASS'
    monkeypatch.setattr(ChannelWorker, '_execute_sub_step_logic', execute)
    engine = Engine()
    assert engine.start_channel_test(1, legacy_recipe())
    deadline = time.monotonic() + 3
    while engine.workers and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.005)
    assert not engine.workers
    assert ran == ['one', 'two']
    assert not hasattr(engine, "sync_groups")
