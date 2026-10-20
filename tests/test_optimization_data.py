import json
import os
import sqlite3

from openpyxl import load_workbook

from core.production_statistics import ProductionStatistics
from core.run_context import build_run_context
from core.excel_recorder import export_test_record
from data.db_manager import DBManager


def completed(db, sn, result, date='2026-10-17 10:00:00'):
    identity=db.start_new_test(1,'CH1',sn,'recipe')
    db._execute_async('UPDATE test_main SET start_time=? WHERE test_id=?',(date,identity),wait=True)
    if result!='RUNNING':db.finish_test(identity,result)
    return identity


def test_restart_statistics_latest_result_first_pass_and_persistent_reset(tmp_path):
    path=str(tmp_path/'tests.db')
    db=DBManager(path)
    try:
        for sn,status in [('SN1','NG'),('SN1','NG'),('SN1','PASS'),('SN2','NG'),
                          ('STOP','STOPPED'),('BAD','RECORD_ERROR'),('BUSY','RUNNING')]:
            completed(db,sn,status)
        completed(db,'OTHERDAY','PASS','2026-10-16 10:00:00')
    finally:db.close()
    db=DBManager(path)
    try:
        rows=db.load_production_statistics('2026-10-17 00:00:00','2026-10-18 00:00:00')
        stats=ProductionStatistics();stats.restore(rows)
        assert stats.counts()==(2,1,1)
        assert stats.counts(False)==(4,1,3)
        assert stats.details()==dict(products=2,attempts=4,retests=2,first_passed=0,first_pass_rate=0)
        db.reset_production_statistics()
        assert db.load_production_statistics('2026-10-17','2026-10-18')==[]
        assert len(db.load_production_statistics('2026-10-17','2026-10-18',include_cleared=True))==4
        with sqlite3.connect(path) as conn:assert conn.execute('SELECT COUNT(*) FROM test_main').fetchone()[0]==8
        completed(db,'SN1','PASS')
    finally:db.close()
    db=DBManager(path)
    try:
        stats.restore(db.load_production_statistics('2026-10-17','2026-10-18'))
        assert stats.counts()==(1,1,0)
        assert stats.details()['first_pass_rate']==100
    finally:db.close()


def test_anonymous_products_do_not_merge_after_restore():
    stats=ProductionStatistics()
    stats.restore([('', 'PASS',1),('', 'NG',2)])
    assert stats.counts()==(2,1,1)
    assert stats.details()['retests']==0


def test_context_is_immutable_and_credentials_are_filtered():
    recipe={'name':'R','items':[{'name':'step','password':'hidden'}],'operator_name':'Alice'}
    cfg={'hv_ip':'192.0.2.1','sim1_number':2,'sim1_password':'must-not-store',
         'mes_url':'https://private','api_key':'must-not-store',
         'rn_boards':{'resistor1':{'ip':'192.0.2.2','channels':6,'token':'hidden'}}}
    context=build_run_context(recipe,cfg)
    before=json.dumps(context,sort_keys=True)
    recipe['items'][0]['name']='changed';cfg['hv_ip']='192.0.2.3'
    assert json.dumps(context,sort_keys=True)==before
    assert 'must-not-store' not in before and 'hidden' not in before and 'https://private' not in before
    assert context['configured_hardware']['sim1_number']==2
    assert context['configured_hardware']['rn_boards']['resistor1']['channels']==6
    assert len(context['recipe_sha256'])==64


def test_trace_stays_in_database_and_export_creates_only_xls(tmp_path):
    db=DBManager(str(tmp_path/'tests.db'))
    db.load_sys_config=lambda:{'hv_ip':'192.0.2.1','password':'secret'}
    try:
        identity=db.start_traced_test(1,'CH1','SN',{'name':'R','items':[]},operator='Alice')
        with sqlite3.connect(db.db_path) as conn:
            row=conn.execute('SELECT context_json,operator FROM test_main WHERE test_id=?',(identity,)).fetchone()
        evidence=json.loads(row[0]);assert row[1]=='Alice'
        assert 'secret' not in row[0]
        db.finish_test(identity,'PASS')
        path=export_test_record(db.db_path,identity,str(tmp_path/'reports'))
        from pathlib import Path
        report=Path(path)
        assert list(report.parent.iterdir())==[report]
        with sqlite3.connect(db.db_path) as conn:
            retained=json.loads(conn.execute('SELECT context_json FROM test_main WHERE test_id=?',(identity,)).fetchone()[0])
            owner=conn.execute('SELECT test_id FROM report_exports WHERE report_path=?',
                               (os.path.normcase(str(report.resolve())),)).fetchone()
        assert retained==evidence and owner==(identity,)
    finally:db.close()
