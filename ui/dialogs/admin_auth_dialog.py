import sys
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QMessageBox
)
from PySide6.QtCore import Qt

class AdminAuthDialog(QDialog):
    """
    管理员身份授权验证对话框：用于操作员执行敏感操作（如停止工步）时的二次鉴权。
    """
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.authorized = False

        self.setWindowTitle("管理员权限二次授权")
        self.setFixedSize(380, 220)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1F31;
                color: #FFFFFF;
                font-family: 'Microsoft YaHei', sans-serif;
            }
            QLabel {
                color: #E0E0E0;
                font-size: 13px;
            }
            QLineEdit, QComboBox {
                background-color: #292942;
                color: #FFFFFF;
                border: 1px solid #4D4D70;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #00FFCC;
            }
            QPushButton {
                background-color: #3A3A5A;
                color: #FFFFFF;
                border: 1px solid #555577;
                border-radius: 4px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4A4A72;
                border: 1px solid #00FFCC;
            }
        """)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        tip_lbl = QLabel("⚠️ 本操作涉及停止运行中的老化工步，需管理员授权：")
        tip_lbl.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 12px;")
        tip_lbl.setWordWrap(True)
        layout.addWidget(tip_lbl)

        # 管理员账户选择
        row1 = QHBoxLayout()
        lbl_user = QLabel("管理员账号:")
        lbl_user.setFixedWidth(85)
        self.combo_admin = QComboBox()
        
        # 筛选管理员
        all_users = self.db_manager.get_all_users_with_role() if hasattr(self.db_manager, 'get_all_users_with_role') else []
        admins = [u["username"] for u in all_users if u.get("role") == "admin"]
        if "ADMIN" not in admins:
            admins.insert(0, "ADMIN")
        self.combo_admin.addItems(admins)
        
        row1.addWidget(lbl_user)
        row1.addWidget(self.combo_admin)
        layout.addLayout(row1)

        # 密码输入
        row2 = QHBoxLayout()
        lbl_pwd = QLabel("管理员密码:")
        lbl_pwd.setFixedWidth(85)
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        self.edit_pwd.setPlaceholderText("请输入管理员密码")
        row2.addWidget(lbl_pwd)
        row2.addWidget(self.edit_pwd)
        layout.addLayout(row2)

        layout.addStretch()

        # 按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("确认授权并停止")
        btn_ok.setStyleSheet("background-color: #DC3545; color: white; border: none;")
        btn_ok.clicked.connect(self._on_confirm)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def _on_confirm(self):
        admin_user = self.combo_admin.currentText().strip()
        pwd = self.edit_pwd.text().strip()
        if not pwd:
            QMessageBox.warning(self, "提示", "请输入管理员密码！")
            return

        if self.db_manager.verify_user(admin_user, pwd):
            self.authorized = True
            self.accept()
        else:
            QMessageBox.warning(self, "验证失败", "管理员密码错误，授权未通过！")
            self.edit_pwd.clear()
            self.edit_pwd.setFocus()
