import json
import time
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from ui.tabs.overview_tab import OverviewTab

app=QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path,monkeypatch):
    db=Mock();db.get_recipe_dir.return_value=str(tmp_path);db.load_sys_config.return_value={};db.get_user_role.return_value='operator'
    widget=OverviewTab(db_manager=db);widget.stop_monitor()
    widget._can_debug_tests=lambda:False
    widget.set_recipe_selection_role('worker',False)
    monkeypatch.setattr(QMessageBox,'warning',Mock())
    yield widget
    widget.begin_shutdown()
    for name in ('_prepare_task','_plc_task','_stats_task','_recipe_match_task'):getattr(widget,name).shutdown()
    widget.close()


def recipes(page,tmp_path,entries):
    for name,rule in entries:
        (tmp_path/f'{name}.json').write_text(json.dumps(dict(name=name,sn_regex=rule,items=[])),encoding='utf-8')
    page.update_recipes([f'{name}.json' for name,_ in entries])


def select_and_wait(page):
    assert page.select_test_recipe()
    deadline=time.monotonic()+3
    while page._recipe_match_task.isRunning() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.002)
    assert not page._recipe_match_task.isRunning()
    return bool(page.current_recipe_name)


def test_operator_starts_empty_and_cannot_manually_select(page,tmp_path):
    recipes(page,tmp_path,[('A','^A')])
    assert page.current_recipe_name=='' and page.combo_recipe.currentIndex()==-1
    page.combo_recipe.setEnabled(True)
    assert not page.combo_recipe.isEnabled()
    page.combo_recipe.setCurrentIndex(0);page.on_recipe_combo_changed(0)
    assert page.current_recipe_name==''


def test_unique_match_loads_only_matching_recipe_without_start(page,tmp_path):
    recipes(page,tmp_path,[('A','^A[0-9]+$'),('B','^B[0-9]+$')])
    page.request_scan_and_start=Mock(return_value='B123')
    page.sync_recipe_number_to_plc=Mock();page.apply_selected_recipe_can=Mock()
    page.start_single_channel_test=Mock()
    assert select_and_wait(page)
    page.request_scan_and_start.assert_called_once_with(scan_only=True)
    assert page.current_recipe_name=='B' and page.combo_recipe.currentText()=='B'
    assert not page.combo_recipe.isEnabled() and page._confirmed_barcode==''
    page.start_single_channel_test.assert_not_called()
    page.set_recipe_selection_role('worker',False)
    assert page.current_recipe_name=='B'


@pytest.mark.parametrize('rules,title',[
    (['^A','^A'],'条码匹配多个配方'),
    (['^B','^C'],'未匹配到配方'),
    (['[','^A'],'配方规则检查失败'),
    (['',''],'未匹配到配方'),
])
def test_ambiguous_missing_and_invalid_rules_never_load(page,tmp_path,rules,title):
    recipes(page,tmp_path,[('A',rules[0]),('B',rules[1])])
    page.current_recipe_name='previous';page.current_recipe_data={'name':'previous'}
    page.request_scan_and_start=Mock(return_value='A123')
    assert not select_and_wait(page)
    assert page.current_recipe_name=='' and page.current_recipe_data=={}
    assert QMessageBox.warning.call_args.args[1]==title


def test_account_change_clears_operator_selection(page):
    page.current_recipe_name='old';page.current_recipe_data={'name':'old'}
    page.set_recipe_selection_role('another worker',False)
    assert page.current_recipe_name==''
    page._can_debug_tests=lambda:True
    page.set_recipe_selection_role('ADMIN',True)
    assert page.combo_recipe.isEnabled()


def test_selection_scan_returns_barcode_without_starting_production(page,monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog,'getText',lambda *a:(' A123 ',True))
    page.start_single_channel_test=Mock()
    assert page.request_scan_and_start(scan_only=True)=='A123'
    assert page._confirmed_barcode=='' and not page._scan_pending
    page.start_single_channel_test.assert_not_called()


def test_serial_scanner_trigger_and_release_for_recipe_selection(page):
    from types import SimpleNamespace
    from PySide6.QtCore import QObject,Signal,QTimer
    class Scanner(QObject):
        barcode=Signal(str)
        config={'scanner_enabled':True}
        port=SimpleNamespace(isOpen=lambda:True)
        def begin_scan(self,owner):
            self.owner=owner;self.started=True
            QTimer.singleShot(0,lambda:self.barcode.emit('SERIAL123'))
            return True
        def end_scan(self,owner):
            assert owner is self.owner
            self.ended=True
    scanner=Scanner();page.serial_scanner=scanner
    assert page.request_scan_and_start(scan_only=True)=='SERIAL123'
    assert scanner.started and scanner.ended and not page._scan_pending


def test_recipe_matching_runs_in_background_and_gui_remains_responsive(page,tmp_path,monkeypatch):
    import threading
    from PySide6.QtCore import QTimer
    import core.recipe_matching as matching
    recipes(page,tmp_path,[('A','^A[0-9]+$')])
    page.request_scan_and_start=Mock(return_value='A123')
    page.sync_recipe_number_to_plc=Mock();page.apply_selected_recipe_can=Mock()
    entered=threading.Event();release=threading.Event();worker_threads=[]
    original=matching.match_recipe_files
    def delayed(*args,**kwargs):
        worker_threads.append(threading.get_ident());entered.set();assert release.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(matching,'match_recipe_files',delayed)
    try:
        started=time.perf_counter();assert page.select_test_recipe()
        assert time.perf_counter()-started<.5 and entered.wait(1)
        assert worker_threads[0]!=threading.get_ident() and page._scan_pending
        ticks=[];QTimer.singleShot(0,lambda:ticks.append(True));app.processEvents();assert ticks
        release.set();deadline=time.monotonic()+3
        while page._recipe_match_task.isRunning() and time.monotonic()<deadline:app.processEvents();time.sleep(.002)
        assert page.current_recipe_name=='A' and not page._scan_pending
    finally:release.set()
