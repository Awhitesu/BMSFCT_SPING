# -*- coding: utf-8 -*-
"""离屏渲染 SettingsTab 布局预览图。"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication


def main():
    app = QApplication(sys.argv)
    try:
        from ui.tabs.settings_tab import SettingsTab
        w = SettingsTab()
        w.set_dedup_checked(True)
        w.set_reset_enabled(True)
        w.resize(1200, 700)
        w.show()
        app.processEvents()
        ok = w.grab().save("tests/settings_tab_preview.png")
        print("saved:", ok, flush=True)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
