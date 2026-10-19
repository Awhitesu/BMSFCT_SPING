"""Repeat Qt tests in fresh processes, retaining the last test and fatal traceback.

Run with the workspace venv: python tools/qt_crash_stress.py --runs 10
No instruments are connected by this test runner.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--output', default='logs/qt_crash_stress')
    parser.add_argument('--procdump', default='')
    parser.add_argument('--native-dump', action='store_true')
    parser.add_argument('--timeout',type=float,default=180)
    parser.add_argument('tests', nargs='*', default=['tests'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root / args.output
    folder.mkdir(parents=True, exist_ok=True)
    child = '''
import faulthandler, sys, threading, gc, os
faulthandler.enable(all_threads=True)
from PySide6.QtWidgets import QApplication
import pytest
app = QApplication([])
import PySide6, shiboken6
from PySide6.QtCore import qVersion
print('QT_RUNTIME', PySide6.__version__, shiboken6.__version__, qVersion(), flush=True)
if os.environ.get('QT_NATIVE_DUMP'):
    from tools.native_exception_dump import install
    dump_handler = install(os.environ['QT_NATIVE_DUMP'])
class Trace:
    def pytest_runtest_logstart(self, nodeid, location):
        print('BEGIN', nodeid, flush=True)
    def pytest_runtest_logfinish(self, nodeid, location):
        print('END', nodeid, flush=True)
raise SystemExit(pytest.main(sys.argv[1:] + ['-q', '-x'], plugins=[Trace()]))
'''
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8',
                       PYTHONFAULTHANDLER='1')
    if args.procdump:
        environment['PYTHONPATH'] = str(root / '.venv/Lib/site-packages')
    if args.native_dump:
        environment['QT_NATIVE_DUMP'] = str(folder)
    failed = False
    records=[]
    for index in range(args.runs):
        log = folder / f'run_{index:03d}.log'
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            command = [sys.executable, '-u', '-c', child, *args.tests]
            if args.procdump:
                command = [args.procdump, '-accepteula', '-e', '-x', str(folder),
                           sys._base_executable, '-u', '-c', child, *args.tests]
            seen=set(folder.glob('exception_*.json'))
            process = subprocess.Popen(command,cwd=root,env=environment,stdout=stream,stderr=subprocess.STDOUT)
            while process.poll() is None:
                if args.native_dump:
                    for marker in set(folder.glob('exception_*.json'))-seen:
                        try:evidence=json.loads(marker.read_text(encoding='ascii'))
                        except (OSError,ValueError):continue
                        seen.add(marker)
                        executable=root/'tools/native_debug/procdump64.exe'
                        if executable.exists():
                            try:
                                snapshot=subprocess.run([str(executable),'-accepteula',str(evidence['pid']),
                                    str(folder/f'av_{evidence["pid"]}.dmp')],capture_output=True,timeout=25)
                                output=snapshot.stdout+snapshot.stderr
                                stream.write(output.decode('utf-16-le' if b'\x00' in output else 'utf-8',errors='replace'))
                                stream.flush()
                            finally:
                                import ctypes
                                from ctypes import wintypes
                                kernel=ctypes.WinDLL('kernel32')
                                kernel.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR]
                                kernel.OpenEventW.restype=wintypes.HANDLE
                                kernel.SetEvent.argtypes=[wintypes.HANDLE]
                                kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                                event=kernel.OpenEventW(2,False,f'Local\\BMSFCTNativeCrash_{evidence["pid"]}')
                                if event:kernel.SetEvent(event);kernel.CloseHandle(event)
                if time.monotonic()-started>args.timeout:
                    stream.write('\nCAMPAIGN_TIMEOUT\n');stream.flush()
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                    break
                time.sleep(.05)
            code=process.wait(timeout=10)
        print(f'run={index} exit={code} seconds={time.monotonic()-started:.1f} log={log}', flush=True)
        records.append(dict(run=index,exit_code=code,seconds=round(time.monotonic()-started,3),log=str(log)))
        (folder/'summary.json').write_text(json.dumps(dict(requested_runs=args.runs,
            completed_runs=len(records),records=records),ensure_ascii=False,indent=2),encoding='utf-8')
        if code:
            failed = True
            break
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
