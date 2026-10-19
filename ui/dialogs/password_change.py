from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox


def ensure_initial_password_changed(db, username, initial_password, parent):
    if not db.needs_password_change(username):
        return True
    password, accepted = QInputDialog.getText(parent, '修改初始密码', '请设置新密码：', QLineEdit.Password)
    if not accepted:
        return False
    confirm, accepted = QInputDialog.getText(parent, '确认新密码', '请再次输入新密码：', QLineEdit.Password)
    if not accepted:
        return False
    if not password.strip() or password != confirm or password == initial_password:
        QMessageBox.warning(parent, '修改密码', '两次密码须一致，且不能继续使用初始密码')
        return False
    ok, message = db.update_user(username, username, password)
    if not ok:
        QMessageBox.warning(parent, '修改密码', message)
    return ok
