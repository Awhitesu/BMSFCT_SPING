# -*- coding: utf-8 -*-
"""离屏渲染 OverviewTab 布局预览图。"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication


def main():
    app = QApplication(sys.argv)
    print("app created", flush=True)
    try:
        from ui.tabs.overview_tab import OverviewTab
        print("imported", flush=True)
        w = OverviewTab()
        print("constructed", flush=True)
        w.resize(1600, 900)
        w.show()
        app.processEvents()
        print("shown", flush=True)
        img = w.grab()
        ok = img.save("tests/overview_layout_preview.png")
        print("saved:", ok, flush=True)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
