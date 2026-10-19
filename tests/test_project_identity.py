import json
from pathlib import Path
import sqlite3
import pytest
from data.db_manager import DBManager


def test_rename_preserves_identity_clear_boundary_and_historical_names(tmp_path):
    path=tmp_path/'identity.db';db=DBManager(str(path))
    try:
        assert db.save_recipe_json('old',dict(name='old',items=[]))
        first=db.start_new_test(1,'','SN1','old');db.finish_test(first,'PASS')
        db.reset_project_statistics('old')
        second=db.start_new_test(1,'','SN2','old');db.finish_test(second,'NG')
        before=db.load_project_statistics('old')
        assert db.rename_recipe('old','new')
        assert db.load_project_statistics('new')==before
        with sqlite3.connect(path) as conn:
            identity=conn.execute("SELECT project_id FROM project_identity WHERE project_name='new'").fetchone()[0]
            assert len(identity)==32
            assert conn.execute('SELECT DISTINCT recipe_name FROM test_main').fetchall()==[('old',)]
        # Reusing the old filename creates an independent project, not an alias.
        assert db.save_recipe_json('old',dict(name='old',items=[]))
        assert db.load_project_statistics('old')==[]
        fresh=db.start_new_test(1,'','SN3','old');db.finish_test(fresh,'PASS')
        assert len(db.load_project_statistics('new'))==1
        assert len(db.load_project_statistics('old'))==1
    finally:db.close()
    reopened=DBManager(str(path))
    try:assert reopened.load_project_statistics('new')==before
    finally:reopened.close()


def test_legacy_records_and_project_clear_migrate_during_rename(tmp_path):
    db=DBManager(str(tmp_path/'legacy.db'))
    try:
        db.save_recipe_json('legacy',dict(name='legacy',items=[]))
        db._execute_async("INSERT INTO test_main(recipe_name,master_code,result,start_time,end_time) VALUES(?,?,?,?,?)",
            ('legacy','SN','PASS','2020-01-01','2020-01-01'),wait=True)
        db._execute_async('INSERT INTO project_statistics_state(project_name,cutoff_test_id) VALUES(?,?)',('legacy',0),wait=True)
        assert db.rename_recipe('legacy','renamed')
        assert len(db.load_project_statistics('renamed'))==1
    finally:db.close()


def test_rename_database_failure_keeps_original_file_and_identity(tmp_path):
    db=DBManager(str(tmp_path/'failure.db'))
    try:
        db.save_recipe_json('old',dict(name='old',items=[]));db.load_project_statistics('old')
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("CREATE TRIGGER fail_identity BEFORE UPDATE ON project_identity BEGIN SELECT RAISE(ABORT,'injected rename failure'); END")
        assert not db.rename_recipe('old','new')
        assert Path(db.get_recipe_dir(),'old.json').exists()
        assert not Path(db.get_recipe_dir(),'new.json').exists()
        assert db.load_recipe_json('old')['name']=='old'
    finally:db.close()
