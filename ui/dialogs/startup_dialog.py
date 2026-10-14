import sys
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                               QLabel, QTextEdit, QPushButton, QProgressBar, QApplication)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QTextCursor

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
            
            success = self.device_manager.init_all_devices(logger=logger)
            self.finished_signal.emit(success)
        except Exception as e:
            self.log_signal.emit(f"[!] 后台初始化异常: {e}")
            self.finished_signal.emit(False)


class StartupCheckDialog(QDialog):
    def __init__(self, device_manager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        
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
        self.lbl_title.setText("正在进行硬件通讯自检，请稍候...")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: white;")
        self.progress.setRange(0, 0)
        self.log_text.clear()
        
        self.btn_retry.setEnabled(False)
        self.btn_enter.setEnabled(False)
        
        # 启动后台线程
        self.worker = InitWorker(self.device_manager)
        self.worker.log_signal.connect(self._append_log)
        self.worker.finished_signal.connect(self._on_check_finished)
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
            
        html_msg = f'<span style="color: {color};">{msg}</span>'
        self.log_text.append(html_msg)
        # 滚动到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)

    @Slot(bool)
    def _on_check_finished(self, success):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        
        self.btn_retry.setEnabled(True)
        self.btn_enter.setEnabled(True)
        
        # 统计脱机设备数量
        status_list = self.device_manager.get_all_device_status()
        offline_count = sum(1 for s in status_list if s["status"] == "离线")
        
        if offline_count == 0:
            self.lbl_title.setText("硬件自检全部通过！")
            self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #4CAF50;")
            self.btn_enter.setText("进入系统")
            
            # 自动进入系统
            QTimer.singleShot(200, self.accept)
        else:
            self.lbl_title.setText(f"自检完成，存在 {offline_count} 个离线/异常设备！")
            self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #FF9800;")
            self.btn_enter.setText("强制进入系统")
            self.btn_enter.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
            # 存在异常时，取消自动放行，强制要求操作员看一眼日志并手动点击进入

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
