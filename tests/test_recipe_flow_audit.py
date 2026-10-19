import json
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.engine import TestEngine as Engine
from data.db_manager import DBManager
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


@pytest.fixture
def pages(tmp_path):
    db = DBManager(str(tmp_path / 'audit.db'))
    overview = OverviewTab(Engine(), db)
    # These cases exercise administrator manual recipe selection.
    overview._can_debug_tests = Mock(return_value=True)
    overview.stop_monitor()
    editor = ConfigTab(db)
    yield db, overview, editor
    overview.begin_shutdown()
    overview._prepare_task.shutdown()
    overview._plc_task.shutdown()
    overview.close()
    editor.close()
    db.close()


def recipe():
    return dict(name='good', sn_regex='^SN[0-9]{8}$', recipe_number=3,
        items=[dict(name='voltage', min='1', max='4', unit='V', sub_steps=[
            dict(name='read', action='read', type='读取变量', params='VAR:voltage')])])


def test_save_as_collection_preserves_barcode_validation(pages):
    _, _, editor = pages
    editor.load_recipe_to_tree(recipe())
    editor.edit_sn_regex.setText('^SN[0-9]{8}$')
    copied = editor._collect_recipe_data('copy')
    assert copied['sn_regex'] == '^SN[0-9]{8}$'


@pytest.mark.parametrize('failure', ['missing', 'json', 'shape', 'partial'])
def test_invalid_new_recipe_cannot_leave_old_sequence_executable(pages, tmp_path, failure):
    _, page, _ = pages
    good = tmp_path / 'good.json'
    good.write_text(json.dumps(recipe()), encoding='utf-8')
    page.load_recipe_from_file(str(good))
    assert page.current_recipe_name == 'good'
    assert page.step_tree.topLevelItemCount() == 1
    bad = tmp_path / 'bad.json'
    if failure == 'json':
        bad.write_text('{', encoding='utf-8')
    elif failure == 'shape':
        bad.write_text('[]', encoding='utf-8')
    elif failure == 'partial':
        data = recipe()
        data['items'].append({'name': 123, 'sub_steps': []})
        bad.write_text(json.dumps(data), encoding='utf-8')
    with patch('PySide6.QtWidgets.QMessageBox.warning'):
        assert page.load_recipe_from_file(str(bad)) is False
    assert page.current_recipe_data == {}
    assert page.current_recipe_path == ''
    assert page.step_tree.topLevelItemCount() == 0
    assert page.serialize_tree_to_dict()['items'] == []


def test_deleted_selection_does_not_keep_previous_recipe(pages, tmp_path):
    db, page, _ = pages
    good = tmp_path / 'good.json'
    good.write_text(json.dumps(recipe()), encoding='utf-8')
    page.load_recipe_from_file(str(good))
    page.combo_recipe.clear()
    page.combo_recipe.addItem('removed', 'removed.json')
    with patch('PySide6.QtWidgets.QMessageBox.warning'):
        page.on_recipe_combo_changed(0, sync_plc=False)
    assert page.current_recipe_data == {}
    assert page.step_tree.topLevelItemCount() == 0


def test_empty_recipe_list_clears_executable_sequence(pages, tmp_path):
    _, page, _ = pages
    path = tmp_path / 'good.json'
    path.write_text(json.dumps(recipe()), encoding='utf-8')
    page.load_recipe_from_file(str(path))
    page.update_recipes([])
    assert page.current_recipe_data == {}
    assert page.step_tree.topLevelItemCount() == 0


def test_selection_is_resolved_only_in_configured_recipe_directory(pages):
    db, page, _ = pages
    page.combo_recipe.clear()
    page.combo_recipe.addItem('removed', 'removed.json')
    page.load_recipe_from_file = Mock(return_value=False)
    page.on_recipe_combo_changed(0, sync_plc=False)
    import os
    page.load_recipe_from_file.assert_called_once_with(
        os.path.join(db.get_recipe_dir(), 'removed.json'), sync_plc=False)
