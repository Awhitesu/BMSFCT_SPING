from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from data.db_manager import DBManager
from ui.dialogs.startup_dialog import StartupCheckDialog


app = QApplication.instance() or QApplication([])


@pytest.fixture
def startup():
    manager = SimpleNamespace(get_all_device_status=lambda: [])
    with patch.object(StartupCheckDialog, '_start_check', lambda self: None), \
            patch.object(StartupCheckDialog, '_bring_to_front', lambda self: None):
        dialog = StartupCheckDialog(manager)
    yield dialog
    dialog.reject()
    dialog.deleteLater()


def test_exit_wins_over_already_queued_automatic_entry(startup):
    startup._on_check_finished(True)
    startup._thread_finished()
    startup.reject()
    app.processEvents()
    assert startup.result() == QDialog.Rejected


def test_old_automatic_entry_does_not_accept_a_new_check(startup):
    startup._on_check_finished(True)
    startup._thread_finished()
    startup._check_generation += 1
    # New check can finish before the old queued callback is delivered.
    startup._last_success = True
    app.processEvents()
    assert startup.result() == QDialog.Rejected


def test_current_successful_check_still_enters(startup):
    startup._on_check_finished(True)
    startup._thread_finished()
    app.processEvents()
    assert startup.result() == QDialog.Accepted


def test_close_wins_over_queued_automatic_entry(startup):
    startup._on_check_finished(True)
    startup._thread_finished()
    startup.close()
    app.processEvents()
    assert startup.result() == QDialog.Rejected


def test_startup_log_preserves_literal_device_text(startup):
    message = '[!] <instrument> error & invalid <value>'
    startup._append_log(message)
    assert startup.log_text.toPlainText() == message


def test_missing_account_modification_and_deletion_do_not_report_success(tmp_path):
    db = DBManager(str(tmp_path / 'accounts.db'))
    try:
        assert not db.update_user('missing', 'renamed', 'secret')[0]
        assert not db.delete_user('missing')[0]
        assert db.add_user('operator1', 'secret')[0]
        assert db.update_user('operator1', 'operator2', 'changed')[0]
        assert db.verify_user('operator2', 'changed')
        assert db.delete_user('operator2')[0]
        assert not db.delete_user('operator2')[0]
    finally:
        db.close()


def test_main_window_uses_authenticated_user_during_ui_construction():
    from core.user_session import get_current_user, set_current_user
    from ui.main_window import MainWindow
    original = get_current_user()
    seen = []
    set_current_user('operator1')
    try:
        with patch('data.db_manager.DBManager'), patch('devices.manager.DeviceManager'), \
                patch('core.engine.TestEngine'), \
                patch('ui.dialogs.startup_dialog.StartupCheckDialog') as check, \
                patch.object(MainWindow, '_init_ui', lambda self: seen.append(self.current_user)):
            check.return_value.exec.return_value = QDialog.Accepted
            window = MainWindow()
            assert seen == ['operator1']
            window.deleteLater()
    finally:
        set_current_user(original)
