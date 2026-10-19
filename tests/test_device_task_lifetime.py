import gc
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import delete, isValid

from ui.device_task import DeviceTask


app = QApplication.instance() or QApplication([])


def pump(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


def test_page_last_reference_cannot_be_released_in_worker():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-X', 'faulthandler', str(root / 'tools/qt_task_lifetime_probe.py')],
        cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'LIFETIME_PROBE_PASS' in result.stdout


def test_callable_final_reference_is_released_on_gui_thread():
    released, done = [], []
    class Token:
        def __del__(self):
            released.append(threading.get_ident())
    task = DeviceTask()
    try:
        task.run(lambda token=Token(): None, lambda *_: done.append(True))
        pump(lambda: bool(done))
        assert released == [threading.get_ident()]
    finally:
        task.shutdown()
        delete(task)


def test_unused_worker_stays_gui_owned_and_is_deleted_with_runtime():
    from PySide6.QtCore import QEvent
    page=QWidget();task=DeviceTask(page)
    runtime,worker,thread=task._runtime,task.worker,task.thread
    assert worker.parent() is runtime
    assert worker.thread() is app.thread()
    assert not thread.isRunning()
    delete(page)
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    assert not isValid(runtime) and not isValid(worker) and not isValid(thread)


def test_deleting_page_during_io_does_not_delete_live_thread_or_invoke_callback():
    page = QWidget()
    task = DeviceTask(page)
    runtime, thread = task._runtime, task.thread
    entered, release = threading.Event(), threading.Event()
    callbacks = []
    def action():
        entered.set()
        release.wait(3)
        return 1
    task.run(action, lambda *_: callbacks.append(True))
    assert entered.wait(2)
    try:
        delete(page)
        assert runtime.owner_gone
        assert not thread.wait(0)
    finally:
        release.set()
        assert thread.wait(3000)
        QCoreApplication.processEvents()
    assert callbacks == []


def test_finished_callback_can_start_next_invocation_without_losing_ownership():
    task = DeviceTask()
    results, released = [], []
    class Token:
        def __del__(self):
            released.append(threading.get_ident())
    def on_finished():
        if len(results) == 1:
            assert task.run(lambda token=Token(): (gc.collect(), 2)[1], completed)
    def completed(ok, value):
        assert ok
        results.append(value)
    task.finished.connect(on_finished)
    try:
        task.run(lambda: 1, completed)
        pump(lambda: len(results) == 2)
        assert results == [1, 2]
        assert released == [threading.get_ident()]
    finally:
        task.shutdown()
        delete(task)


def test_owner_destruction_retains_runtime_during_native_thread_teardown():
    page = QWidget()
    task = DeviceTask(page)
    runtime, thread = task._runtime, task.thread
    entered, release = threading.Event(), threading.Event()
    done = []
    thread.finished.connect(lambda: (entered.set(), release.wait(3)), Qt.DirectConnection)
    task.run(lambda: 1, lambda *_: done.append(True))
    pump(lambda: bool(done))
    task.begin_shutdown()
    assert entered.wait(2)
    try:
        assert not thread.isRunning()
        assert not thread.wait(0)
        delete(page)
        app.processEvents()
        assert isValid(runtime) and isValid(thread)
    finally:
        release.set()
        assert thread.wait(3000)
        app.processEvents()


def test_meter_task_retains_action_until_native_exit_and_releases_on_gui():
    from ui.tabs.multimeter_tab import MeterTask
    released = []
    class Token:
        def __del__(self):
            released.append(threading.get_ident())
    task = MeterTask(lambda token=Token(): 1)
    entered, release = threading.Event(), threading.Event()
    task.finished.connect(lambda: (entered.set(), release.wait(3)), Qt.DirectConnection)
    task.start()
    assert entered.wait(2)
    try:
        assert not task.isRunning()
        assert not task.wait(0)
        task._lifetime.retire()
        assert task in task._lifetime.tasks
        assert released == []
    finally:
        release.set()
        assert task.wait(3000)
    task._lifetime.retire()
    assert task not in task._lifetime.tasks
    assert released == [threading.get_ident()]
    delete(task)


def test_window_shutdown_includes_replaced_application_owned_threads():
    from ui.tabs.multimeter_tab import MeterTask
    from ui.async_shutdown import request_ui_shutdown, threads_finished
    page = QWidget()
    task = MeterTask(lambda: 1)
    entered, release = threading.Event(), threading.Event()
    task.finished.connect(lambda: (entered.set(), release.wait(3)), Qt.DirectConnection)
    task.start()
    assert entered.wait(2)
    try:
        # The page no longer refers to this previous task, but it is not joined.
        threads = request_ui_shutdown(page)
        assert task in threads
        assert not threads_finished(threads)
    finally:
        release.set()
        assert task.wait(3000)
        task._lifetime.retire()
        delete(task)
        delete(page)
