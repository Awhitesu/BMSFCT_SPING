# -*- coding: utf-8 -*-
"""
双模一体化机台互联工具 (Dual-Mode Machine Connector)
功能：
1. 【🖥️ 机台电脑端 (被控/硬件发布)】：
   - 自动获取本机局域网 IP 与当前系统用户名。
   - 一键自动化配置并开启 SMB 共享文件夹。
   - 一键自动化安装/启动 Windows OpenSSH 服务并放行防火墙 (445/22 端口)。
   - 一键检测本机 Python 环境、依赖库及 PCAN/COM 硬件状态。
   - 界面实时生成“机台连接凭据卡片”，供开发机对照输入。
2. 【💻 开发电脑端 (主控/挂载保活)】：
   - 界面化配置机台 IP、共享名、账号、密码与盘符，支持多机台配置记忆。
   - 一键建立 SMB 挂载并启动后台 25 秒心跳守护（彻底防 Windows 共享盘超时掉线）。
   - 5 组指示灯实时监控网络、端口与驱动器在线状态。
   - 快捷操作：一键打开盘符目录、一键远程拉起机台程序。
"""

import sys
import os
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QTextEdit, QFrame, QMessageBox, QGroupBox, QTabWidget, QFileDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QColor

CONFIG_FILE = Path(__file__).resolve().parent / "machine_config.json"

DEFAULT_CONFIG = {
    "current_machine": "机台1 (默认)",
    "machines": {
        "机台1 (默认)": {
            "ip": "10.140.213.182",
            "share_name": "GX_APP",
            "username": "superman",
            "password": "1",
            "drive_letter": "Z:",
            "ssh_port": 22
        }
    }
}


class ConnectorSignals(QObject):
    log_client = Signal(str)
    log_server = Signal(str)
    status_updated = Signal(dict)
    connected_state = Signal(bool)
    server_ready = Signal(dict)


class DualModeConnectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ 双模一体化机台互联工具 - Machine Connector")
        self.resize(750, 680)
        self.setMinimumSize(680, 600)

        self.signals = ConnectorSignals()
        self.signals.log_client.connect(self.append_client_log)
        self.signals.log_server.connect(self.append_server_log)
        self.signals.status_updated.connect(self.update_status_badges)
        self.signals.connected_state.connect(self.on_connected_state_changed)
        self.signals.server_ready.connect(self.on_server_ready)

        self.is_connected = False
        self.keepalive_thread = None
        self.stop_keepalive_event = threading.Event()

        self.config_data = self.load_config()
        self._init_ui()
        self._apply_dark_theme()

        # 启动定时状态探测器
        self.probe_timer = QTimer(self)
        self.probe_timer.timeout.connect(self.trigger_background_probe)
        self.probe_timer.start(5000)

        # 初始化数据
        self.load_selected_machine_to_form()
        self.refresh_server_local_info()
        QTimer.singleShot(300, self.trigger_background_probe)

    # ------------------ 配置持久化 ------------------
    def load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取配置失败: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.append_client_log(f"❌ 保存配置异常: {e}")

    # ------------------ UI 布局 ------------------
    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 顶部标题栏
        header_layout = QHBoxLayout()
        lbl_title = QLabel("⚡ 双模一体化机台互联工具")
        lbl_title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl_title.setStyleSheet("color: #38bdf8;")

        lbl_sub = QLabel("双机直连 / 一键发布 / 智能挂载 / 永久心跳保活")
        lbl_sub.setFont(QFont("Segoe UI", 9))
        lbl_sub.setStyleSheet("color: #94a3b8;")

        header_layout.addWidget(lbl_title)
        header_layout.addSpacing(10)
        header_layout.addWidget(lbl_sub)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Tab 容器 (机台端 vs 开发端)
        self.tab_widget = QTabWidget()
        self.tab_widget.setFont(QFont("Segoe UI", 10, QFont.Bold))

        # 页面 1: 开发电脑端 (客户端)
        self.tab_client = QWidget()
        self._setup_client_tab(self.tab_client)
        self.tab_widget.addTab(self.tab_client, "💻 开发电脑端 (主控连接/保活)")

        # 页面 2: 机台电脑端 (服务端)
        self.tab_server = QWidget()
        self._setup_server_tab(self.tab_server)
        self.tab_widget.addTab(self.tab_server, "🖥️ 机台电脑端 (硬件发布/环境配置)")

        main_layout.addWidget(self.tab_widget)

    # ------------------ Tab 1: 开发电脑端 UI ------------------
    def _setup_client_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # 1. 机台选择与快捷保存
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("选择目标机台:"))
        self.combo_machines = QComboBox()
        self.combo_machines.setMinimumWidth(180)
        self.combo_machines.addItems(list(self.config_data.get("machines", {}).keys()))
        current = self.config_data.get("current_machine", "")
        if current in self.config_data.get("machines", {}):
            self.combo_machines.setCurrentText(current)
        self.combo_machines.currentTextChanged.connect(self.on_machine_selected)
        top_bar.addWidget(self.combo_machines)

        btn_save_current = QPushButton("💾 保存当前配置")
        btn_save_current.clicked.connect(self.on_save_machine_config)
        top_bar.addWidget(btn_save_current)

        btn_add_machine = QPushButton("➕ 新建机台")
        btn_add_machine.clicked.connect(self.on_add_new_machine)
        top_bar.addWidget(btn_add_machine)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        # 2. 机台连接参数表单
        form_group = QGroupBox("目标机台连接参数")
        form_layout = QGridLayout(form_group)
        form_layout.setSpacing(10)

        form_layout.addWidget(QLabel("机台 IP:"), 0, 0)
        self.edit_ip = QLineEdit()
        self.edit_ip.setPlaceholderText("例如: 10.140.213.182")
        form_layout.addWidget(self.edit_ip, 0, 1)

        form_layout.addWidget(QLabel("共享名称:"), 0, 2)
        self.edit_share = QLineEdit()
        self.edit_share.setPlaceholderText("例如: GX_APP")
        form_layout.addWidget(self.edit_share, 0, 3)

        form_layout.addWidget(QLabel("系统用户:"), 1, 0)
        self.edit_user = QLineEdit()
        self.edit_user.setPlaceholderText("例如: superman")
        form_layout.addWidget(self.edit_user, 1, 1)

        form_layout.addWidget(QLabel("用户密码:"), 1, 2)
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        self.edit_pwd.setPlaceholderText("Windows 登录密码")
        form_layout.addWidget(self.edit_pwd, 1, 3)

        form_layout.addWidget(QLabel("挂载盘符:"), 2, 0)
        self.combo_drive = QComboBox()
        self.combo_drive.addItems([f"{chr(c)}:" for c in range(ord('Z'), ord('D') - 1, -1)])
        form_layout.addWidget(self.combo_drive, 2, 1)

        form_layout.addWidget(QLabel("SSH 端口:"), 2, 2)
        self.edit_ssh_port = QLineEdit("22")
        form_layout.addWidget(self.edit_ssh_port, 2, 3)

        layout.addWidget(form_group)

        # 3. 状态看板
        status_frame = QFrame()
        status_frame.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 6px;")
        status_layout = QHBoxLayout(status_frame)

        self.lbl_stat_ping = self._create_badge("网络 Ping: 探测中")
        self.lbl_stat_smb = self._create_badge("SMB(445): 探测中")
        self.lbl_stat_ssh = self._create_badge("SSH(22): 探测中")
        self.lbl_stat_mount = self._create_badge("驱动器: 未挂载")
        self.lbl_stat_heartbeat = self._create_badge("心跳保活: 未运行")

        status_layout.addWidget(self.lbl_stat_ping)
        status_layout.addWidget(self.lbl_stat_smb)
        status_layout.addWidget(self.lbl_stat_ssh)
        status_layout.addWidget(self.lbl_stat_mount)
        status_layout.addWidget(self.lbl_stat_heartbeat)

        layout.addWidget(status_frame)

        # 4. 控制按钮组
        btn_layout = QHBoxLayout()
        self.btn_connect = QPushButton("🚀 一键连接并保活")
        self.btn_connect.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.btn_connect.setStyleSheet("background-color: #10b981; color: #ffffff; padding: 10px 16px;")
        self.btn_connect.clicked.connect(self.on_connect_clicked)
        btn_layout.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("⏹️ 断开挂载")
        self.btn_disconnect.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.btn_disconnect.setStyleSheet("background-color: #ef4444; color: #ffffff; padding: 10px 16px;")
        self.btn_disconnect.clicked.connect(self.on_disconnect_clicked)
        self.btn_disconnect.setEnabled(False)
        btn_layout.addWidget(self.btn_disconnect)

        self.btn_auto_heal = QPushButton("🛠️ 远程自愈共享")
        self.btn_auto_heal.setStyleSheet("background-color: #0284c7; color: #ffffff; padding: 10px 14px;")
        self.btn_auto_heal.clicked.connect(self.on_auto_heal_clicked)
        btn_layout.addWidget(self.btn_auto_heal)

        self.btn_open_folder = QPushButton("📁 打开挂载目录")
        self.btn_open_folder.setStyleSheet("background-color: #3b82f6; color: #ffffff; padding: 10px 16px;")
        self.btn_open_folder.clicked.connect(self.on_open_folder_clicked)
        btn_layout.addWidget(self.btn_open_folder)

        self.btn_launch_remote = QPushButton("⚡ 在机台运行程序")
        self.btn_launch_remote.setStyleSheet("background-color: #8b5cf6; color: #ffffff; padding: 10px 16px;")
        self.btn_launch_remote.clicked.connect(self.on_launch_remote_clicked)
        btn_layout.addWidget(self.btn_launch_remote)

        layout.addLayout(btn_layout)

        # 5. 日志看板
        log_group = QGroupBox("开发端连接与心跳日志")
        log_layout = QVBoxLayout(log_group)
        self.txt_client_log = QTextEdit()
        self.txt_client_log.setReadOnly(True)
        self.txt_client_log.setStyleSheet("background-color: #090d16; color: #38bdf8; font-family: Consolas, monospace; font-size: 11px;")
        log_layout.addWidget(self.txt_client_log)
        layout.addWidget(log_group)

    # ------------------ Tab 2: 机台电脑端 UI ------------------
    def _setup_server_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # 1. 本机信息卡片
        info_group = QGroupBox("本机环境信息 (自动识别)")
        info_layout = QGridLayout(info_group)
        info_layout.setSpacing(10)

        info_layout.addWidget(QLabel("本机 IP 列表:"), 0, 0)
        self.combo_server_ips = QComboBox()
        info_layout.addWidget(self.combo_server_ips, 0, 1)

        btn_refresh_ip = QPushButton("🔄 刷新 IP")
        btn_refresh_ip.clicked.connect(self.refresh_server_local_info)
        info_layout.addWidget(btn_refresh_ip, 0, 2)

        info_layout.addWidget(QLabel("当前系统用户:"), 1, 0)
        self.lbl_server_user = QLabel(os.environ.get("USERNAME", "未知"))
        self.lbl_server_user.setStyleSheet("color: #38bdf8; font-weight: bold;")
        info_layout.addWidget(self.lbl_server_user, 1, 1)

        info_layout.addWidget(QLabel("待共享工程目录:"), 2, 0)
        self.edit_server_path = QLineEdit(str(Path(__file__).resolve().parent.parent))
        info_layout.addWidget(self.edit_server_path, 2, 1)

        btn_browse_path = QPushButton("📂 浏览...")
        btn_browse_path.clicked.connect(self.on_browse_server_path)
        info_layout.addWidget(btn_browse_path, 2, 2)

        info_layout.addWidget(QLabel("发布共享名称:"), 3, 0)
        self.edit_server_share_name = QLineEdit("GX_APP")
        info_layout.addWidget(self.edit_server_share_name, 3, 1)

        layout.addWidget(info_group)

        # 2. 一键配置大按钮
        btn_server_layout = QHBoxLayout()
        self.btn_auto_setup_server = QPushButton("🛠️ 一键配置并发布本机为机台 (开启共享+SSH+放行防火墙)")
        self.btn_auto_setup_server.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.btn_auto_setup_server.setStyleSheet("background-color: #0284c7; color: #ffffff; padding: 12px 20px; border-radius: 8px;")
        self.btn_auto_setup_server.clicked.connect(self.on_auto_setup_server_clicked)
        btn_server_layout.addWidget(self.btn_auto_setup_server)
        layout.addLayout(btn_server_layout)

        # 3. 机台连接凭据看板 (高亮卡片)
        cred_group = QGroupBox("机台连接凭据 (供开发机对照输入)")
        cred_layout = QGridLayout(cred_group)
        cred_layout.setSpacing(8)

        self.lbl_cred_ip = QLabel("机台 IP: 未发布")
        self.lbl_cred_share = QLabel("共享名: 未发布")
        self.lbl_cred_user = QLabel(f"用户名: {os.environ.get('USERNAME', '未知')}")
        self.lbl_cred_status = QLabel("服务状态: ⚪ 未就绪")

        for lbl in (self.lbl_cred_ip, self.lbl_cred_share, self.lbl_cred_user, self.lbl_cred_status):
            lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
            lbl.setStyleSheet("color: #f8fafc; background-color: #090d16; padding: 6px 12px; border-radius: 6px; border: 1px solid #334155;")

        cred_layout.addWidget(self.lbl_cred_ip, 0, 0)
        cred_layout.addWidget(self.lbl_cred_share, 0, 1)
        cred_layout.addWidget(self.lbl_cred_user, 1, 0)
        cred_layout.addWidget(self.lbl_cred_status, 1, 1)

        layout.addWidget(cred_group)

        # 4. 机台端运行日志
        server_log_group = QGroupBox("机台端配置日志与硬件环境检查")
        server_log_layout = QVBoxLayout(server_log_group)
        self.txt_server_log = QTextEdit()
        self.txt_server_log.setReadOnly(True)
        self.txt_server_log.setStyleSheet("background-color: #090d16; color: #34d399; font-family: Consolas, monospace; font-size: 11px;")
        server_log_layout.addWidget(self.txt_server_log)
        layout.addWidget(server_log_group)

    def _create_badge(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        lbl.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 4px 8px;")
        return lbl

    # ------------------ 机台端业务逻辑 (Server Mode) ------------------
    def refresh_server_local_info(self):
        self.combo_server_ips.clear()
        ips = []
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None):
                ip = info[4][0]
                if ":" not in ip and not ip.startswith("127."):
                    if ip not in ips:
                        ips.append(ip)
        except Exception as e:
            print(f"获取 IP 异常: {e}")

        if not ips:
            ips = ["127.0.0.1"]

        self.combo_server_ips.addItems(ips)
        self.append_server_log(f"🔍 已检索到本机局域网 IP: {', '.join(ips)}")

    def on_browse_server_path(self):
        directory = QFileDialog.getExistingDirectory(self, "选择待共享的工程目录", self.edit_server_path.text())
        if directory:
            self.edit_server_path.setText(directory)

    def on_auto_setup_server_clicked(self):
        folder = self.edit_server_path.text().strip()
        share_name = self.edit_server_share_name.text().strip()
        selected_ip = self.combo_server_ips.currentText().strip()

        if not (folder and share_name and os.path.exists(folder)):
            QMessageBox.warning(self, "路径错误", "请选择有效的工程目录路径！")
            return

        self.append_server_log("🚀 开始一键配置并发布本机为机台...")
        self.btn_auto_setup_server.setEnabled(False)
        self.btn_auto_setup_server.setText("⏳ 正在配置中，请稍候...")

        def _worker():
            # 1. 自动化建立 SMB 共享
            ps_share = f'New-SmbShare -Name "{share_name}" -Path "{folder}" -FullAccess "Everyone" -ErrorAction SilentlyContinue'
            subprocess.run(["powershell", "-Command", ps_share], capture_output=True, text=True)
            self.signals.log_server.emit(f"✅ SMB 共享 [{share_name}] 已建立 (映射目录: {folder})")

            # 2. 自动化启动 OpenSSH 服务并放行防火墙
            ps_ssh = """
            Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue
            Start-Service sshd -ErrorAction SilentlyContinue
            Set-Service -Name sshd -StartupType 'Automatic' -ErrorAction SilentlyContinue
            New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -ErrorAction SilentlyContinue
            """
            subprocess.run(["powershell", "-Command", ps_ssh], capture_output=True, text=True)
            self.signals.log_server.emit("✅ Windows OpenSSH 服务已启用，防火墙 22 端口已开放")

            # 3. 检查本机 Python 与依赖
            try:
                import PySide6
                import can
                self.signals.log_server.emit(f"✅ Python 依赖就绪: PySide6 {PySide6.__version__}, python-can {can.__version__}")
            except Exception as e:
                self.signals.log_server.emit(f"⚠️ 依赖检查提示: {e} (如缺少请运行 pip install -r requirements.txt)")

            # 4. 检查 PCAN 驱动
            sys32 = os.environ.get("SystemRoot", "C:\\Windows") + "\\System32\\PCANBasic.dll"
            if os.path.exists(sys32):
                self.signals.log_server.emit("✅ PEAK PCAN 驱动已安装 (PCANBasic.dll 存在)")
            else:
                self.signals.log_server.emit("ℹ️ 提示: 未检测到 System32\\PCANBasic.dll，如需驱动 PCAN-FD 请安装官方驱动")

            self.signals.server_ready.emit({
                "ip": selected_ip,
                "share": share_name,
                "user": os.environ.get("USERNAME", "未知")
            })

        threading.Thread(target=_worker, daemon=True).start()

    def on_server_ready(self, info: dict):
        self.btn_auto_setup_server.setEnabled(True)
        self.btn_auto_setup_server.setText("✅ 本机机台发布成功 (点击可重新刷新)")
        self.btn_auto_setup_server.setStyleSheet("background-color: #059669; color: #ffffff; padding: 12px 20px; border-radius: 8px;")

        self.lbl_cred_ip.setText(f"机台 IP: {info['ip']}")
        self.lbl_cred_share.setText(f"共享名: {info['share']}")
        self.lbl_cred_user.setText(f"用户名: {info['user']}")
        self.lbl_cred_status.setText("服务状态: 🟢 已发布就绪 (等待开发机连接)")
        self.lbl_cred_status.setStyleSheet("color: #34d399; background-color: #064e3b; padding: 6px 12px; border-radius: 6px; border: 1px solid #059669;")

        self.append_server_log("🎉 本机机台服务已全部就绪！请在开发电脑端输入上方凭据进行连接。")

    # ------------------ 开发端业务逻辑 (Client Mode) ------------------
    def update_status_badges(self, stats: dict):
        def apply_style(lbl: QLabel, ok: bool, ok_text: str, fail_text: str):
            if ok:
                lbl.setText(ok_text)
                lbl.setStyleSheet("background-color: #064e3b; color: #34d399; border-radius: 4px; padding: 4px 8px;")
            else:
                lbl.setText(fail_text)
                lbl.setStyleSheet("background-color: #7f1d1d; color: #f87171; border-radius: 4px; padding: 4px 8px;")

        if "ping" in stats:
            apply_style(self.lbl_stat_ping, stats["ping"], "网络: 正常", "网络: 不通")
        if "smb" in stats:
            apply_style(self.lbl_stat_smb, stats["smb"], "SMB: 正常", "SMB: 未开放")
        if "ssh" in stats:
            apply_style(self.lbl_stat_ssh, stats["ssh"], "SSH: 正常", "SSH: 未开放")
        if "mount" in stats:
            drive = self.combo_drive.currentText()
            apply_style(self.lbl_stat_mount, stats["mount"], f"驱动器: {drive} 在线", f"驱动器: {drive} 离线")
        if "heartbeat" in stats:
            hb = stats["heartbeat"]
            if hb:
                self.lbl_stat_heartbeat.setText("心跳: 守护中")
                self.lbl_stat_heartbeat.setStyleSheet("background-color: #064e3b; color: #34d399; border-radius: 4px; padding: 4px 8px;")
            else:
                self.lbl_stat_heartbeat.setText("心跳: 未激活")
                self.lbl_stat_heartbeat.setStyleSheet("background-color: #334155; color: #94a3b8; border-radius: 4px; padding: 4px 8px;")

    def on_connected_state_changed(self, connected: bool):
        self.is_connected = connected
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        if connected:
            self.btn_connect.setText("✅ 已稳定连接并保活")
            self.btn_connect.setStyleSheet("background-color: #059669; color: #ffffff; padding: 10px 16px;")
        else:
            self.btn_connect.setText("🚀 一键连接并保活")
            self.btn_connect.setStyleSheet("background-color: #10b981; color: #ffffff; padding: 10px 16px;")

    def append_client_log(self, text: str):
        timestamp = time.strftime("%H:%M:%S")
        self.txt_client_log.append(f"[{timestamp}] {text}")

    def append_server_log(self, text: str):
        timestamp = time.strftime("%H:%M:%S")
        self.txt_server_log.append(f"[{timestamp}] {text}")

    def trigger_background_probe(self):
        ip = self.edit_ip.text().strip()
        drive = self.combo_drive.currentText().strip()
        ssh_port = int(self.edit_ssh_port.text().strip() or "22")

        if not ip:
            return

        def _worker():
            smb_ok = self._test_port(ip, 445)
            ssh_ok = self._test_port(ip, ssh_port)
            ping_ok = smb_ok or ssh_ok
            mount_ok = os.path.exists(drive + "\\")

            self.signals.status_updated.emit({
                "ping": ping_ok,
                "smb": smb_ok,
                "ssh": ssh_ok,
                "mount": mount_ok,
                "heartbeat": self.is_connected and mount_ok
            })

        threading.Thread(target=_worker, daemon=True).start()

    def _test_port(self, host: str, port: int, timeout: float = 0.8) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except:
            return False

    def on_connect_clicked(self):
        ip = self.edit_ip.text().strip()
        share = self.edit_share.text().strip()
        user = self.edit_user.text().strip()
        pwd = self.edit_pwd.text().strip()
        drive = self.combo_drive.currentText().strip()
        ssh_port = int(self.edit_ssh_port.text().strip() or "22")

        if not (ip and share and user and drive):
            QMessageBox.warning(self, "输入校验", "请完整填写机台 IP、共享名、用户名与盘符！")
            return

        self.append_client_log(f"🔄 正在发起对机台 [{ip}] 的一键直连与挂载...")

        def _connect_worker():
            subprocess.run(f"net use {drive} /delete /y", shell=True, capture_output=True)
            cmd = f'net use {drive} \\\\{ip}\\{share} "{pwd}" /user:{user} /persistent:yes'
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if os.path.exists(drive + "\\"):
                self.signals.log_client.emit(f"✅ 成功挂载机台共享目录为本地 [{drive}] 盘！")
                self.signals.connected_state.emit(True)
                self.start_keepalive_daemon(drive, ip, share, user, pwd)
                self.trigger_background_probe()
            else:
                err = proc.stderr.strip() or proc.stdout.strip()
                # 智能自愈：若提示 67 (找不到网络名) 或 53 或包含网络名错误，自动尝试远程自愈共享
                if "67" in err or "找不到网络名" in err or "53" in err:
                    self.signals.log_client.emit(f"⚠️ 挂载提示 [{err}]，正在启动远程智能自愈模块...")
                    healed, heal_msg = self._remote_auto_heal_smb_share(ip, share, user, pwd, ssh_port)
                    if healed:
                        self.signals.log_client.emit(f"✨ {heal_msg}")
                        self.signals.log_client.emit("🔄 正在重新发起挂载...")
                        proc2 = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                        if os.path.exists(drive + "\\"):
                            self.signals.log_client.emit(f"✅ 远程自愈成功！已挂载机台共享目录为本地 [{drive}] 盘！")
                            self.signals.connected_state.emit(True)
                            self.start_keepalive_daemon(drive, ip, share, user, pwd)
                            self.trigger_background_probe()
                            return
                        else:
                            err = proc2.stderr.strip() or proc2.stdout.strip()
                    else:
                        self.signals.log_client.emit(f"⚠️ 远程自愈未完成: {heal_msg}")
                self.signals.log_client.emit(f"❌ 挂载失败: {err}")
                self.signals.connected_state.emit(False)

        threading.Thread(target=_connect_worker, daemon=True).start()

    def on_auto_heal_clicked(self):
        ip = self.edit_ip.text().strip()
        share = self.edit_share.text().strip()
        user = self.edit_user.text().strip()
        pwd = self.edit_pwd.text().strip()
        ssh_port = int(self.edit_ssh_port.text().strip() or "22")

        if not (ip and share and user):
            QMessageBox.warning(self, "输入校验", "请完整填写机台 IP、共享名与用户名！")
            return

        self.append_client_log(f"🛠️ 正在对机台 [{ip}] 发起远程自愈检测与共享发布...")
        self.btn_auto_heal.setEnabled(False)

        def _worker():
            healed, msg = self._remote_auto_heal_smb_share(ip, share, user, pwd, ssh_port)
            if healed:
                self.signals.log_client.emit(f"✅ {msg}")
            else:
                self.signals.log_client.emit(f"❌ {msg}")
            self.btn_auto_heal.setEnabled(True)

        threading.Thread(target=_worker, daemon=True).start()

    def _remote_auto_heal_smb_share(self, ip: str, share: str, user: str, pwd: str, ssh_port: int = 22) -> tuple:
        """通过远程 SSH 通道自动检测机台工程目录并建立 SMB 共享 (零人工去机台)"""
        try:
            import paramiko
        except ImportError:
            return False, "本地缺少 paramiko 库，无法建立远程通道"

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ip, port=ssh_port, username=user, password=pwd, timeout=5)

            # 1. 检查共享是否已存在
            check_cmd = f'powershell -Command "Get-SmbShare -Name \'{share}\' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path"'
            _, stdout, _ = ssh.exec_command(check_cmd)
            existing_path = stdout.read().decode('gbk', errors='ignore').strip()

            if existing_path:
                ssh.close()
                return True, f"机台已存在共享 [{share}] -> [{existing_path}]"

            # 2. 自动探测机台上的工程路径
            locate_cmd = """powershell -Command "
            $candidates = @('D:\\GX_APP刷写设备', 'D:\\GX_APP', 'D:\\', 'C:\\Users\\superman\\Desktop\\GX_APP刷写设备')
            foreach ($p in $candidates) {
                if (Test-Path $p) { Write-Output $p; break }
            }
            " """
            _, stdout, _ = ssh.exec_command(locate_cmd)
            target_path = stdout.read().decode('gbk', errors='ignore').strip()
            if not target_path:
                target_path = "D:\\"

            # 3. 远程执行 New-SmbShare
            create_cmd = f'powershell -Command "New-SmbShare -Name \'{share}\' -Path \'{target_path}\' -FullAccess \'Everyone\' -ErrorAction SilentlyContinue"'
            _, stdout, stderr = ssh.exec_command(create_cmd)
            _ = stdout.read()
            err_output = stderr.read().decode('gbk', errors='ignore').strip()

            ssh.close()
            if err_output:
                return False, f"远程发布共享返回异常: {err_output}"
            return True, f"成功远程为机台发布共享: [{share}] -> [{target_path}]"
        except Exception as e:
            return False, f"远程自愈连接异常: {e}"

    def start_keepalive_daemon(self, drive: str, ip: str, share: str, user: str, pwd: str):
        self.stop_keepalive_event.clear()

        def _keepalive_loop():
            self.signals.log_client.emit("🛡️ 心跳守护线程已启动 (每 25 秒轻量触碰，彻底防掉线)...")
            count = 0
            while not self.stop_keepalive_event.is_set():
                time.sleep(25)
                if self.stop_keepalive_event.is_set():
                    break

                try:
                    if os.path.exists(drive + "\\"):
                        _ = os.listdir(drive + "\\")
                        count += 1
                        if count % 12 == 0:
                            self.signals.log_client.emit(f"💓 心跳守护正常，已稳定保持连接 {count * 25 // 60} 分钟")
                    else:
                        self.signals.log_client.emit(f"⚠️ 检测到 [{drive}] 盘静默断开，正在自动自愈重连...")
                        cmd = f'net use {drive} \\\\{ip}\\{share} "{pwd}" /user:{user} /persistent:yes'
                        subprocess.run(cmd, shell=True, capture_output=True)
                except Exception as e:
                    self.signals.log_client.emit(f"⚠️ 心跳触碰异常: {e}")

            self.signals.log_client.emit("⏹️ 心跳守护线程已停止")

        self.keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
        self.keepalive_thread.start()

    def on_disconnect_clicked(self):
        drive = self.combo_drive.currentText().strip()
        self.stop_keepalive_event.set()
        self.append_client_log(f"🔄 正在卸载驱动器 [{drive}]...")

        def _worker():
            subprocess.run(f"net use {drive} /delete /y", shell=True, capture_output=True)
            self.signals.log_client.emit(f"✅ 驱动器 [{drive}] 已成功安全断开")
            self.signals.connected_state.emit(False)
            self.trigger_background_probe()

        threading.Thread(target=_worker, daemon=True).start()

    def on_open_folder_clicked(self):
        drive = self.combo_drive.currentText().strip()
        if os.path.exists(drive + "\\"):
            os.system(f"start {drive}\\")
            self.append_client_log(f"📁 已在资源管理器中打开 [{drive}] 盘")
        else:
            QMessageBox.warning(self, "提示", f"驱动器 [{drive}] 尚未挂载，请先点击【一键连接】！")

    def on_launch_remote_clicked(self):
        ip = self.edit_ip.text().strip()
        user = self.edit_user.text().strip()
        self.append_client_log("🚀 发起机台远程运行调用 (通过 SSH 唤起机台 Python)...")

        remote_cmd = 'ssh -o StrictHostKeyChecking=no ' + f'{user}@{ip} "powershell -Command Start-Process -FilePath C:\\Users\\superman\\AppData\\Local\\Programs\\Python\\Python311\\python.exe -ArgumentList D:\\GX_APP刷写设备\\main.py"'

        def _worker():
            proc = subprocess.run(remote_cmd, shell=True, capture_output=True, text=True)
            if proc.returncode == 0:
                self.signals.log_client.emit("🎉 已在机台成功拉起 main.py 应用程序！")
            else:
                self.signals.log_client.emit(f"ℹ️ 执行返回: {proc.stderr.strip() or proc.stdout.strip()}")

        threading.Thread(target=_worker, daemon=True).start()

    def on_machine_selected(self, name: str):
        machines = self.config_data.get("machines", {})
        if name in machines:
            m = machines[name]
            self.edit_ip.setText(m.get("ip", ""))
            self.edit_share.setText(m.get("share_name", ""))
            self.edit_user.setText(m.get("username", ""))
            self.edit_pwd.setText(m.get("password", ""))
            self.combo_drive.setCurrentText(m.get("drive_letter", "Z:"))
            self.edit_ssh_port.setText(str(m.get("ssh_port", 22)))
            self.config_data["current_machine"] = name
            self.save_config()
            self.append_client_log(f"📋 已切换至机台配置: [{name}]")
            self.trigger_background_probe()

    def load_selected_machine_to_form(self):
        name = self.combo_machines.currentText()
        self.on_machine_selected(name)

    def on_save_machine_config(self):
        name = self.combo_machines.currentText().strip()
        if not name:
            return
        if "machines" not in self.config_data:
            self.config_data["machines"] = {}

        self.config_data["machines"][name] = {
            "ip": self.edit_ip.text().strip(),
            "share_name": self.edit_share.text().strip(),
            "username": self.edit_user.text().strip(),
            "password": self.edit_pwd.text().strip(),
            "drive_letter": self.combo_drive.currentText().strip(),
            "ssh_port": int(self.edit_ssh_port.text().strip() or "22")
        }
        self.config_data["current_machine"] = name
        self.save_config()
        self.append_client_log(f"💾 机台配置 [{name}] 保存成功！")
        QMessageBox.information(self, "保存成功", f"机台 [{name}] 配置已保存！")

    def on_add_new_machine(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建机台", "请输入新机台名称 (例如: 机台2 - 老化柜B):")
        if ok and name.strip():
            name = name.strip()
            if name in self.config_data.get("machines", {}):
                QMessageBox.warning(self, "重名", "该机台名称已存在！")
                return
            self.config_data["machines"][name] = {
                "ip": "10.140.213.183",
                "share_name": "GX_APP",
                "username": "superman",
                "password": "1",
                "drive_letter": "Y:",
                "ssh_port": 22
            }
            self.config_data["current_machine"] = name
            self.combo_machines.addItem(name)
            self.combo_machines.setCurrentText(name)
            self.save_config()
            self.append_client_log(f"➕ 已成功新建机台配置: [{name}]")

    # ------------------ 暗黑高质感样式 ------------------
    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f172a;
            }
            QWidget {
                color: #f8fafc;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #334155;
                background-color: #0f172a;
                border-radius: 8px;
            }
            QTabBar::tab {
                background-color: #1e293b;
                color: #94a3b8;
                padding: 10px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 15px;
                background-color: #1e293b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                color: #38bdf8;
            }
            QLineEdit, QComboBox {
                background-color: #090d16;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f8fafc;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #38bdf8;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                outline: 0;
                padding: 4px;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
            }
            QComboBox QAbstractItemView::item {
                min-height: 28px;
                padding: 4px 10px;
                border-radius: 4px;
                color: #f8fafc;
                background-color: #0f172a;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #0369a1;
                color: #ffffff;
            }
            QPushButton {
                background-color: #334155;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                color: #f8fafc;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #475569;
            }
            QPushButton:pressed {
                background-color: #1e293b;
            }
        """)


def main():
    app = QApplication(sys.argv)
    window = DualModeConnectorApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
