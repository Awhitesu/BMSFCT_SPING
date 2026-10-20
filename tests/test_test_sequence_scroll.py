"""按实际窗口行数翻页，不改变测试节点、数值、选择或执行索引。"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from ui.tabs.overview_tab import OverviewTab
from ui.widgets.test_sequence_tree import TestSequenceTree as SequenceTree


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


def test_fifteen_rows_then_sixteenth_becomes_first_and_thirty_first_starts_next_page(tree):
    set_page_rows(tree, 15)
    for index in range(15):
        tree.show_running_item(tree.topLevelItem(index))
        assert top_row(tree) is tree.topLevelItem(0)
    tree.show_running_item(tree.topLevelItem(15))
    assert top_row(tree) is tree.topLevelItem(15)
    for index in range(16, 30):
        tree.show_running_item(tree.topLevelItem(index))
        assert top_row(tree) is tree.topLevelItem(15)
    tree.show_running_item(tree.topLevelItem(30))
    assert top_row(tree) is tree.topLevelItem(30)


def test_last_page_can_start_with_sixteenth_row_even_with_only_twenty_total(tree):
    while tree.topLevelItemCount() > 20:
        tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
    set_page_rows(tree, 15)
    current = tree.topLevelItem(15)
    tree.show_running_item(current)
    assert tree.visualItemRect(current).top() == 0
    assert top_row(tree) is current
    for index in range(16, 20):
        tree.topLevelItem(index).setText(2, 'finished')
        tree.doItemsLayout()
        settle()
        tree.show_running_item(tree.topLevelItem(index))
        assert top_row(tree) is current


def test_fit_in_one_page_does_not_gain_a_scrollbar(tree):
    set_page_rows(tree, 15)
    while tree.topLevelItemCount() > 10:
        tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
    settle()
    tree.show_running_item(tree.topLevelItem(9))
    assert tree.verticalScrollBar().maximum() == 0
    assert top_row(tree) is tree.topLevelItem(0)


def test_page_size_follows_actual_resize_instead_of_fixed_fifteen(tree):
    set_page_rows(tree, 8)
    tree.show_running_item(tree.topLevelItem(7))
    assert top_row(tree) is tree.topLevelItem(0)
    tree.show_running_item(tree.topLevelItem(8))
    assert top_row(tree) is tree.topLevelItem(8)
    set_page_rows(tree, 20)
    tree.show_running_item(tree.topLevelItem(19))
    assert top_row(tree) is tree.topLevelItem(0)
    tree.show_running_item(tree.topLevelItem(20))
    assert top_row(tree) is tree.topLevelItem(20)


def test_partial_bottom_row_starts_a_new_page(tree):
    set_page_rows(tree, 15)
    tree.resize(tree.width(), tree.height() - 3)
    settle()
    current = tree.topLevelItem(14)
    assert tree.visualItemRect(current).bottom() >= tree.viewport().height()
    tree.show_running_item(current)
    assert tree.visualItemRect(current).top() == 0


def test_expanded_children_count_toward_available_page_height(tree):
    parent = tree.topLevelItem(0)
    for index in range(14):
        QTreeWidgetItem(parent, [f'1.{index + 1}', 'Child', ''])
    parent.setExpanded(True)
    set_page_rows(tree, 15)
    tree.show_running_item(parent)
    assert top_row(tree) is parent
    next_parent = tree.topLevelItem(1)
    tree.show_running_item(next_parent)
    assert tree.visualItemRect(next_parent).top() == 0
    assert parent.isExpanded()


def test_repeated_layout_does_not_inflate_scroll_range_or_move_current_page(tree):
    set_page_rows(tree, 15)
    current = tree.topLevelItem(45)
    tree.show_running_item(current)
    expected = tree.verticalScrollBar().maximum()
    for _ in range(5):
        tree.doItemsLayout()
        settle()
        assert tree.verticalScrollBar().maximum() == expected
        assert tree.visualItemRect(current).top() == 0


def test_following_row_preserves_selection_checks_and_values(tree):
    set_page_rows(tree, 15)
    selected = tree.topLevelItem(0)
    selected.setCheckState(0, Qt.Checked)
    tree.setCurrentItem(selected)
    expected = [[tree.topLevelItem(index).text(col) for col in range(3)] for index in range(60)]
    current = tree.topLevelItem(15)
    tree.show_running_item(current)
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
        assert top_row(tree) is tree.topLevelItem(15)
        assert tree.topLevelItem(15).text(6) == 'RUNNING'
        assert page._launch_prepared_test(dict(items=[{}] * 60, operator_name='tester'), 'SN')
        assert top_row(tree) is tree.topLevelItem(0)
        assert tree.topLevelItem(15).text(4) == '--'
    finally:
        page.begin_shutdown()
        tree.close()
        tree.setParent(page)
        page.close()
