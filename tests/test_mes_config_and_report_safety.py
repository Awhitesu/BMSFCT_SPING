import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from core import system_config
from core.excel_recorder import export_test_record
from ui.tabs.mes_tab import MesTab, _MesRequestWorker
from test_excel_recorder import _make_db


app = QApplication.instance() or QApplication([])


def test_concurrent_config_updates_preserve_other_device_fields(tmp_path):
    path = str(tmp_path / 'sys_config.json')
    system_config.update({'device_ip': '127.0.0.1'}, path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: system_config.update({f'field{n}': n}, path), range(30)))
    result = system_config.load(path)
    assert result == {'device_ip': '127.0.0.1', **{f'field{n}': n for n in range(30)}}


def test_mes_save_preserves_broken_config_and_does_not_show_success(tmp_path):
    with patch.object(MesTab, 'load_config', lambda self: None):
        page = MesTab()
    path = tmp_path / 'sys_config.json'
    path.write_text('{broken', encoding='utf-8')
    page.config_path = str(path)
    try:
        with patch('ui.tabs.mes_tab.QMessageBox.warning') as warning, patch('ui.tabs.mes_tab.QMessageBox.information') as info:
            assert not page.save_config()
            warning.assert_called_once()
            info.assert_not_called()
        assert path.read_text(encoding='utf-8') == '{broken'
    finally:
        page.close()


@pytest.mark.parametrize('timeout', ['nan', 'inf', '-1', '0'])
def test_mes_invalid_timeout_never_saves(tmp_path, timeout):
    with patch.object(MesTab, 'load_config', lambda self: None):
        page = MesTab()
    page.config_path = str(tmp_path / 'config.json')
    page.timeout_edit.setText(timeout)
    try:
        with patch('ui.tabs.mes_tab.QMessageBox.warning'):
            assert not page.save_config(False)
        assert not Path(page.config_path).exists()
    finally:
        page.close()


def test_mes_worker_always_emits_completion_after_exception():
    worker = _MesRequestWorker({'url': 'http://localhost', 'timeout': 1}, {})
    results = []
    worker.finished.connect(lambda result, payload: results.append(result))
    with patch('ui.tabs.mes_tab.MesClient', side_effect=ValueError('bad settings')):
        worker.run()
    assert len(results) == 1 and not results[0].passed
    assert 'bad settings' in results[0].message


def test_reports_for_same_barcode_and_second_are_distinct(tmp_path):
    db = str(tmp_path / 'test.db')
    first = _make_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO test_main SELECT 2,channel_id,shelf_code,master_code,slave_codes,recipe_name,start_time,end_time,result FROM test_main WHERE test_id=?', (first,))
    one = export_test_record(db, first, str(tmp_path / 'reports'))
    two = export_test_record(db, 2, str(tmp_path / 'reports'))
    assert one != two
    assert Path(one).is_file() and Path(two).is_file()


def test_report_recipe_cannot_escape_output_directory(tmp_path):
    db = str(tmp_path / 'test.db')
    identity = _make_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE test_main SET recipe_name='../../escaped' WHERE test_id=?", (identity,))
    root = tmp_path / 'reports'
    path = Path(export_test_record(db, identity, str(root)))
    assert path.is_relative_to(root)


def test_default_config_path_does_not_follow_working_directory(tmp_path, monkeypatch):
    expected = system_config.default_path()
    monkeypatch.chdir(tmp_path)
    assert system_config.default_path() == expected
