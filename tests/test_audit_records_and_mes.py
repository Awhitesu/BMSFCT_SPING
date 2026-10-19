import sqlite3
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import xlrd

from core.excel_recorder import export_test_record, _sanitize
from core.mes_client import MesClient
from core.passwords import verify_password
from data.db_manager import DBManager
from test_excel_recorder import _make_db


@pytest.mark.parametrize('body', [{}, {'message': 'failed'}, None, False, [],
    '<html>Login required</html>', {'success': 0}, {'success': None},
    {'success': True, 'code': 500}, {'success': True, 'passed': False},
    {'success': True, 'error': 'denied'}])
def test_mes_unknown_or_contradictory_response_does_not_pass(body):
    assert not MesClient._interpret_response(True, body)[0]


@pytest.mark.parametrize('body', [{'success': True}, {'code': 0}, {'result': 0},
                                {'passed': 1}, {'success': 'true'}, True, 'OK'])
def test_mes_explicit_affirmative_responses_remain_supported(body):
    assert MesClient._interpret_response(True, body)[0]
    assert not MesClient._interpret_response(False, body)[0]


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), 0, -1])
def test_mes_invalid_timeout_never_opens_network(timeout):
    with pytest.raises(ValueError):
        MesClient('http://unused', timeout)


def test_export_preserves_raw_text_without_excel_formulas(tmp_path):
    db = tmp_path / 'test.db'
    identity = _make_db(str(db))
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE test_main SET master_code=?, recipe_name=?', ('=1+1', '=formula_project'))
        connection.execute('UPDATE test_items_results SET item_name=?, measured_value=?',
                           ('=SUM(1,2)', '#N/A\x00raw'))
    report = export_test_record(str(db), identity, str(tmp_path / 'reports'))
    book=xlrd.open_workbook(report)
    sheet=book.sheet_by_index(0)
    cells=[cell for row in range(sheet.nrows) for cell in sheet.row(row)]
    assert any(cell.value=='=1+1' and cell.ctype==xlrd.XL_CELL_TEXT for cell in cells)
    assert any(cell.value=='=SUM(1,2)' and cell.ctype==xlrd.XL_CELL_TEXT for cell in cells)
    assert any(cell.value=='#N/A\\x00raw' and cell.ctype==xlrd.XL_CELL_TEXT for cell in cells)


def test_export_failure_preserves_existing_complete_report(tmp_path):
    db = tmp_path / 'test.db'
    identity = _make_db(str(db))
    report = Path(export_test_record(str(db), identity, str(tmp_path / 'reports')))
    original = report.read_bytes()
    def failed_save(book, filename):
        filename.write(b'incomplete zip')
        raise OSError('disk full')
    with patch('core.xls_report.xlwt.Workbook.save', failed_save), pytest.raises(OSError, match='disk full'):
        export_test_record(str(db), identity, str(tmp_path / 'reports'))
    assert report.read_bytes() == original
    assert list(report.parent.glob('*.xls')) == [report]


def test_report_components_are_portable_and_bounded():
    assert _sanitize('CON') == '_CON'
    assert len(_sanitize('SN' * 200)) <= 64


@pytest.mark.parametrize('name', ['../outside', '..\\outside', 'CON', 'bad/name', 'name.', 'a' * 129])
def test_recipe_save_load_delete_cannot_escape_or_use_invalid_names(tmp_path, name):
    db = DBManager(str(tmp_path / 'db.sqlite'))
    outside = tmp_path / 'outside.json'
    outside.write_text('{"preserve":true}', encoding='utf-8')
    try:
        assert not db.save_recipe_json(name, {'name': 'bad', 'items': []})
        assert db.load_recipe_json(name) is None
        assert not db.delete_recipe(name)
        assert outside.read_text(encoding='utf-8') == '{"preserve":true}'
        assert not db.rename_recipe('missing', 'missing')
    finally:
        db.close()
    assert db.queue.unfinished_tasks == 0


def test_legacy_unicode_password_and_bad_hash_do_not_break_login():
    assert verify_password('中文密码', '中文密码')
    assert not verify_password('wrong', '中文密码')
    assert not verify_password('password', 'pbkdf2_sha256$999999999999$salt$digest')
    assert not verify_password('password', None)


@pytest.mark.parametrize('value,expected', [(float('nan'), 'nan'), (float('inf'), 'inf'), (True, 'True'),
    ([1, float('nan')], '[1, "nan"]'), (b'\x00\xff', '00 FF')])
def test_invalid_raw_measurement_is_retained_in_database(tmp_path, value, expected):
    db = DBManager(str(tmp_path / 'db.sqlite'))
    try:
        identity = db.start_new_test(1, 'CH1', 'SN', 'recipe')
        db.log_item_result(identity, 'raw', 0, 1, value, 'NG')
        db.log_step_event(identity, dict(execution_id='run', step_id='step', parent_step_id='item',
            attempt=1, status='FAIL', value={'raw': value}))
        db.finish_test(identity, 'FAIL')
        with sqlite3.connect(db.db_path) as conn:
            actual = conn.execute('SELECT measured_value FROM test_items_results').fetchone()[0]
            raw_json = conn.execute('SELECT value_json FROM test_step_events').fetchone()[0]
        assert actual == expected
        parsed = json.loads(raw_json, parse_constant=lambda token: pytest.fail('Nonstandard JSON number ' + token))
        if isinstance(value, list):
            assert parsed['raw'] == [1, 'nan']
        else:
            assert parsed['raw'] == (value if isinstance(value, bool) else expected)
    finally:
        db.close()


def test_late_database_finish_cannot_overwrite_record_error_with_pass(tmp_path):
    db = DBManager(str(tmp_path / 'db.sqlite'))
    identity = db.start_new_test(1, 'CH1', 'SN', 'recipe')
    original_execute = db._execute_async
    def delayed(sql, params=(), wait=False):
        if wait:
            original_execute(sql, params, wait=False)
            raise TimeoutError('commit not confirmed before timeout')
        return original_execute(sql, params, wait=False)
    try:
        with patch.object(db, '_execute_async', side_effect=delayed):
            with pytest.raises(TimeoutError):
                db.finish_test(identity, 'PASS')
    finally:
        db.close()
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute('SELECT result FROM test_main WHERE test_id=?', (identity,)).fetchone()[0] == 'RECORD_ERROR'
