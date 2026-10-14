from PySide6.QtCore import QObject, QThread, Signal, Slot


class _DeviceWorker(QObject):
    result = Signal(bool, object)

    @Slot(object)
    def execute(self, action):
        try:
            self.result.emit(True, action())
        except Exception as exc:
            self.result.emit(False, str(exc))


class DeviceTask(QObject):
    """One reusable worker thread; reject overlapping work rather than accumulate it."""
    requested = Signal(object)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = QThread(self)
        self.worker = _DeviceWorker()
        self.worker.moveToThread(self.thread)
        self.thread.finished.connect(self.worker.deleteLater)
        self.requested.connect(self.worker.execute)
        self.worker.result.connect(self._result)
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
        if not self.thread.isRunning():
            self.thread.start()
        self.requested.emit(action)
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

    def shutdown(self):
        self.closing = True
        self.thread.quit()
        self.thread.wait()
