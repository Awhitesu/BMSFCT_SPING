"""Deterministically hold a worker in result emission while its owner is released.

Run in a child process: the old implementation can terminate the process natively.
No hardware is accessed.
"""
import gc
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from ui.device_task import DeviceTask


def main():
    app = QApplication([])
    gc.disable()  # Isolate reference ownership from cyclic collection timing.
    entered, release = threading.Event(), threading.Event()
    done, destroyed = [], []
    main_thread = threading.get_ident()
    page = QWidget()
    task = DeviceTask(page)
    thread = task.thread
    page.destroyed.connect(lambda: destroyed.append(threading.get_ident()), Qt.DirectConnection)
    task.worker.result.connect(lambda *_: (entered.set(), release.wait(5)), Qt.DirectConnection)
    task.run(lambda owner=page: 1, lambda *_: done.append(True))
    del page
    try:
        assert entered.wait(3), 'Worker did not enter the controlled race'
        end = time.monotonic() + 3
        while not done and time.monotonic() < end:
            app.processEvents()
            time.sleep(.001)
        print(f'GUI={main_thread} callback={done} destroyed={destroyed}', flush=True)
        assert done == [True]
        assert destroyed == [main_thread], 'Page retained by a queued worker argument'
    finally:
        release.set()
        # Owner deletion requests quit without destroying the live QThread.
        assert thread.wait(3000)
        app.processEvents()
    print('LIFETIME_PROBE_PASS', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
