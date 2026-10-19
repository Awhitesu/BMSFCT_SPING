
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QWidget, QMessageBox, QComboBox
)
from PySide6.QtCore import Qt

class UserAccountDialog(QDialog):
    """
    系统用户管理与切换弹窗：
    - 切换登录：所有身份均可切换账户
    - 删除账户：仅管理员权限可操作，下拉选择删除目标（内置超级管理员 ADMIN 严禁删除，当前登录账户不可删除）
    - 新增账户：仅管理员权限可操作，可指定账户权限（管理员/操作员）
    """
    def __init__(self, db_manager, current_user="ADMIN", parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.current_user = current_user
        self.logged_in_user = current_user
        self.current_role = self.db_manager.get_user_role(current_user)

        self.setWindowTitle("系统用户管理与权限控制")
        self.resize(520, 540)
        self.setMinimumSize(420, 420)
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
            QTabWidget::pane {
                border: 1px solid #3E3E5C;
                background-color: #24253B;
                border-radius: 6px;
                padding: 12px;
            }
            QTabBar::tab {
                background-color: #1F1F35;
                color: #A0A0B0;
                padding: 8px 18px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #24253B;
                color: #00FFCC;
                font-weight: bold;
                border-top: 2px solid #00FFCC;
            }
            QTabBar::tab:disabled {
                background-color: #171827;
                color: #555566;
            }
        """)

        self._init_ui()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)
        self._update_permission_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # 1. 顶部当前登录状态卡片
        self.lbl_current_info = QLabel()
        self.lbl_current_info.setTextFormat(Qt.RichText)
        self._refresh_header_text()
        main_layout.addWidget(self.lbl_current_info)

        # 2. 核心 Tab 导航
        self.tabs = QTabWidget()

        # --- Tab 1: 切换登录 ---
        tab_switch = QWidget()
        switch_layout = QVBoxLayout(tab_switch)
        switch_layout.setSpacing(12)

        row_sw1 = QHBoxLayout()
        lbl_sw_u = QLabel("选择账户:")
        lbl_sw_u.setFixedWidth(75)
        self.combo_switch_user = QComboBox()
        row_sw1.addWidget(lbl_sw_u)
        row_sw1.addWidget(self.combo_switch_user)
        switch_layout.addLayout(row_sw1)

        row_sw2 = QHBoxLayout()
        lbl_sw_p = QLabel("账户密码:")
        lbl_sw_p.setFixedWidth(75)
        self.edit_switch_pwd = QLineEdit()
        self.edit_switch_pwd.setEchoMode(QLineEdit.Password)
        self.edit_switch_pwd.setPlaceholderText("请输入密码")
        row_sw2.addWidget(lbl_sw_p)
        row_sw2.addWidget(self.edit_switch_pwd)
        switch_layout.addLayout(row_sw2)

        switch_layout.addStretch()

        btn_switch = QPushButton("确认切换登录")
        btn_switch.setStyleSheet("background-color: #0275D8; color: white; border: none; min-height: 32px;")
        btn_switch.clicked.connect(self._on_switch_login)
        switch_layout.addWidget(btn_switch)

        self.tabs.addTab(tab_switch, "切换登录")

        # --- Tab 2: 删除账户 (需管理员权限) ---
        tab_del = QWidget()
        del_layout = QVBoxLayout(tab_del)
        del_layout.setSpacing(12)

        del_tip = QLabel("⚠️ 提示：选择需要注销删除的账户，超级管理员 ADMIN 不可删除。")
        del_tip.setStyleSheet("color: #FFC107; font-size: 12px;")
        del_tip.setWordWrap(True)
        del_layout.addWidget(del_tip)

        row_del1 = QHBoxLayout()
        lbl_del_u = QLabel("目标账户:")
        lbl_del_u.setFixedWidth(75)
        self.combo_del_user = QComboBox()
        self.combo_del_user.currentIndexChanged.connect(self._on_del_combo_changed)
        row_del1.addWidget(lbl_del_u)
        row_del1.addWidget(self.combo_del_user)
        del_layout.addLayout(row_del1)

        self.lbl_del_target_role = QLabel("权限类型: --")
        self.lbl_del_target_role.setStyleSheet("color: #00FFCC; font-size: 12px; margin-left: 80px;")
        del_layout.addWidget(self.lbl_del_target_role)

        del_layout.addStretch()

        self.btn_del = QPushButton("确认删除选中账户")
        self.btn_del.setStyleSheet("background-color: #DC3545; color: white; border: none; min-height: 32px;")
        self.btn_del.clicked.connect(self._on_delete_account)
        del_layout.addWidget(self.btn_del)

        self.tabs.addTab(tab_del, "删除账户")

        # --- Tab 3: 新增账户 (需管理员权限) ---
        tab_add = QWidget()
        add_layout = QVBoxLayout(tab_add)
        add_layout.setSpacing(10)

        a_row1 = QHBoxLayout()
        a_lbl1 = QLabel("新用户名:")
        a_lbl1.setFixedWidth(75)
        self.edit_add_user = QLineEdit()
        self.edit_add_user.setPlaceholderText("请输入新账户名称")
        a_row1.addWidget(a_lbl1)
        a_row1.addWidget(self.edit_add_user)
        add_layout.addLayout(a_row1)

        a_row_role = QHBoxLayout()
        a_lbl_r = QLabel("账户权限:")
        a_lbl_r.setFixedWidth(75)
        self.combo_add_role = QComboBox()
        self.combo_add_role.addItems(["操作员", "管理员"])
        a_row_role.addWidget(a_lbl_r)
        a_row_role.addWidget(self.combo_add_role)
        add_layout.addLayout(a_row_role)

        a_row2 = QHBoxLayout()
        a_lbl2 = QLabel("初始密码:")
        a_lbl2.setFixedWidth(75)
        self.edit_add_pwd = QLineEdit()
        self.edit_add_pwd.setEchoMode(QLineEdit.Password)
        self.edit_add_pwd.setPlaceholderText("请输入初始密码")
        a_row2.addWidget(a_lbl2)
        a_row2.addWidget(self.edit_add_pwd)
        add_layout.addLayout(a_row2)

        a_row3 = QHBoxLayout()
        a_lbl3 = QLabel("确认密码:")
        a_lbl3.setFixedWidth(75)
        self.edit_add_pwd2 = QLineEdit()
        self.edit_add_pwd2.setEchoMode(QLineEdit.Password)
        self.edit_add_pwd2.setPlaceholderText("请再次输入密码")
        a_row3.addWidget(a_lbl3)
        a_row3.addWidget(self.edit_add_pwd2)
        add_layout.addLayout(a_row3)

        add_layout.addStretch()

        btn_add = QPushButton("创建新账户")
        btn_add.setStyleSheet("background-color: #28A745; color: white; border: none; min-height: 32px;")
        btn_add.clicked.connect(self._on_add_account)
        add_layout.addWidget(btn_add)

        self.tabs.addTab(tab_add, "新增账户")

        password_page = QWidget()
        password_layout = QVBoxLayout(password_page)
        self.edit_old_password = QLineEdit()
        self.edit_new_password = QLineEdit()
        self.edit_confirm_password = QLineEdit()
        for label, editor in (("当前密码", self.edit_old_password), ("新密码", self.edit_new_password), ("确认新密码", self.edit_confirm_password)):
            password_layout.addWidget(QLabel(label))
            editor.setEchoMode(QLineEdit.Password)
            password_layout.addWidget(editor)
        change_button = QPushButton("修改当前账号密码")
        change_button.clicked.connect(self._change_password)
        password_layout.addWidget(change_button)
        self.tabs.addTab(password_page, "修改密码")

        main_layout.addWidget(self.tabs)

        # 3. 底部状态与操作区
        self.lbl_perm_hint = QLabel()
        self.lbl_perm_hint.setStyleSheet("font-size: 12px;")
        main_layout.addWidget(self.lbl_perm_hint)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bottom_layout.addWidget(btn_close)
        main_layout.addLayout(bottom_layout)

        self._refresh_user_combos()

    def _refresh_header_text(self):
        is_admin = (self.current_role == "admin")
        role_tag = "超级管理员" if self.current_user == "ADMIN" and is_admin else ("管理员" if is_admin else "操作员")
        tag_color = "#00FFCC" if is_admin else "#FFC107"
        self.lbl_current_info.setText(
            f"当前已登录账户:  <span style='color:#FFFFFF; font-weight:bold; font-size:15px;'>{self.current_user}</span>"
            f"  <span style='background-color:#292942; color:{tag_color}; padding:2px 8px; border-radius:4px; font-weight:bold;'>[{role_tag}]</span>"
        )

    def _update_permission_ui(self):
        is_admin = (self.current_role == "admin")
        self._refresh_header_text()

        # 权限判定：仅管理员权限可操作“删除账户”与“新增账户”；操作员仅可切换账户
        self.tabs.setTabEnabled(1, is_admin)
        self.tabs.setTabEnabled(2, is_admin)

        if is_admin:
            self.tabs.setTabToolTip(1, "选择账户并执行删除")
            self.tabs.setTabToolTip(2, "创建包含管理员或操作员权限的新账户")
            self.lbl_perm_hint.setText("<span style='color:#00FFCC;'>✓ 管理员模式：可执行账户切换、删除与新建。</span>")
        else:
            self.tabs.setTabToolTip(1, "权限不足：操作员不可删除账户")
            self.tabs.setTabToolTip(2, "权限不足：操作员不可新增账户")
            self.lbl_perm_hint.setText("<span style='color:#FFC107;'>🔒 操作员模式：仅可切换登录。删除与新增需管理员权限。</span>")

    def _refresh_user_combos(self):
        users_info = self.db_manager.get_all_users_with_role()

        # 刷新切换登录下拉框
        self.combo_switch_user.clear()
        for u in users_info:
            uname = u["username"]
            role_label = "管理员" if u["role"] == "admin" else "操作员"
            self.combo_switch_user.addItem(f"{uname} ({role_label})", uname)

        idx = self.combo_switch_user.findData(self.current_user)
        if idx >= 0:
            self.combo_switch_user.setCurrentIndex(idx)

        # 刷新删除账户下拉框
        self.combo_del_user.clear()
        for u in users_info:
            uname = u["username"]
            role_label = "管理员" if u["role"] == "admin" else "操作员"
            self.combo_del_user.addItem(f"{uname} ({role_label})", uname)
        self._on_del_combo_changed()

    def _on_del_combo_changed(self):
        target_u = self.combo_del_user.currentData()
        if not target_u:
            self.lbl_del_target_role.setText("权限类型: --")
            return
        role = self.db_manager.get_user_role(target_u)
        role_label = "超级管理员" if target_u.upper() == "ADMIN" else ("管理员" if role == "admin" else "操作员")
        color = "#00FFCC" if role == "admin" else "#FFC107"
        self.lbl_del_target_role.setText(f"权限类型: <span style='color:{color}; font-weight:bold;'>{role_label}</span>")

    def _change_password(self):
        if not self.db_manager.verify_user(self.current_user, self.edit_old_password.text()):
            QMessageBox.warning(self, "修改密码", "当前密码错误")
            return
        password = self.edit_new_password.text()
        if not password or password != self.edit_confirm_password.text():
            QMessageBox.warning(self, "修改密码", "新密码不能为空，且两次输入须一致")
            return
        ok, message = self.db_manager.update_user(self.current_user, self.current_user, password)
        if ok:
            for editor in (self.edit_old_password, self.edit_new_password, self.edit_confirm_password):
                editor.clear()
        QMessageBox.information(self, "修改密码", message)

    def _on_switch_login(self):
        target_user = self.combo_switch_user.currentData()
        pwd = self.edit_switch_pwd.text()
        if not target_user:
            QMessageBox.warning(self, "提示", "请选择要切换的账户！")
            return
        if not pwd:
            QMessageBox.warning(self, "提示", "请输入登录密码！")
            return

        if self.db_manager.verify_user(target_user, pwd):
            from ui.dialogs.password_change import ensure_initial_password_changed
            if not ensure_initial_password_changed(self.db_manager, target_user, pwd, self):
                return
            self.logged_in_user = target_user
            self.current_user = target_user
            self.current_role = self.db_manager.get_user_role(target_user)
            self._update_permission_ui()
            QMessageBox.information(self, "切换成功", f"当前已成功切换为账户: {target_user}")
            self.accept()
        else:
            QMessageBox.warning(self, "登录失败", "密码错误，验证未通过！")

    def _on_delete_account(self):
        if self.current_role != "admin":
            QMessageBox.warning(self, "权限拦截", "仅管理员可以执行删除账户操作！")
            return

        target_u = self.combo_del_user.currentData()
        if not target_u:
            QMessageBox.warning(self, "提示", "请选择需要删除的账户！")
            return

        if target_u.upper() == "ADMIN":
            QMessageBox.warning(self, "操作被拦截", "超级管理员 ADMIN 为系统内置核心账户，不可删除！")
            return

        if target_u == self.current_user:
            QMessageBox.warning(self, "操作被拦截", "不可删除当前正在登录的账户！")
            return

        reply = QMessageBox.question(
            self,
            "确认删除账户",
            f"确定要彻底注销并删除账户 【{target_u}】 吗？\n\n此操作不可逆！",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            succ, msg = self.db_manager.delete_user(target_u)
            if succ:
                QMessageBox.information(self, "删除成功", msg)
                self._refresh_user_combos()
            else:
                QMessageBox.warning(self, "删除失败", msg)

    def _on_add_account(self):
        if self.current_role != "admin":
            QMessageBox.warning(self, "权限拦截", "仅管理员可以执行新增账户操作！")
            return

        new_u = self.edit_add_user.text().strip()
        new_role = "admin" if self.combo_add_role.currentText() == "管理员" else "operator"
        new_p = self.edit_add_pwd.text()
        confirm_p = self.edit_add_pwd2.text()

        if not new_u:
            QMessageBox.warning(self, "提示", "新用户名不能为空！")
            return
        if not new_p:
            QMessageBox.warning(self, "提示", "账户密码不能为空！")
            return
        if new_p != confirm_p:
            QMessageBox.warning(self, "提示", "两次输入的密码不一致！")
            return

        succ, msg = self.db_manager.add_user(new_u, new_p, role=new_role)
        if succ:
            role_display = "管理员" if new_role == "admin" else "操作员"
            QMessageBox.information(self, "创建成功", f"新账户 【{new_u}】（{role_display}）创建成功！")
            self.edit_add_user.clear()
            self.edit_add_pwd.clear()
            self.edit_add_pwd2.clear()
            self._refresh_user_combos()
            self.tabs.setCurrentIndex(0)
        else:
            QMessageBox.warning(self, "创建失败", f"创建失败: {msg}")
