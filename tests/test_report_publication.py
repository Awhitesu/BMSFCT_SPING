"""仅输出 XLS，同时保留原子发布、进程互斥和重复导出归属。"""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import xlrd

from core import excel_recorder as recorder
from test_excel_recorder import _make_db


def export_in_process(arguments):
    return recorder.export_test_record(*arguments)


def assert_only_reports(root):
    assert not list(root.rglob('.trace'))
    assert all(path.suffix=='.xls' for path in root.rglob('*') if path.is_file())


def registry_rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute('SELECT report_path,test_id,source_key,sha256 FROM report_exports ORDER BY report_path').fetchall()


def test_archive_and_mes_leave_only_xls_and_track_both_paths_in_database(tmp_path):
    db=tmp_path/'test.db';test_id=_make_db(str(db))
    root=tmp_path/'archive';mes=tmp_path/'mes'
    report=Path(recorder.export_test_record(str(db),test_id,settings={
        'report_root_dir':str(root),'mes_report_dir':str(mes)}))
    copy=mes/report.name
    assert copy.read_bytes()==report.read_bytes()
    assert_only_reports(root);assert_only_reports(mes)
    rows=registry_rows(db)
    assert len(rows)==2
    assert {row[0] for row in rows}=={recorder._path_key(report),recorder._path_key(copy)}
    assert all(row[1]==test_id and row[3]==hashlib.sha256(report.read_bytes()).hexdigest() for row in rows)
    assert xlrd.open_workbook(str(report)).sheet_names()==['Sheet1']


def test_reexport_in_a_fresh_process_reuses_path_without_trace(tmp_path):
    db=tmp_path/'test.db';test_id=_make_db(str(db));root=tmp_path/'reports'
    first=Path(recorder.export_test_record(str(db),test_id,str(root)))
    with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as pool:
        second=pool.submit(export_in_process,(str(db),test_id,str(root))).result(timeout=15)
    assert Path(second)==first and len(list(root.rglob('*.xls')))==1
    assert_only_reports(root)


def test_reexport_updates_same_record_and_its_checksum(tmp_path):
    db=tmp_path/'test.db';test_id=_make_db(str(db));root=tmp_path/'reports'
    report=Path(recorder.export_test_record(str(db),test_id,str(root)))
    before=registry_rows(db)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE test_items_results SET measured_value=4 WHERE id=1')
    assert Path(recorder.export_test_record(str(db),test_id,str(root)))==report
    assert registry_rows(db)[0][3]!=before[0][3]
    assert_only_reports(root)


def test_existing_unowned_report_is_preserved_including_occupied_duplicate_path(tmp_path):
    db=tmp_path/'test.db';test_id=_make_db(str(db));root=tmp_path/'reports'
    original=Path(recorder.export_test_record(str(db),test_id,str(root)))
    source=registry_rows(db)[0][2]
    original.write_bytes(b'another manually supplied report')
    duplicate=original.parent/'重复记录'/f'T{test_id}_{source[:8]}'/original.name
    duplicate.parent.mkdir(parents=True);duplicate.write_bytes(b'also belongs to someone else')
    report=Path(recorder.export_test_record(str(db),test_id,str(root)))
    assert report.parent.name==duplicate.parent.name+'_2'
    assert original.read_bytes()==b'another manually supplied report'
    assert duplicate.read_bytes()==b'also belongs to someone else'
    assert Path(recorder.export_test_record(str(db),test_id,str(root)))==report
    assert_only_reports(root)


def test_different_databases_with_same_test_id_do_not_overwrite(tmp_path):
    root=tmp_path/'reports';databases=[tmp_path/'one.db',tmp_path/'two.db']
    paths=[]
    for db in databases:
        test_id=_make_db(str(db))
        paths.append(Path(recorder.export_test_record(str(db),test_id,str(root))))
    assert paths[0]!=paths[1]
    for db,path in zip(databases,paths):
        assert Path(recorder.export_test_record(str(db),1,str(root)))==path
    assert_only_reports(root)


def test_failed_replace_preserves_original_registry_and_leaves_no_temp_or_lock(tmp_path,monkeypatch):
    db=tmp_path/'test.db';test_id=_make_db(str(db));root=tmp_path/'reports'
    path=Path(recorder.export_test_record(str(db),test_id,str(root)))
    original=path.read_bytes();owners=registry_rows(db)
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE test_items_results SET measured_value=5 WHERE id=1')
    replace=recorder.os.replace
    def fail(*_):raise PermissionError('report locked')
    monkeypatch.setattr(recorder.os,'replace',fail)
    with pytest.raises(PermissionError,match='report locked'):
        recorder.export_test_record(str(db),test_id,str(root))
    assert path.read_bytes()==original and registry_rows(db)==owners
    assert_only_reports(root)
    monkeypatch.setattr(recorder.os,'replace',replace)
    assert Path(recorder.export_test_record(str(db),test_id,str(root)))==path


def test_existing_trace_is_only_read_for_upgrade_and_is_not_recreated(tmp_path):
    db=tmp_path/'test.db';test_id=_make_db(str(db));root=tmp_path/'reports'
    report=Path(recorder.export_test_record(str(db),test_id,str(root)))
    row=registry_rows(db)[0]
    legacy=report.parent/'.trace'/(report.name+'.json')
    legacy.parent.mkdir();legacy.write_text(json.dumps(dict(test_id=test_id,source_key=row[2])),encoding='utf-8')
    legacy_bytes=legacy.read_bytes()
    with sqlite3.connect(db) as conn:conn.execute('DROP TABLE report_exports')
    assert Path(recorder.export_test_record(str(db),test_id,str(root)))==report
    assert legacy.read_bytes()==legacy_bytes and list(legacy.parent.iterdir())==[legacy]
    legacy.unlink();legacy.parent.rmdir()
    assert Path(recorder.export_test_record(str(db),test_id,str(root)))==report
    assert_only_reports(root)


def test_parallel_processes_serialize_same_filename_without_leaving_auxiliary_files(tmp_path):
    root=tmp_path/'reports';db=tmp_path/'test.db';_make_db(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO test_main SELECT 2,channel_id,shelf_code,master_code,slave_codes,recipe_name,start_time,end_time,result FROM test_main')
    jobs=[(str(db),identity,str(root)) for identity in (1,2,1,2,1,2)]
    with ProcessPoolExecutor(max_workers=3,mp_context=multiprocessing.get_context('spawn')) as pool:
        results=list(pool.map(export_in_process,jobs,timeout=20))
    assert len(set(results))==2
    assert len(list(root.rglob('*.xls')))==2 and len(registry_rows(db))==2
    assert_only_reports(root)


def test_os_lock_is_cleaned_after_abrupt_process_exit(tmp_path):
    root=Path(__file__).resolve().parents[1]
    code='''from pathlib import Path
import os, sys
from core.excel_recorder import _file_lock
with _file_lock(Path(sys.argv[1]), 'example.xls'):
    os._exit(0)
'''
    subprocess.run([sys.executable,'-c',code,str(tmp_path)],cwd=root,check=True,timeout=10)
    assert list(tmp_path.iterdir())==[]
    with recorder._file_lock(tmp_path,'example.xls'):
        assert len(list(tmp_path.iterdir()))==1
    assert list(tmp_path.iterdir())==[]
