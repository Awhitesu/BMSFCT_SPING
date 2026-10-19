import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox

from core.engine import TestEngine as Engine
from data.db_manager import DBManager
from ui.main_window import MainWindow
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


def pump(predicate):
    end = time.monotonic() + 4
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


def test_dead_database_worker_with_queued_records_cannot_report_clean_close():
    db = DBManager.__new__(DBManager)
    db._queue_lock = threading.Lock()
    db._closed = False
    db.queue = queue.Queue()
    db.queue.put(('INSERT INTO example VALUES(?)', (1,), None, None))
    db.worker_thread = SimpleNamespace(is_alive=lambda: False)
    with pytest.raises(RuntimeError, match='uncommitted'):
        db.close()


@pytest.mark.parametrize('failure', ['outputs', 'initialize'])
def test_hardware_configuration_does_not_hide_cleanup_or_initialization_failure(failure):
    database = Mock()
    database.load_sys_config.return_value = {}
    database.save_sys_config.return_value = True
    page = HardwareConfigTab(database)
    page.status_timer.stop()
    manager = SimpleNamespace(safe_shutdown_outputs=Mock(return_value=failure != 'outputs'),
        disconnect_all=Mock(return_value=True), update_config=Mock(),
        init_all_devices=Mock(return_value=False), get_all_device_status=lambda: [],
        last_shutdown_errors=['output not confirmed'], last_shutdown_unverified=[])
    page.device_manager = manager
    page.run_operation = lambda text, action: action()
    try:
        with pytest.raises(RuntimeError):
            page.save_config(initialize=True)
        if failure == 'outputs':
            manager.disconnect_all.assert_not_called()
            manager.update_config.assert_not_called()
        else:
            manager.init_all_devices.assert_called_once()
    finally:
        page.shutdown()
        page.close()


def test_confirmed_force_exit_is_respected_after_database_close_error():
    class Window(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self.setCentralWidget(QWidget())
            self.status_bar = self.statusBar()
            self.engine = Engine()
            self.tab_overview = OverviewTab(self.engine)
            self.tab_overview.setParent(self)
            self.device_manager = SimpleNamespace(safe_shutdown_outputs=Mock(return_value=True),
                                                   disconnect_all=Mock(return_value=True))
            self.db_manager = SimpleNamespace(close=Mock(side_effect=OSError('database close failed')))
    window = Window()
    with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QMessageBox.Yes) as question:
        try:
            window.close()
            pump(lambda: getattr(window, '_shutdown_done', False))
            question.assert_called_once()
            assert window.db_manager.close.call_count == 2
        finally:
            window._ignore_output_error = True
            pump(lambda: getattr(window, '_shutdown_done', False))
            from PySide6.QtCore import QCoreApplication, QEvent
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
