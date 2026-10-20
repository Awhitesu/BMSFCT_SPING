"""Parent policy persistence and retry-before-stop execution, without hardware."""
import copy
import time
from collections import Counter
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QEvent, QCoreApplication
from PySide6.QtWidgets import QApplication, QDialog

from core.ai_recipe_assistant import validate_recipe, summarize_changes
from core.engine import TestEngine as Engine, ChannelWorker
from ui.dialogs.test_item_dialog import TestItemDialog as ParentItemDialog
from ui.dialogs.bulk_edit_dialog import BulkEditDialog
from ui.tabs.config_tab import ConfigTab


app = QApplication.instance() or QApplication([])


def item(strategy='任何NG停止', retry='不复测', name='first'):
    return dict(name=name, mode='范围判定', standard_type='数值', min='1', max='3',
                unit='V', retry_count=retry, strategy=strategy, sub_steps=[
                    dict(name='setup', device='测试占位', action='设置', params='2',
                         type='设置仪表', is_judgment=False, fail_strategy='失败停止'),
                    dict(name='measure', device='读取变量', action='读取变量', params='VAR:value',
                         type='读取变量', is_judgment=True, fail_strategy='失败停止')])


@pytest.fixture
def editor():
    db = Mock()
    db.load_sys_config.return_value = {}
    db.list_recipes.return_value = []
    page = ConfigTab(db)
    page.current_recipe_name = 'policy'
    yield page
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('strategy,label', [('任何NG停止', '失败停止'), ('NG继续', '失败继续'),
                                           ('关键NG停止', '失败继续'), (None, '失败停止')])
def test_parent_policy_roundtrips_editor_dialog_and_clipboard(editor, strategy, label):
    data = item()
    if strategy is None:
        data.pop('strategy')
    else:
        data['strategy'] = strategy
    expected = strategy or '任何NG停止'
    editor.load_recipe_to_tree(dict(name='policy', can_config={}, items=[data]))
    saved = editor._collect_recipe_data('policy')
    assert saved['items'][0]['strategy'] == expected
    dialog = ParentItemDialog(editor, data=saved['items'][0])
    try:
        assert [dialog.stop_combo.itemText(i) for i in range(dialog.stop_combo.count())] == ['失败停止', '失败继续']
        assert dialog.stop_combo.currentText() == label
        assert dialog.get_data()['strategy'] == expected
    finally:
        dialog.close()
        dialog.deleteLater()
    node = editor.step_tree.topLevelItem(0)
    editor.step_tree.setCurrentItem(node)
    node.setSelected(True)
    editor.batch_copy_nodes()
    editor.batch_paste_nodes()
    pasted = editor._collect_recipe_data('copy')
    assert [row['strategy'] for row in pasted['items']] == [expected, expected]
    editor.load_recipe_to_tree(pasted)
    assert [row['strategy'] for row in editor._collect_recipe_data('reload')['items']] == [expected, expected]


def test_add_and_edit_parent_policy_without_changing_child_policy(editor, monkeypatch):
    def accept_dialog(dialog):
        dialog.name_edit.setText('parent')
        dialog.stop_combo.setCurrentText('失败继续')
        dialog.retry_combo.setCurrentText('复测3次')
        return QDialog.Accepted
    monkeypatch.setattr(ParentItemDialog, 'exec', accept_dialog)
    editor.add_test_item()
    assert editor._collect_recipe_data('policy')['items'][0]['strategy'] == 'NG继续'
    data = item('NG继续', '复测3次')
    editor.load_recipe_to_tree(dict(name='policy', can_config={}, items=[data]))
    editor.step_tree.setCurrentItem(editor.step_tree.topLevelItem(0))
    children_before = copy.deepcopy(editor._collect_recipe_data('policy')['items'][0]['sub_steps'])
    def edit_dialog(dialog):
        assert dialog.stop_combo.currentText() == '失败继续'
        assert dialog.retry_combo.currentText() == '复测3次'
        dialog.stop_combo.setCurrentText('失败停止')
        return QDialog.Accepted
    monkeypatch.setattr(ParentItemDialog, 'exec', edit_dialog)
    editor.edit_node()
    saved = editor._collect_recipe_data('policy')['items'][0]
    assert saved['strategy'] == '任何NG停止'
    assert saved['retry_count'] == '复测3次'
    assert saved['sub_steps'] == children_before
    assert all(sub['fail_strategy'] == '失败停止' for sub in saved['sub_steps'])


@pytest.mark.parametrize('standard', ['数值', '字符串', '变量', '不判断'])
def test_stop_selector_and_confirmation_remain_visible(standard):
    dialog = ParentItemDialog(data=dict(standard_type=standard))
    try:
        dialog.show()
        app.processEvents()
        assert dialog.stop_combo.isVisible()
        assert dialog.stop_combo.geometry().bottom() < dialog.btn_ok.geometry().top()
        assert dialog.rect().contains(dialog.btn_ok.geometry())
        assert dialog.stop_combo.height() >= dialog.stop_combo.minimumSizeHint().height()
    finally:
        dialog.close()
        dialog.deleteLater()


def run_sequence(monkeypatch, strategy, retry, pass_on=None, communication_failure=False):
    calls = []
    attempts = Counter()
    def execute(worker, sub):
        parent = worker.steps[worker.current_step_index].name
        name = sub.params['name']
        calls.append((parent, name))
        if name == 'setup':
            result = 'PASS'
            success = True
        else:
            attempts[parent] += 1
            passed = parent == 'second' or (pass_on is not None and attempts[parent] >= pass_on)
            success = passed or not communication_failure
            result = 2 if passed else ('通信失败' if communication_failure else 9)
        worker.current_step_results.append((result, sub.params['is_judgment']))
        return success, result
    monkeypatch.setattr(ChannelWorker, '_execute_sub_step_logic', execute)
    engine = Engine()
    finished = []
    results = []
    engine.channel_test_finished.connect(lambda cid, passed: finished.append(passed))
    engine.step_finished.connect(lambda cid, index, name, passed, value: results.append((name, passed)))
    try:
        assert engine.start_channel_test(1, [item(strategy, retry), item(name='second')])
        deadline = time.monotonic() + 3
        while (engine.workers or not finished) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert not engine.workers and finished
        return calls, results, finished
    finally:
        engine.stop_all()
        app.processEvents()


@pytest.mark.parametrize('strategy', ['任何NG停止', 'NG继续'])
@pytest.mark.parametrize('retry,rounds', [('不复测', 1), ('复测1次', 2), ('复测3次', 4)])
@pytest.mark.parametrize('communication_failure', [False, True])
def test_stop_policy_runs_only_after_all_parent_retries(monkeypatch, strategy, retry, rounds, communication_failure):
    calls, results, finished = run_sequence(monkeypatch, strategy, retry, communication_failure=communication_failure)
    expected = [('first', 'setup'), ('first', 'measure')] * rounds
    expected_results = [('first', False)]
    if strategy == 'NG继续':
        expected += [('second', 'setup'), ('second', 'measure')]
        expected_results.append(('second', True))
    assert calls == expected
    assert results == expected_results
    assert finished == [False]


@pytest.mark.parametrize('strategy', ['任何NG停止', 'NG继续'])
@pytest.mark.parametrize('pass_on', [1, 2, 4])
def test_pass_stops_retrying_and_advances_for_both_policies(monkeypatch, strategy, pass_on):
    calls, results, finished = run_sequence(monkeypatch, strategy, '复测3次', pass_on=pass_on)
    assert calls == [('first', 'setup'), ('first', 'measure')] * pass_on + [('second', 'setup'), ('second', 'measure')]
    assert results == [('first', True), ('second', True)]
    assert finished == [True]


def test_ai_recipe_edit_validates_and_describes_parent_policy():
    old = dict(name='policy', items=[item()])
    new = copy.deepcopy(old)
    new['items'][0]['strategy'] = 'NG继续'
    assert validate_recipe(new) == []
    assert any('停止策略' in line for line in summarize_changes(old, new))
    new['items'][0]['strategy'] = '未知策略'
    assert any('strategy' in error for error in validate_recipe(new))


@pytest.mark.parametrize('strategy,label', [('任何NG停止', '失败停止'), ('NG继续', '失败继续')])
@pytest.mark.parametrize('selection', ['parents', 'children', 'all'])
def test_bulk_stop_updates_only_target_parents_and_survives_reload(editor, monkeypatch, strategy, label, selection):
    rows = [item('NG继续', '复测1次', 'first'), item(name='second'), item(name='third')]
    editor.load_recipe_to_tree(dict(name='policy', can_config={}, items=rows))
    expected = copy.deepcopy(editor._collect_recipe_data('policy'))
    targets = [0, 2] if selection != 'all' else [0, 1, 2]
    if selection != 'all':
        for index in targets:
            parent = editor.step_tree.topLevelItem(index)
            if selection == 'parents':
                parent.setSelected(True)
            else:
                parent.child(0).setSelected(True)
                parent.child(1).setSelected(True)
    def accept_bulk(dialog):
        assert not dialog.cb_stop.isChecked()
        assert not dialog.stop_combo.isEnabled()
        assert [dialog.stop_combo.itemText(i) for i in range(dialog.stop_combo.count())] == ['失败停止', '失败继续']
        dialog.cb_stop.setChecked(True)
        assert dialog.stop_combo.isEnabled()
        dialog.stop_combo.setCurrentText(label)
        return QDialog.Accepted
    monkeypatch.setattr(BulkEditDialog, 'exec', accept_bulk)
    editor.bulk_edit_nodes()
    for index in targets:
        expected['items'][index]['strategy'] = strategy
    assert editor._collect_recipe_data('policy') == expected
    editor.load_recipe_to_tree(expected)
    assert editor._collect_recipe_data('policy') == expected
    restored = ParentItemDialog(editor, data=expected['items'][targets[0]])
    try:
        assert restored.stop_combo.currentText() == label
    finally:
        restored.close()
        restored.deleteLater()


def test_bulk_retest_change_leaves_unchecked_stop_policies_untouched(editor, monkeypatch):
    editor.load_recipe_to_tree(dict(name='policy', can_config={}, items=[item('NG继续'), item(name='second')]))
    expected = copy.deepcopy(editor._collect_recipe_data('policy'))
    def accept_bulk(dialog):
        dialog.cb_retry.setChecked(True)
        dialog.retry_combo.setCurrentText('复测3次')
        dialog.stop_combo.setCurrentText('失败继续')
        assert not dialog.get_config()['change_stop']
        return QDialog.Accepted
    monkeypatch.setattr(BulkEditDialog, 'exec', accept_bulk)
    editor.bulk_edit_nodes()
    for row in expected['items']:
        row['retry_count'] = '复测3次'
    assert editor._collect_recipe_data('policy') == expected


def test_cancelled_bulk_stop_change_preserves_all_parameters(editor, monkeypatch):
    editor.load_recipe_to_tree(dict(name='policy', can_config={}, items=[item(), item(name='second')]))
    expected = copy.deepcopy(editor._collect_recipe_data('policy'))
    def cancel_bulk(dialog):
        dialog.cb_stop.setChecked(True)
        dialog.stop_combo.setCurrentText('失败继续')
        return QDialog.Rejected
    monkeypatch.setattr(BulkEditDialog, 'exec', cancel_bulk)
    editor.bulk_edit_nodes()
    assert editor._collect_recipe_data('policy') == expected
