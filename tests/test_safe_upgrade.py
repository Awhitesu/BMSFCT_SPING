import hashlib
import json
import os
from pathlib import Path
import subprocess
import pytest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows upgrade script')
SCRIPT=Path(__file__).resolve().parents[1]/'tools'/'upgrade_app.ps1'


def fixture(tmp_path):
    source=tmp_path/'new release';target=tmp_path/'production'
    for root,content in ((source,b'new program'),(target,b'old program')):
        root.mkdir();(root/'App.exe').write_bytes(content)
        (root/'_internal').mkdir();(root/'_internal'/'module.bin').write_bytes(content)
        (root/'config').mkdir();(root/'config'/'sys_config.json').write_bytes(content)
        (root/'recipes').mkdir();(root/'recipes'/'project.json').write_bytes(content)
        (root/'bms_test_data.db').write_bytes(content)
        (root/'build-info.json').write_text(json.dumps(dict(executable='App.exe',app_version='2026.10.19.2',sha256=hashlib.sha256(content).hexdigest())),encoding='utf-8')
    return source,target


def run(source,target):
    return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(SCRIPT),
        '-SourceDirectory',str(source),'-TargetDirectory',str(target)],capture_output=True,timeout=60)


def test_upgrade_preserves_database_users_recipes_and_settings(tmp_path):
    source,target=fixture(tmp_path)
    result=run(source,target)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert (target/'App.exe').read_bytes()==b'new program'
    assert (target/'_internal'/'module.bin').read_bytes()==b'new program'
    for path in ('bms_test_data.db','config/sys_config.json','recipes/project.json'):
        assert (target/path).read_bytes()==b'old program'
    backups=list((target/'update_backups').iterdir())
    assert len(backups)==1
    assert (backups[0]/'bms_test_data.db').read_bytes()==b'old program'
    assert (backups[0]/'App.exe').read_bytes()==b'old program'
    assert (backups[0]/'upgrade-result.json').exists()


def test_upgrade_bad_checksum_never_changes_target(tmp_path):
    source,target=fixture(tmp_path)
    (source/'App.exe').write_bytes(b'corrupt')
    assert run(source,target).returncode!=0
    assert (target/'App.exe').read_bytes()==b'old program'
    assert not (target/'update_backups').exists()


def test_upgrade_rejects_same_directory(tmp_path):
    source,target=fixture(tmp_path)
    assert run(target,target).returncode!=0
    assert (target/'App.exe').read_bytes()==b'old program'
