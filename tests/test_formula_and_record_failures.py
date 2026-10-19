from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, SubStep, SubStepType, TestStep as Step, StepType
from core.formula_eval import evaluate_formula


app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('bad', ['ERROR', 'TIMEOUT 0', None, True, float('nan'), float('inf')])
def test_formula_rejects_invalid_referenced_input(bad):
    with pytest.raises(ValueError):
        evaluate_formula('STEP1 + 1', {'STEP1': bad})


def test_formula_only_requires_referenced_inputs_and_preserves_precision():
    assert evaluate_formula('step1 / 10', {'STEP1': .001, 'UNUSED': 'ERROR'}) == .0001
    assert evaluate_formula('round(max(A, 1) / 3, 2)', {'A': 2}) == .67


@pytest.mark.parametrize('formula', ['(1).__class__', '[1][0]', '1e308 * 1e308', 'missing+1'])
def test_formula_invalid_expression_fails(formula):
    with pytest.raises((ValueError, OverflowError)):
        evaluate_formula(formula, {})


def test_variable_and_formula_do_not_round_measurements():
    worker = ChannelWorker(1, [], device_manager=SimpleNamespace())
    worker.variables['voltage'] = .001234567
    ok, value = worker._execute_sub_step_logic(SubStep(SubStepType.READ_VAR, {'params': 'VAR:voltage'}))
    assert ok and value == .001234567
    ok, value = worker._execute_sub_step_logic(SubStep(SubStepType.CALCULATE, {'params': 'FORMULA:voltage/10'}))
    assert ok and value == .001234567 / 10


@pytest.mark.parametrize('where', ['item', 'event'])
def test_record_queue_rejection_finishes_and_shuts_down(where):
    step = Step('read', StepType.CUSTOM)
    step.min_limit, step.max_limit = 0, 1
    step.sub_steps = [SubStep(SubStepType.WAIT, {'params': '0'})]
    db = Mock()
    getattr(db, 'log_item_result' if where == 'item' else 'log_step_event').side_effect = RuntimeError('queue closed')
    manager = Mock()
    manager.safe_shutdown_outputs.return_value = True
    worker = ChannelWorker(1, [step], device_manager=manager, db_manager=db)
    worker.set_test_info(1)
    worker.is_running = True
    seen = []
    worker.test_finished.connect(lambda cid, passed: seen.append(passed))
    with patch.object(worker, '_export_excel_record'), patch('core.engine.QTimer.singleShot'):
        if where == 'item':
            worker.current_step_results = [(.5, True)]
            worker.on_step_complete()
        else:
            worker.execute_sub_step(step.sub_steps[0])
    assert seen == [False]
    assert worker.record_error == 'queue closed'
    assert not worker.is_running
    manager.safe_shutdown_outputs.assert_called_once()
    db.finish_test.assert_called_once_with(1, 'RECORD_ERROR')
