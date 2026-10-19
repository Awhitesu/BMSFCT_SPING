import base64
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox
from data.db_manager import DBManager
from ui.tabs.overview_tab import OverviewTab
from ui.dialogs.step_dialog import StepDialog

app=QApplication.instance() or QApplication([])


def test_checked_debug_bypasses_scan_and_sn_rule_without_changing_barcode(tmp_path):
    db=DBManager(str(tmp_path/'debug.db'))
    page=OverviewTab(db_manager=db);page.stop_monitor();page.engine=SimpleNamespace(workers={})
    page.current_recipe_data={'sn_regex':'^PRODUCTION[0-9]+$'}
    page.serialize_tree_to_dict=lambda:dict(name='r',items=[dict(name='A'),dict(name='B')])
    calls=[]
    try:
        with patch.object(page,'_can_debug_tests',return_value=True), \
             patch.object(page,'request_scan_and_start',side_effect=AssertionError('must not scan')), \
             patch.object(page._prepare_task,'run',side_effect=lambda action,callback:calls.append(action) or True), \
             patch.object(page,'_prepare_run',return_value=(False,'done')) as prepare:
            assert page._debug_test_items([1])
            calls[0]()
        snapshot,barcode=prepare.call_args.args
        assert barcode=='' and snapshot['_debug_run']
        assert snapshot['_mes_precheck']=={}
        assert [i.get('skip_runtime',False) for i in snapshot['items']]==[True,False]
        assert page.txt_sn.text()=='' and page._confirmed_barcode==''
        assert page._debug_requested_indices is None
    finally:
        page.begin_shutdown();page.close();db.close()


def test_production_start_still_requires_scan(tmp_path):
    db=DBManager(str(tmp_path/'production.db'));page=OverviewTab(db_manager=db);page.stop_monitor();page.engine=SimpleNamespace(workers={})
    try:
        with patch.object(QMessageBox,'warning') as warning, patch.object(page._prepare_task,'run') as run:
            assert not page.start_single_channel_test()
            warning.assert_called_once();run.assert_not_called()
    finally:page.begin_shutdown();page.close();db.close()


def test_imported_uds_rows_delete_multi_and_roundtrip():
    script='10 03 => 50 03\n22 F1 90 => 62 F1 90\n22 01 01 => 62 01 01'
    page=StepDialog(step_data=dict(device='UDS 诊断',action='UDS 顺序交互',type='CAN交互',
        params='LIST64:'+base64.urlsafe_b64encode(script.encode()).decode()))
    try:
        # Select the UDS page explicitly without depending on localized action labels.
        for i in range(page.device_combo.count()):
            if 'UDS' in page.device_combo.itemText(i):page.device_combo.setCurrentIndex(i);break
        assert page.uds_table.rowCount()==3
        assert page.uds_delete_btn.parent() is not None
        assert page.page_uds.isAncestorOf(page.uds_delete_btn)
        page.uds_table.clearSelection()
        selection=page.uds_table.selectionModel()
        for row in (0,2):
            selection.select(page.uds_table.model().index(row,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        page.uds_delete_btn.click()
        assert page.uds_table.rowCount()==1
        assert page._uds_script()=='22 F1 90 => 62 F1 90'
        saved=page.get_data();restored=StepDialog(step_data=saved)
        try:
            assert restored._uds_script()=='22 F1 90 => 62 F1 90'
            restored.clear_uds_rows()
            assert restored._uds_script()==''
        finally:restored.close()
    finally:page.close()
