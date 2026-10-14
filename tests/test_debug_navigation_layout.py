import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget,QStyleOptionViewItem
from ui.tabs.debug_tab import DebugTab

app=QApplication.instance() or QApplication([])


class DebugNavigationTests(unittest.TestCase):
    def setUp(self):
        self.page=DebugTab()
        for label,group in [('电池模拟器控制','电源与模拟器'),('DG2052 双通道信号发生器','测量与信号'),
                            ('CAN调试（PCAN / ZLG CANFDNET-400U）','通讯与控制'),('电阻板（6块）','板卡与继电器')]:
            self.page.add_debug_tab(QWidget(),label,group)

    def tearDown(self):self.page.close()

    def test_all_groups_initially_collapsed(self):
        self.assertTrue(all(not group.isExpanded() for group in self.page.groups.values()))
        self.assertEqual(self.page.pages.currentIndex(),0)

    def test_search_expands_and_clear_restores_previous_state(self):
        self.page.groups['电源与模拟器'].setExpanded(True)
        self.page.search.setText('DG2052')
        self.assertTrue(self.page.groups['测量与信号'].isExpanded())
        self.page.search.clear()
        self.assertTrue(self.page.groups['电源与模拟器'].isExpanded())
        self.assertFalse(self.page.groups['测量与信号'].isExpanded())

    def test_full_names_and_wrapping_are_preserved(self):
        self.assertEqual(self.page.navigation.textElideMode(),Qt.ElideNone)
        item=self.page.items[2]
        self.assertEqual(item.toolTip(0),item.text(0))
        index=self.page.navigation.indexFromItem(item)
        option=QStyleOptionViewItem();option.font=self.page.navigation.font()
        self.page.navigation.viewport().resize(500,500)
        wide=self.page.navigation.itemDelegate().sizeHint(option,index).height()
        self.page.navigation.viewport().resize(160,500)
        narrow=self.page.navigation.itemDelegate().sizeHint(option,index).height()
        self.assertGreater(narrow,wide)


if __name__=='__main__':unittest.main()
