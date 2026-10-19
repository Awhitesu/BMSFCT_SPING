import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication

from ui.widgets.byte_grid_widget import ByteGridWidget


def test_context_menu_from_scroll_children_preserves_selection():
    global _app
    _app = QApplication.instance() or QApplication([])
    app = _app
    grid = ByteGridWidget()
    grid.setContextMenuPolicy(Qt.CustomContextMenu)
    grid.set_bytes(bytes(range(8)))
    grid.resize(600, 120)
    grid.show()
    app.processEvents()
    grid.set_selection(1, 3)
    positions = []
    grid.customContextMenuRequested.connect(positions.append)
    try:
        for child in (grid._blocks[2], grid.inner_widget, grid.scroll_area.viewport()):
            pos = QPoint(5, 5)
            global_pos = child.mapToGlobal(pos)
            event = QContextMenuEvent(QContextMenuEvent.Mouse, pos, global_pos)
            QApplication.sendEvent(child, event)
            assert positions[-1] == grid.mapFromGlobal(global_pos)
        assert len(positions) == 3
        assert grid.get_selected_range() == (1, 3, b'\x01\x02\x03')
    finally:
        grid.close()
        grid.deleteLater()
