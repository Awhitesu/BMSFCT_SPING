import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.engine import TestEngine as Engine
from data.db_manager import DBManager
from ui.tabs.overview_tab import OverviewTab

app=QApplication.instance() or QApplication([])


def test_bulk_check_operations_skip_disabled_items_and_select_failures(tmp_path):
    db=DBManager(str(tmp_path/'selection.db'))
    page=OverviewTab(db_manager=db);page.stop_monitor()
    recipe=dict(name='R',items=[dict(name='A'),dict(name='B'),dict(name='#disabled'),dict(name='C',skip_runtime=True)])
    path=tmp_path/'selection.json';path.write_text(json.dumps(recipe),encoding='utf-8')
    page.load_recipe_from_file(str(path))
    def checked():
        return [i for i in range(page.step_tree.topLevelItemCount()) if page.step_tree.topLevelItem(i).checkState(0)==Qt.Checked]
    try:
        assert page.step_tree.topLevelItemCount() == 3  # masked recipe rows are not loaded
        with patch.object(page,'_can_debug_tests',return_value=True):
            page._select_debug_items('all');assert checked()==[0,1]
            page._select_debug_items('invert');assert checked()==[]
            page.step_tree.topLevelItem(0).setCheckState(0,Qt.Checked)
            page._select_debug_items('invert');assert checked()==[1]
            page._select_debug_items('none');assert checked()==[]
            page.step_tree.topLevelItem(1).setText(6,'FAIL')
            page.step_tree.topLevelItem(2).setText(6,'FAIL')
            page._select_debug_items('failed');assert checked()==[1]
            page.is_testing=True
            page._select_debug_items('none');assert checked()==[1]
            page.is_testing=False
        with patch.object(page,'_can_debug_tests',return_value=False):
            page._select_debug_items('none');assert checked()==[1]
    finally:
        page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close();db.close()


def test_debug_snapshot_skips_unchecked_items_without_mutating_recipe(tmp_path):
    db=DBManager(str(tmp_path/'debug.db'))
    page=OverviewTab(db_manager=db);page.stop_monitor()
    page.engine=SimpleNamespace(workers={})
    recipe=dict(name='R', items=[dict(name=f'item{i}', min='0',max='1', sub_steps=[]) for i in range(3)])
    path=tmp_path/'recipe.json';path.write_text(json.dumps(recipe),encoding='utf-8')
    page.load_recipe_from_file(str(path))
    page.txt_sn.setText('SN');page._confirmed_barcode='SN'
    captured=[]
    try:
        assert all(page.step_tree.topLevelItem(i).flags() & Qt.ItemIsUserCheckable for i in range(3))
        page._debug_requested_indices={1,2}
        with patch.object(page,'_can_debug_tests',return_value=True), \
             patch.object(page._prepare_task,'run',side_effect=lambda action,callback:captured.append(action) or True), \
             patch.object(page,'_prepare_run',return_value=(False,'stop')) as prepare:
            assert page.start_single_channel_test()
            captured[0]()
        snapshot=prepare.call_args.args[0]
        assert snapshot['_debug_run'] is True
        assert [item.get('skip_runtime',False) for item in snapshot['items']] == [True,False,False]
        assert all('skip_runtime' not in item for item in page.current_recipe_data['items'])
    finally:
        page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close();db.close()


def test_debug_preparation_skips_mes_and_does_not_create_production_record():
    page=SimpleNamespace(_prepare_cancel=threading.Event(),preparation_log=Mock(),
        _apply_recipe_hardware=Mock(return_value=(True,'ready')),db_manager=Mock())
    with patch('core.mes_precheck.check_before_test',return_value=(True,'OK')) as gate:
        snapshot={'_debug_run':True,'operator_name':'admin','_mes_precheck':{'mes_precheck_enabled':True}}
        assert OverviewTab._prepare_run(page,snapshot,'')[0]
    gate.assert_not_called()
    assert '_mes_precheck_result' not in snapshot
    page.db_manager.start_new_test.assert_not_called()
    page.db_manager.start_traced_test.assert_not_called()


def test_debug_result_never_changes_yield_or_ng_alarm():
    page=OverviewTab();page.stop_monitor();page.ng_alarm.configure(True,1)
    page.engine=SimpleNamespace(workers={1:SimpleNamespace(sn_barcode='SN',execution_id='D1',debug_run=True,record_error='')})
    try:
        with patch.object(page,'_show_consecutive_ng_alarm') as alarm:
            page.on_worker_test_finished(1,False)
        assert page.production_statistics.events == []
        assert page.ng_alarm.count == 0
        alarm.assert_not_called()
        assert page.lbl_big_status.text() == 'DEBUG FAIL'
    finally:page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close()


def test_engine_debug_executes_with_no_production_record(tmp_path):
    db=Mock()
    engine=Engine(db_manager=db)
    recipe=[dict(name='debug',min='--',max='--',sub_steps=[dict(type='等待',params='1ms')])]
    finished=[];engine.channel_test_finished.connect(lambda cid,ok:finished.append(ok))
    try:
        assert engine.start_channel_test(1,recipe,debug_run=True)
        assert engine.workers[1].debug_run is True
        assert engine.workers[1].test_id == -1
        deadline=time.monotonic()+8
        while engine.workers and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert not engine.workers
        assert finished
        db.start_new_test.assert_not_called();db.start_traced_test.assert_not_called()
        db.finish_test.assert_not_called()
    finally:
        engine.stop_all()
