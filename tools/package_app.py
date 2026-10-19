"""Repeatable local Windows packaging. Never starts the instrument application."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'BMS软件注入&APP刷写系统.exe'


def next_version(previous=None, today=None):
    today = today or datetime.now().date()
    day = (today.year, today.month, today.day)
    if previous:
        parts = tuple(int(n) for n in previous.split('.'))
        if len(parts) != 4 or any(n < 0 or n > 65535 for n in parts):
            raise ValueError('版本记录无效，请检查 version.json')
        if parts[:3] >= day:
            day, revision = parts[:3], parts[3] + 1
        else:
            revision = 1
    else:
        revision = 1
    if revision > 65535:
        raise ValueError('当日版本序号超过Windows文件版本范围')
    return '.'.join(map(str, (*day, revision)))


@contextmanager
def packaging_lock(root):
    import msvcrt
    path = root / 'build' / 'packaging.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        if path.stat().st_size == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('已有打包任务正在运行，请等待其完成。') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def prepare_version(work):
    previous = []
    for path in (ROOT / 'version.json', ROOT / 'dist/BMSFCT/build-info.json',
                 ROOT / 'dist/BMSFCT-onefile/build-info.json'):
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('app_version'):
                previous.append(data['app_version'])
    last = max(previous, key=lambda v: tuple(map(int, v.split('.')))) if previous else None
    version = next_version(last)
    manifest = {'app_version': version, 'built_at': datetime.now().astimezone().isoformat()}
    directory = work / 'version'
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / 'app_version.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    info = directory / 'version_info.txt'
    parts = tuple(map(int, version.split('.')))
    info.write_text(f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f,
        flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
      kids=[StringFileInfo([StringTable('080404b0', [
        StringStruct('FileDescription', 'BMSFCT'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'BMSFCT'),
        StringStruct('ProductVersion', '{version}')])]),
        VarFileInfo([VarStruct('Translation', [2052, 1200])])])''', encoding='utf-8')
    return manifest, manifest_path, info


def check_environment(root=ROOT):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('请在 64 位 Windows Python 环境中打包。')
    versions = {name: importlib.metadata.version(name) for name in
                ('pyinstaller', 'PySide6', 'shiboken6', 'python-can', 'pyserial','xlwt','cantools','regex')}
    if versions['PySide6'] != versions['shiboken6']:
        raise RuntimeError('PySide6 与 shiboken6 版本不一致，请先修复项目虚拟环境。')
    required = ['main.py', 'AgingTest_BMS.spec', 'resources/style.qss', 'resources/icon.ico',
                'resources/test_report_layout.json',
                'resources/seedkey/SeedKeyHost-x86.exe', 'resources/seedkey/SeedKeyHost-x64.exe',
                '故障码.txt', 'PCAN-Basic/x64/PCANBasic.dll', '力捷峰U盘/Demo/OpteeQDLLx64.dll',
                '力捷峰U盘/Demo/libcrypto-3.dll', 'zlgcan(20260806)/zlgcan_x64/zlgcan.dll',
                'zlgcan_python_260529/zlgcan.py']
    missing = [p for p in required if not (root / p).is_file()]
    if missing:
        raise RuntimeError('缺少打包文件：' + '、'.join(missing))
    return versions


def copy_missing(source, destination):
    """Initialize runtime files while retaining target machine configuration."""
    if not source.exists():
        return
    for path in source.rglob('*'):
        if path.is_file() and path.suffix != '.bak':
            target = destination / path.relative_to(source)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


def copy_database(source, destination):
    """SQLite online backup includes users, roles and parameters consistently."""
    if destination.exists() or not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.packaging.tmp')
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('数据库备份完整性检查失败')
        if destination.exists():
            return False
        temporary.rename(destination)
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description='BMSFCT 本地增量打包；默认目录版，速度更快。')
    parser.add_argument('--onefile', action='store_true', help='生成单文件 EXE，打包及启动较慢')
    parser.add_argument('--clean', action='store_true', help='清理 PyInstaller 缓存后构建（不删除配置）')
    parser.add_argument('--check', action='store_true', help='只检查打包环境，不构建')
    parser.add_argument('--open', action='store_true', help='完成后打开输出目录')
    parser.add_argument('--versioned', action='store_true', help='目录版输出到独立版本目录，避免覆盖运行中的程序')
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    from build_seedkey_hosts import build
    build(ROOT)
    versions = check_environment()
    print('环境检查通过：' + json.dumps(versions, ensure_ascii=False), flush=True)
    if args.check:
        return 0
    with packaging_lock(ROOT):
        return build_release(args, versions)


def build_release(args, versions):
    mode = 'onefile' if args.onefile else 'onedir'
    dist = ROOT / 'dist' / ('BMSFCT-onefile' if args.onefile else '')
    output = dist if args.onefile else dist / 'BMSFCT'
    work = ROOT / 'build' / mode
    staging = work / 'staging'
    staged_output = staging if args.onefile else staging / 'BMSFCT'
    logs = ROOT / 'logs' / 'packaging'
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / (datetime.now().strftime('%Y%m%d_%H%M%S') + '.log')
    manifest, version_file, version_info = prepare_version(work)
    if getattr(args,'versioned',False) and not args.onefile:
        output=dist/('BMSFCT_V'+manifest['app_version'])
    environment = dict(os.environ, BMS_BUILD_MODE=mode, BMS_VERSION_FILE=str(version_file),
                       BMS_VERSION_INFO=str(version_info), PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(staging),
               '--workpath', str(work)]
    if args.clean:
        command.append('--clean')
    command.append(str(ROOT / 'AgingTest_BMS.spec'))
    started = time.monotonic()
    print('开始构建：V' + manifest['app_version'] + '；' + mode + '；日志：' + str(log_path), flush=True)
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        for line in process.stdout:
            log.write(line); log.flush()
            print(line, end='', flush=True)
        code = process.wait()
    if code:
        raise RuntimeError(f'打包失败（退出码 {code}），请查看 {log_path}。如 EXE 正在运行，请关闭后重试。')
    if not (staged_output / APP_NAME).is_file():
        raise RuntimeError('未找到预期 EXE：' + str(staged_output / APP_NAME))
    if not args.onefile:
        template=staged_output/'_internal/resources/test_report_layout.json'
        if not template.is_file() or template.read_bytes()!=(ROOT/'resources/test_report_layout.json').read_bytes():
            raise RuntimeError('报告格式资源缺失或不一致，停止发布：'+str(template))
        for architecture in ('x86','x64'):
            helper=staged_output/f'_internal/resources/seedkey/SeedKeyHost-{architecture}.exe'
            source=ROOT/f'resources/seedkey/SeedKeyHost-{architecture}.exe'
            if not helper.is_file() or helper.read_bytes()!=source.read_bytes():
                raise RuntimeError('SeedKey辅助程序缺失或不一致，停止发布：'+str(helper))
    # PyInstaller only replaces disposable staging, never the deployment folder.
    shutil.copytree(staged_output, output, dirs_exist_ok=True)
    executable = output / APP_NAME
    for name in ('upgrade_app.ps1','upgrade_app.cmd'):
        shutil.copy2(ROOT / 'tools' / name, output / name)
    guide=output/'使用说明'/'统计追溯与现场安全升级_20261019.md'
    guide.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'doc'/'统计追溯与现场安全升级_20261019.md',guide)
    for name in ('config', 'recipes'):
        copy_missing(ROOT / name, output / name)
    copied_database = copy_database(ROOT / 'bms_test_data.db', output / 'bms_test_data.db')
    print('人员和系统数据库：' + ('已备份到成品' if copied_database else '保留成品已有数据库（或源数据库不存在）'), flush=True)
    with executable.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = dict(app_version=manifest['app_version'], built_at=manifest['built_at'], mode=mode, versions=versions,
                    elapsed_seconds=round(time.monotonic()-started, 1), executable=APP_NAME,
                    sha256=checksum)
    (output / 'build-info.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / '版本信息.txt').write_text(f'BMSFCT V{manifest["app_version"]}\n构建时间：{manifest["built_at"]}\nSHA256：{checksum}\n', encoding='utf-8-sig')
    temporary_version = ROOT / 'version.json.tmp'
    temporary_version.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary_version.replace(ROOT / 'version.json')
    with (logs / 'releases.jsonl').open('a', encoding='utf-8') as history:
        history.write(json.dumps(metadata, ensure_ascii=False) + '\n')
    print('发布版本：V' + manifest['app_version'], flush=True)
    print(f'\n打包完成，耗时 {metadata["elapsed_seconds"]} 秒。\n应用：{executable}\n目录版请复制整个 BMSFCT 文件夹。', flush=True)
    print('现有工位升级：退出软件后，在新版目录运行 upgrade_app.cmd，选择原现场目录；不要覆盖现场数据库、config 或 recipes。',flush=True)
    if args.open:
        os.startfile(output)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('\n打包失败：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
