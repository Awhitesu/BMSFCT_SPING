import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from core.mes_client import MesCheckResult
from core.mes_precheck import check_before_test, validate_settings
from data.db_manager import DBManager
from ui.tabs.overview_tab import OverviewTab
from ui.tabs.settings_tab import SettingsTab

app = QApplication.instance() or QApplication([])


def settings():
    return dict(mes_precheck_enabled=True, mes_url='http://mes.example.test/check',
                mes_equipment_no='STATION-1', mes_order_no='ORDER-1', mes_timeout=2)


@pytest.mark.parametrize('result', [MesCheckResult(False, '未到本站，请先完成前工站'),
                                   MesCheckResult(False, 'MES 请求失败: timeout')])
def test_denial_prevents_hardware_and_test_record_creation(result):
    page = SimpleNamespace(_prepare_cancel=threading.Event(), preparation_log=Mock(),
                           _apply_recipe_hardware=Mock(), db_manager=Mock())
    with patch('core.mes_precheck.MesClient') as client:
        client.return_value.move_in_check.return_value=result
        ok, reason = OverviewTab._prepare_run(page, {'_mes_precheck':settings(), 'operator_name':'alice'}, 'SN-1')
    assert not ok and result.message in reason
    client.return_value.move_in_check.assert_called_once_with('STATION-1','ORDER-1','SN-1','alice')
    page._apply_recipe_hardware.assert_not_called()
    page.db_manager.start_new_test.assert_not_called()
    page.db_manager.start_traced_test.assert_not_called()


def test_pass_precedes_hardware_and_record():
    events=[]
    page = SimpleNamespace(_prepare_cancel=threading.Event(), preparation_log=Mock(), channel_id=0,
                           _apply_recipe_hardware=Mock(side_effect=lambda s:(events.append('hardware') or True, 'ready')),
                           db_manager=SimpleNamespace(start_new_test=lambda *a,**kw:events.append('record') or 123))
    with patch('core.mes_precheck.MesClient') as client:
        client.return_value.move_in_check.side_effect=lambda *args:events.append('mes') or MesCheckResult(True,'允许测试')
        assert OverviewTab._prepare_run(page, {'_mes_precheck':settings(),'operator_name':'alice'}, 'SN-2')[0]
    assert events == ['mes','hardware','record']


def test_scan_first_checks_mes_then_defers_hardware_until_arrival():
    events=[]
    page=SimpleNamespace(_prepare_cancel=threading.Event(),preparation_log=Mock(),channel_id=0,
        _apply_recipe_hardware=Mock(side_effect=lambda s:(events.append('hardware') or True,'ready')),
        db_manager=SimpleNamespace(start_new_test=lambda *a,**kw:events.append('record') or 123))
    snapshot={'_mes_precheck':settings(),'operator_name':'alice','_wait_board':True}
    with patch('core.mes_precheck.MesClient') as client:
        client.return_value.move_in_check.side_effect=lambda *a:events.append('mes') or MesCheckResult(True,'OK')
        assert OverviewTab._prepare_run(page,snapshot,'SN')[0]
        assert events==['mes']
        original=dict(snapshot['_mes_precheck_result'])
        snapshot['_wait_board']=False
        assert OverviewTab._prepare_run(page,snapshot,'SN')[0]
        assert events==['mes','hardware','record']
        assert snapshot['_mes_precheck_result']==original
        client.return_value.move_in_check.assert_called_once()


def test_scan_first_early_arrival_does_not_scan_and_approved_arrival_resumes():
    page=SimpleNamespace(_closing=False,_prepare_cancel=threading.Event(),_waiting_board=None,
        db_manager=SimpleNamespace(load_sys_config=lambda:{'test_start_mode':'scan_first'}),
        _log_test_event=Mock(),request_scan_and_start=Mock())
    OverviewTab._on_board_arrival(page)
    page.request_scan_and_start.assert_not_called()
    snapshot={'_wait_board':True};resume=Mock(return_value=True)
    page._waiting_board=(snapshot,'SN');page._arrival_continue=resume
    page._confirmed_barcode='SN';page.txt_sn=SimpleNamespace(text=lambda:'SN');page.lbl_big_status=Mock()
    OverviewTab._on_board_arrival(page)
    resume.assert_called_once();assert page._waiting_board is None and not snapshot['_wait_board']


def test_start_mode_setting_emits_selected_mode():
    page=SettingsTab();captured=[]
    try:
        assert page.start_mode.currentData()=='board_first'
        page.start_mode_save_requested.connect(captured.append)
        page.start_mode.setCurrentIndex(page.start_mode.findData('scan_first'));page.save_start_mode.click()
        assert captured==['scan_first']
    finally:page.close()


def test_scan_first_waiting_stop_discards_approved_barcode(tmp_path):
    db=DBManager(str(tmp_path/'scan-first.db'));db.save_sys_config(dict(settings(),test_start_mode='scan_first'))
    engine=Mock();engine.workers={};engine.device_manager.easy320.is_connected=True
    page=OverviewTab(engine,db);page.stop_monitor()
    page._confirmed_barcode='SN';page.txt_sn.setText('SN');page.current_recipe_data={}
    page._pending_plc_recipe=None;page._plc_config_error=''
    page.serialize_tree_to_dict=Mock(return_value={'name':'recipe','items':[{'name':'read'}]})
    page._apply_recipe_hardware=Mock();page._launch_prepared_test=Mock()
    try:
        with patch('core.mes_precheck.MesClient') as client:
            client.return_value.move_in_check.return_value=MesCheckResult(True,'OK')
            assert page.start_single_channel_test()
            deadline=time.monotonic()+4
            while page._waiting_board is None and time.monotonic()<deadline:app.processEvents();time.sleep(.002)
            assert page._waiting_board is not None and page.preparing
            page._apply_recipe_hardware.assert_not_called();page._launch_prepared_test.assert_not_called()
            page.stop_single_channel_test()
            assert page._waiting_board is None and not page.preparing and page._confirmed_barcode==''
            page._on_board_arrival();page._launch_prepared_test.assert_not_called()
    finally:page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close();db.close()


def test_disabled_gate_makes_no_request():
    with patch('core.mes_precheck.MesClient') as client:
        assert check_before_test({}, 'SN', 'user', lambda:False, Mock()) == (True,'')
    client.assert_not_called()


def test_stop_during_request_discards_late_pass():
    cancel=threading.Event()
    with patch('core.mes_precheck.MesClient') as client:
        client.return_value.move_in_check.side_effect=lambda *a:cancel.set() or MesCheckResult(True,'OK')
        assert check_before_test(settings(), 'SN', 'user', cancel.is_set, Mock())[0] is False


@pytest.mark.parametrize('field,value', [('mes_url',''),('mes_equipment_no',''),
    ('mes_timeout',float('nan')),('mes_timeout',0),('mes_precheck_enabled','true')])
def test_bad_settings_fail_closed(field,value):
    config=settings(); config[field]=value
    with pytest.raises(ValueError): validate_settings(config)


def test_settings_emits_saved_gate_configuration():
    page=SettingsTab(); captured=[]
    try:
        page.set_mes_settings(settings())
        page.mes_save_requested.connect(captured.append)
        page.btn_save_mes.click()
        assert captured == [settings()]
    finally:page.close()


def test_slow_mes_is_background_and_denial_shows_reason_without_launch(tmp_path):
    db=DBManager(str(tmp_path/'gate.db')); db.save_sys_config(settings())
    engine=Mock(); engine.workers={}; page=OverviewTab(engine,db)
    page.stop_monitor()
    page._confirmed_barcode='SN-3';page.txt_sn.setText('SN-3')
    page.current_recipe_data={};page._pending_plc_recipe=None;page._plc_config_error=''
    page.serialize_tree_to_dict=Mock(return_value={'name':'recipe','items':[{'name':'read'}]})
    page._apply_recipe_hardware=Mock();page._launch_prepared_test=Mock()
    entered, release=threading.Event(),threading.Event()

    def request(*args):
        assert threading.get_ident()!=thread_id
        entered.set();assert release.wait(5)
        return MesCheckResult(False,'前工站未完成')

    thread_id=threading.get_ident()
    try:
        with patch('core.mes_precheck.MesClient') as client, \
             patch('PySide6.QtWidgets.QMessageBox.critical') as popup:
            client.return_value.move_in_check.side_effect=request
            started=time.monotonic()
            assert page.start_single_channel_test()
            assert time.monotonic()-started<1
            assert entered.wait(3)
            assert page.preparing
            release.set()
            deadline=time.monotonic()+5
            while page.preparing and time.monotonic()<deadline:
                app.processEvents();time.sleep(.002)
            assert not page.preparing
            assert '前工站未完成' in popup.call_args.args[2]
            assert page._confirmed_barcode == ''
            page._apply_recipe_hardware.assert_not_called()
            page._launch_prepared_test.assert_not_called()
    finally:
        release.set();page.begin_shutdown();page._prepare_task.shutdown();page._plc_task.shutdown();page.close();db.close()
