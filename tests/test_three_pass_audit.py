from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from core.engine import TestEngine as Engine
from data.db_manager import DBManager
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


@pytest.fixture
def pages(tmp_path):
    db = DBManager(str(tmp_path / 'three_pass.db'))
    editor = ConfigTab(db)
    overview = OverviewTab(Engine(), db)
    overview.stop_monitor()
    yield db, editor, overview
    overview.begin_shutdown()
    overview._prepare_task.shutdown()
    overview._plc_task.shutdown()
    overview.close()
    editor.close()
    db.close()


def recipe():
    return dict(name='original', recipe_number=1, items=[dict(name='item', min='', max='',
        standard_type='不判断', sub_steps=[dict(name='wait', action='等待', params='0', type='等待')])])


def test_round1_editing_old_recipe_does_not_add_an_unrequested_barcode_rule(pages):
    _, editor, _ = pages
    editor.load_recipe_to_tree(recipe())
    assert editor._collect_recipe_data('copy')['sn_regex'] == ''


@pytest.mark.parametrize('corrupt', [False, True])
def test_round2_failed_editor_selection_does_not_overwrite_target_with_old_steps(pages, corrupt):
    db, editor, _ = pages
    assert db.save_recipe_json('original', recipe())
    editor.refresh_recipe_list()
    original = editor.recipe_tree.topLevelItem(0)
    editor.recipe_tree.setCurrentItem(original)
    editor.on_recipe_selected(original, 0)
    assert editor.step_tree.topLevelItemCount() == 1
    target = QTreeWidgetItem(['bad'])
    editor.recipe_tree.addTopLevelItem(target)
    editor.recipe_tree.setCurrentItem(target)
    if corrupt:
        from pathlib import Path
        Path(db.get_recipe_dir(), 'bad.json').write_text('{', encoding='utf-8')
    with patch('PySide6.QtWidgets.QMessageBox.warning'):
        editor.on_recipe_selected(target, 0)
    assert editor.current_recipe_name is None
    assert editor.step_tree.topLevelItemCount() == 0
    assert not editor.edit_container.isEnabled()


def test_round2_partial_editor_load_failure_clears_half_built_tree(pages):
    db, editor, _ = pages
    data = recipe()
    data['items'].append({'name': 'malformed', 'sub_steps': []})
    assert db.save_recipe_json('partial', data)
    target = QTreeWidgetItem(['partial'])
    editor.recipe_tree.addTopLevelItem(target)
    editor.recipe_tree.setCurrentItem(target)
    with patch('PySide6.QtWidgets.QMessageBox.warning'):
        editor.on_recipe_selected(target, 0)
    assert editor.current_recipe_name is None
    assert editor.step_tree.topLevelItemCount() == 0
    assert not editor.edit_container.isEnabled()


@pytest.mark.parametrize('save_as', [False, True])
def test_round2_save_rejects_selected_name_that_was_never_loaded(pages, save_as):
    db, editor, _ = pages
    editor.load_recipe_to_tree(recipe())
    editor.current_recipe_name = 'original'
    target = QTreeWidgetItem(['different'])
    editor.recipe_tree.addTopLevelItem(target)
    editor.recipe_tree.setCurrentItem(target)
    with patch.object(db, 'save_recipe_json') as save, \
            patch('PySide6.QtWidgets.QMessageBox.warning'), \
            patch('PySide6.QtWidgets.QInputDialog.getText') as prompt:
        (editor.save_recipe_as if save_as else editor.save_recipe)()
        save.assert_not_called()
        prompt.assert_not_called()


def test_round3_close_blocks_late_scan_request(pages):
    _, _, page = pages
    page._closing = True
    with patch('PySide6.QtWidgets.QInputDialog.getText', return_value=('', False)) as prompt:
        assert page.request_scan_and_start() is False
        prompt.assert_not_called()


def test_round3_scanner_dialog_cannot_be_opened_recursively_by_another_start(pages):
    _, _, page = pages
    from unittest.mock import Mock
    page.start_single_channel_test = Mock(return_value=True)
    entries = []
    def prompt(*args, **kwargs):
        entries.append(1)
        if len(entries) == 1:
            assert page.request_scan_and_start() is False
            return 'SN001', True
        return '', False
    with patch('PySide6.QtWidgets.QInputDialog.getText', side_effect=prompt):
        assert page.request_scan_and_start() is True
    assert len(entries) == 1
    page.start_single_channel_test.assert_called_once()


def test_round3_closing_during_scan_does_not_launch_test_when_dialog_returns(pages):
    _, _, page = pages
    from unittest.mock import Mock
    page.start_single_channel_test = Mock(return_value=True)
    def prompt(*args, **kwargs):
        page._closing = True
        return 'SN001', True
    with patch('PySide6.QtWidgets.QInputDialog.getText', side_effect=prompt):
        assert page.request_scan_and_start() is False
    page.start_single_channel_test.assert_not_called()


def test_round3_direct_test_start_is_blocked_after_shutdown_begins(pages):
    _, _, page = pages
    page._closing = True
    page._confirmed_barcode = 'SN001'
    page.txt_sn.setText('SN001')
    page.serialize_tree_to_dict = lambda: recipe()
    with patch.object(page._prepare_task, 'run', return_value=True) as run:
        assert page.start_single_channel_test() is False
        run.assert_not_called()
