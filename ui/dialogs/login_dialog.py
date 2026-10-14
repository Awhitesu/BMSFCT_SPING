from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class LoginDialog(QDialog):
    """系统启动登录窗口。

    用户和权限都来自 DBManager，登录通过后只暴露已验证的用户名，
    不在界面层保存或复制密码。
    """

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.selected_user = None

        self.setWindowTitle("系统登录")
        self.setModal(True)
        self.setMinimumSize(440, 300)
        self.resize(480, 330)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._init_ui()
        self._load_users()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        title = QLabel("APP刷写系统")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 24px; font-weight: 700; color: #f8fafc; "
            "letter-spacing: 2px; padding: 4px;"
        )
        layout.addWidget(title)

        subtitle = QLabel("请登录后继续设备自检")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #94a3b8; font-size: 13px;")
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        user_row = QHBoxLayout()
        user_label = QLabel("用户名")
        user_label.setMinimumWidth(64)
        self.combo_user = QComboBox()
        self.combo_user.setMinimumHeight(36)
        user_row.addWidget(user_label)
        user_row.addWidget(self.combo_user, 1)
        layout.addLayout(user_row)

        password_row = QHBoxLayout()
        password_label = QLabel("密　码")
        password_label.setMinimumWidth(64)
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        self.edit_pwd.setPlaceholderText("请输入登录密码")
        self.edit_pwd.setMinimumHeight(36)
        self.edit_pwd.setClearButtonEnabled(True)
        self.edit_pwd.returnPressed.connect(self.handle_login)
        password_row.addWidget(password_label)
        password_row.addWidget(self.edit_pwd, 1)
        layout.addLayout(password_row)

        self.lbl_error = QLabel("")
        self.lbl_error.setAlignment(Qt.AlignCenter)
        self.lbl_error.setStyleSheet("color: #ef4444; font-size: 13px;")
        self.lbl_error.setMinimumHeight(20)
        layout.addWidget(self.lbl_error)

        button_row = QHBoxLayout()
        self.btn_cancel = QPushButton("退出")
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_login = QPushButton("登录并开始自检")
        self.btn_login.setProperty("buttonRole", "primary")
        self.btn_login.setMinimumHeight(38)
        self.btn_login.setDefault(True)
        self.btn_login.clicked.connect(self.handle_login)

        button_row.addWidget(self.btn_cancel)
        button_row.addWidget(self.btn_login, 2)
        layout.addLayout(button_row)

    def _load_users(self):
        self.combo_user.clear()
        try:
            users = self.db_manager.get_all_users_with_role()
        except Exception:
            users = []

        role_names = {"admin": "管理员", "engineer": "工程师", "operator": "操作员"}
        for user in users:
            username = user.get("username", "").strip()
            if not username:
                continue
            role = (user.get("role") or "operator").lower()
            self.combo_user.addItem(
                f"{username}  ·  {role_names.get(role, role)}",
                username,
            )

        if self.combo_user.count() == 0:
            self.lbl_error.setText("未找到可用账号，请联系管理员")
            self.btn_login.setEnabled(False)
        else:
            self.edit_pwd.setFocus()

    def handle_login(self):
        username = self.combo_user.currentData()
        password = self.edit_pwd.text()

        if not username:
            self.lbl_error.setText("请选择用户")
            return
        if not password:
            self.lbl_error.setText("请输入密码")
            self.edit_pwd.setFocus()
            return

        self.btn_login.setEnabled(False)
        try:
            verified = self.db_manager.verify_user(username, password)
        except Exception as exc:
            QMessageBox.critical(self, "登录失败", f"无法验证用户：{exc}")
            verified = False
        finally:
            self.btn_login.setEnabled(True)

        if verified:
            self.selected_user = username
            self.edit_pwd.clear()
            self.accept()
            return

        self.lbl_error.setText("用户名或密码错误")
        self.edit_pwd.selectAll()
        self.edit_pwd.setFocus()
