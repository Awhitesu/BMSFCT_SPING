"""同步显示结果，按像素连续跟随最新测试行。"""
from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget


class TestSequenceTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        bar = self.verticalScrollBar()
        self._scroll_target = None
        self._scroll_clock = QElapsedTimer()
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.PreciseTimer)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._advance_scroll)
        bar.sliderPressed.connect(self.stop_auto_scroll)
        bar.actionTriggered.connect(lambda _action: self.stop_auto_scroll())

    def stop_auto_scroll(self):
        timer = getattr(self, '_scroll_timer', None)
        if timer is not None:
            timer.stop()
        self._scroll_target = None

    def _advance_scroll(self):
        """每帧追踪唯一的最新目标，不为工步排队播放动画。"""
        if self._scroll_target is None:
            self.stop_auto_scroll()
            return
        bar = self.verticalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), self._scroll_target))
        distance = target - bar.value()
        if not self.isVisible():
            bar.setValue(target)
            self.stop_auto_scroll()
            return
        elapsed = min(32, max(1, self._scroll_clock.restart()))
        # 约每 12ms 消除一半余量；新目标沿用同一帧时钟，不反复重启动画。
        # 移动距离较大时，每帧保留至少半屏内容，避免视口整页替换。
        limit = max(1, self.viewport().height() // 2)
        step = min(abs(distance), limit, max(1, round(abs(distance) * (1 - 2 ** (-elapsed / 12)))))
        bar.setValue(bar.value() + (step if distance > 0 else -step))
        if bar.value() == target:
            self.stop_auto_scroll()

    def clear(self):
        self.stop_auto_scroll()
        super().clear()

    def scrollToTop(self):
        self.stop_auto_scroll()
        super().scrollToTop()

    def wheelEvent(self, event):
        self.stop_auto_scroll()
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        self.stop_auto_scroll()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_auto_scroll()
        super().closeEvent(event)

    def updateGeometries(self):
        previous_offset = self.verticalScrollBar().value()
        super().updateGeometries()
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        # 预留末尾空间，支持调试时直接定位首个执行项；一屏能显示时不增加滚动。
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
                # Qt 会先按默认范围钳制位置，恢复留白范围后也恢复原视口。
                if previous_offset > bar.value():
                    bar.setValue(previous_offset)

    def show_running_item(self, item, *, animated=True):
        """启动时直接定位；运行时让当前行连续跟随在视口约七成处。"""
        if not animated:
            self.stop_auto_scroll()
        rect = self.visualItemRect(item)
        if rect.isEmpty():
            return
        bar = self.verticalScrollBar()
        offset = self._scroll_target if self._scroll_target is not None else bar.value()
        target_rect = rect.translated(0, bar.value() - offset)
        height = self.viewport().height()
        if not animated and rect.top() >= 0 and rect.bottom() < height:
            return
        row_height = max(1, rect.height())
        anchor = min(max(0, height - row_height), int(height * .7) // row_height * row_height)
        if animated and 0 <= target_rect.top() <= anchor:
            return
        target = bar.value() + rect.top() - (anchor if animated else 0)
        if target > bar.maximum():
            # 重置结果后 Qt 可能尚未恢复末尾留白范围。
            self.updateGeometries()
            target = bar.value() + self.visualItemRect(item).top() - (anchor if animated else 0)
        target = max(bar.minimum(), min(bar.maximum(), target))
        if not animated or not self.isVisible():
            self.stop_auto_scroll()
            bar.setValue(target)
        elif target == bar.value():
            self.stop_auto_scroll()
        else:
            self._scroll_target = target
            if not self._scroll_timer.isActive():
                self._scroll_clock.start()
                self._scroll_timer.start()
