from pathlib import Path
from core.engine import TestEngine as Engine, ChannelWorker, SubStep, SubStepType
from core.single_channel_recipe import single_channel_items


def test_external_reporting_and_multichannel_runtime_removed():
    engine = Engine()
    for name in ('api_client', 'update_api_client', '_heartbeat_worker',
                 'handle_barrier_reached', 'resource_queues', 'sync_groups',
                 'begin_batch_start', 'report_system_step_info'):
        assert not hasattr(engine, name)
    worker = ChannelWorker(1, [])
    assert not hasattr(worker, '_upload_final_data_and_reset')
    root = Path(__file__).resolve().parents[1]
    assert not (root / 'core/api_client.py').exists()
    assert not (root / 'ui/tabs/api_tab.py').exists()


def test_retired_board_fails_instead_of_passing_silently():
    worker = ChannelWorker(1, [])
    ok, result = worker._execute_sub_step_logic(SubStep(
        SubStepType.SET_INSTRUMENT, {'device': 'Aging Board', 'params': '1,2'}))
    assert not ok
    assert '配方' in result


def test_old_synchronization_compatibility_does_not_drop_real_steps():
    original = [{'name': 'legacy', 'target_board': '主机', 'sub_steps': [
        {'name': 'sync', 'type': '同步屏障'},
        {'name': 'measure', 'type': '读取仪表', 'device': '34461A', 'sync_exec': True}]}]
    adapted = single_channel_items(original)
    assert [s['name'] for s in adapted[0]['sub_steps']] == ['measure']
    assert original[0]['sub_steps'][1]['sync_exec']
