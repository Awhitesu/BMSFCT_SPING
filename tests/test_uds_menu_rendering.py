import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMenu
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def test_uds_right_click_menu_is_opaque_and_changes_mode_without_losing_range():
    original = app.styleSheet()
    app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    dialog = StepDialog()
    try:
        dialog.category_combo.setCurrentText('报文交互')
        dialog.device_combo.setCurrentText('UDS 交互列表')
        dialog.uds_tabs.setCurrentIndex(3)
        dialog.resize(1150, 850)
        dialog.show()
        app.processEvents()
        grid = dialog.uds_byte_grid
        grid.set_bytes(bytes.fromhex('62 F1 03 42 41 54 32 03 00 00 42 43'))
        grid.set_selection(6, 4)
        captured = []
        errors = []
        def inspect_menu():
            menu = app.activePopupWidget()
            try:
                assert isinstance(menu, QMenu)
                picture = menu.grab()
                image = picture.toImage()
                ratio = picture.devicePixelRatio()
                assert image.pixelColor(int(5*ratio), int(5*ratio)).alpha() == 255
                assert image.pixelColor(int(5*ratio), int(5*ratio)).name() == '#151e2e'
                geometry = [menu.actionGeometry(action) for action in menu.actions()]
                assert all(rect.height() >= 25 for rect in geometry)
                assert all(first.bottom() < second.top() for first, second in zip(geometry, geometry[1:]))
                Path('logs/layout/video_172118').mkdir(parents=True, exist_ok=True)
                picture.save('logs/layout/video_172118/menu_verified.png')
                menu.actions()[2].trigger()  # BCD
                captured.append(True)
            except Exception as exc:
                errors.append(exc)
            finally:
                if menu is not None:
                    menu.close()
        QTimer.singleShot(0, inspect_menu)
        dialog._uds_selection_menu(QPoint(30, 30))
        assert not errors, errors
        assert captured
        assert dialog.uds_extract_mode.currentIndex() == 5
        assert dialog.uds_extract_offset.value() == 6
        assert dialog.uds_extract_len.value() == 4
        assert grid.get_selected_range() == (6, 4, bytes.fromhex('32 03 00 00'))
    finally:
        dialog.close()
        dialog.deleteLater()
        app.setStyleSheet(original)
