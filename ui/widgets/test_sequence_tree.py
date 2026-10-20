"""按当前视口可见行翻页，保留当前测试行之后的显示空间。"""
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget


class TestSequenceTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    def updateGeometries(self):
        previous_offset = self.verticalScrollBar().value()
        super().updateGeometries()
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        # 允许最后一页留白，使页首行能真正置顶；列表能一屏显示时不增加滚动。
        last = None
        for index in range(self.topLevelItemCount() - 1, -1, -1):
            candidate = self.topLevelItem(index)
            if not candidate.isHidden():
                last = candidate
                break
        while last is not None and last.isExpanded():
            child = next((last.child(index) for index in range(last.childCount() - 1, -1, -1)
                          if not last.child(index).isHidden()), None)
            if child is None:
                break
            last = child
        if last is not None:
            height = self.visualItemRect(last).height()
            if height > 0:
                bar.setMaximum(bar.maximum() + max(0, self.viewport().height() - height))
                # Qt 会先按默认范围钳制末页位置，恢复留白范围后也恢复页首。
                if previous_offset > bar.value():
                    bar.setValue(previous_offset)

    def show_running_item(self, item):
        """本页完整可见的行保持位置；越过页底/页首时，将当前行置顶。"""
        rect = self.visualItemRect(item)
        if rect.isEmpty():
            return
        if rect.top() < 0 or rect.bottom() >= self.viewport().height():
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + rect.top())
