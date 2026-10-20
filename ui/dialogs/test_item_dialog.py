from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                QLineEdit, QPushButton, QComboBox)


class TestItemDialog(QDialog):
    """
    测试项编辑对话框（单通道版）。
    """
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle("编辑测试项 (判定条件)")
        self.setFixedSize(450, 600)
        self.setStyleSheet("""
            QDialog { background-color: #1F1F35; color: white; }
            QLabel { font-size: 13px; color: #B0B0B0; margin-top: 5px; }
            QComboBox, QLineEdit { 
                background-color: #16213E; 
                border: 1px solid #0F3460; 
                border-radius: 4px; 
                padding: 6px;
                color: white;
                font-size: 13px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(15, 15, 15, 15)
        
        layout.addWidget(QLabel("测试项名称:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如: 放电电量测试")
        layout.addWidget(self.name_edit)
        
        # 1. 增加标准类型判定
        layout.addWidget(QLabel("标准类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["数值", "字符串", "变量", "不判断"])
        layout.addWidget(self.type_combo)
        
        # 2. 判定下限/期望值
        self.lbl_min = QLabel("判定下限 (Min):")
        layout.addWidget(self.lbl_min)
        self.min_edit = QLineEdit()
        self.min_edit.setPlaceholderText("请输入下限数值...")
        layout.addWidget(self.min_edit)
        self.variable_combo=QComboBox()
        self.variable_combo.setEditable(True)
        self.variable_combo.setPlaceholderText('选择或输入期望变量名')
        tree=getattr(parent,'step_tree',None) or getattr(parent,'tree',None) or getattr(parent,'recipe_tree',None)
        if tree is None and parent is not None:
            from PySide6.QtWidgets import QTreeWidget
            trees=parent.findChildren(QTreeWidget)
            tree=trees[0] if trees else None
        names=set()
        if tree is not None:
            from core.uds_field_write import decode
            import re
            for i in range(tree.topLevelItemCount()):
                item=tree.topLevelItem(i)
                for j in range(item.childCount()):
                    match=re.search(r'WRITE64:([^\s]+)',item.child(j).text(2))
                    if match:
                        try:
                            name=decode(match[1]).get('variable','')
                            if name:names.add(name)
                        except (ValueError,TypeError):pass
        from ui.widgets.variable_step_form import catalog
        names.update(catalog(parent)[1] if parent is not None else set())
        self.variable_combo.addItems(sorted(names));self.variable_combo.setCurrentIndex(-1)
        layout.addWidget(self.variable_combo)
        
        # 3. 判定上限
        self.lbl_max = QLabel("判定上限 (Max):")
        layout.addWidget(self.lbl_max)
        self.max_edit = QLineEdit()
        self.max_edit.setPlaceholderText("请输入上限数值...")
        layout.addWidget(self.max_edit)

        # 增加单位填写栏
        layout.addWidget(QLabel("单位 (Unit):"))
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("默认为 NULL (例如: V, A, Ah, ℃...)")
        layout.addWidget(self.unit_edit)

        # 4. 增加NG复测选择
        layout.addWidget(QLabel("NG 复测选择:"))
        self.retry_combo = QComboBox()
        self.retry_combo.addItems(["不复测", "复测1次", "复测3次"])
        layout.addWidget(self.retry_combo)

        layout.addWidget(QLabel("停止策略:"))
        self.stop_combo = QComboBox()
        self.stop_combo.addItem("失败停止", "任何NG停止")
        self.stop_combo.addItem("失败继续", "NG继续")
        self.stop_combo.setToolTip("先执行复测；最终仍失败时，停止当前测试序列或保留FAIL并执行下一个父工步。")
        layout.addWidget(self.stop_combo)

        # 绑定类型切换逻辑
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        
        # 填充数据
        if data:
            self.name_edit.setText(data.get('name', ''))
            self.type_combo.setCurrentText(data.get('standard_type', '数值'))
            self.min_edit.setText(str(data.get('min', '') if data.get('min') is not None and data.get('min') != "--" else ''))
            self.max_edit.setText(str(data.get('max', '') if data.get('max') is not None and data.get('max') != "--" else ''))
            self.unit_edit.setText(data.get('unit', '') if data.get('unit') is not None and data.get('unit') != "NULL" else '')
            self.retry_combo.setCurrentText(data.get('retry_count', '不复测'))
            strategy = data.get('strategy', '任何NG停止')
            if strategy == '关键NG停止':
                # Keep the legacy token and its existing engine behavior.
                self.stop_combo.setItemData(1, strategy)
            self.stop_combo.setCurrentIndex(max(0, self.stop_combo.findData(strategy)))
            if data.get('standard_type')=='变量':self.variable_combo.setCurrentText(str(data.get('min','')))
        self.on_type_changed(self.type_combo.currentText())
        
        layout.addStretch()
        
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setStyleSheet("background-color: #28A745; min-height: 32px; font-weight: bold; color: white; border: none; border-radius: 4px;")
        self.btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_ok)
        
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setStyleSheet("background-color: #6C757D; min-height: 32px; color: white; border: none; border-radius: 4px;")
        btn_layout.addWidget(self.btn_cancel)
        self.btn_cancel.clicked.connect(self.reject)
        layout.addLayout(btn_layout)

    def on_type_changed(self, text):
        self.variable_combo.setVisible(text=='变量')
        if text == '变量':
            self.lbl_min.setText('期望字符串变量（运行时精确匹配）:')
            self.lbl_min.show();self.min_edit.hide();self.lbl_max.hide();self.max_edit.hide()
        elif text == "字符串":
            self.lbl_min.setText("期望字符串 (精确匹配):")
            self.min_edit.setPlaceholderText("请输入期待的精确匹配字符串，例如 PASS...")
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
            self.lbl_min.setText("判定下限 (Min):")
            self.min_edit.setPlaceholderText("请输入下限数值...")
            self.lbl_min.show()
            self.min_edit.show()
            self.lbl_max.show()
            self.max_edit.show()

    def get_data(self):
        is_var=self.type_combo.currentText()=='变量'
        is_str = self.type_combo.currentText() in ("字符串","变量")
        is_none = self.type_combo.currentText() == "不判断"
        mn = "" if is_none else self.min_edit.text().strip()
        if is_var:mn=self.variable_combo.currentText().strip()
        mx = "" if (is_str or is_none) else self.max_edit.text().strip()
        unit_val = self.unit_edit.text().strip()
        
        return {
            'name': self.name_edit.text().strip(),
            'min': mn if mn else "--",
            'max': mx if mx else "--",
            'standard_type': self.type_combo.currentText(),
            'retry_count': self.retry_combo.currentText(),
            'strategy': self.stop_combo.currentData(),
            'unit': unit_val if unit_val else "NULL"
        }
