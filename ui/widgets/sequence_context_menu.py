"""测试序列菜单使用无动画的本地绘制，避免远程桌面下原生高亮迟滞。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QStyleFactory


class SequenceContextMenu(QMenu):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._menu_style = QStyleFactory.create('Fusion')
        self._menu_style.setParent(self)
        self.setStyle(self._menu_style)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.aboutToHide.connect(self.deleteLater)
