from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import QStyledItemDelegate


class TestStatusDelegate(QStyledItemDelegate):
    """Keep result colors legible even when the row is selected or hovered."""

    COLORS = {
        'PASS': '#28A745', 'OK': '#28A745',
        'FAIL': '#DC3545', 'NG': '#DC3545',
        'RUNNING': '#FFC107',
    }

    def paint(self, painter, option, index):
        text = str(index.data(Qt.DisplayRole) or '')
        color = self.COLORS.get(text.upper())
        if color is None:
            super().paint(painter, option, index)
            return
        painter.save()
        try:
            painter.fillRect(option.rect, QColor(color))
            font = QFont(option.font)
            available = max(1, option.rect.width() - 8)
            required = QFontMetricsF(font).horizontalAdvance(text)
            if required > available:
                if font.pixelSize() > 0:
                    font.setPixelSize(max(1, int(font.pixelSize() * available / required)))
                else:
                    font.setPointSizeF(max(1, font.pointSizeF() * available / required))
            painter.setFont(font)
            painter.setPen(QColor('#000000'))
            painter.drawText(option.rect.adjusted(4, 0, -4, 0), Qt.AlignCenter, text)
        finally:
            painter.restore()
