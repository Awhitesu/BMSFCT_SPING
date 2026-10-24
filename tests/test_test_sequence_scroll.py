"""按像素连续跟随最新工步，不改变测试节点、数值、选择或执行索引。"""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSlider, QApplication, QTreeWidgetItem

from ui.tabs.overview_tab import OverviewTab
from ui.widgets.test_sequence_tree import TestSequenceTree as SequenceTree
from core.engine import TestEngine as Engine


app = QApplication.instance() or QApplication([])


def settle():
    for _ in range(4):
        app.processEvents()


def set_page_rows(tree, count):
    tree.scrollToTop()
    settle()
    height = tree.visualItemRect(tree.topLevelItem(0)).height()
    tree.resize(tree.width(), tree.height() + count * height - tree.viewport().height())
    settle()
    assert tree.viewport().height() == count * height


@pytest.fixture
def tree():
    widget = SequenceTree()
    widget.setHeaderLabels(['序号', '名称', '值'])
    widget.resize(800, 500)
    for index in range(60):
        QTreeWidgetItem(widget, [str(index + 1), f'Test {index + 1}', str(index)])
    widget.show()
    settle()
    yield widget
    widget.close()


def top_row(tree):
    return tree.itemAt(20, 0)


def wait_for_scroll(tree):
    deadline = time.monotonic() + 1
    while tree._scroll_timer.isActive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert not tree._scroll_timer.isActive()
    settle()


def follow(tree, item):
    tree.show_running_item(item)
    wait_for_scroll(tree)


def test_twenty_visible_rows_advance_one_row_at_a_time_after_follow_position(tree):
    set_page_rows(tree, 20)
    for index in range(15):
        follow(tree, tree.topLevelItem(index))
        assert top_row(tree) is tree.topLevelItem(0)
    row_height = tree.visualItemRect(tree.topLevelItem(0)).height()
    for index in range(15, 26):
        previous = tree.verticalScrollBar().value()
        follow(tree, tree.topLevelItem(index))
        assert tree.verticalScrollBar().value() - previous == row_height
        assert top_row(tree) is tree.topLevelItem(index - 14)
        assert tree.visualItemRect(tree.topLevelItem(index)).top() == 14 * row_height


def test_last_items_keep_the_same_follow_position_after_layout(tree):
    while tree.topLevelItemCount() > 20:
        tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
    set_page_rows(tree, 15)
    current = tree.topLevelItem(15)
    follow(tree, current)
    follow_y = tree.visualItemRect(current).top()
    assert 0 < follow_y < tree.viewport().height()
    for index in range(16, 20):
        tree.topLevelItem(index).setText(2, 'finished')
        tree.doItemsLayout()
        settle()
        follow(tree, tree.topLevelItem(index))
        assert tree.visualItemRect(tree.topLevelItem(index)).top() == follow_y


def test_fit_in_one_page_does_not_gain_a_scrollbar(tree):
    set_page_rows(tree, 15)
    while tree.topLevelItemCount() > 10:
        tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
    settle()
    follow(tree, tree.topLevelItem(9))
    assert tree.verticalScrollBar().maximum() == 0
    assert top_row(tree) is tree.topLevelItem(0)


def test_follow_position_adapts_to_actual_viewport_height(tree):
    set_page_rows(tree, 8)
    follow(tree, tree.topLevelItem(4))
    assert top_row(tree) is tree.topLevelItem(0)
    follow(tree, tree.topLevelItem(6))
    assert top_row(tree) is tree.topLevelItem(1)
    set_page_rows(tree, 20)
    follow(tree, tree.topLevelItem(13))
    assert top_row(tree) is tree.topLevelItem(0)
    follow(tree, tree.topLevelItem(15))
    assert top_row(tree) is tree.topLevelItem(1)


def test_partial_bottom_row_moves_into_view_without_becoming_first(tree):
    set_page_rows(tree, 15)
    tree.resize(tree.width(), tree.height() - 3)
    settle()
    current = tree.topLevelItem(14)
    assert tree.visualItemRect(current).bottom() >= tree.viewport().height()
    follow(tree, current)
    rect = tree.visualItemRect(current)
    assert 0 < rect.top() < tree.viewport().height() * .8
    assert rect.bottom() < tree.viewport().height()


def test_expanded_children_count_toward_continuous_follow_distance(tree):
    parent = tree.topLevelItem(0)
    for index in range(14):
        QTreeWidgetItem(parent, [f'1.{index + 1}', 'Child', ''])
    parent.setExpanded(True)
    set_page_rows(tree, 15)
    follow(tree, parent)
    assert top_row(tree) is parent
    next_parent = tree.topLevelItem(1)
    follow(tree, next_parent)
    assert 0 < tree.visualItemRect(next_parent).top() < tree.viewport().height() * .8
    assert parent.isExpanded()


def test_repeated_layout_does_not_inflate_scroll_range_or_move_follow_position(tree):
    set_page_rows(tree, 15)
    current = tree.topLevelItem(45)
    follow(tree, current)
    expected = tree.verticalScrollBar().maximum()
    follow_y = tree.visualItemRect(current).top()
    for _ in range(5):
        tree.doItemsLayout()
        settle()
        assert tree.verticalScrollBar().maximum() == expected
        assert tree.visualItemRect(current).top() == follow_y


def test_following_row_preserves_selection_checks_and_values(tree):
    set_page_rows(tree, 15)
    selected = tree.topLevelItem(0)
    selected.setCheckState(0, Qt.Checked)
    tree.setCurrentItem(selected)
    expected = [[tree.topLevelItem(index).text(col) for col in range(3)] for index in range(60)]
    current = tree.topLevelItem(15)
    follow(tree, current)
    assert tree.currentItem() is selected and selected.checkState(0) == Qt.Checked
    assert [[tree.topLevelItem(index).text(col) for col in range(3)] for index in range(60)] == expected


def test_worker_start_follows_only_its_own_channel_and_next_run_resets_scroll():
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    page.engine = SimpleNamespace(start_channel_test=Mock(return_value=True))
    tree = page.step_tree
    tree.setParent(None)
    tree.resize(800, 500)
    for index in range(60):
        QTreeWidgetItem(tree, [str(index + 1), f'Test {index + 1}', '', '', str(index), '', 'WAIT'])
    tree.show()
    settle()
    try:
        set_page_rows(tree, 15)
        page.on_worker_step_started(2, 15, 'other channel')
        page.on_worker_step_started(1, -1, 'invalid')
        assert top_row(tree) is tree.topLevelItem(0)
        page.on_worker_step_started(1, 15, 'Test 16')
        wait_for_scroll(tree)
        assert top_row(tree) is tree.topLevelItem(5)
        assert tree.topLevelItem(15).text(6) == 'RUNNING'
        assert page._launch_prepared_test(dict(items=[{}] * 60, operator_name='tester'), 'SN')
        assert top_row(tree) is tree.topLevelItem(0)
        assert tree.topLevelItem(15).text(4) == '--'
    finally:
        page.begin_shutdown()
        tree.close()
        tree.setParent(page)
        page.close()


@pytest.mark.parametrize('selected,commented', [((0,),False),((4,),False),((0,16),False),((2,48),False),((49,),False),((0,),True)])
def test_skipped_items_do_not_move_scroll_after_selected_tests_finish(selected,commented):
    with patch.object(OverviewTab,'load_default_or_first_recipe',lambda self:None):
        page=OverviewTab(Engine())
    page.stop_monitor()
    tree=page.step_tree;tree.setParent(None);tree.resize(800,500)
    items=[]
    for index in range(60):
        skipped=index not in selected
        name=('#' if skipped and commented else '')+f'Test {index+1}'
        item=dict(name=name,standard_type='不判断',min='',max='',skip_runtime=skipped and not commented,
                  sub_steps=[dict(name='delay',device='等待',action='固定延时',type='等待',params='1ms')])
        items.append(item)
        row=QTreeWidgetItem(tree,[str(index+1),name,'','','','','WAIT'])
        row.setCheckState(0,Qt.Checked if not skipped else Qt.Unchecked)
        QTreeWidgetItem(row,[f'{index+1}.1','delay','','','','','WAIT'])
    started=[];completed=[]
    page.engine.step_started.connect(lambda _channel,index,_name:started.append(index))
    page.engine.channel_test_finished.connect(lambda _channel,passed:completed.append(passed))
    tree.show();settle()
    try:
        set_page_rows(tree,15)
        assert page._launch_prepared_test(dict(name='selected-scroll',items=items,operator_name='tester',_debug_run=True),'')
        deadline=time.monotonic()+5
        while page.engine.workers and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert not page.engine.workers
        wait_for_scroll(tree)
        assert completed==[True]
        assert started==list(selected)
        expected_top=selected[0] if selected[0]>=15 else max(0,selected[-1]-10)
        assert top_row(tree) is tree.topLevelItem(expected_top), (
            tree.indexOfTopLevelItem(top_row(tree)), tree.verticalScrollBar().value(),
            tree.verticalScrollBar().maximum(), tree._scroll_target,
            tree.visualItemRect(tree.topLevelItem(expected_top)))
        for index in range(60):
            assert tree.topLevelItem(index).text(6)==('PASS' if index in selected else 'SKIP')
            assert tree.topLevelItem(index).child(0).text(6)==('PASS' if index in selected else 'SKIP')
            assert tree.topLevelItem(index).checkState(0)==(Qt.Checked if index in selected else Qt.Unchecked)
    finally:
        page.engine.stop_all()
        deadline=time.monotonic()+5
        while page.engine.workers and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        page.begin_shutdown();tree.close();tree.setParent(page);page.close()


def test_continuous_follow_changes_target_without_jumping_or_restarting_timer(tree):
    set_page_rows(tree, 15)
    bar = tree.verticalScrollBar()
    current = tree.topLevelItem(15)
    tree.show_running_item(current)
    target = tree._scroll_target
    assert bar.value() == 0
    QTest.qWait(60)
    assert 0 < bar.value() < target
    previous = bar.value()
    tree.show_running_item(tree.topLevelItem(16))
    assert tree._scroll_target > target
    assert bar.value() == previous
    assert tree._scroll_timer.isActive()
    wait_for_scroll(tree)
    assert 0 < tree.visualItemRect(tree.topLevelItem(16)).top() < tree.viewport().height() * .8


def test_fast_steps_follow_latest_target_with_overlapping_viewports(tree):
    set_page_rows(tree, 15)
    positions = []
    tree.verticalScrollBar().valueChanged.connect(positions.append)
    for index in (15, 30, 45, 46, 47):
        tree.show_running_item(tree.topLevelItem(index))
    wait_for_scroll(tree)
    assert top_row(tree) is tree.topLevelItem(37)
    assert positions == sorted(positions)
    assert all(b - a <= tree.viewport().height() // 2 for a, b in zip([0] + positions, positions))
    assert tree._scroll_target is None


def test_manual_scroll_cancels_auto_scroll(tree):
    set_page_rows(tree, 15)
    tree.show_running_item(tree.topLevelItem(15))
    QTest.qWait(40)
    bar = tree.verticalScrollBar()
    bar.triggerAction(QAbstractSlider.SliderSingleStepAdd)
    manual_offset = bar.value()
    QTest.qWait(200)
    assert bar.value() == manual_offset
    assert not tree._scroll_timer.isActive() and tree._scroll_target is None


@pytest.mark.parametrize('action', ['scrollToTop', 'clear'])
def test_reset_cancels_pending_animation(tree, action):
    set_page_rows(tree, 15)
    tree.show_running_item(tree.topLevelItem(15))
    QTest.qWait(40)
    getattr(tree, action)()
    QTest.qWait(200)
    assert tree.verticalScrollBar().value() == 0
    assert not tree._scroll_timer.isActive() and tree._scroll_target is None


def test_hidden_tree_follows_latest_row_without_an_animation(tree):
    set_page_rows(tree, 15)
    tree.hide()
    current = tree.topLevelItem(15)
    tree.show_running_item(current)
    assert not tree._scroll_timer.isActive()
    assert 0 < tree.visualItemRect(current).top() < tree.viewport().height() * .8


@pytest.mark.parametrize('initial_top,expected_top', [(0, 49), (40, 40), (49, 49), (80, 49)])
def test_debug_start_at_fiftieth_item_never_returns_to_unselected_first_item(initial_top, expected_top):
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    tree = page.step_tree
    tree.setParent(None)
    tree.resize(800, 500)
    items = []
    for index in range(120):
        items.append(dict(name=f'Test {index + 1}', skip_runtime=index != 49, sub_steps=[]))
        row = QTreeWidgetItem(tree, [str(index + 1), items[-1]['name'], '', '', '', '', 'WAIT'])
        row.setCheckState(0, Qt.Checked if index == 49 else Qt.Unchecked)
    tree.show()
    settle()
    offsets = []
    at_engine_start = []
    page.engine = SimpleNamespace(start_channel_test=Mock(side_effect=lambda *args, **kwargs:
        at_engine_start.append(tree.indexOfTopLevelItem(top_row(tree))) or True))
    try:
        set_page_rows(tree, 20)
        tree.setCurrentItem(tree.topLevelItem(49))
        tree.scrollToTop()
        tree.show_running_item(tree.topLevelItem(initial_top), animated=False)
        tree.verticalScrollBar().valueChanged.connect(offsets.append)
        assert page._launch_prepared_test(dict(items=items, operator_name='tester', _debug_run=True), '')
        assert at_engine_start == [expected_top]
        assert not tree._scroll_timer.isActive()
        # Earlier unselected rows still receive SKIP results without moving the view.
        for index in range(49):
            page.on_worker_step_finished(1, index, items[index]['name'], True, '跳过')
        page.on_worker_step_started(1, 49, items[49]['name'])
        wait_for_scroll(tree)
        assert top_row(tree) is tree.topLevelItem(expected_top)
        assert 0 not in offsets
        assert tree.currentItem() is tree.topLevelItem(49)
        assert tree.topLevelItem(49).checkState(0) == Qt.Checked
        assert tree.topLevelItem(0).checkState(0) == Qt.Unchecked
    finally:
        page.begin_shutdown()
        tree.close()
        tree.setParent(page)
        page.close()
