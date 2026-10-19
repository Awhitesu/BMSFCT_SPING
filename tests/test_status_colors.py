import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem

from ui.widgets.test_status_delegate import TestStatusDelegate as StatusDelegate


@pytest.mark.parametrize('text,color', [('NG', '#DC3545'), ('FAIL', '#DC3545'),
    ('OK', '#28A745'), ('PASS', '#28A745'), ('RUNNING', '#FFC107')])
@pytest.mark.parametrize('state', [QStyle.State_None, QStyle.State_Selected, QStyle.State_MouseOver])
def test_status_color_survives_selection_and_hover(text, color, state):
    app = QApplication.instance() or QApplication([])
    tree = QTreeWidget()
    tree.setColumnCount(7)
    tree.setStyleSheet('QTreeWidget::item:selected {background: blue; color: cyan;}')
    item = QTreeWidgetItem(tree)
    item.setText(6, text)
    delegate = StatusDelegate(tree)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 140, 40)
    option.state = state
    option.font = app.font()
    image = QImage(140, 40, QImage.Format_ARGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    delegate.paint(painter, option, tree.indexFromItem(item, 6))
    painter.end()
    assert image.pixelColor(2, 2).name().upper() == color
    assert any(image.pixelColor(x, y) == QColor('black')
               for x in range(20, 120) for y in range(5, 35))
    tree.close()
