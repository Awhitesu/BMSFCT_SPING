import time
import threading
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent,QTimer

from data.db_manager import DBManager
from ui.tabs.overview_tab import OverviewTab
from ui.tabs.simulator_tab import SimulatorTab,ReadAllWorker

app=QApplication.instance() or QApplication([])


def pump(predicate):
    end=time.monotonic()+4
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.005)
    assert predicate()


def test_statistics_restore_uses_background_and_refreshes_pending_changes(tmp_path,monkeypatch):
    db=DBManager(str(tmp_path/'test.db'))
    db.load_sys_config=lambda:{}
    one=db.start_new_test(1,'CH1','SN','R');db.finish_test(one,'NG')
    read=db.load_project_statistics
    entered=threading.Event();release=threading.Event()
    def delayed(project):
        entered.set();assert release.wait(3);return read(project)
    db.load_project_statistics=delayed
    page=OverviewTab(db_manager=db)
    page.plc_signal_timer.stop()
    page.current_recipe_name='R';page.refresh_statistics()
    try:
        pump(entered.is_set)
        ticks=[];QTimer.singleShot(0,lambda:ticks.append(True));pump(lambda:bool(ticks))
        two=db.start_new_test(1,'CH1','SN','R');db.finish_test(two,'PASS')
        page.refresh_statistics();release.set()
        pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert (page.total_count,page.pass_count,page.fail_count)==(1,1,0)
        assert page.production_statistics.details()['retests']==1
        assert '0.00%' in page.card_stat_rate.toolTip()
        from PySide6.QtWidgets import QMessageBox
        messages=[]
        monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(True))
        reset=db.reset_project_statistics
        reset_entered=threading.Event();reset_release=threading.Event()
        def delayed_reset(project):
            reset_entered.set();assert reset_release.wait(2);return reset(project)
        db.reset_project_statistics=delayed_reset
        page.reset_statistics()
        try:
            pump(reset_entered.is_set)
            assert page._run_busy() and not page.btn_run_test.isEnabled()
            assert not page.start_single_channel_test()
        finally:reset_release.set()
        pump(lambda:not page._stats_task.isRunning())
        assert page.total_count==0 and messages==[True]
        assert not page._run_busy() and page.btn_run_test.isEnabled()
    finally:
        release.set();page.begin_shutdown()
        for task in (page._stats_task,page._prepare_task,page._plc_task):task.shutdown()
        page.close();page.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        db.close()


def test_simulator_updates_existing_cell_and_clears_old_warning():
    page=SimulatorTab()
    page.status_timer.stop()
    try:
        page._rows={1:0};page.table.setRowCount(1)
        page.on_read_all_progress(1,2.7)
        item=page.table.item(0,1)
        page.on_channel_diagnostic(1,'偏差超过')
        assert item.data(256) is True
        page.on_read_all_progress(1,2.7)
        assert page.table.item(0,1) is item
        assert item.text()=='2.7000' and item.data(256) is False
        page.on_read_all_progress(1,float('nan'));assert item.text()=='Error'
        page.on_read_all_progress(999,2.7)
    finally:
        page.shutdown();page.close();page.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


def test_incomplete_snapshot_marks_all_requested_channels_invalid():
    sim=Mock();sim.read_voltage_snapshot.return_value=[(2.7,2.7,True)]
    worker=ReadAllWorker([(1,sim,1),(2,sim,2)])
    samples=[];worker.progress.connect(lambda ch,value:samples.append((ch,value)))
    worker.run()
    assert samples==[(1,-1.0),(2,-1.0)]
