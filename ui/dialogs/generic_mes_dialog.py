"""Explicit editing of config/generic_mes.json, separate from the master switch."""
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox,
                              QScrollArea, QVBoxLayout)

from core.generic_mes import load_file, save_file
from ui.widgets.generic_mes_settings import GenericMesSettings


class GenericMesDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle('锐能MES参数配置')
        self.resize(980, 560)
        self.setMinimumSize(760, 480)
        layout = QVBoxLayout(self)
        file_label = QLabel(f'配置文件：{path}')
        file_label.setWordWrap(True)
        layout.addWidget(file_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.settings = GenericMesSettings()
        scroll.setWidget(self.settings)
        layout.addWidget(scroll, 1)
        self.settings.save_requested.connect(self._save)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if path.exists():
            try:
                self.settings.set_settings(load_file(path, validate=False))
            except Exception as exc:
                QMessageBox.warning(self, '锐能MES配置读取失败', str(exc))

    def _save(self, settings):
        try:
            save_file(settings, self.path)
        except Exception as exc:
            QMessageBox.warning(self, '锐能MES配置未保存', str(exc))
            return
        QMessageBox.information(self, '保存成功', f'锐能MES参数已保存到：\n{self.path}\n下次启用锐能MES并扫码时生效。')
        self.accept()
