# -*- coding: utf-8 -*-
"""离屏渲染 MainWindow 布局预览图（跳过启动自检弹窗）。"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QDialog


class _FakeStartupDialog:
    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return QDialog.Accepted


def main():
    app = QApplication(sys.argv)
    print("app created", flush=True)
    try:
        import ui.dialogs.startup_dialog as startup_dialog
        startup_dialog.StartupCheckDialog = _FakeStartupDialog
        from ui.main_window import MainWindow
        print("imported", flush=True)
        w = MainWindow()
        print("constructed", flush=True)
        w.resize(1600, 950)
        w.show()
        app.processEvents()
        ok = w.grab().save("tests/main_window_preview.png")
        print("saved:", ok, flush=True)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
