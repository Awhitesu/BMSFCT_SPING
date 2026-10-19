import time
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, TestStep as Step, StepType, SubStep, SubStepType
from ui.tabs.overview_tab import OverviewTab


app=QApplication.instance() or QApplication([])


def test_popup_two_jobs_same_result_are_both_logged_and_duplicate_is_ignored():
    page=OverviewTab();page.plc_signal_timer.stop()
    try:
        for index in range(2):
            state=dict(kind='SG400',started_at=time.monotonic()+index+1,
                       project=f'project{index}.opj',active=True)
            page.on_flash_status_changed(1,state)
            assert page.flash_dialog.isVisible()
            state.update(active=False,result='PASS')
            page.on_flash_status_changed(1,state)
            page.on_flash_status_changed(1,state)
        assert page.test_log_view.toPlainText().count('结束: PASS')==2
        assert page.flash_dialog.flash_progress_bar.value()==1000
    finally:
        page.begin_shutdown();page.flash_dialog.close();page.close()


def test_popup_close_suppresses_reopen_and_reset_allows_next_run():
    page=OverviewTab();page.plc_signal_timer.stop()
    try:
        state=dict(kind='E2',started_at=time.monotonic()+1,active=True)
        page.on_flash_status_changed(1,state)
        page.flash_dialog.close()
        page.on_flash_status_changed(1,state)
        assert not page.flash_dialog.isVisible()
        page._reset_flash_status()
        page.on_flash_status_changed(1,state)
        assert page.flash_dialog.isVisible()
        page.flash_dialog.render_state(dict(state,active=False,result='FAIL'))
        assert page.flash_dialog.flash_progress_bar.value()==0
    finally:
        page.begin_shutdown();page.flash_dialog.close();page.close()


def test_finished_popup_reelides_project_after_resize():
    page=OverviewTab();page.plc_signal_timer.stop()
    try:
        dialog=page.flash_dialog
        dialog.resize(460,160);dialog.show();app.processEvents()
        dialog.render_state(dict(kind='SG400',project='X'*200+'.opj',result='PASS'))
        before=dialog.lbl_flash_record.text()
        dialog.resize(1000,160);app.processEvents();app.processEvents()
        assert len(dialog.lbl_flash_record.text())>len(before)
    finally:
        page.begin_shutdown();page.flash_dialog.close();page.close()


def test_result_log_does_not_depend_on_visible_tree_rows():
    page=OverviewTab();page.plc_signal_timer.stop()
    try:
        page.step_tree.clear()
        page.on_worker_sub_step_finished(1,0,0,'FAIL','device timeout')
        assert 'device timeout' in page.test_log_view.toPlainText()
    finally:
        page.begin_shutdown();page.close()


def test_execution_logs_full_parameters_limits_and_retry():
    page=OverviewTab();page.plc_signal_timer.stop()
    step=Step('power check',StepType.CUSTOM)
    step.min_limit=2.5;step.max_limit=3.5;step.unit='V'
    sub=SubStep(SubStepType.WAIT,dict(device='simulator',action='wait',params='0',
                                    channel=24,voltage=3.3,password='do-not-log'))
    step.add_sub_step(sub)
    worker=ChannelWorker(1,[step])
    worker._retry_count=2
    worker.log_message.connect(page.on_worker_log_message)
    try:
        with patch('core.engine.QTimer.singleShot'):
            worker._execute_sub_step_logic(sub)
        text=page.test_log_view.toPlainText()
        assert all(value in text for value in ('simulator','wait','24','3.3','2.5','3.5','重试 2',sub.step_id))
        assert 'do-not-log' not in text
        assert '[REDACTED]' in text
    finally:
        worker.deleteLater();page.begin_shutdown();page.close()
