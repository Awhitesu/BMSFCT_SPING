"""Durable 锐能 upload queue. Network outcomes never change a test verdict."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from core.generic_mes import GenericMesClient, build_upload_payload, validate_upload_payload

_SEND_LOCK = threading.Lock()
PAYLOAD_SCHEMA = 2


def outbox_root(db_path=None):
    from core import system_config
    root = Path(db_path).resolve().parent if db_path else Path(system_config.default_path()).parent.parent
    return root / 'data' / 'mes_uploads' / 'ruineng'


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def create_upload_job(config, sn, passed, rows, db_path=None, execution_id=None):
    """Persist before any request, including payloads that cannot be sent yet."""
    job = dict(created_at=_now(), execution_id=execution_id, state='pending', payload_schema=PAYLOAD_SCHEMA,
               url=config['generic_mes_upload_url'], timeout=config['generic_mes_timeout'],
               max_attempts=config['generic_mes_upload_attempts'], history=[], last_error='',
               source=dict(config=config, sn=sn, passed=bool(passed), rows=rows))
    try:
        job['payload'] = build_upload_payload(config, sn, passed, rows, validate=False)
    except Exception as exc:
        job['payload'] = None
        job['last_error'] = f'上传数据构建失败：{exc}'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    path = outbox_root(db_path) / 'pending' / f'{stamp}_{uuid.uuid4().hex}.json'
    _write(path, job)
    return str(path)


def pending_jobs(db_path=None):
    return sorted((outbox_root(db_path) / 'pending').glob('*.json'), key=lambda path: path.name)


def send_job(path, cancelled=lambda: False):
    """One upload round (1–2 requests); replay uses the original target/data."""
    path = Path(path)
    with _SEND_LOCK:
        if not path.exists():
            return dict(passed=True, message='记录已补传归档', path=str(path), attempts=0)
        try:
            with path.open(encoding='utf-8') as stream:
                job = json.load(stream)
            if job.get('state') == 'sent':
                return _archive(path, job, 0)
            if cancelled():
                return dict(passed=False, message='上传已暂停，文件保留待补传', path=str(path), attempts=0)
            if job.get('payload_schema') != PAYLOAD_SCHEMA:
                source = job.get('source')
                if not isinstance(source, dict) or not source.get('rows'):
                    raise ValueError('旧待传文件缺少原始测试明细，无法按新字段规则补传；文件已保留')
                corrected = build_upload_payload(source['config'], source['sn'], source['passed'], source['rows'], validate=False)
                job.setdefault('legacy_payload', job.get('payload'))
                job['payload'] = corrected
                job['payload_schema'] = PAYLOAD_SCHEMA
                job['payload_migrated_at'] = _now()
                _write(path, job)
            budget = job['max_attempts']
            if type(budget) is not int or not 1 <= budget <= 2:
                raise ValueError('上传次数必须为1或2')
            for attempt in range(budget):
                if cancelled():
                    return dict(passed=False, message='上传已暂停，文件保留待补传', path=str(path), attempts=attempt)
                try:
                    if job.get('payload') is None:
                        source = job['source']
                        job['payload'] = build_upload_payload(source['config'], source['sn'], source['passed'], source['rows'], validate=False)
                    validate_upload_payload(job['payload'])
                    result = GenericMesClient(job['url'], job['timeout']).post(job['payload'])
                    passed, reason = result.passed is True, result.message
                    response, status = result.response, result.status_code
                except Exception as exc:
                    passed, reason, response, status = False, str(exc), None, None
                job['history'].append(dict(time=_now(), passed=passed, message=reason,
                                           status_code=status, response=response))
                job['last_error'] = '' if passed else reason
                job['state'] = 'sent' if passed else 'pending'
                _write(path, job)
                if passed:
                    return _archive(path, job, attempt + 1)
                if attempt + 1 < budget:
                    time.sleep(.5)
            return dict(passed=False, message=job['last_error'], path=str(path), attempts=budget)
        except Exception as exc:
            # Keep the original file even for malformed records or disk failures.
            return dict(passed=False, message=f'待上传文件处理失败：{exc}', path=str(path), attempts=0)


def _archive(path, job, attempts):
    destination = path.parent.parent / 'sent' / path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, destination)
    return dict(passed=True, message='锐能MES上传成功', path=str(destination), attempts=attempts)
