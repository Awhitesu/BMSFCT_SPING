import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def test_overview_columns_controls_and_long_recipe_fit(tmp_path):
    for name in ('msyh.ttc', 'msyhbd.ttc', 'consola.ttf'):
        path = Path('C:/Windows/Fonts') / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    original_font = app.font()
    app.setFont(QFont('Microsoft YaHei', 10))
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    for i in range(120):
        row = QTreeWidgetItem(page.step_tree, [str(i+1), 'UDS Read VIN / current',
                              '100', '200', '0123456789ABCDEF', 'mA', 'RUNNING'])
        row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
        row.setCheckState(0, Qt.Checked)
        QTreeWidgetItem(row, ['1', 'Long child ' * 30, '', '', '123.456', '', 'PASS'])
    page.step_tree.expandAll()
    page.lbl_product_model.setText('Project_CANFD_UDS_' * 20)
    artifacts = Path('logs/layout')
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        for width, height in ((1920, 950), (1366, 650), (1280, 600), (1024, 600)):
            page.resize(width, height)
            page.show()
            for _ in range(8):
                app.processEvents()
            tree = page.step_tree
            assert sum(tree.columnWidth(c) for c in range(7)) == tree.viewport().width()
            assert not tree.horizontalScrollBar().isVisible()
            assert not page._layout_scroll.horizontalScrollBar().isVisible()
            assert not page._layout_scroll.verticalScrollBar().isVisible()
            weights = [1,3,2,2,2,1,1.5]
            for col, weight in enumerate(weights):
                assert abs(tree.columnWidth(col) - tree.viewport().width() * weight / sum(weights)) < 1
            controls = [page.top_title_frame, page.lbl_product_model, page.lbl_timer,
                        page.lbl_big_status, page.yield_stats_panel, page.txt_sn, page.btn_run_test]
            for first, second in zip(controls, controls[1:]):
                first_bottom = first.mapTo(page.right_panel, first.rect().bottomLeft()).y()
                second_top = second.mapTo(page.right_panel, second.rect().topLeft()).y()
                assert first_bottom < second_top
            page.grab().save(str(artifacts / f'overview_verified_{width}.png'))
    finally:
        page.begin_shutdown()
        page.close()
        page.deleteLater()
        app.setFont(original_font)
