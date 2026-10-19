from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QComboBox, QCheckBox, QGroupBox)


class BulkEditDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量修改测试项参数")
        self.setMinimumWidth(400)
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请勾选需要统一批量修改的测试项参数："))
        
        # 0. 测试项名称
        self.cb_name = QCheckBox("统一修改测试项名称")
        layout.addWidget(self.cb_name)
        
        self.name_widget = QGroupBox("名称设置")
        name_layout = QVBoxLayout(self.name_widget)
        
        h_name = QHBoxLayout()
        h_name.addWidget(QLabel("新名称前缀:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如: 主板电流测试")
        h_name.addWidget(self.name_edit)
        name_layout.addLayout(h_name)
        
        self.cb_name_inc = QCheckBox("后缀数字双位递增 (如: 前缀_01, 前缀_02...)")
        self.cb_name_inc.setChecked(True)
        name_layout.addWidget(self.cb_name_inc)
        
        self.name_widget.setEnabled(False)
        self.cb_name.toggled.connect(self.name_widget.setEnabled)
        layout.addWidget(self.name_widget)
        
        # 1. 判定范围
        self.cb_range = QCheckBox("统一修改判定范围 / 期望值")
        layout.addWidget(self.cb_range)
        
        self.range_widget = QGroupBox("期望范围配置")
        range_layout = QVBoxLayout(self.range_widget)
        
        h_type = QHBoxLayout()
        h_type.addWidget(QLabel("标准类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["数值", "字符串", "不判断"])
        h_type.addWidget(self.type_combo)
        range_layout.addLayout(h_type)
        
        h_min = QHBoxLayout()
        self.lbl_min = QLabel("下限 (Min):")
        h_min.addWidget(self.lbl_min)
        self.min_edit = QLineEdit()
        self.min_edit.setPlaceholderText("请输入下限数值...")
        h_min.addWidget(self.min_edit)
        range_layout.addLayout(h_min)
        
        h_max = QHBoxLayout()
        self.lbl_max = QLabel("上限 (Max):")
        h_max.addWidget(self.lbl_max)
        self.max_edit = QLineEdit()
        self.max_edit.setPlaceholderText("请输入上限数值...")
        h_max.addWidget(self.max_edit)
        range_layout.addLayout(h_max)
        
        self.range_widget.setEnabled(False)
        self.cb_range.toggled.connect(self.range_widget.setEnabled)
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        layout.addWidget(self.range_widget)
        
        # 2. 单位
        self.cb_unit = QCheckBox("统一修改单位 (Unit)")
        layout.addWidget(self.cb_unit)
        
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("例如: V, A, Ah, ℃... (留空表示 NULL)")
        self.unit_edit.setEnabled(False)
        self.cb_unit.toggled.connect(self.unit_edit.setEnabled)
        layout.addWidget(self.unit_edit)
        
        # 3. NG复测
        self.cb_retry = QCheckBox("统一修改 NG 复测选择")
        layout.addWidget(self.cb_retry)
        
        self.retry_combo = QComboBox()
        self.retry_combo.addItems(["不复测", "复测1次", "复测3次"])
        self.retry_combo.setEnabled(False)
        self.cb_retry.toggled.connect(self.retry_combo.setEnabled)
        layout.addWidget(self.retry_combo)
        
        layout.addSpacing(10)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("执行批量修改")
        btn_ok.clicked.connect(self.accept)
        btn_ok.setStyleSheet("background-color: #28A745; color: white; font-weight: bold; height: 32px; border: none; border-radius: 4px;")
        
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setStyleSheet("background-color: #6C757D; color: white; height: 32px; border: none; border-radius: 4px;")
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def on_type_changed(self, text):
        if text == "字符串":
            self.lbl_min.setText("期望值 (精确匹配):")
            self.min_edit.setPlaceholderText("请输入期待的字符串，例如 PASS...")
            self.lbl_min.show()
            self.min_edit.show()
            self.lbl_max.hide()
            self.max_edit.hide()
        elif text == "不判断":
            self.lbl_min.hide()
            self.min_edit.hide()
            self.lbl_max.hide()
            self.max_edit.hide()
        else:
            self.lbl_min.setText("下限 (Min):")
            self.min_edit.setPlaceholderText("请输入下限数值...")
            self.lbl_min.show()
            self.min_edit.show()
            self.lbl_max.show()
            self.max_edit.show()

    def get_config(self):
        is_str = self.type_combo.currentText() == "字符串"
        is_none = self.type_combo.currentText() == "不判断"
        mn = "" if is_none else self.min_edit.text().strip()
        mx = "" if (is_str or is_none) else self.max_edit.text().strip()
        
        return {
            "change_name": self.cb_name.isChecked(),
            "name_prefix": self.name_edit.text().strip(),
            "name_inc": self.cb_name_inc.isChecked(),
            "change_range": self.cb_range.isChecked(),
            "standard_type": self.type_combo.currentText(),
            "min": mn,
            "max": mx,
            "change_unit": self.cb_unit.isChecked(),
            "unit": self.unit_edit.text().strip(),
            "change_retry": self.cb_retry.isChecked(),
            "retry_count": self.retry_combo.currentText()
        }
