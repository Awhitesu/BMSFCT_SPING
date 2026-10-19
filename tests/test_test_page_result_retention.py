import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.main_window import MainWindow
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


@pytest.fixture
def completed_page(tmp_path):
    data = dict(name='sample',recipe_number=1,items=[dict(name='voltage',min=1,max=5,unit='V',
        sub_steps=[dict(action='read',device='Variable',type='读取变量',params='VAR:voltage')])])
    path = tmp_path/'sample.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    with patch.object(OverviewTab,'load_default_or_first_recipe',lambda self:None):
        page = OverviewTab()
    page._can_debug_tests = Mock(return_value=True)
    page.stop_monitor()
    assert page.load_recipe_from_file(str(path))
    page.combo_recipe.addItem('sample','sample.json')
    row = page.step_tree.topLevelItem(0)
    row.setText(4,'3.456');row.setText(6,'PASS');row.setExpanded(True)
    row.setCheckState(0,Qt.Checked);row.child(0).setText(4,'3.456');row.child(0).setText(6,'PASS')
    page.step_tree.setCurrentItem(row.child(0))
    page.lbl_big_status.setText('PASS');page.lbl_timer.setText('01:23');page.elapsed_seconds=83
    page.txt_sn.setText('SN123');page._confirmed_barcode='SN123'
    page.test_log_view.setPlainText('last completed run')
    yield page,path,data
    page.is_testing=False;page.begin_shutdown();page.close()


def test_returning_tabs_preserves_rows_values_selection_and_summary(completed_page):
    page,path,data = completed_page
    row = page.step_tree.topLevelItem(0)
    window = SimpleNamespace(tab_config=SimpleNamespace(get_all_recipes=lambda:['sample.json','new.json']),tab_overview=page)
    for _ in range(3):
        MainWindow.on_tab_changed(window,1)
        MainWindow.on_tab_changed(window,0)
    assert page.step_tree.topLevelItem(0) is row
    assert row.text(4)=='3.456' and row.text(6)=='PASS'
    assert row.child(0).text(4)=='3.456' and row.child(0).text(6)=='PASS'
    assert row.isExpanded() and row.checkState(0)==Qt.Checked
    assert page.step_tree.currentItem() is row.child(0)
    assert page.lbl_big_status.text()=='PASS' and page.lbl_timer.text()=='01:23'
    assert page.txt_sn.text()=='SN123' and page.test_log_view.toPlainText()=='last completed run'
    assert page.combo_recipe.currentData()=='sample.json'
    assert page._refresh_recipe_for_new_test()
    assert page.step_tree.topLevelItem(0) is row  # unchanged file needs no rebuild
    page.engine=SimpleNamespace(start_channel_test=Mock(return_value=True))
    snapshot=page.serialize_tree_to_dict();snapshot['operator_name']='tester'
    assert page._launch_prepared_test(snapshot,'SN456')
    assert row.text(4)=='--' and row.child(0).text(4)=='--'
    assert page.lbl_timer.text()=='00:00'


def test_edited_recipe_is_loaded_only_on_new_test_and_keeps_scanned_sn(completed_page):
    page,path,data = completed_page
    data['items'][0]['max']=9
    path.write_text(json.dumps(data),encoding='utf-8')
    row=page.step_tree.topLevelItem(0)
    page.update_recipes(['sample.json'],preserve_results=True)
    assert page.step_tree.topLevelItem(0) is row and row.text(4)=='3.456'
    assert page._refresh_recipe_for_new_test()
    assert page.step_tree.topLevelItem(0).text(3)=='9'
    assert page.txt_sn.text()=='SN123' and page._confirmed_barcode=='SN123'


def test_removed_recipe_keeps_display_but_cannot_run_stale_sequence(completed_page):
    page,path,data=completed_page
    path.unlink()
    row=page.step_tree.topLevelItem(0)
    page.update_recipes([],preserve_results=True)
    assert page.step_tree.topLevelItem(0) is row and row.text(6)=='PASS'
    assert page.combo_recipe.currentIndex()==-1
    with patch.object(QMessageBox,'warning') as warning:
        assert not page._refresh_recipe_for_new_test()
        warning.assert_called_once()
    assert page.step_tree.topLevelItem(0) is row


def test_changed_recipe_rejects_debug_indices_from_old_sequence(completed_page):
    page,path,data=completed_page
    data['items'].insert(0,dict(name='new first',sub_steps=[]))
    path.write_text(json.dumps(data),encoding='utf-8')
    page.update_recipes(['sample.json'],preserve_results=True)
    with patch.object(QMessageBox,'information') as message:
        assert not page._refresh_recipe_for_new_test(debug_run=True)
        message.assert_called_once()
    assert page.step_tree.topLevelItem(0).text(1)=='new first'
