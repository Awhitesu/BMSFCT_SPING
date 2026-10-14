import datetime
import threading
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QTextEdit, QGroupBox, 
                               QFormLayout, QGridLayout, QComboBox, QMessageBox, 
                               QSpinBox, QDoubleSpinBox, QFrame)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont, QColor


class ApiTab(QWidget):
    """
    老化监控系统 POST API 交互与监控 Tab 页
    包含 API 宿主参数配置、实时心跳状态展示、Dark 报文日志控制台、以及一键手动接口调用面板。
    """
    def __init__(self, engine, db_manager):
        super().__init__()
        self.engine = engine
        self.db_manager = db_manager
        
        self._init_ui()
        self.load_config()

        # 订阅来自 TestEngine 的底层网络通讯报文信号
        if self.engine:
            self.engine.api_log_message.connect(self.append_log)

        # 定时器用于更新心跳时间差及服务器活跃指示
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.start(1000)

        # 记录最近一次成功的 API 调用交互时间
        self.last_success_time = None
        self.last_heartbeat_time = None

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # ==========================================
        # 左半部分: 配置与手动接口触发面板 (2 权重)
        # ==========================================
        left_layout = QVBoxLayout()
        left_layout.setSpacing(12)

        # 1. API 服务器网络配置
        config_group = QGroupBox("API 服务端配置 (HTTP POST)")
        config_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        config_form = QFormLayout()
        config_form.setVerticalSpacing(8)

        self.edit_host = QLineEdit("127.0.0.1")
        self.edit_host.setPlaceholderText("服务主机 IP 地址")
        config_form.addRow("服务器 Host:", self.edit_host)

        self.edit_port = QSpinBox()
        self.edit_port.setRange(1, 65535)
        self.edit_port.setValue(8008)
        config_form.addRow("服务器 Port:", self.edit_port)

        btn_save = QPushButton("💾 保存并载入配置")
        btn_save.setFixedHeight(35)
        btn_save.setStyleSheet("font-weight: bold; background-color: #007BFF; color: white;")
        btn_save.clicked.connect(self.save_config)
        
        config_v = QVBoxLayout()
        config_v.addLayout(config_form)
        config_v.addWidget(btn_save)
        config_group.setLayout(config_v)
        left_layout.addWidget(config_group)

        # 2. 系统状态展示看板
        status_group = QGroupBox("实时 API 通信看板")
        status_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        status_grid = QGridLayout()
        status_grid.setSpacing(10)

        status_grid.addWidget(QLabel("心跳指示灯:"), 0, 0)
        self.lbl_status_led = QLabel("●")
        self.lbl_status_led.setStyleSheet("color: red; font-size: 24px; font-weight: bold;")
        status_grid.addWidget(self.lbl_status_led, 0, 1)

        status_grid.addWidget(QLabel("心跳通信状态:"), 1, 0)
        self.lbl_heartbeat_status = QLabel("心跳未就绪")
        self.lbl_heartbeat_status.setStyleSheet("font-weight: bold; color: #DC3545;")
        status_grid.addWidget(self.lbl_heartbeat_status, 1, 1)

        status_grid.addWidget(QLabel("最近上报温度:"), 2, 0)
        self.lbl_reported_temp = QLabel("-- ℃")
        self.lbl_reported_temp.setStyleSheet("font-size: 16px; font-weight: bold; color: lime;")
        status_grid.addWidget(self.lbl_reported_temp, 2, 1)

        status_group.setLayout(status_grid)
        left_layout.addWidget(status_group)

        # 3. 手动 API 模拟触发面板
        debug_group = QGroupBox("手动 API 模拟测试面板")
        debug_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        debug_v = QVBoxLayout()

        # 手动参数输入表单
        params_form = QFormLayout()
        params_form.setSpacing(6)
        
        self.spin_channel = QSpinBox()
        self.spin_channel.setRange(1, 60)
        self.spin_channel.setValue(1)
        params_form.addRow("调试通道号 (Channel ID):", self.spin_channel)

        self.edit_sn = QLineEdit("SN_Manual2026")
        params_form.addRow("产品 SN 条码:", self.edit_sn)

        self.edit_item_name = QLineEdit("电压回读测试")
        params_form.addRow("测试项名称 (Item Name):", self.edit_item_name)

        self.edit_item_value = QLineEdit("12.05")
        params_form.addRow("测量值 (Test Value):", self.edit_item_value)

        self.combo_item_res = QComboBox()
        self.combo_item_res.addItems(["PASS", "FAIL"])
        params_form.addRow("单项判定结果 (Result):", self.combo_item_res)

        self.spin_manual_temp = QDoubleSpinBox()
        self.spin_manual_temp.setRange(-50.0, 150.0)
        self.spin_manual_temp.setValue(55.4)
        params_form.addRow("模拟高低温箱温度 (℃):", self.spin_manual_temp)

        debug_v.addLayout(params_form)

        # 操作按钮矩阵
        buttons_grid = QGridLayout()
        buttons_grid.setSpacing(8)

        btn_prepare = QPushButton("1. Prepare (绑定)")
        btn_prepare.clicked.connect(self.trigger_prepare)
        buttons_grid.addWidget(btn_prepare, 0, 0)

        btn_start = QPushButton("2. Start Test (启动)")
        btn_start.clicked.connect(self.trigger_start)
        buttons_grid.addWidget(btn_start, 0, 1)

        btn_progress = QPushButton("3. Progress (进度)")
        btn_progress.clicked.connect(self.trigger_progress)
        buttons_grid.addWidget(btn_progress, 1, 0)

        btn_heartbeat = QPushButton("4. Heartbeat (心跳)")
        btn_heartbeat.clicked.connect(self.trigger_heartbeat)
        buttons_grid.addWidget(btn_heartbeat, 1, 1)

        btn_finish = QPushButton("5. Finish Test (判定)")
        btn_finish.clicked.connect(self.trigger_finish)
        buttons_grid.addWidget(btn_finish, 2, 0)

        btn_data = QPushButton("6. Upload Data (数据)")
        btn_data.clicked.connect(self.trigger_upload_data)
        buttons_grid.addWidget(btn_data, 2, 1)

        btn_reset = QPushButton("7. Reset (重置通道)")
        btn_reset.setStyleSheet("background-color: #DC3545; color: white;")
        btn_reset.clicked.connect(self.trigger_reset)
        buttons_grid.addWidget(btn_reset, 3, 0, 1, 2)

        btn_reset_all = QPushButton("8. Reset All (重置大屏所有通道)")
        btn_reset_all.setStyleSheet("background-color: #C2185B; color: white;")
        btn_reset_all.clicked.connect(self.trigger_reset_all)
        buttons_grid.addWidget(btn_reset_all, 4, 0, 1, 2)

        debug_v.addLayout(buttons_grid)
        debug_group.setLayout(debug_v)
        left_layout.addWidget(debug_group)

        left_layout.addStretch()
        main_layout.addLayout(left_layout, 2)

        # ==========================================
        # 右半部分: 黑色科技感报文交互控制台 (3 权重)
        # ==========================================
        right_layout = QVBoxLayout()
        console_group = QGroupBox("老化系统服务端 API 实时底层报文流 (Console Logs)")
        console_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        
        console_v = QVBoxLayout()
        
        self.txt_console = QTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.document().setMaximumBlockCount(800)
        # 黑色主题 Consolas 字体
        self.txt_console.setStyleSheet("""
            background-color: #000000; 
            color: #00FF00; 
            font-family: 'Consolas', 'Courier New', monospace; 
            font-size: 12px;
            border: 2px solid #333333;
            border-radius: 5px;
        """)
        self.txt_console.append("[System] 老化系统 API 交互控制台监视开启...")
        console_v.addWidget(self.txt_console)

        # 辅助操作栏
        action_bar = QHBoxLayout()
        btn_clear = QPushButton("🧹 清空控制台")
        btn_clear.clicked.connect(self.txt_console.clear)
        btn_clear.setStyleSheet("background-color: #6C757D; color: white;")
        
        btn_test_conn = QPushButton("🔌 检测连接")
        btn_test_conn.setStyleSheet("background-color: #28A745; color: white;")
        btn_test_conn.clicked.connect(self.test_connection)
        
        action_bar.addWidget(btn_clear)
        action_bar.addWidget(btn_test_conn)
        console_v.addLayout(action_bar)

        console_group.setLayout(console_v)
        right_layout.addWidget(console_group)

        main_layout.addLayout(right_layout, 3)

    def load_config(self):
        """载入全局系统 API 参数配置"""
        if self.db_manager:
            cfg = self.db_manager.load_sys_config() or {}
            self.edit_host.setText(cfg.get("api_host", "127.0.0.1"))
            self.edit_port.setValue(int(cfg.get("api_port", 8008)))

    def save_config(self):
        """保存并更新 API 客户端配置"""
        data = {
            "api_host": self.edit_host.text().strip(),
            "api_port": self.edit_port.value()
        }
        if self.db_manager:
            if self.db_manager.save_sys_config(data):
                if self.engine:
                    self.engine.update_api_client()
                QMessageBox.information(self, "配置成功", "API 通讯参数已成功更新并动态应用！")
                self.append_log(f"[Local Config] API Host & Port updated: {data['api_host']}:{data['api_port']}")
            else:
                QMessageBox.critical(self, "配置失败", "保存配置出错。")

    @Slot(str)
    def append_log(self, msg: str):
        """向黑色控制台添加实时报文，高亮不同级别的日志"""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{now}] {msg}"
        
        # 拦截特定特征的交互以更新面板状态
        if "[API RESP] /api/system/heartbeat -> Status: 200" in msg:
            self.last_success_time = datetime.datetime.now()
            self.last_heartbeat_time = datetime.datetime.now()
        elif "[API RESP]" in msg and "Status: 200" in msg:
            self.last_success_time = datetime.datetime.now()

        # 根据日志内容着色
        if "API ERR" in msg or "ERR" in msg:
            color = "#FF4D4D"  # 红色
        elif "API REQ" in msg:
            color = "#00BFFF"  # 浅蓝色
        elif "API RESP" in msg:
            color = "#00FF00"  # 绿色
        else:
            color = "#FFFFFF"  # 白色

        # 使用 HTML 富文本形式加入到 QTextEdit 中确保色彩酷炫且保持滚动至底部
        html = f"<span style='color: {color};'>{formatted_msg}</span>"
        self.txt_console.append(html)

    def update_status_display(self):
        if not self.isVisible(): return
        """定时刷新心跳和连接指示灯显示"""
        now = datetime.datetime.now()
        
        # 判定心跳离线 (超过 15 秒判定离线)
        if self.last_heartbeat_time:
            diff = (now - self.last_heartbeat_time).total_seconds()
            if diff <= 15:
                self.lbl_status_led.setStyleSheet("color: #28A745; font-size: 24px;") # 亮绿灯
                self.lbl_heartbeat_status.setText(f"在线 (上报于 {diff:.0f}s 前)")
                self.lbl_heartbeat_status.setStyleSheet("font-weight: bold; color: #28A745;")
            else:
                self.lbl_status_led.setStyleSheet("color: red; font-size: 24px;") # 亮红灯
                self.lbl_heartbeat_status.setText(f"离线 (断连 {diff:.0f}s)")
                self.lbl_heartbeat_status.setStyleSheet("font-weight: bold; color: red;")
        else:
            self.lbl_status_led.setStyleSheet("color: red; font-size: 24px;")
            self.lbl_heartbeat_status.setText("离线 (未启动心跳)")
            self.lbl_heartbeat_status.setStyleSheet("font-weight: bold; color: red;")

        self.lbl_reported_temp.setText("25.0 ℃")

    def test_connection(self):
        """一键连通性测试 (通过下发心跳包探测网络)"""
        self.append_log("[API Tool] 正在主动检测与 API 服务端的连接状态...")
        temp = 25.0

        # 异步探测
        def task():
            if self.engine and self.engine.api_client:
                success = self.engine.api_client.heartbeat(temp)
                if success:
                    QTimer.singleShot(0, lambda: self.append_log("[API Tool] Connection Test: SUCCESS ✅ (Status 200)"))
                else:
                    QTimer.singleShot(0, lambda: self.append_log("[API Tool] Connection Test: FAILED ❌ (Server Unreachable)"))
            else:
                QTimer.singleShot(0, lambda: self.append_log("[API Tool] API Client is not initialized!"))

        threading.Thread(target=task, daemon=True).start()

    # ==========================================
    # 模拟 API 触发方法列表 (全部采用异步防卡死设计)
    # ==========================================
    def trigger_prepare(self):
        cid = self.spin_channel.value()
        sn = self.edit_sn.text().strip()
        
        def run():
            if self.engine and self.engine.api_client:
                res = self.engine.api_client.prepare(cid, sn)
                self.append_log(f"[API Action] prepare(ch={cid}) Result -> {res}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_start(self):
        cid = self.spin_channel.value()
        def run():
            if self.engine and self.engine.api_client:
                res = self.engine.api_client.start_test(cid)
                self.append_log(f"[API Action] start_test(ch={cid}) Result -> {res}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_progress(self):
        cid = self.spin_channel.value()
        name = self.edit_item_name.text().strip()
        val = self.edit_item_value.text().strip()
        res = self.combo_item_res.currentText()
        
        def run():
            if self.engine and self.engine.api_client:
                res_val = self.engine.api_client.report_progress(
                    channel_id=cid,
                    barcode="主机",
                    name=name,
                    test_value=val,
                    result=res
                )
                self.append_log(f"[API Action] report_progress(ch={cid}) Result -> {res_val}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_heartbeat(self):
        temp = self.spin_manual_temp.value()
        def run():
            if self.engine and self.engine.api_client:
                res = self.engine.api_client.heartbeat(temp)
                self.append_log(f"[API Action] heartbeat(temp={temp}) Result -> {res}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_finish(self):
        cid = self.spin_channel.value()
        res = self.combo_item_res.currentText() == "PASS"
        def run():
            if self.engine and self.engine.api_client:
                res_val = self.engine.api_client.finish_test(cid, res)
                self.append_log(f"[API Action] finish_test(ch={cid}, res={res}) Result -> {res_val}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_upload_data(self):
        cid = self.spin_channel.value()
        sn = self.edit_sn.text().strip()
        res = self.combo_item_res.currentText() == "PASS"
        
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dummy_item = {
            "name": self.edit_item_name.text().strip(),
            "testValue": self.edit_item_value.text().strip(),
            "unit": "V",
            "upperLimit": "12.60",
            "lowerLimit": "11.40",
            "result": self.combo_item_res.currentText(),
            "index": "1",
            "testclass": "主机"
        }

        def run():
            if self.engine and self.engine.api_client:
                res_val = self.engine.api_client.upload_test_data(
                    channel_id=cid,
                    sn_barcode=sn,
                    start_time=now_str,
                    end_time=now_str,
                    status=res,
                    master_test_data=[dummy_item]
                )
                self.append_log(f"[API Action] upload_test_data(ch={cid}) Result -> {res_val}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_reset(self):
        cid = self.spin_channel.value()
        def run():
            if self.engine and self.engine.api_client:
                res = self.engine.api_client.reset(cid)
                self.append_log(f"[API Action] reset(ch={cid}) Result -> {res}")
        threading.Thread(target=run, daemon=True).start()

    def trigger_reset_all(self):
        def run():
            if self.engine and self.engine.api_client:
                self.append_log("[API Action] 🔄 开始批量重置大屏所有 48 个通道...")
                success_count = 0
                import time
                for cid in range(1, 49):
                    res = self.engine.api_client.reset(cid)
                    self.append_log(f"[API Action] reset(ch={cid}) Result -> {res}")
                    if res:
                        success_count += 1
                    time.sleep(0.02)
                self.append_log(f"[API Action] 🔄 批量重置大屏通道完成！成功: {success_count}/48")
        threading.Thread(target=run, daemon=True).start()
