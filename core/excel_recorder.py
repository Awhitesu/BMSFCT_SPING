"""Publish complete reference-format XLS reports for MES collection."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Optional
from core.report_settings import validate_settings
from core.xls_report import build_workbook

_export_lock=threading.RLock()

@contextmanager
def _file_lock(folder,filename):
    """Serialize the same SN/time across processes without publishing an empty XLS."""
    directory=folder/'.trace';directory.mkdir(parents=True,exist_ok=True)
    with (directory/(filename+'.lock')).open('a+b') as stream:
        if stream.seek(0,os.SEEK_END)==0:stream.write(b'0');stream.flush()
        deadline=time.monotonic()+3
        import msvcrt
        while True:
            stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
            except OSError:
                if time.monotonic()>=deadline:raise RuntimeError('同名报告正在写入，请稍后重试导出。')
                time.sleep(.02)
        try:yield
        finally:
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

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
    path=_publish_report(folder,filename,identity,evidence,info,items)
    if config['mes_report_dir']:
        mes_folder=Path(config['mes_report_dir']).expanduser().resolve()
        if mes_folder != path.parent:
            try:_publish_report(mes_folder,filename,identity,evidence,info,items,source_path=path)
            except Exception as exc:
                raise RuntimeError(f'测试报告已保存至 {path}；MES文件输出失败：{exc}') from exc
    return str(path)


def _publish_report(folder,filename,identity,evidence,info,items,source_path=None):
    test_id=identity['test_id']
    with _export_lock,_file_lock(folder,filename):
        path=folder/filename;trace_path=folder/'.trace'/(filename+'.json')
        if path.exists():
            try:existing=json.loads(trace_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):existing={}
            if any(existing.get(k)!=v for k,v in identity.items()):
                folder=folder/'重复记录'/f'T{test_id}_{identity["source_key"][:8]}'
                path=folder/filename;trace_path=folder/'.trace'/(filename+'.json')
        folder.mkdir(parents=True,exist_ok=True);trace_path.parent.mkdir(exist_ok=True)
        book=build_workbook(info,items) if source_path is None else None;temporary=None;trace_temporary=None
        try:
            fd,temporary=tempfile.mkstemp(dir=folder,prefix='.report_',suffix='.tmp')
            with os.fdopen(fd,'wb') as stream:
                if source_path is None:book.save(stream)
                else:
                    with source_path.open('rb') as source:shutil.copyfileobj(source,stream)
                stream.flush();os.fsync(stream.fileno())
            fd,trace_temporary=tempfile.mkstemp(dir=trace_path.parent,suffix='.tmp')
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(dict(identity,context=evidence,info=info),stream,ensure_ascii=False,indent=2,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(trace_temporary,trace_path);os.replace(temporary,path)
        finally:
            for name in (temporary,trace_temporary):
                if name and os.path.exists(name):os.unlink(name)
    return path
