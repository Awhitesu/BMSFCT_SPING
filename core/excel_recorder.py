"""Publish complete reference-format XLS reports for MES collection."""
import datetime
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from typing import Optional
from core.report_settings import validate_settings
from core.xls_report import build_workbook

_export_lock=threading.RLock()

@contextmanager
def _file_lock(folder,filename):
    """Exclusive Windows/SMB lock; delete on close also cleans up after a crash."""
    folder.mkdir(parents=True,exist_ok=True)
    token=hashlib.sha256(filename.casefold().encode('utf-8')).hexdigest()
    lock_path=folder/('.report_lock_'+token+'.tmp')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                                wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    deadline=time.monotonic()+3
    while True:
        # No sharing: another writer cannot keep an old lock open after its owner exits.
        # GENERIC_READ | GENERIC_WRITE | DELETE; OPEN_ALWAYS; TEMPORARY | DELETE_ON_CLOSE.
        handle=kernel.CreateFileW(str(lock_path),0xC0010000,0,None,4,0x04000100,None)
        if handle!=wintypes.HANDLE(-1).value:break
        error=ctypes.get_last_error()
        if error not in (5,32,33,303):raise ctypes.WinError(error)
        if time.monotonic()>=deadline:
            raise RuntimeError('无法获取同名报告写入锁，请确认目录权限或稍后重试导出。') from ctypes.WinError(error)
        time.sleep(.02)
    try:yield
    finally:kernel.CloseHandle(handle)


def _path_key(path):
    return os.path.normcase(os.path.realpath(path))


def _same_report(registry,path,identity):
    row=registry.execute('SELECT test_id,source_key,sha256 FROM report_exports WHERE report_path=?',
                         (_path_key(path),)).fetchone()
    if row is not None:
        if row[:2]!=(identity['test_id'],identity['source_key']):return False
        try:return hashlib.sha256(path.read_bytes()).hexdigest()==row[2]
        except FileNotFoundError:return True  # MES 抓取端可能刚移走同一份报告，允许重新发布。
    # Read existing sidecars only for upgrades; never create or update a .trace directory.
    try:previous=json.loads((path.parent/'.trace'/(path.name+'.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    return isinstance(previous,dict) and all(previous.get(key)==value for key,value in identity.items())

def extract_project_no(recipe_name):
    return (str(recipe_name or '').strip().split('_')[0].strip() or '未知项目')

def _sanitize(text):
    value=''.join(c for c in str(text or '') if c.isalnum() or c in '_-')[:64] or 'UNKNOWN'
    if value.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:value='_'+value
    return value

def _parse_time(value):
    try:return datetime.datetime.fromisoformat(value or '')
    except ValueError:return None

def _duration(start,end):
    if end is None:return ''
    seconds=max(0,int((end-start).total_seconds()))
    return (f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}' if seconds>=3600 else f'{seconds//60:02d}:{seconds%60:02d}')

def export_test_record(db_path:str,test_id:int,base_dir:Optional[str]=None,settings=None):
    conn=sqlite3.connect(db_path);conn.row_factory=sqlite3.Row
    try:
        conn.execute('BEGIN')
        row=conn.execute('SELECT * FROM test_main WHERE test_id=?',(test_id,)).fetchone()
        if row is None:return None
        main=dict(row)
        items=[dict(r) for r in conn.execute('SELECT * FROM test_items_results WHERE test_id=? ORDER BY id',(test_id,))]
        for item in items:
            for key in ('lower_limit','upper_limit','measured_value'):
                if item.get(key+'_raw') is not None:item[key]=item[key+'_raw']
    finally:conn.close()
    evidence=json.loads(main.get('context_json') or '{}');recipe=evidence.get('recipe',{})
    config=validate_settings(evidence.get('report_settings') or settings or {})
    recipe_name=str(main.get('recipe_name') or '');project=str(recipe.get('project_no') or extract_project_no(recipe_name))
    start=_parse_time(main.get('start_time'))
    if start is None:raise ValueError('测试开始时间无效，不能生成带错误时间的MES报告。')
    verdict=str(main.get('result') or '').upper()
    if verdict in ('RUNNING',''):return None
    result={'PASS':'Pass','OK':'Pass','NG':'Fail','FAIL':'Fail'}.get(verdict,verdict)
    filename=f'{_sanitize(main.get("master_code"))}_{"Pass" if verdict in ("PASS","OK") else "Fail"}({start:%Y%m%d_%H-%M-%S}).xls'
    root=Path(base_dir if base_dir is not None else config['report_root_dir']).expanduser().resolve()
    folder=root/_sanitize(project)/start.strftime('%Y-%m-%d')/('PASS' if verdict in ('PASS','OK') else 'FAIL')
    identity=dict(test_id=test_id,source_key=hashlib.sha256(os.path.normcase(os.path.realpath(db_path)).encode()).hexdigest())
    info=dict(sn=main.get('master_code') or '',date=start.strftime('%Y/%m/%d'),time=start.strftime('%H:%M:%S'),
              duration=_duration(start,_parse_time(main.get('end_time'))),result=result,project=project,
              operator=main.get('operator') or '',model=recipe.get('model_name') or recipe_name,
              recipe_file=recipe_name if recipe_name.lower().endswith('.json') else recipe_name+'.json',
              equipment=config['report_equipment_no'],station=config['report_station_name'],
              position1=config['report_position1'],position2=config['report_position2'])
    path=_publish_report(folder,filename,identity,info,items,db_path)
    if config['mes_report_dir']:
        mes_folder=Path(config['mes_report_dir']).expanduser().resolve()
        if mes_folder != path.parent:
            try:_publish_report(mes_folder,filename,identity,info,items,db_path,source_path=path)
            except Exception as exc:
                raise RuntimeError(f'测试报告已保存至 {path}；MES文件输出失败：{exc}') from exc
    return str(path)


def _publish_report(folder,filename,identity,info,items,db_path,source_path=None):
    test_id=identity['test_id']
    with _export_lock,_file_lock(folder,filename),closing(sqlite3.connect(db_path)) as registry:
        registry.execute('''CREATE TABLE IF NOT EXISTS report_exports (
            report_path TEXT PRIMARY KEY, test_id INTEGER NOT NULL,
            source_key TEXT NOT NULL, sha256 TEXT NOT NULL)''')
        registry.commit()
        path=folder/filename;duplicate=folder/'重复记录'/f'T{test_id}_{identity["source_key"][:8]}'
        attempt=0
        while path.exists() and not _same_report(registry,path,identity):
            attempt+=1
            path=(duplicate if attempt==1 else duplicate.with_name(duplicate.name+f'_{attempt}'))/filename
        path.parent.mkdir(parents=True,exist_ok=True)
        book=build_workbook(info,items) if source_path is None else None;temporary=None
        try:
            fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.report_',suffix='.tmp')
            with os.fdopen(fd,'wb') as stream:
                if source_path is None:book.save(stream)
                else:
                    with source_path.open('rb') as source:shutil.copyfileobj(source,stream)
                stream.flush();os.fsync(stream.fileno())
            checksum=hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
            # Keep the database write transaction short: workbook rendering and copying are done.
            registry.execute('BEGIN IMMEDIATE')
            registry.execute('''INSERT INTO report_exports(report_path,test_id,source_key,sha256)
                VALUES (?,?,?,?) ON CONFLICT(report_path) DO UPDATE SET
                test_id=excluded.test_id,source_key=excluded.source_key,sha256=excluded.sha256''',
                (_path_key(path),test_id,identity['source_key'],checksum))
            os.replace(temporary,path)
            registry.commit()
        finally:
            registry.rollback()
            if temporary and os.path.exists(temporary):os.unlink(temporary)
    return path
