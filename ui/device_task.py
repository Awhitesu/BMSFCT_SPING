from PySide6.QtCore import QObject, QThread, QTimer, QCoreApplication, Signal, Slot


class _DeviceWorker(QObject):
    result = Signal(bool, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.action = None

    @Slot()
    def execute(self):
        action, self.action = self.action, None
        try:
            value = action()
            ok = True
        except Exception as exc:
            ok, value = False, str(exc)
        # No queued signal argument may own the page on this thread. The GUI
        # runtime keeps its own reference until the completion is delivered.
        del action
        self.result.emit(ok, value)


class _TaskRuntime(QObject):
    """Application-owned lifetime: deleting a page cannot delete a live QThread."""
    requested = Signal()
    result = Signal(bool, object)

    def __init__(self):
        super().__init__(QCoreApplication.instance())
        self.thread = QThread(self)
        # An unused task never emits QThread.finished. Keep its worker owned
        # by the GUI runtime until the first real invocation instead of
        # stranding a parentless QObject in a thread that never starts.
        self.worker = _DeviceWorker(self)
        self.worker_started = False
        self.thread.finished.connect(self.worker.deleteLater)
        self.requested.connect(self.worker.execute)
        self.worker.result.connect(self._complete)
        self.thread.finished.connect(self._check_exit)
        self.action = None
        self.closing = False
        self.owner_gone = False
        self.exit_timer = QTimer(self)
        self.exit_timer.setInterval(10)
        self.exit_timer.timeout.connect(self._check_exit)

    @Slot(bool, object)
    def _complete(self, ok, value):
        action, self.action = self.action, None
        try:
            self.result.emit(ok, value)
        finally:
            # This slot runs on the GUI thread, including the final decref of
            # action closures that can own widgets.
            del action

    @Slot()
    def close(self):
        self.closing = True
        self.thread.requestInterruption()
        self.thread.quit()
        self._check_exit()

    @Slot()
    def owner_destroyed(self):
        self.owner_gone = True
        self.close()

    @Slot()
    def _check_exit(self):
        if not self.closing:
            return
        if not self.thread.wait(0):
            self.exit_timer.start()
            return
        self.exit_timer.stop()
        self.action = None
        # A quit before the queued invocation ran must also release the closure
        # here, never in native worker teardown.
        self.worker.action = None
        if self.owner_gone:
            self.deleteLater()


class DeviceTask(QObject):
    """One reusable worker thread; reject overlapping work rather than accumulate it."""
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._runtime = _TaskRuntime()
        self.thread, self.worker = self._runtime.thread, self._runtime.worker
        self._runtime.result.connect(self._result)
        self.destroyed.connect(self._runtime.owner_destroyed)
        self.busy = False
        self.closing = False
        self.callback = None

    def isRunning(self):
        return self.busy

    def run(self, action, callback):
        if self.busy or self.closing:
            return False
        self.busy = True
        self.callback = callback
        self._runtime.action = action
        self.worker.action = action
        if not self.thread.isRunning():
            if not self._runtime.worker_started:
                self.worker.setParent(None)
                self.worker.moveToThread(self.thread)
                self._runtime.worker_started = True
            self.thread.start()
        self._runtime.requested.emit()
        return True

    @Slot(bool, object)
    def _result(self, ok, value):
        callback, self.callback = self.callback, None
        try:
            if callback and not self.closing:
                callback(ok, value)
        finally:
            self.busy = False
            if not self.closing:
                self.finished.emit()

    def begin_shutdown(self):
        self.closing = True
        self.callback = None
        self._runtime.close()

    def shutdown(self):
        self.begin_shutdown()
        self.thread.wait()
        self._runtime._check_exit()
