import datetime
import json
import sqlite3
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


def seed(db,count=40):
    for i in range(count):
        tid=db.start_new_test(1,'',f'SN{i}','project')
        db.finish_test(tid,'PASS' if i%2 else 'NG')


def test_reset_evidence_and_cutoff_commit_together_without_deleting_records(tmp_path):
    db=DBManager(str(tmp_path/'production.db'))
    try:
        seed(db)
        db.reset_project_statistics('project')
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT count(*) FROM test_main').fetchone()[0]==40
            cutoff=conn.execute("SELECT s.cutoff_test_id FROM project_statistics_state s JOIN project_identity p ON p.project_id=s.project_name WHERE p.project_name='project'").fetchone()[0]
            row=conn.execute("SELECT operator,details_json FROM statistics_audit WHERE action='project_reset_committed'").fetchone()
            data=json.loads(row[1]);assert row[0]
            assert data['new_cutoff']==int(cutoff)==40
            assert data['before_deduplicated']==[40,20,20]
            assert data['before_attempts']==[40,20,20]
    finally:db.close()


def test_failed_reset_rolls_back_audit_and_cutoff(tmp_path):
    db=DBManager(str(tmp_path/'rollback.db'))
    try:
        seed(db,2);db._execute_async('SELECT 1',wait=True)
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("""CREATE TRIGGER fail_reset BEFORE INSERT ON project_statistics_state
              BEGIN SELECT RAISE(ABORT,'injected reset failure'); END""")
        import pytest
        with pytest.raises(sqlite3.IntegrityError):db.reset_project_statistics('project')
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute("SELECT count(*) FROM statistics_audit WHERE action='project_reset_committed'").fetchone()[0]==0
            assert conn.execute('SELECT count(*) FROM test_main').fetchone()[0]==2
    finally:db.close()


def test_statistics_survive_refresh_restart_and_trace_unexpected_zero(tmp_path,monkeypatch):
    db=DBManager(str(tmp_path/'ui.db'));seed(db)
    page=OverviewTab(db_manager=db);page.stop_monitor()
    page.current_recipe_name='project';page.refresh_statistics()
    try:
        pump(lambda:not page._stats_task.isRunning())
        assert page.total_count==40
        page.refresh_statistics();pump(lambda:not page._stats_task.isRunning())
        assert page.total_count==40
        assert not hasattr(page,'lbl_stats_scope')
        monkeypatch.setattr(db,'load_project_statistics',lambda *args,**kwargs:[])
        page.refresh_statistics();pump(lambda:not page._stats_task.isRunning())
        assert '数量异常下降' in page.card_stat_total.toolTip()
        db._execute_async('SELECT 1',wait=True)
        with sqlite3.connect(db.db_path) as conn:
            evidence=json.loads(conn.execute("SELECT details_json FROM statistics_audit WHERE action='unexpected_decrease' ORDER BY id DESC LIMIT 1").fetchone()[0])
            assert evidence['previous']==[40,20,20] and evidence['current']==[0,0,0]
            assert evidence['db_path']==db.db_path
    finally:
        page.begin_shutdown();page._stats_task.shutdown();page.close();db.close()
    reopened=DBManager(str(tmp_path/'ui.db'))
    try:
        rows=reopened.load_project_statistics('project')
        assert len(rows)==40
    finally:reopened.close()
