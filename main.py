import faulthandler
import logging
import os
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import QDir
from PySide6.QtWidgets import QApplication, QDialog

from data.db_manager import DBManager
from ui.dialogs.login_dialog import LoginDialog
from ui.main_window import MainWindow
from core.user_session import set_current_user


_crash_log_file = None


def setup_crash_logging():
    global _crash_log_file
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.abspath(os.path.dirname(__file__))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"runtime_crash_{datetime.now():%Y%m%d_%H%M%S}.log")
    _crash_log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    _crash_log_file.write(f"[START] {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    _crash_log_file.flush()
    faulthandler.enable(file=_crash_log_file, all_threads=True)

    def excepthook(exc_type, exc_value, exc_tb):
        _crash_log_file.write("[UNCAUGHT_EXCEPTION]\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=_crash_log_file)
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


def _resource_base_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.abspath(os.path.dirname(__file__))


def _load_application_style(app, base_dir):
    resources_dir = os.path.join(base_dir, "resources")
    QDir.addSearchPath("uiicons", os.path.join(resources_dir, "icons"))
    qss_path = os.path.join(resources_dir, "style.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as style_file:
            app.setStyleSheet(style_file.read())


def _authenticate_user():
    """在任何设备初始化之前完成用户验证。"""
    auth_db = DBManager()
    try:
        dialog = LoginDialog(auth_db)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.selected_user
    finally:
        auth_db.close()


def main():
    setup_crash_logging()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("pymodbus").setLevel(logging.DEBUG if os.getenv('GX_VERBOSE_IO') == '1' else logging.WARNING)

    app = QApplication(sys.argv)
    from ui.parameter_wheel_guard import install_parameter_wheel_guard
    install_parameter_wheel_guard(app)
    _load_application_style(app, _resource_base_dir())

    # 启动顺序：用户登录 -> MainWindow 内的设备自检 -> 主界面。
    # 登录窗口关闭后仍要继续创建自检窗口，所以暂时关闭“最后窗口关闭退出”。
    app.setQuitOnLastWindowClosed(False)
    current_user = _authenticate_user()
    if not current_user:
        return 0
    set_current_user(current_user)

    # MainWindow 构造期会运行 StartupCheckDialog，自检被取消时不会显示主界面。
    window = MainWindow()
    window.current_user = current_user
    window.update_user_display()
    window.showMaximized()
    window.raise_()
    window.activateWindow()

    from core.startup_diagnostics import StartupDiagnostics
    window.startup_diagnostics=StartupDiagnostics(window,os.path.join(os.path.dirname(os.path.abspath(__file__)),'logs'))

    app.setQuitOnLastWindowClosed(True)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
