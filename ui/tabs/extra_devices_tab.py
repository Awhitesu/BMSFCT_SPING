from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QGroupBox, QTextEdit, QGridLayout, 
                               QLineEdit, QFrame, QScrollArea)
from PySide6.QtCore import Qt

class ExtraDevicesDebugTab(QWidget):
    def __init__(self, device_manager):
        super().__init__()
        self.mgr = device_manager
        self._init_ui()
        
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        
        # 左侧控制面板
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        
        # 1. 老化功能板控制
        aging_group = QGroupBox("老化功能板 (继电器矩阵)")
        aging_layout = QGridLayout()
        relays = ["KL15", "HALL_POWER", "CAN1", "CAN2", "HV", "ISO_NEG_SHORT"]
        for i, name in enumerate(relays):
            btn_on = QPushButton(f"{name} ON")
            btn_off = QPushButton(f"{name} OFF")
            btn_on.clicked.connect(lambda ch=name: self.control_aging_board(ch, True))
            btn_off.clicked.connect(lambda ch=name: self.control_aging_board(ch, False))
            aging_layout.addWidget(btn_on, i, 0)
            aging_layout.addWidget(btn_off, i, 1)
        aging_group.setLayout(aging_layout)
        left_layout.addWidget(aging_group)
        
        # 2. Easy320 控制
        easy_group = QGroupBox("Easy320 继电器板")
        easy_layout = QVBoxLayout()
        h_layout = QHBoxLayout()
        self.easy_index = QLineEdit("0")
        self.easy_index.setPlaceholderText("继电器索引 0-31")
        h_layout.addWidget(self.easy_index)
        btn_e_on = QPushButton("开启")
        btn_e_off = QPushButton("关闭")
        btn_e_on.clicked.connect(lambda: self.control_easy320(True))
        btn_e_off.clicked.connect(lambda: self.control_easy320(False))
        h_layout.addWidget(btn_e_on)
        h_layout.addWidget(btn_e_off)
        easy_layout.addLayout(h_layout)
        easy_group.setLayout(easy_layout)
        left_layout.addWidget(easy_group)
        
        # 3. CA550 校准仪控制
        ca_group = QGroupBox("横河 CA550 校准仪")
        ca_layout = QVBoxLayout()
        
        # 信息读取
        info_layout = QHBoxLayout()
        btn_idn = QPushButton("读取 *IDN?")
        btn_idn.clicked.connect(self.read_ca550_idn)
        info_layout.addWidget(btn_idn)
        ca_layout.addLayout(info_layout)
        
        # 电压测量
        v_meas_layout = QHBoxLayout()
        v_meas_layout.addWidget(QLabel("电压测量:"))
        self.lbl_ca_val = QLabel("--- V")
        self.lbl_ca_val.setStyleSheet("font-size: 18px; color: yellow; font-weight: bold;")
        v_meas_layout.addWidget(self.lbl_ca_val)
        btn_read_v = QPushButton("单次读取")
        btn_read_v.clicked.connect(self.read_ca550_voltage)
        v_meas_layout.addWidget(btn_read_v)
        ca_layout.addLayout(v_meas_layout)
        
        ca_group.setLayout(ca_layout)
        left_layout.addWidget(ca_group)
        
        left_layout.addStretch()
        left_scroll.setWidget(left_content)
        main_layout.addWidget(left_scroll, 1)
        
        # 右侧日志区
        right_panel = QVBoxLayout()
        log_group = QGroupBox("设备调试日志")
        log_layout = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: black; color: #00FF00; font-family: Consolas;")
        log_layout.addWidget(self.log_output)
        
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_output.clear)
        log_layout.addWidget(btn_clear)
        
        log_group.setLayout(log_layout)
        right_panel.addWidget(log_group)
        main_layout.addLayout(right_panel, 1)

    def log(self, msg):
        self.log_output.append(f"[{Qt.formatDateTime(Qt.currentDateTime(), 'HH:mm:ss')}] {msg}")

    def control_aging_board(self, name, state):
        res = self.mgr.aging_board.set_relay_by_name(name, state)
        self.log(f"老化板 {name} -> {'开启' if state else '关闭'} | 结果: {res}")

    def control_easy320(self, state):
        try:
            idx = int(self.easy_index.text())
            res = self.mgr.easy320.write_relay(idx, state)
            plc_names = {1: "平推气缸", 2: "气缸抬起", 3: "气缸下压"}
            ch_num = idx + 1 if idx < 32 else idx
            alias = f" ({plc_names[ch_num]})" if ch_num in plc_names else ""
            self.log(f"Easy320 CH-{ch_num:02d}{alias} -> {'开启' if state else '关闭'} | 结果: {res}")
        except:
            self.log("错误: Easy320 索引无效")

    def read_ca550_idn(self):
        res = self.mgr.ca550.get_idn()
        self.log(f"CA550 IDN: {res}")

    def read_ca550_voltage(self):
        # 简单逻辑：设置功能并读取
        self.mgr.ca550.set_measure_func(0) # DCV
        self.mgr.ca550.set_measure_state(True)
        val = self.mgr.ca550.read_measure_data()
        self.lbl_ca_val.setText(val)
        self.log(f"CA550 读取电压: {val}")
