import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from core.engine import ChannelWorker, TestStep as Step, StepType
from core.numeric_validation import finite_reading
from core.passwords import PREFIX
from data.db_manager import DBManager
from devices.manager import DeviceManager
from ui.dialogs.startup_dialog import StartupCheckDialog
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def test_successful_substep_retry_discards_failed_measurement():
    from core.engine import SubStep, SubStepType, SubStepFailStrategy
    step = Step('read', StepType.CUSTOM)
    step.min_limit, step.max_limit = 1, 3
    worker = ChannelWorker(1, [step])
    worker.is_running = True
    sub = SubStep(SubStepType.WAIT, {}, SubStepFailStrategy.RETRY_3)
    attempts = iter([(False, -999), (True, 2)])
    def execute(_):
        success, value = next(attempts)
        worker.current_step_results.append((value, True))
        return success, value
    seen = []
    worker.step_finished.connect(lambda *args: seen.append(args[3:]))
    with patch.object(worker, '_execute_sub_step_logic', execute), \
         patch('core.engine.QTimer.singleShot'):
        worker.execute_sub_step(sub)
        assert worker.current_step_results == []
        worker.execute_sub_step(sub)
        worker.on_step_complete()
    worker.is_running = False
    assert seen == [(True, 2.0)]


@pytest.mark.parametrize('raw', ['---', '???', ''])
def test_invalid_reading_retained_in_result(raw):
    step = Step('read', StepType.CUSTOM)
    step.min_limit, step.max_limit = 0, 1
    worker = ChannelWorker(1, [step])
    worker.is_running = True
    worker.current_step_results = [(raw, True)]
    seen = []
    worker.step_finished.connect(lambda *args: seen.append(args[3:]))
    with patch('core.engine.QTimer.singleShot'):
        worker.on_step_complete()
    worker.is_running = False
    assert seen == [(False, raw)]


@pytest.mark.parametrize('delay', ['-0.5', '1.5', '-1', 'nan'])
def test_invalid_wait_does_not_schedule_timer(delay):
    from core.engine import SubStep, SubStepType
    worker = ChannelWorker(1, [])
    worker.is_running = True
    with patch('core.engine.QTimer.singleShot') as timer:
        success, _ = worker._execute_sub_step_logic(SubStep(SubStepType.WAIT, {'params': delay}))
        assert not success
        timer.assert_not_called()
    worker.is_running = False


@pytest.mark.parametrize('value', [True, False, None])
def test_boolean_is_not_instrument_measurement(value):
    from core.device_steps import measurement
    with pytest.raises(ValueError):
        measurement(value, 'V')


def pump(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -float('inf'), 'ERROR', 'ERROR 0', '1ERROR'])
def test_invalid_measurement_cannot_pass(value):
    step = Step('read', StepType.CUSTOM)
    step.min_limit, step.max_limit = 0, 1
    worker = ChannelWorker(1, [step])
    worker.is_running = True
    worker.current_step_results = [(value, True)]
    seen = []
    worker.step_finished.connect(lambda channel, index, name, passed, actual: seen.append(passed))
    worker.on_step_complete()
    worker.is_running = False
    assert seen == [False]


def test_number_sign_scientific_notation_and_units():
    worker = ChannelWorker(1, [])
    assert worker._parse_numeric('-10') == -10
    assert worker._parse_numeric('1e-3') == .001
    assert finite_reading('-10V') == -10
    assert finite_reading('1e-3 A') == .001


def test_password_hash_legacy_upgrade_and_reserved_name(tmp_path):
    db = DBManager(str(tmp_path / 'test.db'))
    try:
        assert not db.add_user('admin', 'test-password', 'operator')[0]
        assert db.add_user('OP', 'test-password', 'operator')[0]
        conn = sqlite3.connect(db.db_path)
        try:
            assert conn.execute("SELECT password FROM sys_users WHERE username='OP'").fetchone()[0].startswith(PREFIX)
            conn.execute("INSERT INTO sys_users(username,password,role) VALUES('legacy','old-password','operator')")
            conn.execute("INSERT INTO sys_users(username,password,role) VALUES('admin','legacy-password','operator')")
            conn.commit()
        finally:
            conn.close()
        assert db.get_user_role('admin') == 'operator'
        assert db.verify_user('legacy', 'old-password')
        conn = sqlite3.connect(db.db_path)
        try:
            assert conn.execute("SELECT password FROM sys_users WHERE username='legacy'").fetchone()[0].startswith(PREFIX)
        finally:
            conn.close()
        assert db.needs_password_change('ADMIN')
        assert db.update_user('ADMIN', 'ADMIN', 'changed-password')[0]
        assert not db.needs_password_change('ADMIN')
        assert db.verify_user('ADMIN', 'changed-password')
    finally:
        db.close()


def test_async_write_failure_is_not_silently_completed(tmp_path):
    db = DBManager(str(tmp_path / 'test.db'))
    try:
        conn = sqlite3.connect(db.db_path)
        try:
            conn.execute("CREATE TRIGGER reject_result BEFORE INSERT ON test_items_results BEGIN SELECT RAISE(ABORT,'test write failure'); END")
            conn.commit()
        finally:
            conn.close()
        tid = db.start_new_test(1, 'CH1', 'SN', 'recipe')
        db.log_item_result(tid, 'read', 0, 1, .5, 'PASS')
        with pytest.raises(RuntimeError):
            db.finish_test(tid, 'PASS')
        conn = sqlite3.connect(db.db_path)
        try:
            assert conn.execute('SELECT result FROM test_main WHERE test_id=?', (tid,)).fetchone()[0] == 'RECORD_ERROR'
        finally:
            conn.close()
    finally:
        db.close()


def test_atomic_config_failure_preserves_previous_file(tmp_path):
    db = DBManager(str(tmp_path / 'test.db'))
    try:
        assert db.save_sys_config({'value': 1})
        with patch('data.db_manager.os.replace', side_effect=OSError('test failure')):
            assert not db.save_sys_config({'value': 2})
        assert db.load_sys_config()['value'] == 1
        path = tmp_path / 'config/sys_config.json'
        path.write_text('{broken', encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            db.load_sys_config()
    finally:
        db.close()


def test_stop_closes_all_simulator_channels_and_boards_preserves_dut():
    manager = DeviceManager.__new__(DeviceManager)
    first = SimpleNamespace(sequence_number=1, is_connected=True, output_controls=Mock(return_value=True))
    second = SimpleNamespace(sequence_number=2, is_connected=True, output_controls=Mock(return_value=True))
    manager.simulator_channels = lambda: [(1, first, 1), (2, first, 2), (25, second, 1)]
    relay = SimpleNamespace(is_connected=True, setting={'kind': 'relay', 'name': 'relay', 'channels': 2}, all_relays=Mock(return_value=[False, False]))
    manager.rn_boards = {'relay1': relay}
    manager.dut_power = SimpleNamespace(is_connected=True, output_control=Mock(return_value=True))
    for key in ('hv_source', 'ca550', 'it6333b', 'dg2052'):
        setattr(manager, key, None)
    assert manager.safe_shutdown_outputs()
    first.output_controls.assert_called_once_with(False, [1, 2], None)
    second.output_controls.assert_called_once_with(False, [1], None)
    relay.all_relays.assert_called_once_with(False)
    manager.dut_power.output_control.assert_not_called()
    assert manager.safe_shutdown_outputs(emergency=True)
    manager.dut_power.output_control.assert_called_once_with(False)


def test_self_check_failure_not_marked_pass():
    manager = SimpleNamespace(get_all_device_status=lambda: [])
    with patch.object(StartupCheckDialog, '_start_check', lambda self: None), patch.object(StartupCheckDialog, '_bring_to_front', lambda self: None):
        dialog = StartupCheckDialog(manager)
        try:
            dialog._on_check_finished(False)
            dialog._thread_finished()
            assert '失败' in dialog.lbl_title.text()
            assert not dialog._last_success
        finally:
            dialog.close()


def test_recipe_frozen_during_test_and_legacy_display_matches_execution(tmp_path):
    page = OverviewTab()
    page.plc_signal_timer.stop()
    data = {'name': 'first', 'items': [{'name': 'read', 'min': '0', 'max': '1', 'sub_steps': [
        {'name': 'sync', 'type': '同步屏障', 'action': 'barrier'},
        {'name': 'actual', 'type': '读取仪表', 'action': 'measure'}]}]}
    path = tmp_path / 'test.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    try:
        page.load_recipe_from_file(str(path))
        assert page.step_tree.topLevelItem(0).childCount() == 1
        assert page.step_tree.topLevelItem(0).child(0).text(1) == 'measure'
        page.is_testing = True
        data['name'] = 'second'
        path.write_text(json.dumps(data), encoding='utf-8')
        page.load_recipe_from_file(str(path))
        assert page.current_recipe_name == 'first'
    finally:
        page.begin_shutdown()
        page.close()


def test_successful_plc_reset_allows_repeated_same_command():
    page = OverviewTab()
    page.plc_signal_timer.stop()
    plc = SimpleNamespace(write_register=Mock(return_value=True))
    page.request_scan_and_start = Mock()
    try:
        page._handle_plc_signal(1, plc)
        pump(lambda: page.request_scan_and_start.call_count == 1)
        page._handle_plc_signal(1, plc)
        pump(lambda: page.request_scan_and_start.call_count == 2)
        assert plc.write_register.call_count == 2
    finally:
        page.begin_shutdown()
        pump(lambda: page._plc_task.thread.wait(0))
        page.close()


def test_preparation_is_async_and_cancel_does_not_start_run(monkeypatch):
    import threading
    from core.engine import TestEngine as Engine
    entered, release = threading.Event(), threading.Event()
    engine = Engine()
    page = OverviewTab(engine)
    page.plc_signal_timer.stop()
    page.serialize_tree_to_dict = lambda: {'name': 'test', 'items': [{'name': 'wait', 'sub_steps': []}]}
    page.current_recipe_data = {}
    page._confirmed_barcode = 'SN'
    page.txt_sn.setText('SN')
    def prepare(snapshot):
        entered.set()
        release.wait(2)
        return True, ''
    page._apply_recipe_hardware = prepare
    try:
        begin = time.monotonic()
        assert page.start_single_channel_test()
        assert time.monotonic() - begin < .2
        pump(entered.is_set)
        assert not page.combo_recipe.isEnabled()
        page.stop_single_channel_test()
        release.set()
        pump(lambda: not page._prepare_task.isRunning())
        assert not engine.workers
        assert page.combo_recipe.isEnabled()
    finally:
        release.set()
        page.begin_shutdown()
        pump(lambda: page._prepare_task.thread.wait(0))
        page.close()


def test_self_check_cancel_waits_until_initialization_ends():
    import threading
    entered = threading.Event()
    def initialize(logger, cancelled):
        entered.set()
        end = time.monotonic() + 2
        while not cancelled() and time.monotonic() < end:
            time.sleep(.01)
        return False
    manager = SimpleNamespace(init_all_devices=initialize, get_all_device_status=lambda: [])
    with patch.object(StartupCheckDialog, '_bring_to_front', lambda self: None):
        dialog = StartupCheckDialog(manager)
        try:
            pump(entered.is_set)
            dialog.reject()
            assert dialog._exit_pending
            assert not dialog.btn_exit.isEnabled()
            pump(lambda: dialog.worker.wait(0))
            QCoreApplication.processEvents()
            assert not dialog._last_success
        finally:
            dialog.worker.requestInterruption()
            pump(lambda: dialog.worker.wait(0))
            dialog.close()


def test_raw_csc_error_never_replaced_by_compensation():
    worker = ChannelWorker(1, [])
    board = SimpleNamespace(can=Mock())
    worker._get_can_board = lambda manager, params=None: board
    worker._parse_eol_params = lambda params: dict(op_name='0x07 CSC批量读取', channel_id=0,
        timeout=.1, kwargs={'PARAM1': '0', 'PARAM2': '1', 'PARAM3': '0',
                           'ARGS': 'MIN_V:2.495,MAX_V:2.505,COMPENSATE:1,FIX_ADJACENT:1'})
    with patch('devices.eol_protocol.EOLProtocol') as protocol:
        protocol.return_value.execute.return_value = SimpleNamespace(success=True, value=0, raw_data=b'')
        ok, value = worker._execute_eol_protocol(SimpleNamespace(), {'params': ''}, lambda message: None)
    assert not ok
    assert worker.variables['CSC_RAW'][0] == 0


def test_operator_is_frozen_for_run():
    from core.user_session import get_current_user, set_current_user
    previous = get_current_user()
    try:
        set_current_user('first')
        worker = ChannelWorker(1, [])
        set_current_user('second')
        assert worker.operator_name == 'first'
    finally:
        set_current_user(previous)


def test_raw_ntc_extreme_preserved_with_old_conversion_flag():
    worker = ChannelWorker(1, [])
    worker._get_can_board = lambda manager, params=None: SimpleNamespace(can=Mock())
    worker._parse_eol_params = lambda params: dict(op_name='0x10 NTC读取', channel_id=0,
        timeout=.1, kwargs={'CONVERT_TEMP': '1'})
    with patch('devices.eol_protocol.EOLProtocol') as protocol:
        protocol.return_value.execute.return_value = SimpleNamespace(success=True, value=-50)
        ok, value = worker._execute_eol_protocol(SimpleNamespace(), {'params': ''}, lambda message: None)
    assert ok
    assert float(value) == -50
    assert worker.variables['NTC_RAW'] == -50


def test_record_failure_emits_failed_completion_and_retires(tmp_path, monkeypatch):
    from core.engine import TestEngine as Engine
    db = DBManager(str(tmp_path / 'test.db'))
    engine = Engine(db_manager=db)
    seen = []
    engine.channel_test_finished.connect(lambda channel, passed: seen.append(passed))
    def execute(worker, sub):
        worker.current_step_results.append((.5, True))
        return True, .5
    monkeypatch.setattr(ChannelWorker, '_execute_sub_step_logic', execute)
    conn = sqlite3.connect(db.db_path)
    try:
        conn.execute("CREATE TRIGGER reject_result BEFORE INSERT ON test_items_results BEGIN SELECT RAISE(ABORT,'test failure'); END")
        conn.commit()
    finally:
        conn.close()
    try:
        assert engine.start_channel_test(1, [{'name': 'read', 'min': '0', 'max': '1',
              'sub_steps': [{'type': '读取仪表', 'is_judgment': True}]}])
        pump(lambda: not engine.workers)
        assert seen == [False]
    finally:
        engine.stop_all()
        pump(lambda: not engine.workers)
        db.close()


def test_initializer_reports_failed_connection_and_checks_board():
    manager = DeviceManager.__new__(DeviceManager)
    for key in ('pcan','opteeq','e2_rfp','easy320','ca550','hv_source','dut_power','it6333b','tbs2102b','dg2052','dmm34461'):
        setattr(manager, key, None)
    manager.simulators = []
    manager.enabled_devices = {}
    manager.db_manager = SimpleNamespace(load_sys_config=lambda: {
        'rn_boards': {'relay1': {'enabled': True, 'ip': '127.0.0.1'}}})
    board = SimpleNamespace(connect=Mock(return_value=False))
    manager.get_rn_board = lambda key, setting=None: board
    assert not manager.init_all_devices()
    board.connect.assert_called_once()


def test_initial_admin_password_change_applies_to_login_and_switch(tmp_path):
    from ui.dialogs.login_dialog import LoginDialog
    from ui.dialogs.user_account_dialog import UserAccountDialog
    db = DBManager(str(tmp_path / 'test.db'))
    try:
        login = LoginDialog(db)
        login.edit_pwd.setText('PSA')
        with patch('ui.dialogs.password_change.QInputDialog.getText', return_value=('', False)):
            login.handle_login()
        assert login.selected_user is None
        assert db.needs_password_change('ADMIN')
        db.add_user('OP', 'operator-password')
        switch = UserAccountDialog(db, 'OP')
        switch.combo_switch_user.setCurrentIndex(switch.combo_switch_user.findData('ADMIN'))
        switch.edit_switch_pwd.setText('PSA')
        with patch('ui.dialogs.password_change.QInputDialog.getText', return_value=('', False)):
            switch._on_switch_login()
        assert switch.logged_in_user == 'OP'
        login.close()
        switch.close()
    finally:
        db.close()


def test_fast_first_step_is_displayed_after_async_preparation(tmp_path):
    from core.engine import TestEngine as Engine
    engine = Engine()
    page = OverviewTab(engine)
    page.plc_signal_timer.stop()
    recipe = {'name': 'test', 'items': [{'name': 'wait', 'min': '--', 'max': '--', 'standard_type': '不判断',
             'sub_steps': [{'name': 'delay', 'type': '等待', 'action': 'delay', 'params': '0'}]}]}
    path = tmp_path / 'test.json'
    path.write_text(json.dumps(recipe), encoding='utf-8')
    page.load_recipe_from_file(str(path))
    page._apply_recipe_hardware = lambda snapshot: (True, '')
    page._confirmed_barcode = 'SN'
    page.txt_sn.setText('SN')
    try:
        assert page.start_single_channel_test()
        pump(lambda: page.total_count == 1 and not engine.workers)
        assert page.step_tree.topLevelItem(0).text(6) == 'PASS'
        assert page.step_tree.topLevelItem(0).child(0).text(6) == 'PASS'
    finally:
        engine.stop_all()
        pump(lambda: not engine.workers)
        page.begin_shutdown()
        pump(lambda: page._prepare_task.thread.wait(0))
        page.close()


def test_window_close_returns_without_waiting_for_slow_ui_operation():
    import threading
    from PySide6.QtWidgets import QMainWindow, QWidget
    from ui.main_window import MainWindow
    from ui.device_task import DeviceTask
    from core.engine import TestEngine as Engine
    class Window(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self.setCentralWidget(QWidget())
            self.status_bar = self.statusBar()
            self.engine = Engine()
            self.tab_overview = OverviewTab(self.engine)
            self.tab_overview.setParent(self)
            self.device_manager = SimpleNamespace(safe_shutdown_outputs=Mock(return_value=True), disconnect_all=Mock())
            self.db_manager = SimpleNamespace(close=Mock())
            self.operation = DeviceTask(self)
    window = Window()
    entered, release = threading.Event(), threading.Event()
    window.operation.run(lambda: (entered.set(), release.wait(2)), lambda *args: None)
    try:
        pump(entered.is_set)
        begin = time.monotonic()
        window.close()
        assert time.monotonic() - begin < .2
        assert not getattr(window, '_shutdown_done', False)
        release.set()
        pump(lambda: getattr(window, '_shutdown_done', False))
        window.device_manager.disconnect_all.assert_called_once()
        window.db_manager.close.assert_called_once()
    finally:
        release.set()
        pump(lambda: getattr(window, '_shutdown_done', False))
        from PySide6.QtCore import QEvent
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
