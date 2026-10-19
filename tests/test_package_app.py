import importlib.util
import pytest
from pathlib import Path

spec = importlib.util.spec_from_file_location('package_app', Path(__file__).resolve().parents[1] / 'tools' / 'package_app.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_database_backup_preserves_users_and_existing_target(tmp_path):
    import sqlite3
    from contextlib import closing
    source, target = tmp_path/'source.db', tmp_path/'target.db'
    with closing(sqlite3.connect(source)) as db, db:
        db.execute('CREATE TABLE sys_users(username TEXT, password TEXT, role TEXT)')
        db.execute('INSERT INTO sys_users VALUES (?,?,?)', ('operator', 'hashed-value', 'operator'))
    assert package.copy_database(source,target)
    with closing(sqlite3.connect(target)) as db, db:
        assert db.execute('SELECT * FROM sys_users').fetchone() == ('operator', 'hashed-value', 'operator')
        db.execute('UPDATE sys_users SET username=?', ('machine-user',))
    assert not package.copy_database(source,target)
    with closing(sqlite3.connect(target)) as db:
        assert db.execute('SELECT username FROM sys_users').fetchone()[0] == 'machine-user'


def test_initial_files_preserve_machine_configuration(tmp_path):
    source, target = tmp_path/'source', tmp_path/'target'
    source.mkdir(); target.mkdir()
    (source/'sys_config.json').write_text('source')
    (source/'new.json').write_text('new')
    (source/'secret.bak').write_text('backup')
    (target/'sys_config.json').write_text('machine')
    (target/'bms_test_data.db').write_bytes(b'existing records')
    package.copy_missing(source,target)
    assert (target/'sys_config.json').read_text() == 'machine'
    assert (target/'new.json').read_text() == 'new'
    assert not (target/'secret.bak').exists()
    assert (target/'bms_test_data.db').read_bytes() == b'existing records'


@pytest.mark.parametrize('versioned',[False,True])
def test_build_uses_staging_and_preserves_production_database(tmp_path, monkeypatch,versioned):
    import json
    import shutil
    source_root=package.ROOT
    for name in ('tools/upgrade_app.ps1','tools/upgrade_app.cmd','doc/统计追溯与现场安全升级_20261019.md'):
        destination=tmp_path/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source_root/name,destination)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    monkeypatch.setattr(package, 'check_environment', lambda: {'pyinstaller':'test'})
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules,'build_seedkey_hosts', SimpleNamespace(build=lambda root:None))
    output = tmp_path/'dist'/('BMSFCT_V'+package.next_version() if versioned else 'BMSFCT')
    output.mkdir(parents=True)
    (output/'bms_test_data.db').write_bytes(b'records')
    (output/'config').mkdir()
    (output/'config'/'sys_config.json').write_text('machine config')
    (tmp_path/'config').mkdir()
    (tmp_path/'config'/'sys_config.json').write_text('source config')
    class Process:
        stdout = ['test build log\n']
        def __init__(self, command, **kwargs):
            stage = Path(command[command.index('--distpath')+1])/'BMSFCT'
            assert stage != output
            stage.mkdir(parents=True)
            (stage/package.APP_NAME).write_bytes(b'MZ test executable')
            template=Path('resources/test_report_layout.json');template.parent.mkdir(parents=True,exist_ok=True)
            template.write_text('{}')
            packaged_template=stage/'_internal'/template;packaged_template.parent.mkdir(parents=True,exist_ok=True)
            packaged_template.write_text('{}')
            for arch in ('x86','x64'):
                helper=Path(f'resources/seedkey/SeedKeyHost-{arch}.exe')
                helper.parent.mkdir(parents=True,exist_ok=True);helper.write_bytes(b'MZ helper')
                packaged=stage/'_internal'/helper
                packaged.parent.mkdir(parents=True,exist_ok=True);packaged.write_bytes(helper.read_bytes())
        def wait(self):
            return 0
    monkeypatch.setattr(package.subprocess, 'Popen', Process)
    assert package.main(['--versioned'] if versioned else []) == 0
    assert (output/'bms_test_data.db').read_bytes() == b'records'
    assert (output/'config'/'sys_config.json').read_text() == 'machine config'
    assert json.loads((output/'build-info.json').read_text(encoding='utf-8'))['mode'] == 'onedir'
    version = json.loads((tmp_path/'version.json').read_text())['app_version']
    assert json.loads((output/'build-info.json').read_text(encoding='utf-8'))['app_version'] == version
    assert (output/'版本信息.txt').exists()
    assert (output/'upgrade_app.ps1').read_bytes()==(source_root/'tools/upgrade_app.ps1').read_bytes()
    assert (output/'upgrade_app.cmd').exists()
    assert (output/'使用说明'/'统计追溯与现场安全升级_20261019.md').exists()


def test_daily_version_increments_and_remains_monotonic():
    from datetime import date
    assert package.next_version(None,date(2026,10,18)) == '2026.10.18.1'
    assert package.next_version('2026.10.18.1',date(2026,10,18)) == '2026.10.18.2'
    assert package.next_version('2026.10.18.9',date(2026,10,19)) == '2026.10.19.1'
    assert package.next_version('2026.10.18.9',date(2026,10,17)) == '2026.10.18.10'


def test_missing_staged_seedkey_helper_blocks_publication(tmp_path,monkeypatch):
    import sys,pytest
    from types import SimpleNamespace
    monkeypatch.setattr(package,'ROOT',tmp_path)
    monkeypatch.setattr(package,'check_environment',lambda:{})
    monkeypatch.setitem(sys.modules,'build_seedkey_hosts',SimpleNamespace(build=lambda root:None))
    class Process:
        stdout=[]
        def __init__(self,command,**kwargs):
            output=Path(command[command.index('--distpath')+1])/'BMSFCT'
            output.mkdir(parents=True);(output/package.APP_NAME).write_bytes(b'MZ')
            template=Path('resources/test_report_layout.json');template.parent.mkdir(parents=True,exist_ok=True);template.write_text('{}')
            packaged=output/'_internal'/template;packaged.parent.mkdir(parents=True,exist_ok=True);packaged.write_text('{}')
        def wait(self):return 0
    monkeypatch.setattr(package.subprocess,'Popen',Process)
    with pytest.raises(RuntimeError,match='SeedKey'):package.main([])
    assert not (tmp_path/'dist/BMSFCT'/package.APP_NAME).exists()
    assert not (tmp_path/'version.json').exists()


def test_failure_does_not_consume_version_and_lock_releases(tmp_path,monkeypatch):
    import json, sys
    from types import SimpleNamespace
    import pytest
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(package,'ROOT',tmp_path)
    monkeypatch.setattr(package,'check_environment',lambda:{})
    monkeypatch.setitem(sys.modules,'build_seedkey_hosts',SimpleNamespace(build=lambda root:None))
    old={'app_version':'2026.10.18.7'}
    (tmp_path/'version.json').write_text(json.dumps(old))
    monkeypatch.setattr(package.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(stdout=[],wait=lambda:1))
    with pytest.raises(RuntimeError):package.main([])
    assert json.loads((tmp_path/'version.json').read_text())==old
    assert package.main(['--check'])==0
    assert json.loads((tmp_path/'version.json').read_text())==old
    with package.packaging_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with package.packaging_lock(tmp_path):pass
    with package.packaging_lock(tmp_path):pass


def test_frozen_version_uses_embedded_manifest(tmp_path,monkeypatch):
    import sys,json
    from core.app_version import get_version,versioned_title
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    (tmp_path/'resources').mkdir()
    (tmp_path/'resources/app_version.json').write_text(json.dumps({'app_version':'2026.10.18.1'}))
    assert get_version()=='2026.10.18.1'
    assert versioned_title('Custom')=='Custom  |  V2026.10.18.1'
