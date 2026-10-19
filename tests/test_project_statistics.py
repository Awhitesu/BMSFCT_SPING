import json
import sqlite3
import threading
import time
from PySide6.QtWidgets import QApplication
from data.db_manager import DBManager
from ui.tabs.overview_tab import OverviewTab

app=QApplication.instance() or QApplication([])


def pump(predicate):
    deadline=time.monotonic()+4
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.002)
    assert predicate()


def add(db,project,sn,result,day):
    tid=db.start_new_test(1,'',sn,project);db.finish_test(tid,result)
    db._execute_async('UPDATE test_main SET start_time=? WHERE test_id=?',(day+' 12:00:00',tid),wait=True)
    return tid


def test_project_lifetime_counts_ignore_dates_and_reset_only_one_project(tmp_path):
    path=tmp_path/'project.db';db=DBManager(str(path))
    try:
        add(db,'A','same','NG','2025-01-01')
        add(db,'A','same','PASS','2026-10-19')
        add(db,'B','same','NG','2024-01-01')
        assert len(db.load_project_statistics('A'))==2
        assert len(db.load_project_statistics('B'))==1
        db.reset_project_statistics('A')
        assert db.load_project_statistics('A')==[]
        assert len(db.load_project_statistics('B'))==1
        add(db,'A','new','PASS','2026-10-20')
    finally:db.close()
    reopened=DBManager(str(path))
    try:
        assert len(reopened.load_project_statistics('A'))==1
        assert len(reopened.load_project_statistics('B'))==1
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT count(*) FROM test_main').fetchone()[0]==4
    finally:reopened.close()


def test_project_statistics_migration_respects_existing_manual_clear(tmp_path):
    db=DBManager(str(tmp_path/'migration.db'))
    try:
        add(db,'A','old','PASS','2020-01-01')
        db.reset_production_statistics()
        add(db,'A','new','NG','2021-01-01')
        add(db,'B','other','PASS','2022-01-01')
        assert len(db.load_project_statistics('A'))==1
        assert len(db.load_project_statistics('B'))==1
        db.reset_project_statistics('A')
        assert len(db.load_project_statistics('B'))==1
    finally:db.close()


def test_switching_recipe_loads_its_own_yield_and_clearing_a_does_not_clear_b(tmp_path,monkeypatch):
    db=DBManager(str(tmp_path/'switch.db'))
    add(db,'A','same','NG','2020-01-01');add(db,'A','same','PASS','2026-10-19')
    add(db,'B','same','NG','2024-01-01')
    paths={}
    for name in ('A','B'):
        path=tmp_path/f'{name}.json';path.write_text(json.dumps(dict(name=name,items=[])),encoding='utf-8');paths[name]=str(path)
    page=OverviewTab(db_manager=db);page.stop_monitor()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox,'information',lambda *args:None)
    try:
        page.load_recipe_from_file(paths['A']);pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert (page.total_count,page.pass_count,page.fail_count)==(1,1,0)
        page.load_recipe_from_file(paths['B']);pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert (page.total_count,page.pass_count,page.fail_count)==(1,0,1)
        page.reset_statistics();pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert page.total_count==0
        page.load_recipe_from_file(paths['A']);pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert (page.total_count,page.pass_count)==(1,1)
        assert not hasattr(page,'lbl_stats_scope')
    finally:
        page.begin_shutdown();page._stats_task.shutdown();page.close();db.close()


def test_slow_previous_project_response_cannot_replace_new_projects_counts(tmp_path):
    db=DBManager(str(tmp_path/'race.db'));add(db,'A','one','PASS','2020-01-01');add(db,'B','two','NG','2021-01-01')
    page=OverviewTab(db_manager=db);page.stop_monitor()
    original=db.load_project_statistics;entered=threading.Event();release=threading.Event()
    def read(project):
        if project=='A':entered.set();assert release.wait(2)
        return original(project)
    db.load_project_statistics=read
    try:
        page.current_recipe_name='A';page.refresh_statistics();assert entered.wait(1)
        page.current_recipe_name='B';page.refresh_statistics();release.set()
        pump(lambda:not page._stats_task.isRunning() and not page._stats_pending)
        assert (page.total_count,page.pass_count,page.fail_count)==(1,0,1)
        page._clear_recipe_state()
        assert page.total_count==0
    finally:
        release.set();page.begin_shutdown();page._stats_task.shutdown();page.close();db.close()
