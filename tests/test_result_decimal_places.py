"""Truncate display across recipe editing, live/cached UI and XLS; retain raw evidence."""
import copy
import json
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
import xlrd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from core.engine import ChannelWorker, TestStep as ParentStep, StepType, NGStrategy
from core.engine import TestEngine as Engine
from core.ai_recipe_assistant import validate_recipe, summarize_changes
from core.excel_recorder import export_test_record
from core.result_display import format_result
from data.db_manager import DBManager
from ui.dialogs.bulk_edit_dialog import BulkEditDialog
from ui.dialogs.monitor_dialog import MonitorDialog
from ui.dialogs.test_item_dialog import TestItemDialog as ItemDialog
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def recipe_item(name='voltage', **overrides):
    return dict(name=name, mode='范围判定', min='0', max='5', unit='V',
                standard_type='数值', decimal_places=2, sub_steps=[dict(
                    name='read', type='读取变量', device='读取变量', action='读取变量',
                    params='VAR:voltage', is_judgment=True)], **overrides)


@pytest.mark.parametrize('value,places,expected', [
    (2.5122222222221, 2, '2.51'), (2.519, 2, '2.51'), (-2.519, 2, '-2.51'),
    (1.15, 2, '1.15'), (0.29, 2, '0.29'), ('1.99999999999999999999', 2, '1.99'),
    (2.999, 0, '2'), (-2.999, 0, '-2'), (2.5, 2, '2.50'),
    (0, 2, '0.00'), (-0.009, 2, '0.00'), ('2.519 mA', 2, '2.51 mA'),
    ('2.519V', 2, '2.51V'), ('1.2345e-3', 4, '0.0012'),
    (2.519, None, '2.519'), ('PASS', 2, 'PASS'), ('FAIL:2.519', 2, 'FAIL:2.519'),
    ('1 | 2.519', 2, '1 | 2.519'), ('NaN', 2, 'NaN'), (float('inf'), 2, 'inf'),
    (True, 2, 'True'), (None, 2, 'None'),
])
def test_truncation_without_binary_multiplication_or_rounding(value, places, expected):
    assert format_result(value, dict(decimal_places=places)) == expected


@pytest.mark.parametrize('places', [-1, 16, True, '2', 2.0])
def test_invalid_legacy_setting_preserves_display(places):
    assert format_result('2.519', dict(decimal_places=places)) == '2.519'


@pytest.mark.parametrize('standard', ['字符串', '变量'])
def test_numeric_looking_identifiers_remain_exact(standard):
    assert format_result('001.239', dict(decimal_places=2, standard_type=standard)) == '001.239'


@pytest.fixture
def editor():
    db = Mock()
    db.load_sys_config.return_value = {}
    db.list_recipes.return_value = []
    page = ConfigTab(db)
    page.current_recipe_name = 'precision'
    yield page
    page.close()
    page.deleteLater()


def test_single_editor_add_edit_copy_and_file_reload(editor, monkeypatch, tmp_path):
    def add(dialog):
        assert dialog.decimal_spin.value() == -1
        dialog.name_edit.setText('voltage')
        dialog.decimal_spin.setValue(2)
        return QDialog.Accepted
    monkeypatch.setattr(ItemDialog, 'exec', add)
    editor.add_test_item()
    row = editor.step_tree.topLevelItem(0)
    editor.step_tree.setCurrentItem(row)
    assert editor._collect_recipe_data('precision')['items'][0]['decimal_places'] == 2

    def edit(dialog):
        assert dialog.decimal_spin.value() == 2
        dialog.decimal_spin.setValue(0)
        return QDialog.Accepted
    monkeypatch.setattr(ItemDialog, 'exec', edit)
    editor.edit_node()
    row.setSelected(True)
    editor.batch_copy_nodes()
    editor.batch_paste_nodes()
    saved = editor._collect_recipe_data('precision')
    assert [item['decimal_places'] for item in saved['items']] == [0, 0]
    path = tmp_path/'precision.json'
    path.write_text(json.dumps(saved), encoding='utf-8')
    editor.load_recipe_to_tree(json.loads(path.read_text(encoding='utf-8')))
    assert editor._collect_recipe_data('precision') == saved


@pytest.mark.parametrize('places', [2, 0, None])
@pytest.mark.parametrize('selection', ['parents', 'children', 'all'])
def test_bulk_decimal_changes_only_targets_and_checked_field(editor, monkeypatch, places, selection):
    rows = [recipe_item(name) for name in ('one', 'two', 'three')]
    editor.load_recipe_to_tree(dict(name='precision', items=rows))
    expected = copy.deepcopy(editor._collect_recipe_data('precision'))
    targets = [0, 2] if selection != 'all' else [0, 1, 2]
    if selection != 'all':
        for index in targets:
            row = editor.step_tree.topLevelItem(index)
            (row.child(0) if selection == 'children' else row).setSelected(True)
    def accept(dialog):
        assert not dialog.cb_decimal.isChecked() and not dialog.decimal_spin.isEnabled()
        dialog.cb_decimal.setChecked(True)
        assert dialog.decimal_spin.isEnabled()
        dialog.decimal_spin.setValue(places if places is not None else -1)
        return QDialog.Accepted
    monkeypatch.setattr(BulkEditDialog, 'exec', accept)
    editor.bulk_edit_nodes()
    for index in targets:
        expected['items'][index]['decimal_places'] = places
    assert editor._collect_recipe_data('precision') == expected
    editor.load_recipe_to_tree(expected)
    assert editor._collect_recipe_data('precision') == expected


def test_unchecked_and_cancelled_bulk_decimal_preserve_all_values(editor, monkeypatch):
    editor.load_recipe_to_tree(dict(name='precision', items=[recipe_item()]))
    expected = copy.deepcopy(editor._collect_recipe_data('precision'))
    def unchecked(dialog):
        dialog.decimal_spin.setValue(5)
        return QDialog.Accepted
    monkeypatch.setattr(BulkEditDialog, 'exec', unchecked)
    editor.bulk_edit_nodes()
    assert editor._collect_recipe_data('precision') == expected
    def cancelled(dialog):
        dialog.cb_decimal.setChecked(True)
        dialog.decimal_spin.setValue(5)
        return QDialog.Rejected
    monkeypatch.setattr(BulkEditDialog, 'exec', cancelled)
    editor.bulk_edit_nodes()
    assert editor._collect_recipe_data('precision') == expected


def test_old_recipe_defaults_to_unlimited(editor):
    item = recipe_item()
    item.pop('decimal_places')
    editor.load_recipe_to_tree(dict(name='precision', items=[item]))
    saved = editor._collect_recipe_data('precision')['items'][0]
    assert saved['decimal_places'] is None
    dialog = ItemDialog(data=saved)
    try:
        assert dialog.decimal_spin.value() == -1
        assert dialog.get_data()['decimal_places'] is None
        assert format_result(2.519, saved) == '2.519'
    finally:
        dialog.close()


def test_live_overview_and_monitor_share_parent_precision(tmp_path):
    data = dict(name='precision', items=[recipe_item()])
    path = tmp_path/'precision.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    monitor = MonitorDialog()
    try:
        assert page.load_recipe_from_file(str(path))
        page.on_worker_sub_step_finished(1, 0, 0, 'PASS', 2.519)
        page.on_worker_step_finished(1, 0, 'voltage', True, 2.519)
        row = page.step_tree.topLevelItem(0)
        assert row.text(4) == row.child(0).text(4) == '2.51'
        assert page.serialize_tree_to_dict()['items'][0]['decimal_places'] == 2
        monitor.load_steps_from_data(data['items'])
        monitor.on_sub_step_finished(1, 0, 0, 'PASS', -2.519)
        monitor.on_step_finished(1, 0, 'voltage', True, -2.519)
        row = monitor.step_tree.topLevelItem(0)
        assert row.text(3) == row.child(0).text(3) == '-2.51'
    finally:
        monitor.close()
        page.begin_shutdown()
        page._prepare_task.shutdown()
        page._plc_task.shutdown()
        page.close()


def test_raw_value_drives_judgment_signals_database_and_cached_monitor():
    step = ParentStep('voltage', StepType.CUSTOM, NGStrategy.CONTINUE_ON_NG)
    step.min_limit, step.max_limit, step.decimal_places = 0, 2.51, 2
    db = Mock()
    worker = ChannelWorker(1, [step], db_manager=db)
    worker.test_id, worker.is_running = 1, True
    worker.current_step_results = [(2.519, True)]
    results = []
    worker.step_finished.connect(lambda *result: results.append(result))
    with patch('core.engine.QTimer.singleShot'):
        worker.on_step_complete()
    assert results[0][-2:] == (False, 2.519)
    assert db.log_item_result.call_args.args[4:6] == (2.519, 'NG')
    assert worker.step_measured_values[(0, 'voltage')] == 2.519
    monitor = MonitorDialog(engine=SimpleNamespace(workers={1: worker}))
    try:
        row = monitor.step_tree.topLevelItem(0)
        assert row.text(3) == '2.51' and row.text(4) == 'NG'
        assert row.data(0, Qt.UserRole)['decimal_places'] == 2
    finally:
        monitor.close()


def test_engine_loads_precision_and_passes_unmodified_reading_to_consumers(monkeypatch):
    def read(worker, sub):
        worker.current_step_results.append((2.519, True))
        return True, 2.519
    monkeypatch.setattr(ChannelWorker, '_execute_sub_step_logic', read)
    engine = Engine()
    results, finished = [], []
    engine.step_finished.connect(lambda *result: results.append(result))
    engine.channel_test_finished.connect(lambda *result: finished.append(result))
    try:
        assert engine.start_channel_test(1, [recipe_item()])
        assert engine.workers[1].steps[0].decimal_places == 2
        deadline = time.monotonic() + 3
        while (engine.workers or not finished) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        assert results[0][-2:] == (True, 2.519) and finished == [(1, True)]
    finally:
        engine.stop_all()
        app.processEvents()


@pytest.mark.parametrize('places', [None, 0, 2, 15, True, -1, 16, 2.0, '2'])
def test_ai_edit_validates_decimal_metadata_and_describes_changes(places):
    old = dict(name='precision', items=[recipe_item()])
    new = copy.deepcopy(old)
    new['items'][0]['decimal_places'] = places
    valid = places is None or type(places) is int and 0 <= places <= 15
    errors = validate_recipe(new)
    assert bool([error for error in errors if 'decimal_places' in error]) is not valid
    if places != 2:
        assert any('小数位数' in line for line in summarize_changes(old, new))


def test_xls_uses_frozen_recipe_identity_and_keeps_database_raw(tmp_path):
    db = DBManager(str(tmp_path/'raw.db'))
    rows = [dict(recipe_item('same'), step_id='one'),
            dict(recipe_item('same'), step_id='two', decimal_places=3),
            dict(recipe_item('serial'), step_id='serial', standard_type='字符串')]
    try:
        identity = db.start_new_test(1, 'CH1', 'SN', 'precision',
                                     context={'recipe': {'items': rows}})
        for row, value in zip(rows, (2.51999, -2.51999, '001.239')):
            db.log_item_result(identity, row['name'], 0, 5, value, 'NG', step_id=row['step_id'])
        db.finish_test(identity, 'FAIL')
        path = export_test_record(db.db_path, identity, str(tmp_path/'reports'))
        sheet = xlrd.open_workbook(path).sheet_by_index(0)
        assert [sheet.cell_value(row, 1) for row in range(18, 21)] == ['2.51', '-2.519', '001.239']
        with sqlite3.connect(db.db_path) as conn:
            values = conn.execute('SELECT measured_value_raw FROM test_items_results ORDER BY id').fetchall()
        assert [value[0] for value in values] == ['2.51999', '-2.51999', '001.239']
    finally:
        db.close()
