import time
import pytest

from PySide6.QtWidgets import QApplication

from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def _make_tab():
    tab = OverviewTab()
    tab.plc_signal_timer.stop()
    return tab


def _sg_status(**overrides):
    status = dict(kind='SG400', phase='下载中', active=True,
                  started_at=time.monotonic(), percent=42.0, project='demo.hex')
    status.update(overrides)
    return status


def test_flash_dialog_auto_show_and_manual_close_suppress():
    tab = _make_tab()
    try:
        assert not tab.flash_dialog.isVisible()
        tab.on_flash_status_changed(1, _sg_status())
        assert tab.flash_dialog.isVisible()
        # 操作员刷写途中主动关闭，本轮不再自动弹出
        tab.flash_dialog.close()
        tab.on_flash_status_changed(1, _sg_status(percent=55.0))
        assert not tab.flash_dialog.isVisible()
        # 新一轮测试复位后可再次弹出
        tab._reset_flash_status()
        tab.on_flash_status_changed(1, _sg_status(started_at=time.monotonic()))
        assert tab.flash_dialog.isVisible()
    finally:
        tab.flash_dialog.close()
        tab.close()


@pytest.mark.parametrize('kind', [None, 'SG400', 'E2'])
@pytest.mark.parametrize('result', ['PASS','FAIL','STOPPED'])
def test_flash_popup_hides_on_terminal_and_next_step_can_show(kind,result):
    tab = _make_tab()
    try:
        started = time.monotonic()
        status = dict(kind=kind,active=True,started_at=started,phase='刷写中')
        tab.on_flash_status_changed(1,status)
        assert tab.flash_dialog.isVisible()
        tab.on_flash_status_changed(1,dict(status,active=False,result=result))
        assert not tab.flash_dialog.isVisible()
        assert not tab._flash_dialog_suppressed
        assert result in tab.test_log_view.toPlainText()
        tab.on_flash_status_changed(1,dict(status,started_at=time.monotonic()))
        assert tab.flash_dialog.isVisible()
        tab._stop_flash_display()
        assert not tab.flash_dialog.isVisible()
    finally:
        tab.begin_shutdown();tab.close()


def test_flash_result_logged_once_and_step_log():
    tab = _make_tab()
    try:
        final = _sg_status(active=False, result='PASS', detail='校验一致')
        tab.on_flash_status_changed(1, final)
        tab.on_flash_status_changed(1, dict(final))
        log = tab.test_log_view.toPlainText()
        assert log.count('刷写[SG400] 结束: PASS') == 1

        tab.on_worker_step_started(1, 0, 'BMS上电')
        tab.on_worker_step_finished(1, 0, 'BMS上电', True, 245)
        tab.on_worker_test_finished(1, True)
        lines = tab.test_log_view.toPlainText().splitlines()
        assert any('步骤1 开始: BMS上电' in line for line in lines)
        assert any('步骤1 结束: BMS上电 => PASS' in line for line in lines)
        assert any('测试结束: PASS' in line for line in lines)
    finally:
        tab.close()
