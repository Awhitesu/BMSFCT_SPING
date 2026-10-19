"""Repeat the suite in disposable source copies with software hardware guards.

This is test isolation, not a VM or a security boundary for untrusted code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone


GUARD = r'''
import ipaddress, json, os, sys, threading
_guard_lock = threading.Lock()
_guard_probe = False
_protected = os.path.normcase(os.path.realpath(os.environ['BMS_PROTECTED_ROOT']))

def _deny(action):
    with _guard_lock:
        with open(os.environ['BMS_GUARD_LOG'], 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'action': action, 'probe': _guard_probe}) + '\n')
    raise PermissionError('Sandbox hardware/workspace guard: ' + action)

def _local(host):
    if isinstance(host, bytes): host = host.decode('ascii', errors='replace')
    if str(host).lower() == 'localhost': return True
    try: return ipaddress.ip_address(str(host).split('%', 1)[0]).is_loopback
    except ValueError: return False

def _original(path):
    if not isinstance(path, (str, bytes, os.PathLike)): return False
    value = os.path.normcase(os.path.realpath(path))
    return value == _protected or value.startswith(_protected + os.sep)

def _audit(event, args):
    if event in ('socket.connect', 'socket.sendto'):
        address = args[-1]
        if not isinstance(address, tuple) or not _local(address[0]): _deny(event)
    elif event == 'socket.getaddrinfo' and not _local(args[0]): _deny(event)
    elif event == 'ctypes.dlopen':
        name = str(args[0]).lower()
        if any(word in name for word in ('pcanbasic', 'zlgcan', 'visa32', 'visa64')): _deny(event)
    elif event == 'open':
        path, mode, flags = args
        writing = (mode and any(mark in mode for mark in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if writing and _original(path): _deny('write_original_workspace')
    elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.rename'):
        paths = args[:2] if event == 'os.rename' else args[:1]
        if any(_original(path) for path in paths): _deny('mutate_original_workspace')

sys.addaudithook(_audit)
import serial
def _serial(*args, **kwargs): return _deny('physical_serial')
serial.Serial = _serial
if os.name == 'nt':
    import serial.serialwin32
    serial.serialwin32.Serial = _serial
import can
_bus = can.Bus
def _can(*args, **kwargs):
    if kwargs.get('interface') != 'virtual': return _deny('physical_can')
    return _bus(*args, **kwargs)
can.Bus = _can
try:
    import pyvisa
    pyvisa.ResourceManager = lambda *a, **k: _deny('physical_visa')
except ImportError:
    pass

def verify_guard():
    global _guard_probe
    import socket
    _guard_probe = True
    results = []
    def network():
        with socket.socket() as sock: sock.connect(('192.0.2.1', 9))
    for action in (network, lambda: serial.Serial('COM_SANDBOX'), lambda: can.Bus(interface='pcan')):
        try: action()
        except PermissionError: results.append(True)
        else: results.append(False)
    _guard_probe = False
    if results != [True, True, True]: raise RuntimeError('Hardware guards failed self-check')
    return results
'''


CHILD = r'''
import faulthandler, json, os, sys
faulthandler.enable(all_threads=True)
import sitecustomize
guards = sitecustomize.verify_guard()
from PySide6.QtWidgets import QApplication
import PySide6, pytest
app = QApplication([])
class Evidence:
    def __init__(self): self.results = []; self.subtests = []
    def pytest_runtest_logstart(self, nodeid, location): print('BEGIN', nodeid, flush=True)
    def pytest_runtest_logreport(self, report):
        value = dict(nodeid=report.nodeid, phase=report.when, outcome=report.outcome, seconds=report.duration)
        if hasattr(report, 'context'): self.subtests.append(value)
        else: self.results.append(value)
    def pytest_runtest_logfinish(self, nodeid, location): print('END', nodeid, flush=True)
    def pytest_sessionfinish(self, session, exitstatus):
        payload = dict(exitstatus=int(exitstatus), collected=session.testscollected,
            reports=self.results, subtests=self.subtests, guards=guards,
            python=sys.version, qt=PySide6.__version__)
        with open(os.environ['BMS_RESULT_JSON'], 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
plugin = Evidence()
raise SystemExit(pytest.main(['tests', '-q', '--basetemp', os.environ['BMS_PYTEST_TEMP']], plugins=[plugin]))
'''


def snapshot(root, target):
    target.mkdir()
    for name in ('core', 'data', 'devices', 'ui', 'resources', 'tests', 'tools'):
        shutil.copytree(root / name, target / name,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
    for path in root.glob('*.py'):
        shutil.copy2(path, target / path.name)
    for name in ('requirements.txt', 'pytest.ini', 'pyproject.toml', 'setup.cfg', '故障码.txt'):
        if (root / name).is_file(): shutil.copy2(root / name, target / name)
    vendor = target / 'zlgcan_python_260529'
    vendor.mkdir()
    for name in ('zlgcan.py', 'zlgcan.dll'):
        shutil.copy2(root / vendor.name / name, vendor / name)
    (target / 'config').mkdir()
    (target / 'config/sys_config.json').write_text('{}', encoding='utf-8')
    (target / 'recipes').mkdir()


def interpreter_copy(root, work):
    # Small venv redirector copy; dependencies are read from the existing venv.
    venv = work / '.venv'
    (venv / 'Scripts').mkdir(parents=True)
    shutil.copy2(root / '.venv/pyvenv.cfg', venv / 'pyvenv.cfg')
    for name in ('python.exe', 'pythonw.exe'):
        shutil.copy2(root / '.venv/Scripts' / name, venv / 'Scripts' / name)
    packages = venv / 'Lib/site-packages'
    packages.mkdir(parents=True)
    dependency_path = str(root / '.venv/Lib/site-packages')
    (packages / 'shared_dependencies.pth').write_text(
        f'import sys; sys.path.append({dependency_path!a})\n', encoding='ascii')
    return venv / 'Scripts/python.exe'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100: parser.error('runs must be 1..100')
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    sandbox = Path(tempfile.mkdtemp(prefix='bmsfct_sandbox_'))
    source = sandbox / 'snapshot'
    snapshot(root, source)
    manifest = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob('*') if p.is_file()}
    (output / 'source_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    protected = ('config/sys_config.json', 'config/sys_config.json.bak', 'bms_test_data.db')
    def protected_hashes():
        return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                if (root / name).is_file() else None for name in protected}
    summary = dict(started_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
        sandbox=str(sandbox), source_files=len(manifest), rounds=[], requested=args.runs,
        protected_before=protected_hashes(),
        scope='Disposable source copies; no production config/database; guarded hardware; not an OS VM')
    for index in range(args.runs):
        folder = sandbox / f'run_{index+1:02d}'
        folder.mkdir()
        work = folder / 'workspace'
        shutil.copytree(source, work)
        python = interpreter_copy(root, work)
        (work / 'sitecustomize.py').write_text(GUARD, encoding='utf-8')
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8',
            PYTHONFAULTHANDLER='1', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
            PYTHONPATH=str(work), BMS_PROTECTED_ROOT=str(root),
            BMS_GUARD_LOG=str(folder / 'guard.jsonl'), BMS_RESULT_JSON=str(folder / 'result.json'),
            BMS_PYTEST_TEMP=str(folder / 'pytest_temp'), TMP=str(folder), TEMP=str(folder))
        started = time.monotonic()
        timed_out = False
        with (folder / 'console.log').open('w', encoding='utf-8') as stream:
            process = subprocess.Popen([str(python), '-u', '-c', CHILD], cwd=work, env=environment,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try: code = process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                timed_out = True
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                code = process.wait(timeout=15)
        record = dict(round=index+1, exit_code=code, timed_out=timed_out, seconds=round(time.monotonic()-started, 3))
        if (folder / 'result.json').exists():
            data = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            calls = [r for r in data['reports'] if r['phase'] == 'call']
            record.update(collected=data['collected'], passed=sum(r['outcome']=='passed' for r in calls),
                failed=sum(r['outcome']=='failed' for r in data['reports']),
                skipped=sum(r['outcome']=='skipped' for r in data['reports']), subtests=len(data['subtests']),
                pytest_exit=data['exitstatus'], python=data['python'], qt=data['qt'])
        guard_file = folder / 'guard.jsonl'
        guards = [json.loads(line) for line in guard_file.read_text(encoding='utf-8').splitlines()] if guard_file.exists() else []
        record['guard_self_checks'] = sum(row['probe'] for row in guards)
        record['guard_blocks_outside_probe'] = [row['action'] for row in guards if not row['probe']]
        record['qualified'] = (code == 0 and record.get('pytest_exit') == 0
                               and record['guard_self_checks'] == 3 and not timed_out)
        destination = output / folder.name
        destination.mkdir()
        for name in ('console.log', 'result.json', 'guard.jsonl'):
            if (folder / name).exists(): shutil.copy2(folder / name, destination / name)
        summary['rounds'].append(record)
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=True), flush=True)
    summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
    summary['protected_after'] = protected_hashes()
    summary['protected_unchanged'] = summary['protected_before'] == summary['protected_after']
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(not row['qualified'] for row in summary['rounds']))


if __name__ == '__main__':
    raise SystemExit(main())
