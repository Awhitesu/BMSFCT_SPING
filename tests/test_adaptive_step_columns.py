import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from ui.widgets.compact_step_layout import adaptive_columns


def test_columns_follow_viewport_with_vertical_scrollbar_and_long_data():
    global _app
    _app = QApplication.instance() or QApplication([])
    app = _app
    table = QTableWidget(30, 10)
    adaptive_columns(table, [1.3, 1.8, 1.5, .8, 2, 1.4, .8, 1, .8, .8])
    table.setItem(0, 1, QTableWidgetItem('AA ' * 100))
    table.show()
    try:
        for width in (520, 900, 1400, 650):
            table.resize(width, 240)
            app.processEvents()
            assert sum(table.columnWidth(c) for c in range(10)) == table.viewport().width()
            assert not table.horizontalScrollBar().isVisible()
            assert table.columnWidth(4) > table.columnWidth(6)
    finally:
        table.close()
        table.deleteLater()
