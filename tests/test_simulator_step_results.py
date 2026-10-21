"""Legacy simulator controls must record results before parent PASS/FAIL judgment."""
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from core.engine import (ChannelWorker, NGStrategy, StepType, SubStep,
                         SubStepFailStrategy, SubStepType, TestEngine as Engine,
                         TestStep as ParentStep)

app = QApplication.instance() or QApplication([])
STAGES = ('broadcast_range', 'broadcast_current', 'broadcast_voltage', 'broadcast_output')


def manager():
    return SimpleNamespace(**{name: Mock(return_value=True) for name in STAGES})


def recipe_item():
    return dict(name='[2.5V]_单体电压设置', min='PASS', max='--', standard_type='字符串',
                retry_count='复测1次', strategy='NG继续', unit='NULL', sub_steps=[dict(
                    name='模拟电池快捷配置', type='设置仪表', device='电池模拟器 (NGI 83624A Simulator)',
                    action='快捷配置', params='2.5V / 1000.0mA / ON / Range:HIGH / CH:ALL',
                    is_judgment=True, fail_strategy='忽略继续')])


@pytest.mark.parametrize('failed_stage', [None, *STAGES])
def test_control_registers_verdict_signal_and_evidence(failed_stage):
    mgr = manager()
    if failed_stage:
        getattr(mgr, failed_stage).return_value = False
    step = ParentStep('setup', StepType.CUSTOM, NGStrategy.CONTINUE_ON_NG)
    sub = SubStep(SubStepType.SET_INSTRUMENT, recipe_item()['sub_steps'][0], SubStepFailStrategy.CONTINUE)
    step.add_sub_step(sub)
    db = Mock()
    worker = ChannelWorker(1, [step], device_manager=mgr, db_manager=db)
    worker.test_id = 7
    results = []
    worker.sub_step_finished.connect(lambda *result: results.append(result))

    ok, raw_value = worker._execute_sub_step_logic(sub)

    expected = failed_stage is None
    status = 'PASS' if expected else 'FAIL'
    assert (ok, raw_value) == (expected, None)
    assert worker.current_step_results == [(status, True)]
    assert results == [(1, 0, 0, status, None)]
    assert worker.last_output == worker.step_outputs[sub.step_id] == dict(ok=expected, value=None)
    event = worker.results_by_id[sub.step_id][0]
    assert event['status'] == status and event['parent_step_id'] == step.step_id
    db.log_step_event.assert_called_once_with(7, event)
    if failed_stage and failed_stage != 'broadcast_output':
        mgr.broadcast_output.assert_not_called()


def test_invalid_control_still_records_failure_without_enabling_outputs():
    mgr = manager()
    step = ParentStep('setup', StepType.CUSTOM)
    data = recipe_item()['sub_steps'][0]
    data['params'] = 'badV / ON / CH:ALL'
    sub = SubStep(SubStepType.SET_INSTRUMENT, data)
    step.add_sub_step(sub)
    worker = ChannelWorker(1, [step], device_manager=mgr)
    ok, value = worker._execute_sub_step_logic(sub)
    assert not ok and value.startswith('FAIL:')
    assert worker.current_step_results == [(value, True)]
    for name in STAGES:
        getattr(mgr, name).assert_not_called()


@pytest.mark.parametrize('outputs,expected,attempts', [
    ([True], True, 1), ([False, False], False, 2), ([False, True], True, 2),
])
def test_first_item_parent_verdict_and_retest_follow_real_control_result(outputs, expected, attempts):
    mgr = manager()
    mgr.broadcast_output.side_effect = outputs
    engine = Engine(device_manager=mgr)
    children, parents, finished = [], [], []
    engine.sub_step_finished.connect(lambda *result: children.append(result))
    engine.step_finished.connect(lambda *result: parents.append(result))
    engine.channel_test_finished.connect(lambda cid, passed: finished.append(passed))
    try:
        assert engine.start_channel_test(1, [recipe_item()], debug_run=True)
        deadline = time.monotonic() + 3
        while (engine.workers or not finished) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert not engine.workers and finished == [expected]
        assert len(children) == attempts
        assert [result[3] for result in children] == ['PASS' if ok else 'FAIL' for ok in outputs]
        assert len(parents) == 1
        assert parents[0][-2:] == (expected, 'PASS' if expected else 'FAIL')
        for name in STAGES:
            assert getattr(mgr, name).call_count == attempts
    finally:
        engine.stop_all()
        app.processEvents()
