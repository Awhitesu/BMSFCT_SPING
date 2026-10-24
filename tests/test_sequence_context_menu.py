"""菜单悬停只绘制高亮，不执行动作或改变全局主题。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTreeWidgetItem, QTreeWidget
import shiboken6

from ui.widgets.sequence_context_menu import SequenceContextMenu
from ui.tabs.overview_tab import OverviewTab
from ui.tabs.config_tab import ConfigTab


app = QApplication.instance() or QApplication([])


def dispose_menu(menu):
    menu.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_hover_switches_promptly_without_actions_or_global_style_change():
    style = app.style()
    owner = QWidget()
    menu = SequenceContextMenu(owner)
    invoked = Mock()
    actions = [menu.addAction(f'Action {i}', invoked) for i in range(10)]
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    latencies = []
    try:
        menu.popup(owner.mapToGlobal(owner.rect().center()))
        app.processEvents()
        for action in actions * 5:
            started = time.monotonic()
            QTest.mouseMove(menu, menu.actionGeometry(action).center(), 0)
            app.processEvents()
            assert menu.activeAction() is action
            latencies.append(time.monotonic() - started)
            QTest.qWait(2)
        assert max(latencies) < .15
        assert len(ticks) >= 3
        invoked.assert_not_called()
        assert app.style() is style
    finally:
        timer.stop()
        dispose_menu(menu)
        owner.close()


def test_test_page_popup_returns_without_modal_loop_and_actions_still_work():
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    for i in range(8):
        node = QTreeWidgetItem(page.step_tree, [str(i), f'item{i}', '', '', '', '', 'WAIT'])
        node.setCheckState(0, Qt.Unchecked)
    page.resize(1000, 700)
    page.show()
    app.processEvents()
    try:
        with patch.object(page, '_can_debug_tests', return_value=True):
            started = time.monotonic()
            page._test_context_menu(page.step_tree.visualItemRect(page.step_tree.topLevelItem(0)).center())
            assert time.monotonic() - started < .15
            menu = page.findChild(SequenceContextMenu)
            assert menu and menu.isVisible()
            assert all(page.step_tree.topLevelItem(i).checkState(0) == Qt.Unchecked for i in range(8))
            next(action for action in menu.actions() if action.text() == '全选').trigger()
            assert all(page.step_tree.topLevelItem(i).checkState(0) == Qt.Checked for i in range(8))
            dispose_menu(menu)
            assert not shiboken6.isValid(menu)
        with patch.object(page, '_can_debug_tests', return_value=False):
            page._test_context_menu(page.step_tree.rect().center())
            assert not page.findChildren(SequenceContextMenu)
    finally:
        page.begin_shutdown()
        for task in (page._prepare_task, page._plc_task, page._stats_task, page._recipe_match_task):
            task.shutdown()
        page.close()


def test_editor_menu_actions_are_freed_after_closing_and_copy_is_unchanged():
    page = ConfigTab.__new__(ConfigTab)
    QWidget.__init__(page)
    page.clipboard_data = None
    page.copy_node = Mock()
    page.step_tree = QTreeWidget(page)
    QVBoxLayout(page).addWidget(page.step_tree)
    item = QTreeWidgetItem(page.step_tree, ['item'])
    page.resize(640, 480)
    page.show()
    app.processEvents()
    try:
        pos = page.step_tree.visualItemRect(item).center()
        for _ in range(4):
            page.on_context_menu(pos)
            menu = page.findChild(SequenceContextMenu)
            assert menu
            next(action for action in menu.actions() if action.text().startswith('复制')).trigger()
            dispose_menu(menu)
            assert not page.findChildren(SequenceContextMenu)
        assert page.copy_node.call_count == 4
    finally:
        page.close()
