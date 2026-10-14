"""
单元测试：交互式可视化字节流拖拽选区组件 (ByteGridWidget)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from ui.widgets.byte_grid_widget import ByteGridWidget

app = QApplication.instance() or QApplication(sys.argv)


class TestByteGridWidget(unittest.TestCase):

    def setUp(self):
        self.widget = ByteGridWidget()

    def tearDown(self):
        self.widget.close()

    def test_set_hex_string_and_blocks_count(self):
        # 62 01 01 0E 10 共 5 个字节
        hex_text = "62 01 01 0E 10"
        self.widget.set_hex_string(hex_text)
        self.assertEqual(len(self.widget._blocks), 5)
        self.assertEqual(self.widget._blocks[0].lbl_hex.text(), "62")
        self.assertEqual(self.widget._blocks[3].lbl_hex.text(), "0E")
        self.assertEqual(self.widget._blocks[4].lbl_hex.text(), "10")

    def test_selection_and_signal_emission(self):
        hex_text = "62 F1 90 4C 46 56"
        self.widget.set_hex_string(hex_text)

        received = []
        self.widget.range_selected.connect(lambda off, l, data: received.append((off, l, data)))

        # 选中从第 3 字节开始，长度 2 (4C 46)
        self.widget.set_selection(3, 2)

        self.assertEqual(len(received), 1)
        offset, length, chunk = received[0]
        self.assertEqual(offset, 3)
        self.assertEqual(length, 2)
        self.assertEqual(chunk, bytes.fromhex("4C 46"))

        # 检查方块的高亮状态
        self.assertFalse(self.widget._blocks[0].is_selected)
        self.assertFalse(self.widget._blocks[2].is_selected)
        self.assertTrue(self.widget._blocks[3].is_selected)
        self.assertTrue(self.widget._blocks[4].is_selected)
        self.assertFalse(self.widget._blocks[5].is_selected)

    def test_selection_overflow_safe_boundary(self):
        self.widget.set_hex_string("62 01 01")
        # 试图选中超出范围的索引
        self.widget.set_selection(2, 10)
        offset, length, chunk = self.widget.get_selected_range()
        self.assertEqual(offset, 2)
        self.assertEqual(length, 1) # 实际只剩 1 字节
        self.assertEqual(chunk, b"\x01")


if __name__ == "__main__":
    unittest.main()
