import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from ui.tabs.config_tab import ProportionalTreeWidget


class SequenceDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = ProportionalTreeWidget()
        self.tree.resize(800, 600)
        self.a, self.b = QTreeWidgetItem(["A"]), QTreeWidgetItem(["B"])
        self.tree.addTopLevelItems([self.a, self.b])
        self.nodes = [QTreeWidgetItem([str(i)]) for i in range(5)]
        self.a.addChildren(self.nodes)
        self.tree.expandAll()
        self.tree.show()
        self.app.processEvents()

    def tearDown(self):
        self.tree.close()

    def test_batch_move_preserves_order(self):
        self.tree._moving_nodes = self.nodes[1:3]
        self.tree._move_nodes(self.a, 5)
        self.assertEqual([self.a.child(i).text(0) for i in range(5)],
                         ["0", "3", "4", "1", "2"])
        self.tree._move_nodes(self.a, 0)
        self.assertEqual([self.a.child(i).text(0) for i in range(5)],
                         ["1", "2", "0", "3", "4"])

    def test_cross_item_move_and_depth(self):
        self.tree._moving_nodes = self.nodes[1:3]
        self.tree._move_nodes(self.b, 0)
        self.assertEqual(self.b.childCount(), 2)
        self.assertIs(self.nodes[1].parent(), self.b)
        self.assertEqual(self.nodes[1].childCount(), 0)
        self.assertTrue(self.nodes[1].isSelected())

    def test_item_cannot_drop_on_step(self):
        self.tree._moving_nodes = [self.b]
        pos = self.tree.visualItemRect(self.nodes[0]).center()
        self.assertIsNone(self.tree._destination(pos))

    def test_step_on_item_means_first_child(self):
        self.tree._moving_nodes = [self.nodes[0]]
        pos = self.tree.visualItemRect(self.b).center()
        self.assertEqual(self.tree._destination(pos), (self.b, 0))


if __name__ == "__main__":
    unittest.main()
