"""上位机使能、失败隔离与先停后写再启动的回归；不操作真实进程。"""
import configparser
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from core import recipe_app_flash as flash
from ui.tabs.config_tab import ConfigTab
from ui.widgets.recipe_app_flash_form import RecipeAppFlashForm


app = QApplication.instance() or QApplication([])


@pytest.fixture
def host(tmp_path, monkeypatch):
    program = tmp_path / 'Host.exe'
    program.touch()
    ini = tmp_path / 'config.ini'
    ini.write_bytes(b'[App]\r\nType=Old\r\nAddr=Old\r\n[Other]\r\nKeep=1\r\n')
    (tmp_path / 'Flash' / 'New-path').mkdir(parents=True)
    controls = {}
    for name in ('shutdown_host', 'start_host', 'wait_seconds'):
        controls[name] = Mock()
        monkeypatch.setattr(flash, name, controls[name])
    return SimpleNamespace(program=program, ini=ini, controls=controls,
        config=dict(enabled=True, restart_name='Host.exe', host_path=str(program), model='New', flash_path='New-path'))


def read_values(ini):
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read_string(ini.read_bytes().decode('utf-8'))
    return parsed['App']['Type'], parsed['App']['Addr']


@pytest.mark.parametrize('config', [None, {}, {'host_path': 'old.exe', 'model': 'A', 'flash_path': 'P'},
    {'enabled': False, 'host_path': '', 'model': '\n', 'flash_path': None},
    {'enabled': 'true', 'restart_name': 'Host.exe'}])
def test_disabled_or_legacy_config_does_nothing_even_with_hidden_incomplete_values(host, config):
    before = host.ini.read_bytes()
    assert flash.validate_app_flash(config) is None
    assert flash.prepare_app_flash(config) is None
    assert host.ini.read_bytes() == before
    for operation in host.controls.values():
        operation.assert_not_called()


@pytest.mark.parametrize('key,label', [('host_path', '上位机路径'), ('model', 'App刷写型号'),
    ('flash_path', 'App刷写路径'), ('restart_name', '重启上位机名称')])
def test_enabled_requires_all_four_fields_before_stopping(host, key, label):
    with pytest.raises(ValueError, match=label):
        flash.prepare_app_flash(dict(host.config, **{key: ' '}))
    host.controls['shutdown_host'].assert_not_called()


@pytest.mark.parametrize('name', ['C:/Host.exe', '../Host.exe', 'Host', 'Host.vi', 'x.exe & echo y',
                                'x*.exe', 'x\n.exe', 'x\t.exe'])
def test_restart_name_rejects_paths_and_commands(host, name):
    with pytest.raises(ValueError):
        flash.validate_app_flash(dict(host.config, restart_name=name))


@pytest.mark.parametrize('name', ['BMS_General.exe', '上位机 & APP.exe', 'App %NAME%!.EXE'])
def test_normal_windows_executable_names_are_preserved(host, name):
    assert flash.validate_app_flash(dict(host.config, restart_name=name))['restart_name'] == name


@pytest.mark.parametrize('failure', ['host', 'restart', 'ini', 'malformed_ini'])
def test_invalid_files_are_reported_before_stopping(host, failure):
    if failure == 'host':
        host.config['host_path'] += '.missing'
    elif failure == 'restart':
        host.config['restart_name'] = 'Missing.exe'
    elif failure == 'ini':
        host.ini.unlink()
    else:
        host.ini.write_text('not an ini', encoding='utf-8')
    with pytest.raises(ValueError, match='参数检查失败'):
        flash.prepare_app_flash(host.config)
    host.controls['shutdown_host'].assert_not_called()
    host.controls['start_host'].assert_not_called()


def test_confirmed_stop_then_verified_files_then_start_without_fixed_waits(host, monkeypatch):
    events = []
    replace = flash.os.replace

    def stop(*_, **__):
        assert read_values(host.ini) == ('Old', 'Old')
        events.append('stop')

    def write(source, target):
        if Path(target) == host.ini:
            assert events == ['stop']
            event = 'write'
        else:
            assert Path(target) == host.program.parent / 'Flash' / 'Record.txt'
            assert events[-1] == 'write'
            event = 'ready'
        replace(source, target)
        events.append(event)

    def start(program, _, **options):
        assert program == host.program
        assert read_values(host.ini) == ('New', 'New-path')
        assert options == dict(timeout=8)
        assert (program.parent / 'Flash' / 'Record.txt').read_text().strip() == 'Ready'
        events.append('start')

    host.controls['shutdown_host'].side_effect = stop
    host.controls['start_host'].side_effect = start
    monkeypatch.setattr(flash.os, 'replace', write)
    assert flash.prepare_app_flash(host.config) == host.config
    assert events == ['stop', 'write', 'ready', 'start']
    host.controls['wait_seconds'].assert_not_called()


def test_stop_failure_does_not_write_or_restart(host):
    before = host.ini.read_bytes()
    host.controls['shutdown_host'].side_effect = TimeoutError('仍在运行')
    with pytest.raises(RuntimeError, match='停止上位机失败.*仍在运行'):
        flash.prepare_app_flash(host.config)
    assert host.ini.read_bytes() == before
    host.controls['start_host'].assert_not_called()
    host.controls['wait_seconds'].assert_not_called()


def test_write_failure_does_not_restart_and_keeps_original(host, monkeypatch):
    before = host.ini.read_bytes()
    monkeypatch.setattr(flash.os, 'replace', Mock(side_effect=PermissionError('locked')))
    with pytest.raises(RuntimeError, match='写入并校验 config.ini失败.*locked'):
        flash.prepare_app_flash(host.config)
    assert host.ini.read_bytes() == before
    assert not list(host.ini.parent.glob('.app_config_*.tmp'))
    host.controls['start_host'].assert_not_called()


def test_readback_mismatch_prevents_restart(host, monkeypatch):
    replace = flash.os.replace

    def corrupt(source, target):
        replace(source, target)
        host.ini.write_bytes(host.ini.read_bytes().replace(b'Type=New', b'Type=Wrong'))

    monkeypatch.setattr(flash.os, 'replace', corrupt)
    with pytest.raises(RuntimeError, match='回读校验失败'):
        flash.prepare_app_flash(host.config)
    host.controls['start_host'].assert_not_called()
    host.controls['wait_seconds'].assert_not_called()


def test_restart_failure_is_not_reported_as_success(host):
    host.controls['start_host'].side_effect = TimeoutError('启动超时')
    with pytest.raises(RuntimeError, match='启动并确认上位机失败.*启动超时'):
        flash.prepare_app_flash(host.config)
    assert read_values(host.ini) == ('New', 'New-path')
    assert host.controls['shutdown_host'].call_count == 2


def test_real_exe_path_determines_start_target(host):
    flash.prepare_app_flash(host.config)
    host.controls['start_host'].assert_called_once_with(host.program, None, timeout=8)
    host.controls['shutdown_host'].assert_called_once_with(host.program, None, logger=None)


def test_mismatched_process_name_is_rejected_before_stopping(host):
    host.config['restart_name'] = 'Other.exe'
    (host.program.parent/'Other.exe').touch()
    before = host.ini.read_bytes()
    with pytest.raises(ValueError, match='文件名不一致'):
        flash.prepare_app_flash(host.config)
    host.controls['shutdown_host'].assert_not_called()
    assert host.ini.read_bytes() == before


def test_preserves_other_settings_saved_by_host_on_exit(host):
    host.controls['shutdown_host'].side_effect = lambda *_, **__: host.ini.write_bytes(
        host.ini.read_bytes().replace(b'Keep=1', b'Keep=2\r\nAdded=3'))
    flash.prepare_app_flash(host.config)
    assert b'Keep=2\r\nAdded=3' in host.ini.read_bytes()
    assert read_values(host.ini) == ('New', 'New-path')


@pytest.mark.parametrize('cancel_at', ['before', 'stopped', 'config_written', 'record_ready'])
def test_cancel_prevents_remaining_phases(host, monkeypatch, cancel_at):
    cancel = threading.Event()
    if cancel_at == 'before':
        cancel.set()
    elif cancel_at == 'stopped':
        host.controls['shutdown_host'].side_effect = lambda *_, **__: cancel.set()
    else:
        name = '_write_and_verify_ini' if cancel_at == 'config_written' else '_initialize_record'
        operation = getattr(flash, name)
        def finish_then_cancel(*args):
            operation(*args)
            cancel.set()
        monkeypatch.setattr(flash, name, finish_then_cancel)
    with pytest.raises(RuntimeError, match='取消'):
        flash.prepare_app_flash(host.config, cancel=cancel.is_set)
    host.controls['start_host'].assert_not_called()
    assert read_values(host.ini) == (('New', 'New-path') if cancel_at in ('config_written', 'record_ready') else ('Old', 'Old'))


def test_checkbox_hides_all_four_fields_and_preserves_values_across_recipes(host):
    form = RecipeAppFlashForm()
    form.show()
    try:
        assert not form.enable_sync.isChecked() and not form.parameters.isVisible()
        form.set_config(host.config)
        assert form.enable_sync.isChecked()
        for field in (form.host_path, form.model, form.flash_path, form.restart_name):
            assert field.isVisible()
        form.enable_sync.setChecked(False)
        assert not form.parameters.isVisible()
        form.model.setEditText('')
        saved = form.get_config()  # 关闭后隐藏的不完整参数可保存。
        assert saved['enabled'] is False and saved['restart_name'] == 'Host.exe'
        form.set_config(None)
        assert not form.enable_sync.isChecked() and form.restart_name.text() == ''
        form.set_config(saved)
        assert not form.parameters.isVisible() and form.restart_name.text() == 'Host.exe'
        form.enable_sync.setChecked(True)
        with pytest.raises(ValueError, match='App刷写型号'):
            form.get_config()
        form.set_config({key: value for key, value in host.config.items() if key != 'enabled'})
        assert not form.enable_sync.isChecked() and form.host_path.text() == str(host.program)
    finally:
        form.close()


def test_editor_serializes_explicit_disabled_state_without_touching_ini(host):
    page = ConfigTab(SimpleNamespace(list_recipes=lambda: []))
    try:
        page.recipe_app_flash.set_config(dict(host.config, enabled=False, model=''))
        saved = page._collect_recipe_data('disabled')
        assert saved['app_flash']['enabled'] is False
        assert saved['app_flash']['restart_name'] == 'Host.exe'
        page.load_recipe_to_tree(saved)
        assert page.recipe_app_flash.get_config() == saved['app_flash']
        assert read_values(host.ini) == ('Old', 'Old')
        page.recipe_app_flash.set_config(None)
        assert page._collect_recipe_data('new')['app_flash']['enabled'] is False
    finally:
        page.close()


@pytest.mark.parametrize('address', ['missing', '../escape', 'C:/outside'])
def test_invalid_flash_directory_cannot_initialize_record_or_start(host, address):
    record = host.program.parent / 'Flash' / 'Record.txt'
    record.write_text('Pass - previous\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='检查刷写文件夹失败'):
        flash.prepare_app_flash(dict(host.config, flash_path=address))
    assert record.read_text() == 'Pass - previous\n'
    host.controls['start_host'].assert_not_called()


@pytest.mark.parametrize('failures', [2, 10])
def test_ready_write_retries_locks_without_starting_early(host, monkeypatch, failures):
    record = host.program.parent / 'Flash' / 'Record.txt'
    record.write_text('Pass - previous\n', encoding='utf-8')
    replace = flash.os.replace
    attempts = []
    def locked(source, target):
        if Path(target) == record:
            host.controls['start_host'].assert_not_called()
            attempts.append(1)
            if len(attempts) <= failures:
                raise PermissionError('record locked')
        replace(source, target)
    monkeypatch.setattr(flash.os, 'replace', locked)
    if failures == 10:
        with pytest.raises(RuntimeError, match='初始化 Record.txt 为 Ready失败.*record locked'):
            flash.prepare_app_flash(host.config)
        assert len(attempts) == 10 and record.read_text() == 'Pass - previous\n'
        host.controls['start_host'].assert_not_called()
    else:
        flash.prepare_app_flash(host.config)
        assert len(attempts) == 3 and record.read_text().strip() == 'Ready'
        assert sum(call.args[0] == .1 for call in host.controls['wait_seconds'].call_args_list) == failures
    assert not list(record.parent.glob('.app_ready_*.tmp'))


def test_cancel_during_start_requests_safe_shutdown_before_reporting_cancelled(host):
    cancel = threading.Event()
    host.controls['start_host'].side_effect = lambda *_, **__: cancel.set()
    with pytest.raises(RuntimeError, match='取消'):
        flash.prepare_app_flash(host.config, cancel=cancel.is_set)
    assert host.controls['shutdown_host'].call_count == 2
    assert host.controls['shutdown_host'].call_args.args == (host.program,)


def test_start_failure_preserves_original_error_if_shutdown_cleanup_also_fails(host):
    host.controls['start_host'].side_effect = TimeoutError('startup failed')
    host.controls['shutdown_host'].side_effect = [None, RuntimeError('shutdown failed')]
    with pytest.raises(RuntimeError, match='startup failed.*安全关闭清理失败.*shutdown failed'):
        flash.prepare_app_flash(host.config)
