import sys
import traceback
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                               QLabel, QLineEdit, QPushButton, QTableWidget, 
                               QTableWidgetItem, QMessageBox, QComboBox, QHeaderView)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

class UserManagerDialog(QDialog):
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.setWindowTitle("高级账号与权限管理")
        self.setFixedSize(500, 420)
        self.setModal(True)
        
        try:
            # 加载配置
            self.cfg = self.db_manager.load_sys_config()
            if not isinstance(self.cfg, dict):
                self.cfg = {}
            raw_users = self.cfg.get("users", {"admin": {"password": "gotion", "role": "admin", "fixed": True}})
            if not isinstance(raw_users, dict):
                raw_users = {"admin": {"password": "gotion", "role": "admin", "fixed": True}}
            self.users = raw_users
            
            self._init_ui()
            self.refresh_table()
        except Exception as e:
            QMessageBox.critical(self, "环境初始化失败", f"初始化账号管理失败：\n{traceback.format_exc()}")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 提示标签
        tip_lbl = QLabel("提示：选择列表中的账号可执行删除操作。系统固定账号不可删除。")
        tip_lbl.setStyleSheet("color: #FFC107; font-size: 12px;")
        layout.addWidget(tip_lbl)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["用户名", "角色权限"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        
        # 新增用户表单
        form_layout = QHBoxLayout()
        self.edit_user = QLineEdit()
        self.edit_user.setPlaceholderText("用户名")
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setPlaceholderText("密码")
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        self.combo_role = QComboBox()
        self.combo_role.addItems(["engineer", "admin"])
        
        self.btn_add = QPushButton("添加/修改")
        self.btn_add.setStyleSheet("background-color: #28A745; color: white; font-weight: bold; min-height: 28px;")
        self.btn_add.clicked.connect(self.add_user)
        
        form_layout.addWidget(self.edit_user)
        form_layout.addWidget(self.edit_pwd)
        form_layout.addWidget(self.combo_role)
        form_layout.addWidget(self.btn_add)
        
        layout.addLayout(form_layout)
        
        # 操作按钮区
        btn_layout = QHBoxLayout()
        
        self.btn_del = QPushButton("删除选中账号")
        self.btn_del.setStyleSheet("background-color: #DC3545; color: white; font-weight: bold; min-height: 30px;")
        self.btn_del.clicked.connect(self.delete_selected_user)
        btn_layout.addWidget(self.btn_del)
        
        btn_layout.addStretch()
        
        self.btn_close = QPushButton("关闭")
        self.btn_close.setMinimumWidth(100)
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)

    def refresh_table(self):
        try:
            self.table.setRowCount(0)
            gray_brush = QBrush(QColor(128, 128, 128))
            for uname, uinfo in self.users.items():
                if not isinstance(uinfo, dict):
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                
                # 用户名
                name_item = QTableWidgetItem(str(uname))
                if uinfo.get("fixed", False):
                    name_item.setForeground(gray_brush)
                self.table.setItem(row, 0, name_item)
                
                # 角色
                role_text = "管理员 (admin)" if uinfo.get("role") == "admin" else "工程师 (engineer)"
                role_item = QTableWidgetItem(role_text)
                if uinfo.get("fixed", False):
                    role_item.setForeground(gray_brush)
                self.table.setItem(row, 1, role_item)
        except Exception as e:
            QMessageBox.critical(self, "刷新列表失败", f"刷新账号列表失败：\n{traceback.format_exc()}")

    def add_user(self):
        try:
            uname = self.edit_user.text().strip()
            pwd = self.edit_pwd.text().strip()
            role = self.combo_role.currentText()
            
            if not uname or not pwd:
                QMessageBox.warning(self, "错误", "用户名和密码不能为空！")
                return
                
            if uname in self.users and isinstance(self.users[uname], dict) and self.users[uname].get("fixed", False):
                QMessageBox.warning(self, "错误", "不能修改系统固定账号！")
                return
                
            self.users[uname] = {
                "password": pwd,
                "role": role,
                "fixed": False
            }
            self.save_users()
            self.refresh_table()
            self.edit_user.clear()
            self.edit_pwd.clear()
            QMessageBox.information(self, "成功", f"账号 {uname} 已保存/更新！")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存账号失败：\n{traceback.format_exc()}")

    def delete_selected_user(self):
        try:
            curr_row = self.table.currentRow()
            if curr_row < 0:
                QMessageBox.warning(self, "警告", "请先在列表中选中需要删除的账号！")
                return
                
            uname_item = self.table.item(curr_row, 0)
            if not uname_item:
                return
            uname = uname_item.text()
            
            # 校验是否固定
            uinfo = self.users.get(uname)
            if isinstance(uinfo, dict) and uinfo.get("fixed", False):
                QMessageBox.warning(self, "错误", f"账号 {uname} 为系统固定账号，禁止删除！")
                return
                
            reply = QMessageBox.question(self, "确认", f"确定要删除选中的账号 {uname} 吗？")
            if reply == QMessageBox.Yes:
                if uname in self.users:
                    del self.users[uname]
                    self.save_users()
                    self.refresh_table()
                    QMessageBox.information(self, "成功", f"账号 {uname} 已成功删除。")
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"删除账号失败：\n{traceback.format_exc()}")

    def save_users(self):
        try:
            self.cfg["users"] = self.users
            self.db_manager.save_sys_config(self.cfg)
        except Exception as e:
            QMessageBox.critical(self, "存盘失败", f"保存配置文件失败：\n{traceback.format_exc()}")
