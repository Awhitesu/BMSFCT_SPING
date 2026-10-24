from unittest.mock import Mock, patch
from types import SimpleNamespace
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


@pytest.fixture
def page():
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        widget = OverviewTab()
    widget.stop_monitor()
    for index in range(423):
        row = QTreeWidgetItem(widget.step_tree, [str(index + 1), f'Voltage {index + 1}',
                                               '2.0', '4.0', '--', 'V', 'WAIT'])
        QTreeWidgetItem(row, ['1', 'read voltage', '', '', '--', 'V', 'WAIT'])
    yield widget
    widget.begin_shutdown()
    widget.close()


def test_result_burst_updates_only_affected_rows_without_resizing_columns(page):
    untouched = page.step_tree.topLevelItem(422)
    untouched.setToolTip(2, 'retain this unrelated tooltip')
    changes = []
    page.step_tree.model().dataChanged.connect(lambda first, last, roles: changes.append(first.row())
        if not first.parent().isValid() else None)
    with patch.object(page, '_fit_test_columns') as fit:
        for index in range(120):
            page.on_worker_step_started(1, index, f'Voltage {index + 1}')
            page.on_worker_sub_step_finished(1, index, 0, 'PASS', 3.65)
            page.on_worker_step_finished(1, index, f'Voltage {index + 1}', True, 3.65)
        fit.assert_not_called()
    assert changes and max(changes) == 119
    assert untouched.text(4) == '--' and untouched.text(6) == 'WAIT'
    assert untouched.toolTip(2) == 'retain this unrelated tooltip'
    for index in range(120):
        row = page.step_tree.topLevelItem(index)
        assert row.text(4) == row.child(0).text(4) == '3.65'
        assert row.text(6) == row.child(0).text(6) == 'PASS'
    log = page.test_log_view.toPlainText()
    assert '步骤1 开始' in log and '步骤120 结束' in log


def test_local_updates_keep_current_tooltips_and_variable_details(page):
    row = page.step_tree.topLevelItem(0)
    row.child(0).setToolTip(1, 'IT6722A\nread voltage')
    page.on_variable_target_resolved(1, 0, 'expected_voltage', 3.65)
    variable_details = row.toolTip(2)
    page.on_worker_step_started(1, 0, 'Voltage')
    assert row.toolTip(6) == 'RUNNING'
    page.on_worker_sub_step_finished(1, 0, 0, 'FAIL', 1.23)
    page.on_worker_step_finished(1, 0, 'Voltage', False, 1.23)
    assert row.toolTip(2) == variable_details
    assert row.toolTip(4) == row.child(0).toolTip(4) == '1.23'
    assert row.toolTip(6) == row.child(0).toolTip(6) == 'FAIL'
    assert row.child(0).toolTip(1) == 'IT6722A\nread voltage'


def test_resizing_columns_does_not_scan_or_modify_result_rows(page):
    row = page.step_tree.topLevelItem(400)
    row.setToolTip(4, 'keep row detail')
    changes = []
    page.step_tree.model().dataChanged.connect(lambda *args: changes.append(args))
    page._fit_test_columns()
    assert changes == []
    assert row.toolTip(4) == 'keep row detail'


def show_page(page):
    page.resize(1280, 800)
    page.show()
    app.processEvents()


def wait_for_scroll(page):
    deadline = time.monotonic() + 1
    while page.step_tree._scroll_timer.isActive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert not page.step_tree._scroll_timer.isActive()


def test_hundred_simultaneous_results_and_final_status_are_immediate_while_view_scrolls(page):
    show_page(page)
    tree = page.step_tree
    positions = [tree.verticalScrollBar().value()]
    tree.verticalScrollBar().valueChanged.connect(positions.append)
    for index in range(100):
        page.on_worker_step_started(1, index, f'Voltage {index + 1}')
        page.on_worker_sub_step_finished(1, index, 0, 'PASS', index + .5)
        page.on_worker_step_finished(1, index, f'Voltage {index + 1}', True, index + .5)
    # No event-loop turn or animation frame is needed to expose the real results.
    for index in range(100):
        row = tree.topLevelItem(index)
        assert row.text(4) == row.child(0).text(4) == str(index + .5)
        assert row.text(6) == row.child(0).text(6) == 'PASS'
    assert tree._scroll_timer.isActive()
    target = tree._scroll_target
    page.on_worker_test_finished(1, True)
    assert page.lbl_big_status.text() == 'PASS'
    assert tree._scroll_timer.isActive() and tree._scroll_target == target
    wait_for_scroll(page)
    assert tree.verticalScrollBar().value() == target
    rect = tree.visualItemRect(tree.topLevelItem(99))
    assert 0 < rect.top() < tree.viewport().height() * .8
    assert rect.bottom() < tree.viewport().height()
    assert positions == sorted(positions)
    assert all(b - a <= tree.viewport().height() // 2 for a, b in zip(positions, positions[1:]))


def test_hundred_fast_steps_keep_synchronous_values_and_continuous_offsets(page):
    show_page(page)
    tree = page.step_tree
    positions = [tree.verticalScrollBar().value()]
    tree.verticalScrollBar().valueChanged.connect(positions.append)
    for index in range(100):
        page.on_worker_step_started(1, index, f'Voltage {index + 1}')
        page.on_worker_step_finished(1, index, f'Voltage {index + 1}', True, index)
        assert tree.topLevelItem(index).text(4) == str(index)
        assert tree.topLevelItem(index).text(6) == 'PASS'
        QTest.qWait(10)
    wait_for_scroll(page)
    assert len(positions) > 25
    assert positions == sorted(positions)
    assert all(b - a <= tree.viewport().height() // 2 for a, b in zip(positions, positions[1:]))
    assert tree.visualItemRect(tree.topLevelItem(99)).bottom() < tree.viewport().height()


def test_retest_replaces_scroll_target_and_old_frames_cannot_overwrite_results(page):
    show_page(page)
    tree = page.step_tree
    page.on_worker_step_started(1, 90, 'Voltage 91')
    page.on_worker_step_finished(1, 90, 'Voltage 91', False, 1.23)
    QTest.qWait(40)
    assert tree.verticalScrollBar().value() > 0
    page.on_worker_step_started(1, 2, 'Voltage 3 retry')
    page.on_worker_step_finished(1, 90, 'Voltage 91', True, 3.65)
    assert tree.topLevelItem(90).text(4) == '3.65'
    assert tree.topLevelItem(90).text(6) == 'PASS'
    assert tree._scroll_target == 0
    wait_for_scroll(page)
    assert tree.verticalScrollBar().value() == 0
    assert tree.topLevelItem(90).text(6) == 'PASS'


def test_stop_does_not_wait_for_scroll_and_next_run_cancels_old_target(page):
    show_page(page)
    page.engine = SimpleNamespace(workers={}, stop_test=Mock(), start_channel_test=Mock(return_value=True))
    tree = page.step_tree
    page.on_worker_step_started(1, 90, 'Voltage 91')
    assert tree._scroll_timer.isActive()
    page.stop_single_channel_test()
    page.engine.stop_test.assert_called_once_with(1)
    assert page.lbl_big_status.text() == 'STOPPED'
    page._prepare_cancel.clear()
    assert page._launch_prepared_test(dict(items=[{}] * 423, operator_name='tester'), 'SN2')
    assert not tree._scroll_timer.isActive() and tree._scroll_target is None
    QTest.qWait(100)
    assert tree.verticalScrollBar().value() == 0
    assert tree.topLevelItem(90).text(4) == '--'
