import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QThread, Qt
from PySide6.QtWidgets import QApplication

from core.engine import TestEngine as Engine
from ui.async_shutdown import threads_finished


app = QApplication.instance() or QApplication([])


def finishing_thread():
    entered, release = threading.Event(), threading.Event()
    thread = QThread()
    # Hold native teardown after Qt has published its finished state.
    thread.finished.connect(lambda: (entered.set(), release.wait(3)), Qt.DirectConnection)
    thread.start()
    thread.quit()
    assert entered.wait(2)
    return thread, release


def test_shutdown_does_not_confuse_finished_signal_with_native_exit():
    thread, release = finishing_thread()
    try:
        assert not thread.isRunning()
        assert not thread.wait(0)
        begin = time.monotonic()
        assert not threads_finished([thread])
        assert time.monotonic() - begin < .1
    finally:
        release.set()
        assert thread.wait(2000)
    assert threads_finished([thread])


def test_engine_keeps_thread_ownership_until_native_exit():
    engine = Engine()
    thread, release = finishing_thread()
    worker = SimpleNamespace(cleanup_error='', record_error='')
    engine.workers[1], engine.threads[1] = worker, thread
    retired = []
    engine.run_retired.connect(retired.append)
    try:
        engine._retire_worker(1, worker)
        assert engine.workers[1] is worker
        assert engine.threads[1] is thread
        assert retired == []
    finally:
        release.set()
        assert thread.wait(2000)
    end = time.monotonic() + 2
    while engine.workers and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(.005)
    assert not engine.workers and not engine.threads
    assert retired == [1]


def test_mes_retains_thread_until_native_exit():
    from PySide6.QtWidgets import QWidget
    from ui.tabs.mes_tab import MesTab
    page = MesTab.__new__(MesTab)
    QWidget.__init__(page)
    thread, release = finishing_thread()
    page._thread = thread
    page._worker = object()
    page.test_button = Mock()
    try:
        with patch('PySide6.QtCore.QTimer.singleShot'):
            page._retire_thread()
        assert page._thread is thread
        page.test_button.setEnabled.assert_not_called()
    finally:
        release.set()
        assert thread.wait(2000)
    page._retire_thread()
    assert page._thread is None and page._worker is None
    page.test_button.setEnabled.assert_called_once_with(True)
    page.close()
