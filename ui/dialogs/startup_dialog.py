import sys
import inspect
import html
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                               QLabel, QTextEdit, QPushButton, QProgressBar, QApplication)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QTextCursor

class InitWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(bool)

    def __init__(self, device_manager):
        super().__init__()
        self.device_manager = device_manager

    def run(self):
        try:
            # 执行初始化，并将日志抛给UI线程
            def logger(msg):
                self.log_signal.emit(msg)
            
            method = self.device_manager.init_all_devices
            kwargs = {"logger": logger}
            if "cancelled" in inspect.signature(method).parameters:
                kwargs["cancelled"] = self.isInterruptionRequested
            success = method(**kwargs)
            self.finished_signal.emit(success)
        except Exception as e:
            self.log_signal.emit(f"[!] 后台初始化异常: {e}")
            self.finished_signal.emit(False)


class StartupCheckDialog(QDialog):
    def __init__(self, device_manager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self._exit_pending = False
        self._last_success = False
        self._check_generation = 0
        self.worker = None
        
        self.setWindowTitle("系统启动自检")
        self.setFixedSize(650, 450)
        self.setModal(True)
        # 禁止用户通过右上角X关闭或ESC关闭，强制自检
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        
        self._init_ui()
        self._start_check()
        QTimer.singleShot(0, self._bring_to_front)

    def _bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 标题
        self.lbl_title = QLabel("正在进行硬件通讯自检，请稍候...")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self.lbl_title)
        
        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0) # Indeterminate mode
        layout.addWidget(self.progress)
        
        # 日志输出框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #1E1E1E; color: #D4D4D4; font-family: Consolas; font-size: 13px;")
        layout.addWidget(self.log_text)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_retry = QPushButton("重新自检")
        self.btn_retry.setMinimumWidth(100)
        self.btn_retry.setMinimumHeight(35)
        self.btn_retry.setEnabled(False)
        self.btn_retry.clicked.connect(self._start_check)
        
        self.btn_enter = QPushButton("强制进入系统")
        self.btn_enter.setMinimumWidth(120)
        self.btn_enter.setMinimumHeight(35)
        self.btn_enter.setEnabled(False)
        self.btn_enter.clicked.connect(self.accept)
        
        self.btn_exit = QPushButton("退出程序")
        self.btn_exit.setMinimumWidth(100)
        self.btn_exit.setMinimumHeight(35)
        self.btn_exit.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_retry)
        btn_layout.addWidget(self.btn_enter)
        btn_layout.addWidget(self.btn_exit)
        
        layout.addLayout(btn_layout)

    def _start_check(self):
        if self._exit_pending:
            return
        if self.worker is not None and not self.worker.wait(0):
            return
        self._check_generation += 1
        self.lbl_title.setText("正在进行硬件通讯自检，请稍候...")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: white;")
        self._last_success = False
        self.progress.setRange(0, 0)
        self.log_text.clear()
        
        self.btn_retry.setEnabled(False)
        self.btn_enter.setEnabled(False)
        
        # 启动后台线程
        self.worker = InitWorker(self.device_manager)
        self.worker.log_signal.connect(self._append_log)
        self.worker.finished_signal.connect(self._on_check_finished)
        self.worker.finished.connect(self._thread_finished)
        self.worker.start()

    @Slot(str)
    def _append_log(self, msg):
        # 简单的着色处理
        color = "#D4D4D4"
        if "[+]" in msg or "完成" in msg or "正常" in msg:
            color = "#4CAF50" # 绿色
        elif "[!]" in msg or "错误" in msg or "异常" in msg or "警告" in msg:
            color = "#F44336" # 红色
        elif "[*]" in msg:
            color = "#2196F3" # 蓝色
            
        html_msg = f'<span style="color: {color};">{html.escape(msg)}</span>'
        self.log_text.append(html_msg)
        # 滚动到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)

    @Slot(bool)
    def _on_check_finished(self, success):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        
        self._last_success = success
        if self._exit_pending:
            return
        self.btn_retry.setEnabled(False)
        self.btn_enter.setEnabled(False)
        
        # 统计脱机设备数量
        status_list = self.device_manager.get_all_device_status()
        offline_count = sum(1 for s in status_list if s["status"] == "离线")
        unverified = [s['name'] for s in status_list if s.get('connection_verified') is False]
        
        if success and offline_count == 0:
            self.lbl_title.setText(f"初始化完成；{len(unverified)} 项目标连接未验证" if unverified else "硬件自检全部通过！")
            color = '#FF9800' if unverified else '#4CAF50'
            self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")
            self.btn_enter.setText("进入系统")
            
            # 自动进入系统
            # Enter only after the initialization thread has actually finished.
        else:
            self.lbl_title.setText(f"自检失败，请检查日志；离线设备：{offline_count} 个")
            self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF9800;")
            self.btn_enter.setText("强制进入系统")
            self.btn_enter.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
            # 存在异常时，取消自动放行，强制要求操作员看一眼日志并手动点击进入

    @Slot()
    def _thread_finished(self):
        if self.worker is not None and not self.worker.wait(0):
            QTimer.singleShot(10, self._thread_finished)
            return
        if self._exit_pending:
            QDialog.reject(self)
            return
        self.btn_retry.setEnabled(True)
        self.btn_enter.setEnabled(True)
        if self._last_success and all(row['status'] != '离线' for row in self.device_manager.get_all_device_status()):
            generation = self._check_generation
            QTimer.singleShot(0, lambda: self._auto_enter(generation))

    def _auto_enter(self, generation):
        if (generation == self._check_generation and self._last_success
                and not self._exit_pending):
            self.accept()

    def accept(self):
        if self._exit_pending:
            return
        if self.worker is not None and not self.worker.wait(0):
            return
        super().accept()

    def reject(self):
        self._exit_pending = True
        if self.worker is not None and not self.worker.wait(0):
            self.worker.requestInterruption()
            self.btn_exit.setEnabled(False)
            self.lbl_title.setText("正在停止自检，等待当前通信结束…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and not self.worker.wait(0):
            event.ignore()
            self.reject()
        else:
            self._exit_pending = True
            event.accept()

if __name__ == "__main__":
    # 简单的独立测试代码
    # 需要将当前路径加到 sys.path 防止引用错误
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
    from data.db_manager import DBManager
    from devices.manager import DeviceManager
    
    app = QApplication(sys.argv)
    db = DBManager()
    dm = DeviceManager(db)
    
    dialog = StartupCheckDialog(dm)
    if dialog.exec():
        print("User chose to enter system.")
    else:
        print("User exited.")
