"""Keep closed test pages out of background cyclic collection and native teardown."""
import gc
import sys
import threading
import time
import weakref

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.engine import TestEngine as Engine
from ui.device_task import DeviceTask
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


def drain_deletions():
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def pump(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        drain_deletions()
        time.sleep(.001)
    assert predicate()


def test_close_shuts_down_page_and_deletes_it_on_gui_before_later_collection():
    page = OverviewTab(Engine())
    destroyed = []
    page.destroyed.connect(lambda: destroyed.append(threading.get_ident()))
    assert page.close()
    assert page._closing and page._prepare_cancel.is_set()
    assert not page.plc_signal_timer.isActive()
    drain_deletions()
    assert not isValid(page)
    assert destroyed == [threading.get_ident()]


def test_close_during_device_io_keeps_worker_alive_and_discards_late_callback():
    page = OverviewTab(Engine())
    runtime = page._prepare_task._runtime
    thread = page._prepare_task.thread
    entered, release = threading.Event(), threading.Event()
    callbacks = []

    def action():
        entered.set()
        release.wait(3)
        return 'finished'

    try:
        assert page._prepare_task.run(action, lambda *result: callbacks.append(result))
        assert entered.wait(2)
        started = time.monotonic()
        assert page.close()
        assert time.monotonic() - started < .15
        drain_deletions()
        assert not isValid(page)
        assert isValid(runtime) and not thread.wait(0)
    finally:
        release.set()
        assert thread.wait(3000)
        pump(lambda: not isValid(runtime))
    assert callbacks == []


def test_pending_gui_deletion_cannot_be_reclaimed_by_worker_collection():
    page = OverviewTab(Engine())
    reference = weakref.ref(page)
    destroyed = []
    page.destroyed.connect(lambda: destroyed.append(threading.get_ident()))
    assert page.close()
    del page
    collector = threading.Thread(target=gc.collect)
    collector.start()
    collector.join(timeout=3)
    assert not collector.is_alive()
    assert reference() is not None and isValid(reference())
    assert destroyed == []
    drain_deletions()
    assert reference() is None or not isValid(reference())
    assert destroyed == [threading.get_ident()]
    assert not app._retiring_overview_pages


def test_repeated_resize_and_close_remains_safe_with_worker_garbage_collection(monkeypatch):
    assert gc.isenabled()
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *error: errors.append(error))
    collector = DeviceTask()
    pages = []
    main_thread = threading.get_ident()
    collected_on = []
    try:
        for _ in range(20):
            page = OverviewTab(Engine())
            pages.append(page)
            page.resize(1100, 720)
            page.show()
            assert page.close()
            drain_deletions()
            assert not isValid(page)
            # A following preparation can trigger collection on its worker.
            done = []
            def collect():
                collected_on.append(threading.get_ident())
                return gc.collect()
            assert collector.run(collect, lambda *_: done.append(True))
            pump(lambda: bool(done))
        assert collected_on and all(thread != main_thread for thread in collected_on)
        assert errors == []
    finally:
        collector.shutdown()
        collector.deleteLater()
        drain_deletions()
