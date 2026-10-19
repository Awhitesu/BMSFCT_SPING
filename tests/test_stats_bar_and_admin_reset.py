from core.app_version import versioned_title
from PySide6.QtWidgets import QApplication, QDialog
from ui.tabs.overview_tab import OverviewTab
from ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def test_stats_initialization_and_counting():
    tab = OverviewTab()
    tab.plc_signal_timer.stop()
    try:
        assert (tab.total_count, tab.pass_count, tab.fail_count) == (0, 0, 0)
        tab.on_worker_test_finished(1, True)
        tab.on_worker_test_finished(1, False)
        assert (tab.total_count, tab.pass_count, tab.fail_count) == (2, 1, 1)
        assert tab.lbl_stat_rate_val.text() == '50.00%'
        tab.reset_statistics()
        assert tab.lbl_stat_rate_val.text() == '0.00%'
    finally:
        tab.close()


def test_main_window_initializes_without_removed_modules(tmp_path, monkeypatch):
    from data.db_manager import DBManager
    from ui.dialogs.startup_dialog import StartupCheckDialog
    real_init = DBManager.__init__
    monkeypatch.setattr(DBManager, '__init__', lambda self, *a, **k:
                        real_init(self, str(tmp_path / 'test.db')))
    monkeypatch.setattr(DBManager, 'load_sys_config', lambda self: {})
    monkeypatch.setattr(StartupCheckDialog, '__init__', lambda self, *a, **k: QDialog.__init__(self))
    monkeypatch.setattr(StartupCheckDialog, 'exec', lambda self: QDialog.Accepted)
    win = MainWindow()
    win.tab_overview.plc_signal_timer.stop()
    try:
        win.current_user = 'ADMIN'
        win.apply_role_permissions()
        assert win.tab_settings.btn_reset_stats.isEnabled()
        assert not hasattr(win, 'lbl_aging_counter')
        assert not hasattr(win.engine, 'api_client')
        win.current_user = 'OP001'
        win.apply_role_permissions()
        assert not win.tab_settings.btn_reset_stats.isEnabled()
    finally:
        win.close()
        import time
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + 3
        while not getattr(win, '_shutdown_done', False) and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(.01)
        assert win._shutdown_done


def test_settings_tab_dedup_wiring(tmp_path, monkeypatch):
    """软件设置页条码去重开关联动 OverviewTab 统计状态"""
    from data.db_manager import DBManager
    from ui.dialogs.startup_dialog import StartupCheckDialog
    real_init = DBManager.__init__
    monkeypatch.setattr(DBManager, '__init__', lambda self, *a, **k:
                        real_init(self, str(tmp_path / 'test.db')))
    monkeypatch.setattr(DBManager, 'load_sys_config', lambda self: {})
    monkeypatch.setattr(DBManager, 'save_sys_config', lambda self, cfg: True)
    monkeypatch.setattr(StartupCheckDialog, '__init__', lambda self, *a, **k: QDialog.__init__(self))
    monkeypatch.setattr(StartupCheckDialog, 'exec', lambda self: QDialog.Accepted)
    win = MainWindow()
    win.tab_overview.plc_signal_timer.stop()
    try:
        assert win.tab_settings.chk_stats_deduplicate.isChecked()
        win.tab_settings.chk_stats_deduplicate.setChecked(False)
        assert win.tab_overview.stats_deduplicate is False
        win.tab_settings.chk_stats_deduplicate.setChecked(True)
        assert win.tab_overview.stats_deduplicate is True
    finally:
        win.close()
        import time
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + 3
        while not getattr(win, '_shutdown_done', False) and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(.01)
        assert win._shutdown_done


def test_settings_tab_titles_save_applies_and_persists(tmp_path, monkeypatch):
    """软件设置页保存界面名称：立即生效并写入系统配置"""
    from data.db_manager import DBManager
    from ui.dialogs.startup_dialog import StartupCheckDialog
    real_init = DBManager.__init__
    saved = {}
    monkeypatch.setattr(DBManager, '__init__', lambda self, *a, **k:
                        real_init(self, str(tmp_path / 'test.db')))
    monkeypatch.setattr(DBManager, 'load_sys_config', lambda self: dict(saved))
    monkeypatch.setattr(DBManager, 'save_sys_config',
                        lambda self, cfg: saved.update(cfg) or True)
    monkeypatch.setattr(StartupCheckDialog, '__init__', lambda self, *a, **k: QDialog.__init__(self))
    monkeypatch.setattr(StartupCheckDialog, 'exec', lambda self: QDialog.Accepted)
    win = MainWindow()
    win.tab_overview.plc_signal_timer.stop()
    try:
        # 设置页仅管理员可用，先以管理员激活
        win.current_user = 'ADMIN'
        win.apply_role_permissions()
        # 默认名称
        assert win.windowTitle() == versioned_title('BMS软件注入&APP刷写系统')
        assert win.header_label.text() == 'APP刷写系统'
        # 设置页修改并保存
        win.tab_settings.set_titles('XX工厂刷写系统', 'XX刷写台')
        win.tab_settings.btn_save_titles.click()
        assert win.windowTitle() == versioned_title('XX工厂刷写系统')
        assert win.header_label.text() == 'XX刷写台'
        assert saved['ui_window_title'] == 'XX工厂刷写系统'
        assert saved['ui_header_title'] == 'XX刷写台'
        # 留空保存恢复默认
        win.tab_settings.set_titles('  ', '')
        win.tab_settings.btn_save_titles.click()
        assert win.windowTitle() == versioned_title('BMS软件注入&APP刷写系统')
        assert win.header_label.text() == 'APP刷写系统'
    finally:
        win.close()
        import time
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + 3
        while not getattr(win, '_shutdown_done', False) and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(.01)
        assert win._shutdown_done
