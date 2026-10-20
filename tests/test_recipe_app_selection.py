"""在测试界面选择型号后同步 INI；开始测试和刷写不重复写入。"""
import configparser
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from core.engine import TestEngine as Engine
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path, monkeypatch):
    for name in ('stop_host', 'start_host', 'wait_seconds'):
        monkeypatch.setattr('core.recipe_app_flash.' + name, Mock())
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        widget = OverviewTab(Engine(SimpleNamespace()))
    widget.stop_monitor()
    widget.set_recipe_selection_role('admin', True)
    widget.db_manager = SimpleNamespace(get_recipe_dir=lambda: str(tmp_path),
                                       get_user_role=lambda user: 'admin', load_sys_config=lambda: {})
    widget.refresh_statistics = Mock()
    widget.sync_recipe_number_to_plc = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    yield widget
    widget.begin_shutdown()
    for name in ('_prepare_task', '_plc_task', '_stats_task', '_recipe_match_task'):
        getattr(widget, name).shutdown()
    widget.close()


def recipe(page, directory, name, host_dir):
    (host_dir / 'Host.exe').touch()
    values = dict(enabled=True, restart_name='Host.exe', host_path=str(host_dir / 'Host.exe'), model=name, flash_path=name + '-APP')
    data = dict(name=name, can_config={}, app_flash=values, items=[dict(name='read', min='0', max='1',
        sub_steps=[dict(type='读取变量', params='VAR:x')])])
    (directory / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')
    page.combo_recipe.addItem(name, f'{name}.json')
    return data


def select(page, index):
    page.combo_recipe.setCurrentIndex(index)
    page.combo_recipe.activated.emit(index)
    deadline = time.monotonic() + 3
    while page._prepare_task.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert not page._prepare_task.isRunning()


def values(path):
    parser = configparser.ConfigParser()
    parser.read(path, encoding='utf-8')
    return parser['App']['Type'], parser['App']['Addr']


@pytest.mark.parametrize('shared_host', [True, False])
def test_selecting_model_immediately_syncs_its_recipe_ini(page, tmp_path, shared_host):
    a, b = tmp_path / 'host-A', tmp_path / ('host-A' if shared_host else 'host-B')
    for folder in {a, b}:
        folder.mkdir()
        (folder / 'config.ini').write_text('[App]\nType=Old\nAddr=Old\n', encoding='utf-8')
    recipe(page, tmp_path, 'A', a)
    recipe(page, tmp_path, 'B', b)
    assert values(a / 'config.ini') == ('Old', 'Old')
    select(page, 0)
    assert values(a / 'config.ini') == ('A', 'A-APP')
    assert page.current_recipe_name == 'A' and not page._recipe_can_error
    if not shared_host:
        assert values(b / 'config.ini') == ('Old', 'Old')
    select(page, 1)
    assert values(b / 'config.ini') == ('B', 'B-APP')
    assert page.current_recipe_name == 'B' and not page._recipe_can_error
    if not shared_host:
        assert values(a / 'config.ini') == ('A', 'A-APP')
    QMessageBox.warning.assert_not_called()


def test_sync_failure_blocks_start_and_reselection_can_recover(page, tmp_path):
    recipe(page, tmp_path, 'A', tmp_path)
    select(page, 0)
    assert 'APP刷写参数同步失败' in page._recipe_can_error
    assert 'config.ini' in page._recipe_can_error
    assert page.lbl_big_status.text() == 'CONFIG ERROR'
    page.engine.start_channel_test = Mock()
    page.txt_sn.setText('SN');page._confirmed_barcode = 'SN'
    assert not page.start_single_channel_test()
    page.engine.start_channel_test.assert_not_called()
    assert not (tmp_path / 'config.ini').exists()
    (tmp_path / 'config.ini').write_text('[App]\n', encoding='utf-8')
    select(page, 0)
    assert values(tmp_path / 'config.ini') == ('A', 'A-APP')
    assert page._recipe_can_error == '' and page.lbl_big_status.text() == 'READY'


def test_loading_without_selection_cannot_run_with_unsynced_parameters(page, tmp_path):
    recipe(page, tmp_path, 'A', tmp_path)
    original = '[App]\nType=Old\nAddr=Old\n'
    ini = tmp_path / 'config.ini';ini.write_text(original, encoding='utf-8')
    assert page.load_recipe_from_file(str(tmp_path / 'A.json'))
    page.txt_sn.setText('SN');page._confirmed_barcode = 'SN'
    page.engine.start_channel_test = Mock()
    assert not page.start_single_channel_test()
    assert QMessageBox.warning.call_args.args[1] == 'APP刷写参数未同步'
    page.engine.start_channel_test.assert_not_called()
    assert ini.read_text(encoding='utf-8') == original


@pytest.mark.parametrize('changed_key,changed_value', [('model', 'Changed'), ('restart_name', 'Another.exe')])
def test_preparation_does_not_write_ini_again_and_changed_parameters_require_reselection(page, tmp_path, monkeypatch,
                                                                                       changed_key, changed_value):
    data = recipe(page, tmp_path, 'A', tmp_path)
    ini = tmp_path / 'config.ini';ini.write_text('[App]\n', encoding='utf-8')
    replacement = Mock(wraps=os.replace)
    monkeypatch.setattr('core.recipe_app_flash.os.replace', replacement)
    select(page, 0)
    assert replacement.call_count == 1
    page.db_manager = None
    snapshot = dict(data, _debug_run=True)
    assert page._prepare_run(snapshot, '')[0]
    assert page._prepare_run(snapshot, '')[0]
    assert replacement.call_count == 1
    page.current_recipe_data['app_flash'][changed_key] = changed_value
    page.txt_sn.setText('SN');page._confirmed_barcode = 'SN'
    assert not page.start_single_channel_test()
    assert QMessageBox.warning.call_args.args[1] == 'APP刷写参数未同步'
    assert values(ini) == ('A', 'A-APP')
    assert replacement.call_count == 1


@pytest.mark.parametrize('enabled', [False, None])
def test_switching_to_disabled_or_legacy_recipe_skips_host_operations(page, tmp_path, monkeypatch, enabled):
    data = recipe(page, tmp_path, 'A', tmp_path)
    ini = tmp_path / 'config.ini'
    ini.write_text('[App]\nType=Old\nAddr=Old\n', encoding='utf-8')
    select(page, 0)
    assert values(ini) == ('A', 'A-APP')
    data['name'] = 'B'
    if enabled is None:
        data['app_flash'].pop('enabled')
    else:
        data['app_flash'].update(enabled=False, model='', host_path='missing.exe')
    (tmp_path / 'B.json').write_text(json.dumps(data), encoding='utf-8')
    page.combo_recipe.addItem('B', 'B.json')
    forbidden = Mock(side_effect=AssertionError('未使能不能操作上位机'))
    for operation in ('stop_host', 'start_host', 'wait_seconds'):
        monkeypatch.setattr('core.recipe_app_flash.' + operation, forbidden)
    select(page, 1)
    forbidden.assert_not_called()
    assert values(ini) == ('A', 'A-APP')
    assert page._selected_app_flash_config is None
    assert not page._recipe_can_error and page.lbl_big_status.text() == 'READY'


def test_failed_host_stop_blocks_can_preparation_and_test_start(page, tmp_path, monkeypatch):
    recipe(page, tmp_path, 'A', tmp_path)
    ini = tmp_path / 'config.ini'
    original = '[App]\nType=Old\nAddr=Old\n'
    ini.write_text(original, encoding='utf-8')
    monkeypatch.setattr('core.recipe_app_flash.stop_host', Mock(side_effect=TimeoutError('stop timeout')))
    page._apply_recipe_hardware = Mock(return_value=(True, 'CAN'))
    select(page, 0)
    assert '停止上位机失败' in page._recipe_can_error
    assert page.lbl_big_status.text() == 'CONFIG ERROR'
    page._apply_recipe_hardware.assert_not_called()
    page.txt_sn.setText('SN');page._confirmed_barcode = 'SN'
    page.engine.start_channel_test = Mock()
    assert not page.start_single_channel_test()
    page.engine.start_channel_test.assert_not_called()
    assert ini.read_text(encoding='utf-8') == original
