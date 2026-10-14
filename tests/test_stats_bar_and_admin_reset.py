# -*- coding: utf-8 -*-
import sys
import os

# 确保项目根路径在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PySide6.QtWidgets import QApplication
from ui.tabs.overview_tab import OverviewTab
from ui.main_window import MainWindow

def test_stats_initialization_and_counting():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    # 1. 验证 OverviewTab 初始状态
    tab = OverviewTab(engine=None, db_manager=None)
    assert tab.total_count == 0, f"Expected total_count == 0, got {tab.total_count}"
    assert tab.pass_count == 0, f"Expected pass_count == 0, got {tab.pass_count}"
    assert tab.fail_count == 0, f"Expected fail_count == 0, got {tab.fail_count}"
    assert tab.lbl_stat_total_val.text() == "0"
    assert tab.lbl_stat_pass_val.text() == "0"
    assert tab.lbl_stat_fail_val.text() == "0"
    assert tab.lbl_stat_rate_val.text() == "0.00%"
    print("[PASS] 1. OverviewTab 初始状态全零检查通过")

    # 2. 模拟测试完成信号
    tab.on_worker_test_finished(1, True)
    assert tab.total_count == 1
    assert tab.pass_count == 1
    assert tab.fail_count == 0
    assert tab.lbl_stat_total_val.text() == "1"
    assert tab.lbl_stat_pass_val.text() == "1"
    assert tab.lbl_stat_rate_val.text() == "100.00%"

    tab.on_worker_test_finished(1, False)
    assert tab.total_count == 2
    assert tab.pass_count == 1
    assert tab.fail_count == 1
    assert tab.lbl_stat_total_val.text() == "2"
    assert tab.lbl_stat_pass_val.text() == "1"
    assert tab.lbl_stat_fail_val.text() == "1"
    assert tab.lbl_stat_rate_val.text() == "50.00%"
    print("[PASS] 2. 模拟测试完成计数自增与百分比计算检查通过")

    # 3. 验证清零方法
    tab.reset_statistics()
    assert tab.total_count == 0
    assert tab.pass_count == 0
    assert tab.fail_count == 0
    assert tab.lbl_stat_total_val.text() == "0"
    assert tab.lbl_stat_pass_val.text() == "0"
    assert tab.lbl_stat_fail_val.text() == "0"
    assert tab.lbl_stat_rate_val.text() == "0.00%"
    print("[PASS] 3. reset_statistics 清零复位检查通过")

    # 4. 验证严格 56px 等高规范
    assert tab.card_stat_total.height() == 56
    assert tab.card_stat_pass.height() == 56
    assert tab.card_stat_fail.height() == 56
    assert tab.card_stat_rate.height() == 56
    assert tab.btn_reset_stats.height() == 56
    assert tab.flash_status_panel.height() == 56
    print("[PASS] 4. 全部卡片与面板 56px 严格等高检查通过")

    # 5. 验证 MainWindow 角色权限与清零按钮可用态联动
    from ui.dialogs.startup_dialog import StartupCheckDialog
    from PySide6.QtWidgets import QDialog
    from devices.manager import DeviceManager
    StartupCheckDialog.exec = lambda self: QDialog.Accepted
    DeviceManager.self_check = lambda self: {}
    DeviceManager.disconnect_all = lambda self: None

    win = MainWindow()
    # 管理员状态
    win.current_user = "ADMIN"
    win.apply_role_permissions()
    assert win.tab_overview.btn_reset_stats.isEnabled() is True
    assert "仅管理员" in win.tab_overview.btn_reset_stats.text()
    assert win.btn_user_account.height() == 56
    assert win.lbl_aging_counter.height() == 56
    print("[PASS] 5.1 管理员模式下清零按钮激活与 56px 等高检查通过")

    # 操作员状态
    win.current_user = "OP001"
    win.apply_role_permissions()
    assert win.tab_overview.btn_reset_stats.isEnabled() is False
    assert "无权操作" in win.tab_overview.btn_reset_stats.text()
    print("[PASS] 5.2 操作员模式下清零按钮自动置灰禁用拦截检查通过")

    print("\n===============================")
    print("ALL 5 REGRESSION TESTS PASSED 100%!")
    print("===============================\n")

if __name__ == "__main__":
    test_stats_initialization_and_counting()
    os._exit(0)
