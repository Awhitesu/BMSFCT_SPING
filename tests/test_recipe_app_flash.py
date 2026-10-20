"""配方隔离、有限区域布局及刷写时不重复改写 INI 的离线回归。"""
import configparser
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, SubStep, SubStepType, TestEngine as Engine
from core.recipe_app_flash import prepare_app_flash, validate_app_flash
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab
from ui.widgets.recipe_app_flash_form import RecipeAppFlashForm

app = QApplication.instance() or QApplication([])


def config(directory, model='型号 A', path='Slate SR'):
    (directory / 'Host.exe').touch()
    return dict(enabled=True, restart_name='Host.exe', host_path=str(directory / 'Host.exe'), model=model, flash_path=path)


@pytest.fixture(autouse=True)
def simulated_host(monkeypatch):
    """离线回归保留真实 INI 读写；只替换进程操作及等待，不操作现场上位机。"""
    for name in ('stop_host', 'start_host', 'wait_seconds'):
        monkeypatch.setattr('core.recipe_app_flash.' + name, Mock())


def read_ini(path, encoding='utf-8-sig'):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(path.read_bytes().decode(encoding))
    return parser


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'gbk'])
def test_write_only_two_keys_and_preserve_encoding_newlines_comments_and_other_sections(tmp_path, encoding):
    original = ';原注释\r\n[app]\r\n  type = Old\r\naddr=\r\nKeep=001\r\n[Other]\r\nType=Other\r\nAddr=X\r\n'
    ini = tmp_path / 'config.ini'
    ini.write_bytes(original.encode(encoding))
    prepare_app_flash(config(tmp_path))
    expected = original.replace('type = Old', 'Type = 型号 A').replace('addr=\r\n', 'Addr=Slate SR\r\n')
    assert ini.read_bytes() == expected.encode(encoding)
    parsed = read_ini(ini, encoding)
    assert parsed['app']['Type'] == '型号 A'
    assert parsed['Other']['Type'] == 'Other'
    modified = ini.stat().st_mtime_ns
    prepare_app_flash(config(tmp_path))
    assert ini.stat().st_mtime_ns == modified


@pytest.mark.parametrize('content', ['[Other]\nKeep=1', '[App]\n', '[App]\nType=Old', '[App]\nAddr=Old\n[Other]\nKeep=1'])
def test_missing_target_keys_or_section_are_added(tmp_path, content):
    ini = tmp_path / 'config.ini'
    ini.write_text(content, encoding='utf-8')
    prepare_app_flash(config(tmp_path))
    parsed = read_ini(ini)
    assert parsed['App']['Type'] == '型号 A'
    assert parsed['App']['Addr'] == 'Slate SR'
    if parsed.has_section('Other'):
        assert parsed['Other']['Keep'] == '1'


def test_directory_host_percent_and_full_path_values_are_literal(tmp_path):
    ini = tmp_path / 'config.ini'
    ini.write_text('[App]\nType=old\nAddr=old\n', encoding='utf-8')
    values = dict(config(tmp_path), host_path=str(tmp_path), model='100% 完整', flash_path=r'D:\Flash Files\A=B')
    prepare_app_flash(values)
    parsed = read_ini(ini)
    assert parsed['App']['Type'] == values['model']
    assert parsed['App']['Addr'] == values['flash_path']


def test_windows_copy_as_path_quotes_do_not_change_ini_directory(tmp_path):
    ini = tmp_path / 'config.ini'
    ini.write_text('[App]\n', encoding='utf-8')
    values = config(tmp_path)
    values['host_path'] = '"' + values['host_path'] + '"'
    normalized = prepare_app_flash(values)
    assert normalized['host_path'] == str(tmp_path / 'Host.exe')
    assert read_ini(ini)['App']['Type'] == '型号 A'


@pytest.mark.parametrize('values', [dict(host_path='x'), dict(model='x'),
                                   dict(host_path='x', model='y', flash_path='a\nb'),
                                   dict(host_path='x', model='y', flash_path='\0')])
def test_partial_and_multiline_settings_are_rejected(values):
    with pytest.raises(ValueError):
        validate_app_flash(dict(values, enabled=True, restart_name='Host.exe'))


def test_missing_malformed_and_ambiguous_ini_are_not_overwritten(tmp_path):
    ini = tmp_path / 'config.ini'
    with pytest.raises(ValueError, match='未找到'):
        prepare_app_flash(config(tmp_path))
    assert not ini.exists()
    for raw in (b'not an ini', b'[App]\nType=a\nType=b', b'[App]\n[app]\n', b'[App]\nType=one\n  two\n'):
        ini.write_bytes(raw)
        with pytest.raises((ValueError, configparser.Error)):
            prepare_app_flash(config(tmp_path))
        assert ini.read_bytes() == raw


def test_failed_atomic_replace_keeps_original_and_removes_temporary_file(tmp_path, monkeypatch):
    ini = tmp_path / 'config.ini'
    original = b'[App]\nType=Old\nAddr=Old\n'
    ini.write_bytes(original)
    monkeypatch.setattr('core.recipe_app_flash.os.replace', Mock(side_effect=PermissionError('locked')))
    with pytest.raises(RuntimeError, match='写入并校验.*locked'):
        prepare_app_flash(config(tmp_path))
    assert ini.read_bytes() == original
    assert not list(tmp_path.glob('.app_config_*.tmp'))


def test_typed_choices_commit_without_duplicates_and_restore_per_recipe(tmp_path):
    form = RecipeAppFlashForm()
    form.show()
    try:
        form.enable_sync.setChecked(True)
        form.restart_name.setText('Host.exe')
        form.host_path.setText(str(tmp_path / 'Host.exe'))
        for combo, first, second in ((form.model, 'A', 'B'), (form.flash_path, 'Path A', 'Path B')):
            combo.lineEdit().setFocus()
            combo.setEditText(first)
            QTest.keyClick(combo.lineEdit(), Qt.Key_Return)
            assert combo.findText(first) >= 0
            assert combo.currentIndex() == combo.findText(first)
            combo.setEditText(second)
            combo.lineEdit().editingFinished.emit()
            combo.setEditText(first)
            combo.lineEdit().editingFinished.emit()
            assert combo.count() == 2
            assert combo.currentIndex() == combo.findText(first)
        saved = form.get_config()
        form.set_config(dict(host_path='B.exe', model='Other', flash_path='OtherPath', model_history=['Other']))
        assert form.model.findText('A') == -1
        form.set_config(json.loads(json.dumps(saved)))
        assert form.get_config() == saved
        form.set_config(None)
        assert form.host_path.text() == '' and form.model.count() == 0 and form.flash_path.count() == 0
    finally:
        form.close()


def test_editor_save_load_and_run_snapshot_carry_settings_without_writing_ini(tmp_path):
    ini = tmp_path / 'config.ini'
    ini.write_bytes(b'[App]\nType=Original\nAddr=Original\n')
    page = ConfigTab(SimpleNamespace(list_recipes=lambda: []))
    try:
        page.recipe_app_flash.set_config(dict(config(tmp_path), model_history=['Past']))
        saved = page._collect_recipe_data('A')
        page.load_recipe_to_tree(dict(name='B', items=[], can_config={}))
        assert page.recipe_app_flash.model.currentText() == ''
        page.load_recipe_to_tree(json.loads(json.dumps(saved)))
        assert page.recipe_app_flash.get_config() == saved['app_flash']
        fake = SimpleNamespace(current_recipe_name='A', current_recipe_data=saved, step_tree=page.step_tree)
        runtime = OverviewTab.serialize_tree_to_dict(fake)
        assert runtime['app_flash'] == saved['app_flash']
        runtime['app_flash']['model_history'].append('changed')
        assert 'changed' not in saved['app_flash']['model_history']
        assert ini.read_bytes() == b'[App]\nType=Original\nAddr=Original\n'
    finally:
        page.close()


def worker(values):
    result = ChannelWorker(1, [], device_manager=SimpleNamespace(pcan=None))
    result.app_flash_config = values
    result.is_running = True
    return result


def test_engine_uses_selected_ini_and_recipe_host_without_rewriting(tmp_path, monkeypatch):
    ini = tmp_path / 'config.ini'
    ini.write_bytes(b'[App]\nType=Old\nAddr=Wrong\n')
    folder = tmp_path / 'Flash' / 'Slate SR'
    folder.mkdir(parents=True)
    (folder / 'right.hex').write_text(':00000001FF', encoding='ascii')
    prepare_app_flash(config(tmp_path))
    selected = ini.read_bytes()
    monkeypatch.setattr('core.recipe_app_flash.prepare_app_flash', Mock(side_effect=AssertionError('工步不能再次同步INI')))
    w = worker(config(tmp_path))
    ok, result = w._execute_sub_step_logic(SubStep(SubStepType.APP_FLASH_CONFIG,
        dict(params='HOST:Z:/Wrong.exe / INI:wrong.ini / SECTION:Other / KEY:Wrong')))
    assert ok and result == 'right.hex'
    assert read_ini(ini)['App']['Type'] == '型号 A'
    assert ini.read_bytes() == selected


def test_engine_uses_two_selected_recipe_values_without_writing_before_start(tmp_path, monkeypatch):
    ini = tmp_path / 'config.ini'
    ini.write_bytes(b'[App]\nType=Old\nAddr=Old\n')
    calls = []

    def run(host, timeout, **kwargs):
        parsed = read_ini(ini)
        calls.append((host, parsed['App']['Type'], parsed['App']['Addr']))
        return True, 'PASS'

    monkeypatch.setattr('core.app_flash_step.run_configured_app_flash', run)
    monkeypatch.setattr('core.recipe_app_flash.prepare_app_flash', Mock(side_effect=AssertionError('刷写不能再次同步INI')))
    sub = SubStep(SubStepType.APP_FLASH, dict(params='HOST:Z:/Old.exe / TIMEOUT:9'))
    for model, path in (('A', 'A-path'), ('B', 'B-path')):
        prepare_app_flash(config(tmp_path, model, path))
        w = worker(config(tmp_path, model, path))
        assert w._execute_sub_step_logic(sub) == (True, 'PASS')
    assert calls == [(str(tmp_path / 'Host.exe'), 'A', 'A-path'), (str(tmp_path / 'Host.exe'), 'B', 'B-path')]


def test_cancel_never_triggers_flash(tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr('core.app_flash_step.run_configured_app_flash', run)
    w = worker(config(tmp_path))
    w.cancel_event.set()
    ok, _ = w._execute_sub_step_logic(SubStep(SubStepType.APP_FLASH, dict(params='HOST:Old.exe')))
    assert not ok
    run.assert_not_called()
    assert not (tmp_path / 'Flash/Record.txt').exists()


def test_host_picker_selects_a_file_and_cancel_preserves_it(tmp_path, monkeypatch):
    form = RecipeAppFlashForm()
    host = tmp_path / 'External Host.exe'
    host.touch()
    chooser = Mock(return_value=(str(host), '上位机程序 (*.exe *.vi)'))
    monkeypatch.setattr('ui.widgets.recipe_app_flash_form.QFileDialog.getOpenFileName', chooser)
    try:
        form.browse_host.click()
        assert form.host_path.text() == str(host)
        chooser.assert_called_once()
        chooser.return_value = ('', '')
        form.browse_host.click()
        assert form.host_path.text() == str(host)
    finally:
        form.close()


def test_legacy_recipe_keeps_original_host_and_does_not_write_ini(tmp_path, monkeypatch):
    ini = tmp_path / 'config.ini'
    original = b'[App]\nType=Old\nAddr=Old\n'
    ini.write_bytes(original)
    run = Mock(return_value=(True, 'PASS'))
    monkeypatch.setattr('core.app_flash_step.run_configured_app_flash', run)
    w = worker(None)
    assert w._execute_sub_step_logic(SubStep(SubStepType.APP_FLASH, dict(params=f'HOST:{tmp_path}/Old.exe')))[0]
    assert run.call_args.args[0] == f'{tmp_path}/Old.exe'
    assert ini.read_bytes() == original


def test_worker_receives_an_independent_snapshot(monkeypatch):
    engine = Engine()
    entered, release = threading.Event(), threading.Event()
    observed = []

    def action(self, sub):
        entered.set()
        release.wait(2)
        observed.append(self.app_flash_config)
        return True, 'OK'

    monkeypatch.setattr(ChannelWorker, '_execute_sub_step_logic', action)
    values = dict(host_path='Host.exe', model='A', flash_path='P', model_history=['A'])
    recipe = [dict(name='test', standard_type='不判断', sub_steps=[dict(type='APP刷写', params='')])]
    assert engine.start_channel_test(1, recipe, app_flash_config=values)
    try:
        deadline = time.monotonic() + 3
        while not entered.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert entered.is_set()
        values['model'] = 'changed'
        values['model_history'].append('changed')
        release.set()
        while engine.workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert not engine.workers
        assert observed[0]['model'] == 'A' and observed[0]['model_history'] == ['A']
    finally:
        release.set()
        engine.stop_all()


def test_app_form_occupies_only_the_blank_row_below_dbc():
    page = ConfigTab(SimpleNamespace(list_recipes=lambda: []))
    dialog = page.recipe_hardware_dialog
    try:
        dialog.show()
        app.processEvents()
        grid = page.dbc_import.parentWidget().layout()
        assert grid.getItemPosition(grid.indexOf(page.recipe_app_flash)) == (9, 0, 1, 2)
        assert grid.getItemPosition(grid.indexOf(page.dbc_import)) == (8, 0, 1, 2)
        form = page.recipe_app_flash
        form.enable_sync.setChecked(True)
        app.processEvents()
        assert form.y() >= page.dbc_import.geometry().bottom()
        assert form.geometry().bottom() < form.parentWidget().height()
        assert form.model.parentWidget().geometry().y() == form.flash_path.parentWidget().geometry().y()
        assert abs(form.model.width() - form.flash_path.width()) <= 1
        assert form.model.height() > 26 and form.flash_path.height() > 26
        assert dialog.size().width() == 950 and dialog.size().height() == 790
        existing = (page.dbc_import, page.recipe_pcan_channel, page.report_model_name)
        before = [widget.geometry() for widget in existing]
        grid.removeWidget(form)
        form.hide()
        grid.activate()
        app.processEvents()
        assert [widget.geometry() for widget in existing] == before
        grid.addWidget(form, 9, 0, 1, 2, Qt.AlignTop)
        form.show()
        page.recipe_app_flash.model.lineEdit().setFocus()
        page.recipe_app_flash.model.setEditText('New model')
        QTest.keyClick(page.recipe_app_flash.model.lineEdit(), Qt.Key_Return)
        assert dialog.isVisible() and page.recipe_app_flash.model.findText('New model') >= 0
    finally:
        dialog.close()
        page.close()
