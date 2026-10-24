"""启动仅列出配方候选，用户选择之前不建立可执行序列。"""
import json
import os
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('is_admin', [True, False])
def test_startup_and_list_refresh_wait_for_selection(tmp_path, is_admin):
    data = dict(name='first', items=[dict(name='voltage', min=1, max=4, unit='V', sub_steps=[])])
    for filename in ('first.json', 'second.json'):
        (tmp_path / filename).write_text(json.dumps(data), encoding='utf-8')
    db = Mock()
    db.get_recipe_dir.return_value = str(tmp_path)
    db.load_sys_config.return_value = {}
    plc_calls, can_calls = [], []
    with patch.object(OverviewTab, '_can_debug_tests', lambda self: is_admin), \
            patch.object(OverviewTab, 'sync_recipe_number_to_plc', lambda self, number: plc_calls.append(number)), \
            patch.object(OverviewTab, 'apply_selected_recipe_can', lambda self, recipe: can_calls.append(recipe)):
        page = OverviewTab(db_manager=db)
        page.stop_monitor()
        try:
            for preserve in (False, True):
                page.update_recipes(['first.json', 'second.json'], preserve_results=preserve)
                assert page.combo_recipe.count() == 2
                assert page.combo_recipe.currentIndex() == -1
                assert page.combo_recipe.currentText() == ''
                assert page.step_tree.topLevelItemCount() == 0
                assert page.current_recipe_data == {} and page.current_recipe_path == ''
            assert plc_calls == can_calls == []
            if is_admin:
                page.combo_recipe.setCurrentIndex(0)
                page.on_recipe_combo_changed(0, sync_plc=False)
                assert page.current_recipe_name == 'first'
                assert page.step_tree.topLevelItemCount() == 1
        finally:
            page.begin_shutdown()
            for name in ('_prepare_task', '_plc_task', '_stats_task', '_recipe_match_task'):
                getattr(page, name).shutdown()
            page.close()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
