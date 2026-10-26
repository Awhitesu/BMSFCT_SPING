"""Exercise the real engine, durable queue and background UI upload together."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication, QEvent

from core.engine import TestEngine as Engine
from core.mes_client import MesCheckResult
from core.mes_outbox import outbox_root, pending_jobs
from data.db_manager import DBManager
from test_generic_mes import settings
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def pump(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


def recipe(passed=True, stop_on_ng=False):
    return [dict(name='电压', min='3', max='4', unit='V', strategy='任何NG停止' if stop_on_ng else 'NG继续',
                 sub_steps=[dict(type='公式计算', params='FORMULA:3.2' if passed else 'FORMULA:2.5', is_judgment=True)])]


def close_page(page, engine, db):
    engine.stop_all()
    pump(lambda: not engine.workers)
    for box in page.findChildren(QMessageBox):
        box.close()
    page.begin_shutdown()
    for name in ('_prepare_task', '_plc_task', '_recipe_match_task', '_stats_task', '_mes_upload_task'):
        getattr(page, name).shutdown()
    page.close()
    db.close()
    app.processEvents()


@pytest.mark.parametrize('passed,upload_passed,stop_on_ng', [
    (True, True, False), (True, False, False), (False, True, False),
    (False, True, True), (False, False, True)])
def test_detection_is_committed_before_upload_and_never_changed(tmp_path, passed, upload_passed, stop_on_ng):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    entered, release = threading.Event(), threading.Event()
    requests, finished = [], []
    def upload(payload):
        requests.append(payload)
        entered.set()
        assert release.wait(4)
        return MesCheckResult(upload_passed, '已接收' if upload_passed else '工单已关闭',
                              200, {'success': upload_passed})
    engine.channel_test_finished.connect(lambda cid, ok: finished.append(ok))
    try:
        with patch('core.generic_mes.GenericMesClient.post', side_effect=upload), \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, recipe(passed, stop_on_ng), sn_barcode='SN-1',
                                             generic_mes_config=settings())
            pump(entered.is_set)
            expected = 'PASS' if passed else 'NG' if stop_on_ng else 'FAIL'
            assert finished == [passed]
            assert page.lbl_big_status.text() == ('PASS' if passed else 'FAIL')
            with sqlite3.connect(db.db_path) as conn:
                assert conn.execute('SELECT result FROM test_main').fetchone()[0] == expected
            assert len(pending_jobs(db.db_path)) == 1
            assert not page.findChildren(QMessageBox)
            release.set()
            pump(lambda: not page._mes_upload_task.isRunning() and not engine.workers)
        assert len(requests) == (1 if upload_passed else 2)
        detail = requests[0]['checkDetail'][0]
        assert requests[0]['isPass'] == ('pass' if passed else 'fail')
        assert detail['checkItem'] == '电压' and detail['checkType'] == '1'
        assert detail['stValue'] == '3.5'
        assert float(detail['realValue']) == (3.2 if passed else 2.5)
        assert detail['itemRes'] == ('pass' if passed else 'fail')
        assert detail['itemUnit'] == 'V'
        assert page.lbl_big_status.text() == ('PASS' if passed else 'FAIL')
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT result FROM test_main').fetchone()[0] == expected
        boxes = page.findChildren(QMessageBox)
        assert bool(boxes) is (not upload_passed)
        if boxes:
            assert '工单已关闭' in boxes[0].text()
        assert len(pending_jobs(db.db_path)) == (0 if upload_passed else 1)
    finally:
        release.set()
        close_page(page, engine, db)


def test_first_failure_second_success_has_no_failure_popup(tmp_path):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    try:
        with patch('core.generic_mes.GenericMesClient.post', side_effect=[
                MesCheckResult(False, '网络波动'), MesCheckResult(True, '成功')]) as post, \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, recipe(), sn_barcode='SN', generic_mes_config=settings())
            pump(lambda: not engine.workers and not page._mes_upload_task.isRunning())
        assert post.call_count == 2
        assert not page.findChildren(QMessageBox)
        assert page.lbl_big_status.text() == 'PASS'
        assert not pending_jobs(db.db_path)
        assert len(list((outbox_root(db.db_path)/'sent').glob('*.json'))) == 1
    finally:
        close_page(page, engine, db)


@pytest.mark.parametrize('debug_run,enabled', [(True, True), (False, False)])
def test_debug_and_disabled_do_not_queue_upload(tmp_path, debug_run, enabled):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(db_manager=db)
    try:
        with patch('core.generic_mes.GenericMesClient.post') as post, \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, recipe(), sn_barcode='SN', debug_run=debug_run,
                generic_mes_config=settings(generic_mes_upload_enabled=enabled))
            pump(lambda: not engine.workers)
            post.assert_not_called()
        assert not pending_jobs(db.db_path)
    finally:
        engine.stop_all()
        pump(lambda: not engine.workers)
        db.close()


def test_manual_stop_does_not_queue_upload(tmp_path):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    retained = []
    engine.step_finished.connect(lambda *args: retained.append(args))
    plan = recipe() + [dict(name='等待', sub_steps=[dict(type='等待', params='TIME:5')])]
    try:
        with patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, plan, sn_barcode='SN', generic_mes_config=settings())
            pump(lambda: bool(retained))
            engine.stop_all()
            pump(lambda: not engine.workers)
        assert not pending_jobs(db.db_path)
    finally:
        engine.stop_all()
        pump(lambda: not engine.workers)
        db.close()


def test_fresh_page_can_replay_previous_session_without_changing_detection(tmp_path):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    try:
        with patch('core.generic_mes.GenericMesClient.post', return_value=MesCheckResult(False, '断网')), \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, recipe(), sn_barcode='OLD-SN', generic_mes_config=settings())
            pump(lambda: not engine.workers and not page._mes_upload_task.isRunning())
        pending = pending_jobs(db.db_path)
        assert len(pending) == 1 and page.lbl_big_status.text() == 'PASS'
        frozen = json.loads(pending[0].read_text(encoding='utf-8'))['payload']
    finally:
        close_page(page, engine, db)
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(db_manager=db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    try:
        with patch('core.generic_mes.GenericMesClient.post', return_value=MesCheckResult(True, '补传成功')) as post:
            page.retry_pending_mes_uploads()
            pump(lambda: not page._mes_upload_task.isRunning())
        post.assert_called_once_with(frozen)
        assert not pending_jobs(db.db_path)
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT master_code,result FROM test_main').fetchone() == ('OLD-SN', 'PASS')
    finally:
        close_page(page, engine, db)


def test_outbox_disk_error_reports_the_problem_without_changing_pass(tmp_path):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    try:
        with patch('core.mes_outbox.create_upload_job', side_effect=OSError('磁盘已满')), \
             patch('core.generic_mes.GenericMesClient.post') as post, \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, recipe(), sn_barcode='SN', generic_mes_config=settings())
            pump(lambda: not engine.workers)
        assert page.lbl_big_status.text() == 'PASS'
        assert '磁盘已满' in page.findChildren(QMessageBox)[0].text()
        post.assert_not_called()
        with sqlite3.connect(db.db_path) as conn:
            assert conn.execute('SELECT result FROM test_main').fetchone()[0] == 'PASS'
    finally:
        close_page(page, engine, db)


def test_closing_during_upload_keeps_pending_file_and_cancels_second_attempt(tmp_path):
    from core.mes_outbox import create_upload_job
    path = create_upload_job(settings(), 'SN', True,
        [dict(name='电压', lower=3, upper=4, value='3.2', unit='V', passed=True)], tmp_path/'test.db')
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    entered, release = threading.Event(), threading.Event()
    runtime = page._mes_upload_task._runtime
    def upload(payload):
        entered.set()
        assert release.wait(3)
        return MesCheckResult(False, '断网')
    try:
        with patch('core.mes_outbox.GenericMesClient.post', side_effect=upload) as post:
            page.enqueue_mes_upload(path)
            pump(entered.is_set)
            started = time.monotonic()
            page.close()
            assert time.monotonic() - started < .2
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            release.set()
            assert runtime.thread.wait(3000)
        assert post.call_count == 1
        assert Path(path).exists()
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
        assert saved['state'] == 'pending' and len(saved['history']) == 1
    finally:
        release.set()
        app.processEvents()


def test_all_completed_items_upload_actual_values_units_standards_and_verdicts(tmp_path):
    db = DBManager(str(tmp_path/'test.db'))
    engine = Engine(SimpleNamespace(safe_shutdown_outputs=lambda *a, **kw: True), db)
    page = OverviewTab(engine, db)
    page.stop_monitor()
    plan = recipe() + recipe(False) + [dict(name='温度', min='10', max='50', unit='℃', strategy='NG继续',
            sub_steps=[dict(type='公式计算', params='FORMULA:25', is_judgment=True)])]
    plan.append(dict(name='#跳过项目', sub_steps=[dict(type='公式计算', params='FORMULA:99')]))
    plan.insert(0, dict(name='#前置跳过', sub_steps=[dict(type='公式计算', params='FORMULA:99')]))
    cfg = settings(generic_mes_item_mapping=[dict(source='电压', checkType='FAKE', checkItem='OVERRIDE',
                                                stValue='9999', itemUnit='A')])
    try:
        with patch('core.generic_mes.GenericMesClient.post', return_value=MesCheckResult(True, '成功')) as post, \
             patch('core.engine.ChannelWorker._export_excel_record', new=lambda self, tid: None):
            assert engine.start_channel_test(1, plan, sn_barcode='SN', generic_mes_config=cfg)
            pump(lambda: not engine.workers and not page._mes_upload_task.isRunning())
        payload = post.call_args.args[0]
        assert payload['isPass'] == 'fail'
        details = payload['checkDetail']
        assert len(details) == 3  # Both same-name voltage items are included.
        assert [row['checkItem'] for row in details] == ['电压', '电压', '温度']
        assert [row['checkType'] for row in details] == ['2', '3', '4']
        assert [row['stValue'] for row in details] == ['3.5', '3.5', '30']
        assert [float(row['realValue']) for row in details] == [3.2, 2.5, 25]
        assert [row['itemUnit'] for row in details] == ['V', 'V', '℃']
        assert [row['itemRes'] for row in details] == ['pass', 'fail', 'pass']
        assert page.lbl_big_status.text() == 'FAIL'
    finally:
        close_page(page, engine, db)
