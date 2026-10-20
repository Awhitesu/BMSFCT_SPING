"""配方独立使能的上位机配置与重启参数。"""
from pathlib import Path
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QSizePolicy, QPushButton, QFileDialog, QCheckBox
from core.recipe_app_flash import validate_app_flash


class RecipeAppFlashForm(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('recipeAppFlashForm')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.enable_sync = QCheckBox('启用上位机配置与重启')
        self.enable_sync.setToolTip('随当前配方保存；未勾选时不停止进程、不写入 config.ini、不启动上位机。')
        layout.addWidget(self.enable_sync)
        self.parameters = QWidget()
        fields = QVBoxLayout(self.parameters)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(8)
        layout.addWidget(self.parameters)
        self.enable_sync.toggled.connect(self.parameters.setVisible)
        self.parameters.hide()
        self.host_path = QLineEdit()
        self.host_path.setPlaceholderText('上位机程序文件路径')
        self.host_path.setFixedHeight(36)
        self.browse_host = QPushButton('选择文件…')
        self.browse_host.setAutoDefault(False)
        self.browse_host.setFixedHeight(36)
        self.browse_host.clicked.connect(self._select_host_file)
        host_row = QHBoxLayout()
        host_row.setSpacing(15)
        host_row.addWidget(QLabel('上位机路径:'))
        host_row.addWidget(self.host_path, 1)
        host_row.addWidget(self.browse_host)
        fields.addLayout(host_row)
        self.model = QComboBox()
        self.flash_path = QComboBox()
        for combo in (self.model, self.flash_path):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.NoInsert)
            combo.setDuplicatesEnabled(False)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            combo.setFixedHeight(42)
            combo.lineEdit().editingFinished.connect(lambda current=combo: self._remember(current))
            combo.view().setTextElideMode(Qt.ElideMiddle)
        self.model.lineEdit().setPlaceholderText('输入型号，回车或离开输入框后加入列表')
        self.flash_path.lineEdit().setPlaceholderText('输入刷写路径，回车或离开输入框后加入列表')
        choices_row = QHBoxLayout()
        choices_row.setSpacing(15)
        for label, field in (('App刷写型号:', self.model), ('App刷写路径:', self.flash_path)):
            half = QWidget()
            half.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            half_layout = QHBoxLayout(half)
            half_layout.setContentsMargins(0, 0, 0, 0)
            half_layout.setSpacing(15)
            half_layout.addWidget(QLabel(label))
            half_layout.addWidget(field, 1)
            choices_row.addWidget(half, 1)
        fields.addLayout(choices_row)
        self.restart_name = QLineEdit()
        self.restart_name.setPlaceholderText('例如 BMS_General.exe（与上位机路径中的文件名一致）')
        self.restart_name.setToolTip('用于停止进程；启动时使用上位机路径中的完整 exe 路径。')
        self.restart_name.setFixedHeight(36)
        restart_row = QHBoxLayout()
        restart_row.setSpacing(15)
        restart_row.addWidget(QLabel('重启上位机名称:'))
        restart_row.addWidget(self.restart_name, 1)
        fields.addLayout(restart_row)
        self.setStyleSheet('''
            QWidget#recipeAppFlashForm QLineEdit {
                min-height: 24px; padding: 5px 8px;
            }
            QWidget#recipeAppFlashForm QComboBox {
                min-height: 30px; padding: 5px 8px;
            }
            QWidget#recipeAppFlashForm QPushButton {
                min-height: 20px; padding: 6px 14px;
            }
            QWidget#recipeAppFlashForm QComboBox QLineEdit {
                min-height: 0px; padding: 0px; border: none; background: transparent;
            }
            QWidget#recipeAppFlashForm QComboBox::drop-down {
                width: 24px; border: none; border-left: 1px solid #334155;
            }
            QWidget#recipeAppFlashForm QComboBox::down-arrow {
                image: url("ARROW_PATH"); width: 12px; height: 12px;
            }
        '''.replace('ARROW_PATH', (Path(__file__).resolve().parents[2] / 'resources/combobox_down.svg').as_posix()))
        self.setToolTip('启用后，在测试界面选择型号时：停止并确认上位机 → 等待500ms → 写入并校验同级config.ini的[App] Type、Addr → 等待500ms → 启动并确认上位机。刷写工步不重复执行。')

    def _select_host_file(self):
        current = Path(os.path.expandvars(self.host_path.text().strip().strip('"')))
        start = current if current.exists() else (current.parent if current.parent.is_dir() else Path.cwd())
        file_path, _ = QFileDialog.getOpenFileName(self, '选择上位机程序文件', str(start),
                                                  '上位机程序 (*.exe *.vi);;所有文件 (*.*)')
        if file_path:
            self.host_path.setText(os.path.normpath(file_path))
            if not self.restart_name.text().strip() and Path(file_path).suffix.casefold() == '.exe':
                self.restart_name.setText(Path(file_path).name)

    @staticmethod
    def _remember(combo):
        value = combo.currentText().strip()
        index = combo.findText(value, Qt.MatchExactly | Qt.MatchCaseSensitive)
        if value and index < 0:
            combo.addItem(value)
            index = combo.count() - 1
            combo.setItemData(index, value, Qt.ToolTipRole)
        combo.setCurrentIndex(index if value else -1)
        combo.setEditText(value)

    def set_config(self, config):
        config = config or {}
        self.enable_sync.setChecked(config.get('enabled') is True)
        self.host_path.setText(config.get('host_path', ''))
        self.restart_name.setText(config.get('restart_name', ''))
        for combo, key, history in ((self.model, 'model', 'model_history'),
                                    (self.flash_path, 'flash_path', 'path_history')):
            combo.blockSignals(True)
            combo.clear()
            for value in config.get(history, []):
                if value and combo.findText(value, Qt.MatchExactly | Qt.MatchCaseSensitive) < 0:
                    combo.addItem(value)
                    combo.setItemData(combo.count() - 1, value, Qt.ToolTipRole)
            combo.setEditText(config.get(key, ''))
            self._remember(combo)
            combo.blockSignals(False)

    def get_config(self):
        for combo in (self.model, self.flash_path):
            self._remember(combo)
        config = dict(enabled=self.enable_sync.isChecked(), restart_name=self.restart_name.text().strip(),
                      host_path=self.host_path.text().strip(), model=self.model.currentText().strip(),
                      flash_path=self.flash_path.currentText().strip(),
                      model_history=[self.model.itemText(i) for i in range(self.model.count())],
                      path_history=[self.flash_path.itemText(i) for i in range(self.flash_path.count())])
        validate_app_flash(config)
        return config
