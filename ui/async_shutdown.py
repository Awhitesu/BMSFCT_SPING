from PySide6.QtCore import QObject, QThread, QTimer, QCoreApplication
from ui.device_task import DeviceTask


def cleanup_resources(manager, database, ignore_errors, result):
    """Background cleanup owns backend resources only, never the Qt window."""
    try:
        if not ignore_errors and not manager.safe_shutdown_outputs(logger=print):
            result['error'] = '部分输出关闭未确认，请检查设备日志和物理输出'
            return
        if manager.disconnect_all() is False and not ignore_errors:
            result['error'] = '设备连接未全部释放：' + '; '.join(manager.last_disconnect_errors)
            return
        database.close()
    except Exception as exc:
        if not ignore_errors:
            result['error'] = str(exc)
        else:
            print('[WARN] Forced exit with unconfirmed cleanup: ' + str(exc))


def request_ui_shutdown(window, exclude=()):
    """Request cancellation without waiting on the GUI thread."""
    threads = set()
    objects = [window, *window.findChildren(QObject)]
    for obj in objects:
        if not isinstance(obj, (QThread, QTimer)):
            obj._closing = True
        if hasattr(obj, 'closing'):
            obj.closing = True
        if isinstance(obj, QTimer) and obj is not getattr(window, '_shutdown_timer', None):
            obj.stop()
        if isinstance(obj, DeviceTask):
            obj.begin_shutdown()
        for value in (obj, *vars(obj).values()):
            if isinstance(value, QThread) and value not in exclude:
                threads.add(value)
    # Detached task runtimes and replaced one-shot tasks are application-owned.
    # They may still be in native teardown even after disappearing from a page.
    app = QCoreApplication.instance()
    if app is not None:
        threads.update(thread for thread in app.findChildren(QThread) if thread not in exclude)
        lifetime = getattr(app, '_meter_task_lifetime', None)
        if lifetime is not None:
            threads.update(thread for thread in lifetime.tasks if thread not in exclude)
    for thread in threads:
        try:
            if hasattr(thread, 'running'):
                thread.running = False
            thread.requestInterruption()
            thread.quit()
        except RuntimeError:
            continue
    return threads


def threads_finished(threads):
    for thread in threads:
        try:
            # finished/isRunning can change before native thread teardown ends.
            # A zero-time join checks completion without blocking the GUI.
            if thread.isRunning() or not thread.wait(0):
                return False
        except RuntimeError:
            pass
    return True
