import base64

import os
import re

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QComboBox, QPushButton, QDoubleSpinBox, 
                               QSpinBox, QStackedWidget, QWidget, QFormLayout, QFrame, 
                               QCheckBox, QScrollArea, QGridLayout, QFileDialog, QInputDialog,
                               QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QListView, QGroupBox,
                               QCompleter)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from core.measurement_units import CURRENT_UNITS, VOLTAGE_UNITS, configured_read_unit, supports_read_units
from ui.widgets.byte_grid_widget import ByteGridWidget
from core.device_steps import DEVICES as STEP_DEVICES, ACTIONS as STEP_ACTIONS, device_key, action_spec, encode as encode_device_step, decode as decode_device_step
from ui.widgets.device_step_form import DeviceStepForm

class SmartProjectComboBox(QComboBox):
    """支持下拉菜单宽度自适应展开与全息长文本防截断的工程选择下拉框"""
    def showPopup(self):
        fm = self.fontMetrics()
        max_w = 0
        for i in range(self.count()):
            t = self.itemText(i)
            max_w = max(max_w, fm.horizontalAdvance(t))
        target_w = max(self.width(), max_w + 60, 560)
        if self.view():
            self.view().setMinimumWidth(target_w)
            self.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        super().showPopup()

class StepDialog(QDialog):
    def __init__(self, parent=None, step_data=None, device_manager=None):
        super().__init__(parent)
        self.device_manager = device_manager or getattr(parent, 'device_manager', None)
        self.is_loading = False # 状态位：标记是否处于数据加载还原中
        self.setWindowTitle("编辑指令 (子工步)")
        self.resize(680, 700)
        self.setStyleSheet("""
            QDialog { background-color: #1A1A2E; color: #E0E0E0; }
            QLabel { font-size: 14px; color: #B0B0B0; }
            QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox { 
                background-color: #16213E; 
                border: 1px solid #0F3460; 
                border-radius: 4px; 
                padding: 8px;
                color: white;
                font-size: 14px;
            }
            QCheckBox { color: #E0E0E0; font-size: 14px; spacing: 10px; }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
                border: 2px solid #4ECCA3;
                border-radius: 4px;
                background-color: #1A1A2E;
            }
            QCheckBox::indicator:checked {
                background-color: #4ECCA3;
                border: 2px solid #4ECCA3;
            }
            QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {
                border: 2px solid #00E5FF;
            }
            QRadioButton { color: #E0E0E0; font-size: 14px; spacing: 10px; }
            QRadioButton::indicator {
                width: 20px;
                height: 20px;
                border: 2px solid #4ECCA3;
                border-radius: 10px;
                background-color: #1A1A2E;
            }
            QRadioButton::indicator:checked {
                background-color: #4ECCA3;
                border: 6px solid #1A1A2E; /* Create a dot effect */
            }
            QComboBox::drop-down {
                border-left: 1px solid #0F3460;
                width: 25px;
            }
            QComboBox QAbstractItemView, QComboBox QListView {
                background-color: #17172B;
                background: #17172B;
                color: #F2F4FF;
                border: 1px solid #335A8A;
                border-radius: 4px;
                outline: 0;
                padding: 4px 0;
                selection-background-color: #0E7490;
                selection-color: #FFFFFF;
            }
            QComboBox QAbstractItemView::item, QComboBox QListView::item {
                background-color: #17172B;
                background: #17172B;
                color: #F2F4FF;
                min-height: 32px;
                padding: 4px 10px;
                border: none;
            }
            QComboBox QAbstractItemView::item:hover, QComboBox QListView::item:hover {
                background-color: #164E63;
                background: #164E63;
                color: #FFFFFF;
            }
            QComboBox QAbstractItemView::item:selected, QComboBox QListView::item:selected {
                background-color: #0E7490;
                background: #0E7490;
                color: #FFFFFF;
                font-weight: bold;
            }
            QPushButton#btn_ok { 
                background-color: #E94560; 
                color: white; 
                font-weight: bold; 
                border-radius: 6px; 
                padding: 12px;
                min-width: 140px;
            }
            QPushButton#btn_cancel { 
                background-color: #533483; 
                color: white; 
                border-radius: 6px; 
                padding: 12px;
                min-width: 100px;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        
        # 1. 设备与大类选择 (分类整理)
        top_frame = QFrame(self)
        top_layout = QFormLayout(top_frame)
        top_layout.setLabelAlignment(Qt.AlignRight)
        top_layout.setSpacing(15)
        
        # 一级分类
        self.category_combo = QComboBox()
        self.category_combo.addItems(["设备操作", "报文交互", "三方协议", "通用交互", "特殊执行"])
        top_layout.addRow("一级分类:", self.category_combo)
        
        # 二级分类 (仅在设备操作下有效)
        self.sub_category_combo = QComboBox()
        self.sub_category_combo.addItems(["直流源", "高压源", "模拟电池", "继电器", "校准源", "下载器", "测量仪器", "信号发生器", "示波器", "板卡"])
        top_layout.addRow("二级分类:", self.sub_category_combo)
        
        self.device_combo = QComboBox()
        top_layout.addRow("控制设备:", self.device_combo)
        
        self.action_combo = QComboBox()
        top_layout.addRow("功能动作:", self.action_combo)
        
        main_layout.addWidget(top_frame)
        
        # 分割线
        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #0F3460;")
        main_layout.addWidget(line)
        
        # 2. 滚动区域 (包裹动态参数和策略区)
        self.main_scroll = QScrollArea(self)
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.NoFrame)
        self.main_scroll.setStyleSheet("background-color: transparent;")
        
        self.main_scroll_content = QWidget() # 容器 Widget
        self.scroll_layout = QVBoxLayout(self.main_scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(15)
        
        # 动态参数配置区
        self.param_stack = QStackedWidget(self)
        self.scroll_layout.addWidget(self.param_stack)
        
        # --- 页面 0: 仪表设置参数 (可多选) ---
        self.page_instr = QWidget()
        instr_layout = QVBoxLayout(self.page_instr)
        instr_layout.setSpacing(15)
        
        # 电压设置行
        v_layout = QHBoxLayout()
        self.cb_volt = QCheckBox("设定电压:")
        self.cb_volt.setChecked(True)
        self.i_volt = QDoubleSpinBox()
        self.i_volt.setRange(0, 1000)
        self.i_volt.setSuffix(" V")
        self.i_volt.setDecimals(3)
        v_layout.addWidget(self.cb_volt)
        v_layout.addWidget(self.i_volt, 1)
        instr_layout.addLayout(v_layout)
        
        # 电流设置行
        c_layout = QHBoxLayout()
        self.cb_curr = QCheckBox("限制电流:")
        self.cb_curr.setChecked(True)
        self.i_curr = QDoubleSpinBox()
        self.i_curr.setRange(0, 500)
        self.i_curr.setSuffix(" A")
        self.i_curr.setDecimals(3)
        c_layout.addWidget(self.cb_curr)
        c_layout.addWidget(self.i_curr, 1)
        instr_layout.addLayout(c_layout)
        
        # 输出状态控制 (可选)
        self.cb_output = QCheckBox("同时控制输出状态")
        self.output_combo = QComboBox()
        self.output_combo.addItems(["保持现状", "开启输出", "关闭输出"])
        o_layout = QHBoxLayout()
        o_layout.addWidget(self.cb_output)
        o_layout.addWidget(self.output_combo, 1)
        instr_layout.addLayout(o_layout)
        
        instr_layout.addStretch()

        # --- 页面 1: CAN 参数 ---
        self.page_can = QWidget()
        c_form = QFormLayout(self.page_can)
        self.c_id = QLineEdit("0x7F0")
        self.c_data = QLineEdit("00 00 00 00 00 00 00 00")
        self.c_wait_id = QLineEdit("0x7F8")
        self.c_type = QComboBox()
        self.c_type.addItems(["Classic", "FD", "FD+BRS"])
        self.c_dlc = QSpinBox()
        self.c_dlc.setRange(0, 15)
        self.c_dlc.setValue(8)
        self.c_channel = QSpinBox()
        self.c_channel.setRange(0, 255)
        self.c_channel.setValue(0)
        self.c_timeout = QSpinBox()
        self.c_timeout.setRange(1, 600000)
        self.c_timeout.setValue(1000)
        self.c_timeout.setSuffix(" ms")
        c_form.addRow("帧 ID (HEX):", self.c_id)
        c_form.addRow("数据 (HEX):", self.c_data)
        c_form.addRow("等待响应 ID:", self.c_wait_id)
        c_form.addRow("CAN 类型:", self.c_type)
        c_form.addRow("DLC:", self.c_dlc)
        c_form.addRow("RNCAN通道:", self.c_channel)
        c_form.addRow("超时:", self.c_timeout)

        # --- 页面 2: 等待参数 ---
        self.page_wait = QWidget()
        w_form = QFormLayout(self.page_wait)
        self.w_time = QSpinBox()
        self.w_time.setRange(0, 86400000)
        self.w_time.setSuffix(" ms")
        self.w_time.setValue(1000)
        w_form.addRow("延时时间:", self.w_time)

        # --- 页面 3: 读取参数选择 (单选以确保判定唯一性) ---
        self.page_read = QWidget()
        read_layout = QVBoxLayout(self.page_read)
        from PySide6.QtWidgets import QRadioButton, QButtonGroup
        self.read_group = QButtonGroup(self)
        
        self.rb_volt = QRadioButton("读取电压")
        self.rb_volt.setChecked(True)
        self.rb_curr = QRadioButton("读取电流")
        
        self.read_group.addButton(self.rb_volt)
        self.read_group.addButton(self.rb_curr)
        
        read_layout.addWidget(self.rb_volt)
        read_layout.addWidget(self.rb_curr)
        self.read_unit_row = QWidget()
        read_unit_layout = QHBoxLayout(self.read_unit_row)
        read_unit_layout.setContentsMargins(0, 0, 0, 0)
        read_unit_layout.addWidget(QLabel("回读单位:"))
        self.read_unit = QComboBox()
        self.read_unit.addItems(VOLTAGE_UNITS)
        read_unit_layout.addWidget(self.read_unit)
        read_unit_layout.addStretch()
        read_layout.addWidget(self.read_unit_row)
        self.read_unit_help = QLabel("数值会换算为所选单位；测试项上下限也须按此单位填写。")
        self.read_unit_help.setWordWrap(True)
        read_layout.addWidget(self.read_unit_help)
        self.rb_volt.toggled.connect(self._update_read_units)
        read_layout.addStretch()

        self.param_stack.addWidget(self.page_instr) # 0: 设置
        self.param_stack.addWidget(self.page_can)   # 1: CAN
        self.param_stack.addWidget(self.page_wait)  # 2: 等待
        self.param_stack.addWidget(self.page_read)  # 3: 读取

        # --- 页面 4: Easy320 参数 (勾选模式) ---
        self.page_easy320 = QWidget()
        e_layout = QVBoxLayout(self.page_easy320)
        
        scroll = QScrollArea()
        scroll_content = QWidget()
        grid = QGridLayout(scroll_content)
        self.easy320_checks = []
        plc_names = {1: "平推气缸", 2: "气缸抬起", 3: "气缸下压"}
        for i in range(32):
            ch_num = i + 1
            alias = f" ({plc_names[ch_num]})" if ch_num in plc_names else ""
            cb = QCheckBox(f"CH-{ch_num}{alias}")
            grid.addWidget(cb, i // 4, i % 4)
            self.easy320_checks.append(cb)
        scroll.setWidget(scroll_content)
        scroll.setWidgetResizable(True)
        
        e_layout.addWidget(QLabel("选择操作通道 (多选):"))
        e_layout.addWidget(scroll)
        self.param_stack.addWidget(self.page_easy320) # 4

        # --- 页面 5: CA550 参数 (参数设置) ---
        self.page_ca550 = QWidget()
        ca_form = QFormLayout(self.page_ca550)
        
        self.ca_output_state = QComboBox()
        self.ca_output_state.addItems(["保持现状", "开启输出", "关闭输出"])
        
        self.ca_type = QComboBox()
        self.ca_type.addItems(["V (电压)", "mA (电流)", "TC_K", "TC_J", "TC_T", "TC_E", "TC_N", "TC_R", "TC_S", "TC_B", "mV", "OHM"])
        
        self.ca_range = QComboBox()
        self.ca_range.addItems(["Auto", "100mV", "1V", "10V", "30V", "20mA", "20mA_SINK"])
        
        self.ca_val = QDoubleSpinBox()
        self.ca_val.setRange(-1000, 2000)
        self.ca_val.setDecimals(3)
        self.ca_val.setValue(0.000)
        
        ca_form.addRow("输出状态:", self.ca_output_state)
        ca_form.addRow("输出类型:", self.ca_type)
        ca_form.addRow("输出量程:", self.ca_range)
        ca_form.addRow("输出设定值:", self.ca_val)
        
        self.param_stack.addWidget(self.page_ca550) # 5

        # --- 页面 6: 电池模拟器快捷批量配置 (LabVIEW 风格) ---
        self.page_sim_batch = QWidget()
        sim_batch_layout = QFormLayout(self.page_sim_batch)
        self.sim_batch_volt = QDoubleSpinBox()
        self.sim_batch_volt.setRange(0, 15); self.sim_batch_volt.setDecimals(3); self.sim_batch_volt.setValue(3.800)
        self.sim_batch_curr = QDoubleSpinBox()
        self.sim_batch_curr.setRange(0, 5000); self.sim_batch_curr.setDecimals(1); self.sim_batch_curr.setValue(1000.0)
        self.sim_batch_curr.setSuffix(" mA")
        self.sim_batch_output = QComboBox()
        self.sim_batch_output.addItems(["ON", "OFF"])
        self.sim_batch_range = QComboBox()
        self.sim_batch_range.addItems(["HIGH (大量程)", "LOW (小量程)"])
        self.sim_batch_channels = QLineEdit("ALL")
        
        sim_batch_layout.addRow("设定电压 (V):", self.sim_batch_volt)
        sim_batch_layout.addRow("设定电流 (mA):", self.sim_batch_curr)
        sim_batch_layout.addRow("输出状态:", self.sim_batch_output)
        sim_batch_layout.addRow("量程范围:", self.sim_batch_range)
        sim_batch_layout.addRow("作用通道:", self.sim_batch_channels)
        self.param_stack.addWidget(self.page_sim_batch) # 6

        # --- 页面 7: 3.5H EOL 协议 ---
        self.page_eol = QWidget()
        eol_form = QFormLayout(self.page_eol)
        
        # 核心通讯参数 (置顶)
        self.eol_channel = QSpinBox()
        self.eol_channel.setRange(0, 255); self.eol_channel.setValue(0)
        self.eol_tx_id = QLineEdit("0x7F0")
        self.eol_rx_id = QLineEdit("0x7F8")
        
        # 新增：CAN 类型与 DLC
        self.eol_can_type = QComboBox()
        self.eol_can_type.addItems(["Classic", "FD", "FD+BRS"])
        self.eol_dlc = QSpinBox()
        self.eol_dlc.setRange(0, 15); self.eol_dlc.setValue(8)
        
        # 业务参数
        self.eol_param2_label = QLabel("读取模式:")
        self.eol_param2 = QComboBox()
        self.eol_param1_label = QLabel("ADC选择:")
        self.eol_param1 = QComboBox()
        
        # 新增 参数3 和 参数4，用于 0x07 CSC控制读取 的节点数目和索引配置
        self.eol_param3_label = QLabel("节点数目:")
        self.eol_param3 = QSpinBox()
        self.eol_param3.setRange(0, 12)
        self.eol_param3.setValue(0)
        self.eol_param3_label.setVisible(False)
        self.eol_param3.setVisible(False)
        
        self.eol_param4_label = QLabel("电芯/Stack索引:")
        self.eol_param4 = QSpinBox()
        self.eol_param4.setRange(0, 65535)
        self.eol_param4.setValue(0)
        self.eol_param4_label.setVisible(False)
        self.eol_param4.setVisible(False)
        
        self.eol_timeout = QSpinBox()
        self.eol_timeout.setRange(1, 600000); self.eol_timeout.setValue(1000); self.eol_timeout.setSuffix(" ms")

        # 新增的绝缘测试专用参数输入框
        self.eol_hv1_label = QLabel("HV1电压:")
        self.eol_hv1 = QDoubleSpinBox()
        self.eol_hv1.setRange(0, 1000)
        self.eol_hv1.setValue(650.0)
        self.eol_hv1.setSuffix(" V")
        
        self.eol_r0_label = QLabel("R0阻值:")
        self.eol_r0 = QDoubleSpinBox()
        self.eol_r0.setRange(0, 10000)
        self.eol_r0.setValue(10.0)
        self.eol_r0.setSuffix(" kΩ")
        
        self.eol_r1_label = QLabel("R1阻值:")
        self.eol_r1 = QDoubleSpinBox()
        self.eol_r1.setRange(0, 100000)
        self.eol_r1.setValue(1000.0)
        self.eol_r1.setSuffix(" kΩ")
        
        self.eol_r2_label = QLabel("R2阻值:")
        self.eol_r2 = QDoubleSpinBox()
        self.eol_r2.setRange(0, 100000)
        self.eol_r2.setValue(1000.0)
        self.eol_r2.setSuffix(" kΩ")
        
        self.eol_r3_label = QLabel("R3阻值:")
        self.eol_r3 = QDoubleSpinBox()
        self.eol_r3.setRange(0, 100000)
        self.eol_r3.setValue(5000.0)
        self.eol_r3.setSuffix(" kΩ")
        
        self.eol_r4_label = QLabel("R4阻值:")
        self.eol_r4 = QDoubleSpinBox()
        self.eol_r4.setRange(0, 100000)
        self.eol_r4.setValue(5000.0)
        self.eol_r4.setSuffix(" kΩ")

        # 隐藏参数 (仅用于后台逻辑，需填充选项以支持数据匹配)
        self.eol_op = QComboBox()
        self.eol_op.addItems([
            "0x03 绝缘控制读取", "0x03 绝缘控制写入",
            "0x04 GPIO控制读取", "0x04 GPIO控制写入",
            "0x05 PWM读取", "0x06 ADC读取",
            "0x07 CSC控制读取", "0x07 CSC控制写入", "0x07 CSC批量读取",
            "0x08 CRASH读取", "0x09 RTC控制读取", "0x09 RTC控制写入",
            "0x10 NTC读取", "0x0A EEPROM控制读取", "0x0A EEPROM控制写入",
            "0x0B 霍尔电流读取", "0xFF 扩展指令",
            "EEPROM测试", "绝缘测试", "DTC诊断", "DTC故障清除", "DTU诊断"
        ])
        self.eol_op.setVisible(False)
        self.eol_args = QLineEdit("")
        self.eol_args.setPlaceholderText("可选，格式 KEY:VALUE / KEY2:VALUE2")
        self.eol_args_label = QLabel("附加参数:")

        # 添加到布局 (按用户指定顺序)
        eol_form.addRow("RNCAN通道:", self.eol_channel)
        eol_form.addRow("发送ID:", self.eol_tx_id)
        eol_form.addRow("接收ID:", self.eol_rx_id)
        eol_form.addRow("CAN 类型:", self.eol_can_type)
        eol_form.addRow("DLC:", self.eol_dlc)
        eol_form.addRow(self.eol_param2_label, self.eol_param2)
        eol_form.addRow(self.eol_param1_label, self.eol_param1)
        eol_form.addRow(self.eol_param3_label, self.eol_param3)
        eol_form.addRow(self.eol_param4_label, self.eol_param4)
        
        temp_opts_layout = QHBoxLayout()
        self.eol_diff_ambient = QCheckBox("计算与环境温度差")
        self.eol_diff_ambient.setStyleSheet("color: #00E5FF;")
        self.eol_diff_ambient.setVisible(False)
        temp_opts_layout.addWidget(self.eol_diff_ambient)

        self.eol_temp_convert = QCheckBox("温度换算")
        self.eol_temp_convert.setStyleSheet("color: #00E5FF;")
        self.eol_temp_convert.setVisible(False)
        temp_opts_layout.addWidget(self.eol_temp_convert)
        temp_opts_layout.addStretch()

        eol_form.addRow("", temp_opts_layout)
        
        self.eol_fix_adjacent = QCheckBox("相邻电芯异常值修正 (之和为4.5~5.5V)")
        self.eol_fix_adjacent.setStyleSheet("color: #00E5FF;")
        self.eol_fix_adjacent.setVisible(False)
        eol_form.addRow("", self.eol_fix_adjacent)
        
        self.eol_compensate_random = QCheckBox("开启异常电压随机补偿")
        self.eol_compensate_random.setStyleSheet("color: #00E5FF;")
        self.eol_compensate_random.setVisible(False)
        eol_form.addRow("", self.eol_compensate_random)
        
        eol_form.addRow("超时时间:", self.eol_timeout)
        eol_form.addRow(self.eol_hv1_label, self.eol_hv1)
        eol_form.addRow(self.eol_r0_label, self.eol_r0)
        eol_form.addRow(self.eol_r1_label, self.eol_r1)
        eol_form.addRow(self.eol_r2_label, self.eol_r2)
        eol_form.addRow(self.eol_r3_label, self.eol_r3)
        eol_form.addRow(self.eol_r4_label, self.eol_r4)
        
        # 兼容旧代码的占位符
        eol_form.addRow("", self.eol_op) 
        eol_form.addRow(self.eol_args_label, self.eol_args)
        
        self.param_stack.addWidget(self.page_eol) # 7

        # --- 页面 8: 老化板继电器参数 (22路勾选模式) ---
        self.page_aging_relay = QWidget()
        ag_layout = QVBoxLayout(self.page_aging_relay)
        
        ag_scroll = QScrollArea()
        ag_scroll_content = QWidget()
        ag_grid = QGridLayout(ag_scroll_content)
        self.aging_relay_checks = []
        
        # 继电器名称列表 (来自 aging_board_driver.py)
        relay_names = [
            "KL15", "CC1_2K_12V", "ISO_NEG_SHORT", "CHAOJI_CC_1K", 
            "IN1_OUT1", "IN2_OUT2", "IN3_OUT3", "CAN_MATCH",
            "SIG1_SIG2_SHORT", "SIG3_SHORT", "CAN1", "CAN2",
            "CAN3", "CAN4", "HV", "ISOD_30K_1M",
            "ISOD_1M_30K", "LINK_PACK_SHORT", "FACH_PACK_SHORT",
            "DC_DC_100K", "DC_DC_500K", "HALL_POWER"
        ]
        
        for i, name in enumerate(relay_names):
            cb = QCheckBox(name)
            ag_grid.addWidget(cb, i // 3, i % 3)
            self.aging_relay_checks.append(cb)
            
        ag_scroll.setWidget(ag_scroll_content)
        ag_scroll.setWidgetResizable(True)
        ag_layout.addWidget(QLabel("选择老化板操作通道 (22路):"))
        ag_layout.addWidget(ag_scroll)
        self.param_stack.addWidget(self.page_aging_relay) # 8

        # --- 页面 9: 变量操作 ---
        self.page_variable = QWidget()
        var_form = QFormLayout(self.page_variable)
        self.var_name = QComboBox()
        self.var_name.setEditable(True)
        self.var_name.lineEdit().setPlaceholderText("可手动输入或选择变量名，如 CSC_CELL_0")
        
        # 预设系统支持的所有共享变量（内置变量 + 192个CSC电芯变量）
        preset_vars = ["正极绝缘", "负极绝缘", "环境温度"]
        for i in range(192):
            preset_vars.append(f"CSC_CELL_{i}")
            
        self.var_name.addItems(preset_vars)
        var_form.addRow("变量名称:", self.var_name)
        self.param_stack.addWidget(self.page_variable) # 9

        # --- 页面 10: 公式计算 ---
        self.page_formula = QWidget()
        formula_layout = QFormLayout(self.page_formula)
        self.formula_expr = QLineEdit("")
        self.formula_expr.setPlaceholderText("请输入计算公式，例如: STEP4 * 1.5 - STEP3")
        self.formula_var = QComboBox()
        self.formula_var.setEditable(True)
        self.formula_var.lineEdit().setPlaceholderText("可选，输入共享变量名以保存计算结果")
        self.formula_var.addItems(preset_vars)
        
        formula_layout.addRow("公式表达式:", self.formula_expr)
        formula_layout.addRow("写入变量名:", self.formula_var)
        self.param_stack.addWidget(self.page_formula) # 10

        # --- 页面 11: 力捷丰 SG400 下载器 ---
        self.page_opteeq = QWidget()
        opteeq_layout = QFormLayout(self.page_opteeq)
        self.opteeq_project = SmartProjectComboBox()
        self.opteeq_project.setEditable(True)
        opteeq_view = QListView()
        opteeq_view.setAutoFillBackground(True)
        self.opteeq_project.setView(opteeq_view)
        self.opteeq_project.view().setAutoFillBackground(True)
        if self.opteeq_project.view().viewport():
            self.opteeq_project.view().viewport().setAutoFillBackground(True)
        if self.opteeq_project.lineEdit():
            self.opteeq_project.lineEdit().setPlaceholderText("选择下载器脱机工程或输入/浏览本地文件")

        opteeq_btn_row = QWidget()
        opteeq_btn_layout = QHBoxLayout(opteeq_btn_row)
        opteeq_btn_layout.setContentsMargins(0, 0, 0, 0)
        opteeq_btn_layout.setSpacing(10)

        self.btn_read_opteeq = QPushButton("🔄 读取下载器工程")
        self.btn_read_opteeq.setStyleSheet("background-color: #0E7490; color: white; padding: 6px 12px; font-size: 13px; font-weight: bold; border-radius: 4px;")
        self.btn_read_opteeq.clicked.connect(self.read_downloader_projects)
        opteeq_btn_layout.addWidget(self.btn_read_opteeq)

        self.btn_opteeq_project = QPushButton("📂 本地文件")
        self.btn_opteeq_project.setStyleSheet("background-color: #334155; color: white; padding: 6px 12px; font-size: 13px; border-radius: 4px;")
        self.btn_opteeq_project.clicked.connect(self.select_opteeq_project)
        opteeq_btn_layout.addWidget(self.btn_opteeq_project)
        opteeq_btn_layout.addStretch()

        self.opteeq_timeout = QSpinBox()
        self.opteeq_timeout.setRange(1, 3600)
        self.opteeq_timeout.setValue(300)
        self.opteeq_timeout.setSuffix(" s")

        opteeq_layout.addRow("烧录工程:", self.opteeq_project)
        opteeq_layout.addRow("工程操作:", opteeq_btn_row)
        opteeq_layout.addRow("完成超时:", self.opteeq_timeout)

        # --- 烧录数据物理比对与源程序防呆校验面板 (Deep Diff) ---
        self.group_burn_verify = QGroupBox("📋 烧录数据物理比对与源程序防呆校验 (Deep Diff)")
        self.group_burn_verify.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #334155;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 14px;
                background-color: #121927;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #38BDF8;
                font-size: 13px;
            }
        """)
        verify_layout = QVBoxLayout(self.group_burn_verify)
        verify_layout.setContentsMargins(12, 10, 12, 10)
        verify_layout.setSpacing(8)

        self.cb_opteeq_verify_diff = QCheckBox("启用单片机 Flash 物理回读与源程序逐字节比对校验")
        self.cb_opteeq_verify_diff.setToolTip("烧录成功后，自动调用下载器从单片机回读 Flash 物理数据，与工程源数据逐字节比对防呆")
        self.cb_opteeq_verify_diff.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 13px;")
        verify_layout.addWidget(self.cb_opteeq_verify_diff)

        # 详细配置子容器
        self.verify_sub_container = QWidget()
        verify_sub_layout = QFormLayout(self.verify_sub_container)
        verify_sub_layout.setContentsMargins(14, 2, 4, 4)
        verify_sub_layout.setSpacing(8)

        self.combo_opteeq_verify_scope = QComboBox()
        self.combo_opteeq_verify_scope.addItems([
            "整包程序比对 (按固件实际大小, 推荐)",
            "首物理扇区 (快速 64KB)",
            "全物理扇区深度比对"
        ])
        verify_sub_layout.addRow("回读比对范围:", self.combo_opteeq_verify_scope)

        self.cb_opteeq_archive_report = QCheckBox("自动生成单板全息比对报告与双轨数据镜像 (logs/burn_records/)")
        self.cb_opteeq_archive_report.setChecked(True)
        self.cb_opteeq_archive_report.setStyleSheet("color: #E2E8F0; font-size: 12px;")
        verify_sub_layout.addRow("留痕归档:", self.cb_opteeq_archive_report)

        self.lbl_opteeq_meta_preview = QLabel("ℹ️ 源程序元数据: 切换或选定工程后自动探测物理扇区与基准CRC")
        self.lbl_opteeq_meta_preview.setStyleSheet("color: #94A3B8; font-size: 12px; font-style: italic;")
        self.lbl_opteeq_meta_preview.setWordWrap(True)
        verify_sub_layout.addRow("源信息预览:", self.lbl_opteeq_meta_preview)

        verify_layout.addWidget(self.verify_sub_container)
        self.verify_sub_container.setEnabled(False)
        self.cb_opteeq_verify_diff.toggled.connect(self.verify_sub_container.setEnabled)
        self.opteeq_project.currentTextChanged.connect(self._update_opteeq_meta_preview)

        opteeq_layout.addRow(self.group_burn_verify)
        self.param_stack.addWidget(self.page_opteeq) # 11

        # --- 页面 13: APP 刷写测试工步 ---
        self.page_app_flash = QWidget()
        app_flash_layout = QFormLayout(self.page_app_flash)
        self.app_flash_program = QLineEdit(os.path.abspath(os.getcwd()))
        self.app_flash_program.setPlaceholderText("上位机程序文件或所在目录")
        app_program_row = QWidget()
        app_program_row_layout = QHBoxLayout(app_program_row)
        app_program_row_layout.setContentsMargins(0, 0, 0, 0)
        app_program_row_layout.addWidget(self.app_flash_program, 1)
        self.btn_app_flash_program = QPushButton("选择文件")
        self.btn_app_flash_program.clicked.connect(self.select_app_flash_program)
        app_program_row_layout.addWidget(self.btn_app_flash_program)
        self.btn_app_flash_dir = QPushButton("选择目录")
        self.btn_app_flash_dir.clicked.connect(self.select_app_flash_directory)
        app_program_row_layout.addWidget(self.btn_app_flash_dir)
        self.app_flash_ini = QLineEdit("config.ini")
        self.app_flash_section = QLineEdit("App")
        self.app_flash_key = QLineEdit("addr")
        self.app_flash_name = QLineEdit()
        self.app_flash_name.setPlaceholderText("可留空；多个刷写文件时填写要选择的文件名")
        self.app_flash_path_help = QLabel(
            "路径选择说明：请选择上位机程序文件（如 .exe），也可以直接填写上位机所在目录。\n"
            "系统会读取上位机同级的 config.ini → [App] → addr，自动到 "
            "上位机目录\\Flash\\<addr值>\\ 中查找刷写文件。\n"
            "测试值只返回文件名（含扩展名），由测试项的字符串标准判定；"
            "目录中只有一个文件时无需填写筛选名称。"
        )
        self.app_flash_path_help.setWordWrap(True)
        self.app_flash_path_help.setStyleSheet(
            "color:#38BDF8; background-color:#0F172A; border:1px solid #1E3A5F; "
            "border-radius:5px; padding:8px;"
        )
        self.app_flash_record_help = QLabel(
            "路径选择说明：请选择与查找刷写文件工步相同的上位机程序或所在目录。\n"
            "系统会自动定位到 上位机目录\\Flash\\Record.txt，写入 Start 后监控 "
            "FLASH / PASS / FAIL。"
        )
        self.app_flash_record_help.setWordWrap(True)
        self.app_flash_record_help.setStyleSheet(
            "color:#38BDF8; background-color:#0F172A; border:1px solid #1E3A5F; "
            "border-radius:5px; padding:8px;"
        )
        self.app_flash_record = QLineEdit(os.path.join("Flash", "Record.txt"))
        self.app_flash_record.setPlaceholderText("Record.txt 的绝对路径")
        self.app_flash_timeout = QSpinBox()
        self.app_flash_timeout.setRange(1, 86400)
        self.app_flash_timeout.setValue(300)
        self.app_flash_timeout.setSuffix(" s")
        self.app_flash_program_label = QLabel("上位机路径:")
        self.app_flash_ini_label = QLabel("INI文件名/路径:")
        self.app_flash_section_label = QLabel("Section (段名):")
        self.app_flash_key_label = QLabel("Key (键名):")
        self.app_flash_name_label = QLabel("文件名筛选 (可选):")
        self.app_flash_record_label = QLabel("Record路径:")
        self.app_flash_timeout_label = QLabel("刷写超时:")
        app_flash_layout.addRow(self.app_flash_program_label, app_program_row)
        app_flash_layout.addRow(self.app_flash_ini_label, self.app_flash_ini)
        app_flash_layout.addRow(self.app_flash_section_label, self.app_flash_section)
        app_flash_layout.addRow(self.app_flash_key_label, self.app_flash_key)
        app_flash_layout.addRow(self.app_flash_name_label, self.app_flash_name)
        app_flash_layout.addRow("", self.app_flash_path_help)
        app_flash_layout.addRow(self.app_flash_record_label, self.app_flash_record)
        app_flash_layout.addRow(self.app_flash_timeout_label, self.app_flash_timeout)
        app_flash_layout.addRow("", self.app_flash_record_help)
        self.param_stack.addWidget(self.page_app_flash) # 12

        # --- 页面 12: UDS 服务交互列表 ---
        self.page_uds = QWidget()
        uds_layout = QVBoxLayout(self.page_uds)
        uds_link_row = QHBoxLayout()
        self.uds_tx_id = QLineEdit("0x7E0")
        self.uds_rx_id = QLineEdit("0x7E8")
        self.uds_can_type = QComboBox()
        self.uds_can_type.addItems(["Classic", "FD", "FD+BRS"])
        self.uds_timeout = QSpinBox()
        self.uds_timeout.setRange(1, 600000)
        self.uds_timeout.setValue(1000)
        self.uds_timeout.setSuffix(" ms")
        for label, widget in (("请求ID", self.uds_tx_id), ("响应ID", self.uds_rx_id),
                              ("CAN类型", self.uds_can_type), ("超时", self.uds_timeout)):
            uds_link_row.addWidget(QLabel(label + ":"))
            uds_link_row.addWidget(widget)
        uds_layout.addLayout(uds_link_row)

        uds_edit_row = QHBoxLayout()
        self.uds_service = QComboBox()
        self.uds_service.addItems([
            "10 诊断会话控制", "11 ECU复位", "14 清除诊断信息",
            "19 读取DTC信息", "22 按标识符读取数据", "23 按地址读取内存",
            "27 安全访问", "28 通讯控制", "2A 按周期读取数据",
            "2C 动态定义数据标识符", "2E 按标识符写数据",
            "2F 输入输出控制", "31 例程控制", "34 请求下载",
            "36 传输数据", "37 请求退出传输", "3D 按地址写内存",
            "3E 测试器在线", "83 访问时间参数", "84 安全数据传输",
            "85 控制DTC设置", "86 基于事件响应", "87 链路控制", "自定义"
        ])
        self.uds_service.setMaxVisibleItems(12)
        self.uds_service.view().setMinimumWidth(320)
        self.uds_service.view().setStyleSheet("""
            QListView {
                background-color: #17172B;
                color: #F2F4FF;
                border: 1px solid #335A8A;
                outline: none;
                padding: 4px;
            }
            QListView::item { min-height: 32px; padding: 2px 10px; }
            QListView::item:hover { background-color: #164E63; color: white; }
            QListView::item:selected { background-color: #0E7490; color: white; }
        """)
        self.uds_service.currentTextChanged.connect(self.on_uds_service_changed)
        self.uds_request = QLineEdit()
        self.uds_request.setPlaceholderText("请求PDU，例如 22 F1 90")
        self.uds_expected = QLineEdit()
        self.uds_expected.setPlaceholderText("期望响应，例如 62 F1 90 ?? ??")
        self.uds_request.textChanged.connect(self.on_uds_request_changed)
        self.uds_request.textChanged.connect(lambda: self._sync_byte_grid_from_pdu())
        self.uds_expected.textChanged.connect(lambda: self._sync_byte_grid_from_pdu())
        self.uds_add_btn = QPushButton("添加到列表")
        self.uds_add_btn.clicked.connect(self.add_uds_row)
        self.uds_update_btn = QPushButton("更新选中")
        self.uds_update_btn.clicked.connect(self.update_uds_row)
        uds_edit_row.addWidget(self.uds_service, 2)
        uds_edit_row.addWidget(self.uds_request, 3)
        uds_edit_row.addWidget(self.uds_expected, 3)
        uds_edit_row.addWidget(self.uds_add_btn)
        uds_edit_row.addWidget(self.uds_update_btn)
        uds_layout.addLayout(uds_edit_row)

        uds_rule_row = QHBoxLayout()
        self.uds_auto_response = QCheckBox("自动生成肯定响应")
        self.uds_auto_response.setChecked(True)
        self.uds_auto_response.toggled.connect(lambda checked: checked and self.on_uds_request_changed(self.uds_request.text()))
        uds_rule_row.addWidget(self.uds_auto_response)
        uds_rule_row.addWidget(QLabel("按响应前缀校验；可手动补充数据，?? 表示任意字节"))
        uds_rule_row.addStretch()
        uds_layout.addLayout(uds_rule_row)

        # --- 新增: UDS 响应数据提取与数值计算配置卡片 (可视化拖拽选区增强) ---
        self.uds_extract_group = QGroupBox("📊 响应数据提取、ASCII转换与多字节算术计算")
        self.uds_extract_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #334155;
                border-radius: 6px;
                margin-top: 6px;
                padding-top: 10px;
                background-color: #0F172A;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 5px;
                color: #38BDF8;
            }
        """)
        uds_ext_vbox = QVBoxLayout(self.uds_extract_group)
        uds_ext_vbox.setSpacing(6)
        uds_ext_vbox.setContentsMargins(10, 8, 10, 8)

        # 0. 协议规范与常用 DID 字典选择栏 (万能兼容与一键预填)
        row_dict = QHBoxLayout()
        row_dict.setSpacing(6)

        self.uds_proto_fmt = QComboBox()
        self.uds_proto_fmt.addItems(["标准 UDS (ISO 14229, 偏移 3)", "私有协议 (含长度字节, 偏移 4)", "自定义协议/偏移"])
        self.uds_proto_fmt.setStyleSheet("background-color: #1E293B; color: #38BDF8; font-weight: bold; font-size: 11px;")
        self.uds_proto_fmt.currentIndexChanged.connect(self._on_uds_proto_fmt_changed)

        from core.uds_dictionary import UDSDictionaryManager
        self.uds_dict_mgr = UDSDictionaryManager()

        self.combo_uds_templates = QComboBox()
        self.combo_uds_templates.setStyleSheet("background-color: #1E293B; color: #FDE047; font-weight: bold; font-size: 11px;")
        if self.combo_uds_templates.view():
            self.combo_uds_templates.view().setMinimumWidth(380)
        self._reload_uds_templates_combo()
        self.combo_uds_templates.currentIndexChanged.connect(self._on_uds_template_selected)

        self.btn_save_did_tpl = QPushButton("💾 存为模板")
        self.btn_save_did_tpl.setToolTip("将当前配置的 DID、数据类型、比例与单位保存到本地字典库，随用随取")
        self.btn_save_did_tpl.setStyleSheet("background-color: #0E7490; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px;")
        self.btn_save_did_tpl.clicked.connect(self._on_save_current_as_template)

        self.btn_import_did_csv = QPushButton("📂 导入CSV字典")
        self.btn_import_did_csv.setToolTip("从外部 CSV 表格批量导入成百上千条企业内部 DID 协议")
        self.btn_import_did_csv.setStyleSheet("background-color: #334155; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px;")
        self.btn_import_did_csv.clicked.connect(self._on_import_did_csv)

        self.btn_live_sniff = QPushButton("📡 在线抓取 (Sniff)")
        self.btn_live_sniff.setToolTip("通过 PCAN 向真实被测 BMS 发送当前请求并抓取真实回包，瞬间生成方块")
        self.btn_live_sniff.setStyleSheet("background-color: #7C3AED; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 11px;")
        self.btn_live_sniff.clicked.connect(self._on_live_sniff_clicked)

        row_dict.addWidget(QLabel("协议:"))
        row_dict.addWidget(self.uds_proto_fmt, 2)
        row_dict.addWidget(QLabel("模板:"))
        row_dict.addWidget(self.combo_uds_templates, 3)
        row_dict.addWidget(self.btn_save_did_tpl)
        row_dict.addWidget(self.btn_import_did_csv)
        row_dict.addWidget(self.btn_live_sniff)
        uds_ext_vbox.addLayout(row_dict)

        # 1. 可视化交互式拖拽字节流组件 (Byte Blocks)
        lbl_grid_tip = QLabel("🖱️ 响应报文方块 (在下方方块上按住鼠标左键直接滑动拖拽选区，系统自动计算偏移量和长度):")
        lbl_grid_tip.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: normal;")
        uds_ext_vbox.addWidget(lbl_grid_tip)

        self.uds_byte_grid = ByteGridWidget(self)
        self.uds_byte_grid.range_selected.connect(self._on_byte_grid_range_selected)
        uds_ext_vbox.addWidget(self.uds_byte_grid)

        # 2. 实时全息预览栏 (选区、Hex、大端整数、物理换算值)
        self.uds_preview_frame = QFrame()
        self.uds_preview_frame.setStyleSheet("""
            QFrame {
                background-color: #090E17;
                border: 1px dashed #0284C7;
                border-radius: 6px;
            }
        """)
        prev_layout = QHBoxLayout(self.uds_preview_frame)
        prev_layout.setContentsMargins(8, 4, 8, 4)
        prev_layout.setSpacing(14)

        self.lbl_byte_range = QLabel("【拖拽选区】 未选择")
        self.lbl_byte_range.setStyleSheet("font-size: 11px; color: #38BDF8; font-weight: bold;")
        self.lbl_byte_hex = QLabel("Hex: --")
        self.lbl_byte_hex.setStyleSheet("font-size: 11px; color: #A78BFA; font-family: Consolas, monospace;")
        self.lbl_byte_int = QLabel("大端整数: --")
        self.lbl_byte_int.setStyleSheet("font-size: 11px; color: #34D399;")
        self.lbl_byte_phys = QLabel("换算物理值: --")
        self.lbl_byte_phys.setStyleSheet("font-size: 11px; color: #FBBF24;")

        prev_layout.addWidget(self.lbl_byte_range)
        prev_layout.addWidget(self.lbl_byte_hex)
        prev_layout.addWidget(self.lbl_byte_int)
        prev_layout.addWidget(self.lbl_byte_phys)
        prev_layout.addStretch()
        uds_ext_vbox.addWidget(self.uds_preview_frame)

        # 3. 快捷设定操作行
        row_quick_btns = QHBoxLayout()
        row_quick_btns.setSpacing(8)
        self.btn_quick_ascii = QPushButton("设为 ASCII 字符串")
        self.btn_quick_ascii.setStyleSheet("background-color: #334155; color: white; padding: 4px 10px; border-radius: 4px; font-size: 11px;")
        self.btn_quick_ascii.clicked.connect(lambda: self.uds_extract_mode.setCurrentIndex(1))

        self.btn_quick_calc = QPushButton("设为数值计算 (Scale/Offset)")
        self.btn_quick_calc.setStyleSheet("background-color: #0E7490; color: white; font-weight: bold; padding: 4px 12px; border-radius: 4px; font-size: 11px;")
        self.btn_quick_calc.clicked.connect(lambda: self.uds_extract_mode.setCurrentIndex(2))

        self.btn_quick_formula = QPushButton("设为自定义公式")
        self.btn_quick_formula.setStyleSheet("background-color: #334155; color: white; padding: 4px 10px; border-radius: 4px; font-size: 11px;")
        self.btn_quick_formula.clicked.connect(lambda: self.uds_extract_mode.setCurrentIndex(3))

        row_quick_btns.addWidget(QLabel("快捷操作:"))
        row_quick_btns.addWidget(self.btn_quick_ascii)
        row_quick_btns.addWidget(self.btn_quick_calc)
        row_quick_btns.addWidget(self.btn_quick_formula)
        row_quick_btns.addStretch()
        uds_ext_vbox.addLayout(row_quick_btns)

        # 4. 第一行: 提取模式、起始偏移、截取长度、写入变量名
        row1_ext = QHBoxLayout()
        self.uds_extract_mode = QComboBox()
        self.uds_extract_mode.addItems(["无数据提取", "ASCII 字符串解码 (如 VIN/版本号)", "数值计算 CALC (原始值 * 比例 + 偏移)", "自定义公式 FORMULA (如 (val*0.05)+12)"])
        self.uds_extract_mode.setStyleSheet("background-color: #1E293B; color: #F8FAFC;")
        self.uds_extract_mode.currentIndexChanged.connect(self._on_uds_extract_mode_changed)

        self.uds_extract_offset = QSpinBox()
        self.uds_extract_offset.setRange(0, 4096)
        self.uds_extract_offset.setValue(3)
        self.uds_extract_offset.setToolTip("起始字节偏移量，默认 3 (跳过 1B SID + 2B DID)")
        self.uds_extract_offset.valueChanged.connect(self._on_spinbox_offset_or_len_changed)

        self.uds_extract_len = QSpinBox()
        self.uds_extract_len.setRange(0, 4096)
        self.uds_extract_len.setValue(0)
        self.uds_extract_len.setToolTip("截取字节长度，0 表示截取到末尾")
        self.uds_extract_len.valueChanged.connect(self._on_spinbox_offset_or_len_changed)

        self.uds_extract_var = QLineEdit()
        self.uds_extract_var.setPlaceholderText("写入变量名 (如 ECU_VIN, BMS_VOLT)")
        self.uds_extract_var.setToolTip("提取或计算后的结果将存入 self.variables[变量名]，供后续工步或判定使用")

        row1_ext.addWidget(QLabel("提取模式:"))
        row1_ext.addWidget(self.uds_extract_mode, 3)
        row1_ext.addWidget(QLabel("偏移:"))
        row1_ext.addWidget(self.uds_extract_offset, 1)
        row1_ext.addWidget(QLabel("长度:"))
        row1_ext.addWidget(self.uds_extract_len, 1)
        row1_ext.addWidget(QLabel("变量名:"))
        row1_ext.addWidget(self.uds_extract_var, 2)
        uds_ext_vbox.addLayout(row1_ext)

        # 第二行: 数值计算专属控件 (端序、有符号、比例、偏移量、单位、自定义公式)
        self.uds_ext_detail_widget = QWidget()
        row2_ext = QHBoxLayout(self.uds_ext_detail_widget)
        row2_ext.setContentsMargins(0, 0, 0, 0)
        row2_ext.setSpacing(8)

        self.uds_extract_endian = QComboBox()
        self.uds_extract_endian.addItems(["大端 (Big-Endian)", "小端 (Little-Endian)"])
        self.uds_extract_signed = QCheckBox("有符号")

        self.uds_extract_scale = QDoubleSpinBox()
        self.uds_extract_scale.setRange(-100000.0, 100000.0)
        self.uds_extract_scale.setDecimals(4)
        self.uds_extract_scale.setValue(1.0)
        self.uds_extract_scale.setToolTip("乘法比例系数 (Scale)")

        self.uds_extract_offset_val = QDoubleSpinBox()
        self.uds_extract_offset_val.setRange(-100000.0, 100000.0)
        self.uds_extract_offset_val.setDecimals(4)
        self.uds_extract_offset_val.setValue(0.0)
        self.uds_extract_offset_val.setToolTip("加法偏移量 (Offset)")

        self.uds_extract_unit = QLineEdit()
        self.uds_extract_unit.setPlaceholderText("单位(V, A, ℃)")
        self.uds_extract_unit.setMaximumWidth(80)

        self.uds_extract_formula = QLineEdit()
        self.uds_extract_formula.setPlaceholderText("公式，如 (val * 0.05) + 12")

        # 标签引用方便切换
        self.lbl_ext_endian = QLabel("端序:")
        self.lbl_ext_scale = QLabel("比例:")
        self.lbl_ext_offset = QLabel("偏移量:")
        self.lbl_ext_unit = QLabel("单位:")
        self.lbl_ext_formula = QLabel("公式:")

        row2_ext.addWidget(self.lbl_ext_endian)
        row2_ext.addWidget(self.uds_extract_endian)
        row2_ext.addWidget(self.uds_extract_signed)
        row2_ext.addWidget(self.lbl_ext_scale)
        row2_ext.addWidget(self.uds_extract_scale)
        row2_ext.addWidget(self.lbl_ext_offset)
        row2_ext.addWidget(self.uds_extract_offset_val)
        row2_ext.addWidget(self.lbl_ext_formula)
        row2_ext.addWidget(self.uds_extract_formula)
        row2_ext.addWidget(self.lbl_ext_unit)
        row2_ext.addWidget(self.uds_extract_unit)

        uds_ext_vbox.addWidget(self.uds_ext_detail_widget)
        uds_layout.addWidget(self.uds_extract_group)
        self._on_uds_extract_mode_changed(0)

        # 面向产线用户的简易入口：选一个诊断项目即可自动生成请求与解析规则。
        self.uds_simple_group = QGroupBox("✨ 简易配置（推荐）")
        simple_layout = QVBoxLayout(self.uds_simple_group)
        simple_row = QHBoxLayout()
        self.uds_simple_template = QComboBox()
        self.uds_simple_template.setEditable(True)
        self.uds_simple_template.setInsertPolicy(QComboBox.NoInsert)
        self.uds_simple_template.completer().setFilterMode(Qt.MatchContains)
        self.uds_simple_template.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.uds_simple_template.completer().setCompletionMode(QCompleter.PopupCompletion)
        if self.uds_simple_template.lineEdit():
            self.uds_simple_template.lineEdit().setPlaceholderText("输入名称或 DID 搜索，例如：总压、VIN、F190")
        self.uds_simple_template.currentIndexChanged.connect(self._on_uds_simple_template_selected)
        self.btn_uds_simple_add = QPushButton("生成并加入列表")
        self.btn_uds_simple_add.setStyleSheet("background-color: #0E7490; color: white; font-weight: bold;")
        self.btn_uds_simple_add.clicked.connect(self._add_simple_uds_item)
        simple_row.addWidget(QLabel("诊断项目:"))
        simple_row.addWidget(self.uds_simple_template, 1)
        simple_row.addWidget(self.btn_uds_simple_add)
        simple_layout.addLayout(simple_row)
        self.lbl_uds_simple_preview = QLabel("选择项目后，系统会自动配置请求、响应校验、数据换算和结果变量。")
        self.lbl_uds_simple_preview.setWordWrap(True)
        self.lbl_uds_simple_preview.setStyleSheet("color: #7DD3FC; padding: 4px;")
        simple_layout.addWidget(self.lbl_uds_simple_preview)
        uds_layout.insertWidget(1, self.uds_simple_group)
        self._reload_uds_templates_combo()

        self.uds_table = QTableWidget(0, 5)
        self.uds_table.setHorizontalHeaderLabels(["名称", "请求PDU", "期望响应", "校验", "数据提取/计算"])
        self.uds_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.uds_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.uds_table.cellDoubleClicked.connect(self.load_uds_row)
        self.uds_table.horizontalHeader().setStretchLastSection(True)
        self.uds_table.setColumnWidth(0, 130)
        self.uds_table.setColumnWidth(1, 150)
        self.uds_table.setColumnWidth(2, 160)
        self.uds_table.setColumnWidth(3, 70)
        self.uds_table.setColumnWidth(4, 220)
        uds_layout.addWidget(self.uds_table, 1)

        uds_control_row = QHBoxLayout()
        for text, handler in (("删除", self.delete_uds_row), ("上移", lambda: self.move_uds_row(-1)),
                              ("下移", lambda: self.move_uds_row(1)), ("清空", self.clear_uds_rows)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            uds_control_row.addWidget(button)
        uds_control_row.addStretch()
        self.uds_cycles = QSpinBox()
        self.uds_cycles.setRange(1, 100000)
        self.uds_cycles.setValue(1)
        self.uds_interval = QSpinBox()
        self.uds_interval.setRange(0, 600000)
        self.uds_interval.setValue(1)
        self.uds_interval.setSuffix(" ms")
        uds_control_row.addWidget(QLabel("循环次数:"))
        uds_control_row.addWidget(self.uds_cycles)
        uds_control_row.addWidget(QLabel("请求间隔:"))
        uds_control_row.addWidget(self.uds_interval)
        self.param_stack.addWidget(self.page_uds) # 13

        # --- 页面 14: 空参数占位页面 (针对无需配置额外参数的动作) ---
        self.page_empty = QWidget()
        empty_layout = QVBoxLayout(self.page_empty)
        empty_layout.setContentsMargins(15, 25, 15, 25)
        self.lbl_empty_param_tip = QLabel("ℹ️ 当前动作无需配置额外参数")
        self.lbl_empty_param_tip.setAlignment(Qt.AlignCenter)
        self.lbl_empty_param_tip.setStyleSheet("color: #64748B; font-size: 14px; font-weight: bold; padding: 25px 0;")
        empty_layout.addWidget(self.lbl_empty_param_tip)
        empty_layout.addStretch()
        self.param_stack.addWidget(self.page_empty) # 14

        # --- 页面 15: 瑞萨 E2 仿真器 (rfp-cli) ---
        self.page_e2_rfp = QWidget()
        e2_layout = QFormLayout(self.page_e2_rfp)

        e2_defaults = {}
        try:
            db = getattr(self.device_manager, 'db_manager', None) if self.device_manager else None
            if db:
                cfg = db.load_sys_config() or {}
                if isinstance(cfg, dict):
                    e2_defaults = cfg
        except Exception:
            e2_defaults = {}

        self.e2_file = SmartProjectComboBox()
        self.e2_file.setEditable(True)
        if self.e2_file.lineEdit():
            self.e2_file.lineEdit().setPlaceholderText("浏览或输入本地烧录工程文件 (.rpj/.mot/.hex/.srec)")

        e2_btn_row = QWidget()
        e2_btn_layout = QHBoxLayout(e2_btn_row)
        e2_btn_layout.setContentsMargins(0, 0, 0, 0)
        e2_btn_layout.setSpacing(10)

        self.btn_e2_file = QPushButton("📂 浏览文件")
        self.btn_e2_file.setStyleSheet("background-color: #334155; color: white; padding: 6px 12px; font-size: 13px; border-radius: 4px;")
        self.btn_e2_file.setToolTip("选择电脑本地的 .rpj / .mot / .hex / .srec 烧录工程文件")
        self.btn_e2_file.clicked.connect(self.select_e2_project)
        e2_btn_layout.addWidget(self.btn_e2_file)
        e2_btn_layout.addStretch()

        self.combo_e2_family = QComboBox()
        self.combo_e2_family.addItems(["RA", "RX", "RL78", "RH850", "RE"])
        family_default = str(e2_defaults.get("e2_device_family", "RH850"))
        if self.combo_e2_family.findText(family_default) >= 0:
            self.combo_e2_family.setCurrentText(family_default)

        self.e2_interface = QLineEdit(str(e2_defaults.get("e2_interface", "")))
        self.e2_interface.setPlaceholderText("如 FINE / CSI / JTAG")

        self.e2_speed = QLineEdit(str(e2_defaults.get("e2_speed", "")))
        self.e2_speed.setPlaceholderText("如 1000000")

        self.e2_timeout = QSpinBox()
        self.e2_timeout.setRange(1, 3600)
        self.e2_timeout.setValue(300)
        self.e2_timeout.setSuffix(" s")

        self.cb_e2_verify_diff = QCheckBox("启用单片机 Flash 物理回读比对校验 (rfp-cli 回读)")
        self.cb_e2_verify_diff.setToolTip("烧录成功后，自动经 rfp-cli 从单片机回读 Flash 物理数据并留痕归档")
        self.cb_e2_verify_diff.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 13px;")

        e2_layout.addRow("工程文件:", self.e2_file)
        e2_layout.addRow("文件操作:", e2_btn_row)
        e2_layout.addRow("器件家族:", self.combo_e2_family)
        e2_layout.addRow("接口:", self.e2_interface)
        e2_layout.addRow("速率:", self.e2_speed)
        e2_layout.addRow("完成超时:", self.e2_timeout)
        e2_layout.addRow(self.cb_e2_verify_diff)
        self.param_stack.addWidget(self.page_e2_rfp) # 15
        self.device_step_form=DeviceStepForm()
        self.param_stack.addWidget(self.device_step_form)


        # 3. 策略与判定设置
        policy_frame = QFrame()
        policy_layout = QFormLayout(policy_frame)
        self.fail_strategy_combo = QComboBox()
        self.fail_strategy_combo.addItems(["失败停止", "忽略继续", "重试3次"])
        policy_layout.addRow("指令执行失败策略:", self.fail_strategy_combo)
        
        self.cb_judgment = QCheckBox("结果输出并参与最终判定")
        self.cb_judgment.setStyleSheet("color: #00E5FF; font-weight: bold;")
        policy_layout.addRow("", self.cb_judgment)

        self.scroll_layout.addWidget(policy_frame)
        
        self.main_scroll.setWidget(self.main_scroll_content)
        main_layout.addWidget(self.main_scroll)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("确认指令")
        self.btn_ok.setObjectName("btn_ok")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("btn_cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_ok)
        main_layout.addLayout(btn_layout)
        
        # 信号连接
        self.category_combo.currentIndexChanged.connect(self.on_category_changed)
        self.sub_category_combo.currentIndexChanged.connect(self.on_sub_category_changed)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        self.action_combo.currentIndexChanged.connect(self.on_action_changed)
        self.eol_op.currentTextChanged.connect(self.on_eol_op_changed)
        self.var_name.currentTextChanged.connect(self.on_var_changed)
        
        # 初始状态：如果有传入数据则执行加载，否则执行默认初始化
        if step_data:
            self._load_data(step_data)
        else:
            self.on_category_changed(0)
            self.on_eol_op_changed()

    def accept(self):
        key=device_key(self.device_combo.currentText())
        spec=action_spec(key,self.action_combo.currentText())
        if spec:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'参数错误',str(exc));return
        super().accept()

    def _combo_value(self, combo):
        if combo.isEditable():
            text = combo.currentText().strip()
            index = combo.currentIndex()
            if index >= 0 and text == combo.itemText(index).strip() and combo.currentData() is not None:
                return str(combo.currentData()).strip()
            return text
        value = combo.currentData() if combo.currentData() is not None else combo.currentText()
        return str(value).strip()

    def _set_combo_by_value(self, combo, value):
        text = str(value)
        index = combo.findData(text)
        if index < 0:
            index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(text)

    def _set_param_combo(self, combo, label, title, items, editable=False, validator=None):
        label.setText(title)
        label.setVisible(bool(items))
        combo.clear()
        combo.setEditable(editable)
        if combo.lineEdit():
            combo.lineEdit().setValidator(validator)
        combo.setVisible(bool(items))
        for text, value in items:
            combo.addItem(text, value)

    def _gpio_items(self):
        return [
            ("0x01 DIO_CHANNEL_HSD_O_00_EN", "0x01"), ("0x02 DIO_CHANNEL_HSD_O_01_EN", "0x02"),
            ("0x03 DIO_CHANNEL_HSD_O_02_EN", "0x03"), ("0x04 DIO_CHANNEL_HSD_O_03_EN", "0x04"),
            ("0x05 DIO_CHANNEL_HSD_O_04_EN", "0x05"), ("0x06 DIO_CHANNEL_HSD_O_05_EN", "0x06"),
            ("0x07 DIO_CHANNEL_HSD_O_06_EN", "0x07"), ("0x08 DIO_CHANNEL_HSD_O_07_EN", "0x08"),
            ("0x09 DIO_CHANNEL_LSD_O_00_EN", "0x09"), ("0x0A DIO_CHANNEL_LSD_O_01_EN", "0x0A"),
            ("0x0B DIO_CHANNEL_LSD_O_02_EN", "0x0B"), ("0x0C DIO_CHANNEL_LSD_O_03_EN", "0x0C"),
            ("0x0D DIO_CHANNEL_LSD_O_04_EN", "0x0D"), ("0x0E DIO_CHANNEL_LSD_O_05_EN", "0x0E"),
            ("0x10 CC1_2015+_S2", "0x10"), ("0x11 CC2_SW3", "0x11"),
            ("0x12 LINK", "0x12"), ("0x13 FAS", "0x13"), ("0x14 SC_EN1", "0x14")
        ]

    def _index_items(self, count, prefix=""):
        return [(f"{prefix}{i}", str(i)) for i in range(count)]

    def _adc_items(self):
        # ADC 选择列表，索引按 16 进制显示
        items = [
            ("0x00 KL30_IN1_V_A2D", "0"), ("0x01 WKD_EXT1_ADC", "1"), ("0x02 WKD_EXT2_ADC", "2"), 
            ("0x03 WKD_INT1_INT2_A2D", "3"), ("0x04 WKD_INT3_INT4_A2D", "4"), ("0x05 WKD_EXT3_ADC", "5"), 
            ("0x06 KL30_IN2_V_A2D", "6"), ("0x07 WKD_EXT6_ADC", "7"), ("0x08 HWREV_A2D", "8"), 
            ("0x09 HSD_O_00_USNS", "9"), ("0x0A HSD_O_01_USNS", "10"), ("0x0B HSD_O_02_USNS", "11"), 
            ("0x0C HSD_O_03_USNS", "12"), ("0x0D WKD_INT6_ADC", "13"), ("0x0E GB2015_CC2_PE", "14"), 
            ("0x0F HSD_O_06_USNS", "15"), ("0x10 HSD_O_07_USNS", "16"), ("0x11 HSD_I_CS1_4", "17"), 
            ("0x12 LSD_V_AD1", "18"), ("0x13 LSD_V_AD2", "19"), ("0x14 NTCF_MCU", "20"), 
            ("0x15 HSD_I_CS7_8", "21"), ("0x16 SIG1_A_ADC", "22"), ("0x17 HALL_IN1_ADC", "23"), 
            ("0x18 SIG3_A_ADC", "24"), ("0x19 LSD_V_AD3", "25"), ("0x1A SBC_VS1", "26"), 
            ("0x1B HSD_O_04_USNS", "27"), ("0x1C LSD_V_AD4", "28"), ("0x1D HSD_O_05_USNS", "29"), 
            ("0x1E NTCF_I_00", "30"), ("0x1F NTCF_I_01", "31"), ("0x20 INPUT2_USNS", "32"), 
            ("0x21 INPUT3_USNS", "33"), ("0x22 HALL_5V_ADC", "34"), ("0x23 WKD_INT7_ADC", "35"), 
            ("0x24 NTCF_I_02", "36"), ("0x25 NTCF_I_03", "37"), ("0x26 NTCF_I_04", "38"), 
            ("0x27 HSD_I_CS5_6", "39"), ("0x28 NTCF_I_05", "40"), ("0x29 CHRG_GB2015_CC1", "41"), 
            ("0x2A CHRG_GB_CC2", "42"), ("0x2B INPUT1_USNS", "43"), ("0x2C OUTPUT3_USNS", "44"), 
            ("0x2D WKD_EXT4_ADC", "45"), ("0x2E Pulse1_ADC", "46"), ("0x2F OUTPUT2_USNS", "47"), 
            ("0x30 OUTPUT1_USNS", "48")
        ]
        return items

    def on_eol_op_changed(self):
        # 注意：这里不能简单的 if self.is_loading: return
        # 因为 _load_data 显式调用它来生成 combo 项
        # 但我们通过判断 combo 是否已有项来决定是否跳过 clear
        op = self.eol_op.currentText()
        if not op: return
        self._set_param_combo(self.eol_param1, self.eol_param1_label, "参数1:", [])
        self._set_param_combo(self.eol_param2, self.eol_param2_label, "参数2:", [])
        self.eol_args.setPlaceholderText("可选，格式 KEY:VALUE / KEY2:VALUE2")
        self.eol_args.setVisible(False)
        if hasattr(self, "eol_args_label"):
            self.eol_args_label.setVisible(False)
        
        # 默认隐藏参数3和参数4及其他特殊选项，并重置标签名称
        if hasattr(self, "eol_param3"):
            self.eol_param3.setVisible(False)
            self.eol_param3_label.setText("参数3:")
            self.eol_param3_label.setVisible(False)
        if hasattr(self, "eol_param4"):
            self.eol_param4.setVisible(False)
            self.eol_param4_label.setText("参数4:")
            self.eol_param4_label.setVisible(False)
        if hasattr(self, "eol_diff_ambient"):
            self.eol_diff_ambient.setVisible(False)
            self.eol_diff_ambient.setChecked(False)
        if hasattr(self, "eol_temp_convert"):
            self.eol_temp_convert.setVisible(False)
            self.eol_temp_convert.setChecked(False)
        if hasattr(self, "eol_fix_adjacent"):
            self.eol_fix_adjacent.setVisible(False)
            self.eol_fix_adjacent.setChecked(False)
        if hasattr(self, "eol_compensate_random"):
            self.eol_compensate_random.setVisible(False)
            self.eol_compensate_random.setChecked(False)

        # 默认隐藏绝缘测试专用参数
        if hasattr(self, "eol_hv1"):
            self.eol_hv1.setVisible(False)
            self.eol_hv1_label.setVisible(False)
            self.eol_r0.setVisible(False)
            self.eol_r0_label.setVisible(False)
            self.eol_r1.setVisible(False)
            self.eol_r1_label.setVisible(False)
            self.eol_r2.setVisible(False)
            self.eol_r2_label.setVisible(False)
            self.eol_r3.setVisible(False)
            self.eol_r3_label.setVisible(False)
            self.eol_r4.setVisible(False)
            self.eol_r4_label.setVisible(False)
 
        if "0x04" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "GPIO通道:", self._gpio_items())
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "读取/写入:", [("读取电平", "READ"), ("写入高电平", "WRITE_HIGH"), ("写入低电平", "WRITE_LOW")])
        elif "0x06" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "ADC选择:", self._adc_items())
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "读取模式:", [("转换值", "VALUE"), ("原始值", "RAW")])
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("可选采样滤波，例 SAMPLES:10, TARGET:2.5, COMP_LIMIT:0.02, COMP_MAX:0.05")
        elif "0x05" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "PWM通道:", self._index_items(16, "PWM "))
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "读取内容:", [("占空比", "DUTY"), ("频率", "FREQ")])
        elif "0x03" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "操作内容:", [("读取绝缘阻抗", "READ"), ("设置控制状态", "WRITE")])
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "控制值:", [("0 P/N均断开", "0"), ("1 P闭合N断开", "1"), ("2 P断开N闭合", "2")])
        elif "绝缘测试" in op:
            # 隐藏操作内容和控制值
            self.eol_param1.setVisible(False)
            self.eol_param1_label.setVisible(False)
            self.eol_param2.setVisible(False)
            self.eol_param2_label.setVisible(False)
            # 显示 6 个绝缘参数输入框
            if hasattr(self, "eol_hv1"):
                self.eol_hv1.setVisible(True)
                self.eol_hv1_label.setVisible(True)
                self.eol_r0.setVisible(True)
                self.eol_r0_label.setVisible(True)
                self.eol_r1.setVisible(True)
                self.eol_r1_label.setVisible(True)
                self.eol_r2.setVisible(True)
                self.eol_r2_label.setVisible(True)
                self.eol_r3.setVisible(True)
                self.eol_r3_label.setVisible(True)
                self.eol_r4.setVisible(True)
                self.eol_r4_label.setVisible(True)
        elif any(k in op for k in ["DTC诊断", "DTC故障清除", "DTU诊断", "DTU清除"]):
            self.eol_param1.setVisible(False)
            self.eol_param1_label.setVisible(False)
            self.eol_param2.setVisible(False)
            self.eol_param2_label.setVisible(False)
        elif "0x07 CSC批量读取" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "起始索引:", [
                (str(i), str(i)) for i in range(256)
            ], editable=True, validator=QIntValidator(0, 255, self))
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "读取个数:", [
                (str(i), str(i)) for i in range(1, 257)
            ], editable=True, validator=QIntValidator(1, 256, self))
            if hasattr(self, "eol_param3"):
                self.eol_param3_label.setText("重试次数:")
                self.eol_param3_label.setVisible(True)
                self.eol_param3.setRange(0, 100)
                self.eol_param3.setValue(3)
                self.eol_param3.setVisible(True)
            if hasattr(self, "eol_param4"):
                self.eol_param4_label.setVisible(False)
                self.eol_param4.setVisible(False)
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("可选校验，格式 MIN_V:2.5,MAX_V:4.5")
            if hasattr(self, "eol_fix_adjacent"):
                self.eol_fix_adjacent.setVisible(True)
            if hasattr(self, "eol_compensate_random"):
                self.eol_compensate_random.setVisible(True)
        elif "0x07" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "操作类别:", [
                ("设置节点数目", "0x01"),
                ("高压读取", "0x02"),
                ("单体电压读取", "0x0E"),
                ("Stack电压读取", "0x0F"),
                ("快充阻抗读取", "0x10")
            ])
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "高压索引:", [
                ("0x00 代表默认", "0x00"),
                ("0x02代表HV1", "0x02"),
                ("0x03代表HV2", "0x03"),
                ("0x04代表HV3", "0x04"),
                ("0x0B代表Link1", "0x0B"),
                ("0x0C代表Link2", "0x0C")
            ])
            if hasattr(self, "eol_param3"):
                self.eol_param3_label.setVisible(True)
                self.eol_param3.setVisible(True)
            if hasattr(self, "eol_param4"):
                self.eol_param4_label.setVisible(True)
                self.eol_param4.setVisible(True)
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("可选采样滤波，例 SAMPLES:10, TARGET:2.5, COMP_LIMIT:0.02, COMP_MAX:0.05")
        elif "0x10" in op:
            self._set_param_combo(
                self.eol_param1,
                self.eol_param1_label,
                "NTC索引(0-255):",
                self._index_items(256, "NTC "),
                editable=True,
                validator=QIntValidator(0, 255, self)
            )
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "温感类型:", [
                ("1 CELL_NTC", "CELL_NTC"),
                ("2 PCB_NTC", "PCB_NTC"),
                ("3 SHUNT", "SHUNT"),
                ("4 NTCF", "NTCF"),
                ("5 FPCB_NTC", "FPCB_NTC")
            ])
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("可选参数，例 TARGET:25,TOL1:0.5,TOL2:2.0,REPLACE:-50:25")
            if hasattr(self, "eol_diff_ambient"):
                self.eol_diff_ambient.setVisible(True)
            if hasattr(self, "eol_temp_convert"):
                self.eol_temp_convert.setVisible(True)
        elif "0x08" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "模式参数:", [("0x01 占空比", "0x01"), ("0x02 频率", "0x02"), ("0x03 阻抗", "0x03"), ("0x04 脉宽", "0x04")])
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "索引:", [("sig1", "0"), ("sig3", "1")])
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("可选采样滤波，例 SAMPLES:10, TARGET:2.5, COMP_LIMIT:0.02, COMP_MAX:0.05")
        elif "EEPROM测试" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "块大小 (字节):", [("32 字节", "32"), ("16 字节", "16"), ("64 字节", "64"), ("8 字节", "8")])
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "地址ADDRESS:", [("1 (0x01)", "1"), ("0 (0x00)", "0"), ("4 (0x04)", "4"), ("8 (0x08)", "8")])
            self.eol_args.setPlaceholderText("无需额外参数")
        elif any(k in op for k in ["DTC诊断", "DTC故障清除", "DTU诊断", "DTU清除"]):
            self.eol_args.setPlaceholderText("无需额外参数")
        elif "0x0A" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "操作:", [("读取数据", "READ"), ("写入数据", "WRITE"), ("设置地址", "SET_ADDR")])
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("ADDRESS:0x00, DATA:00000000")
        elif "0x09" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "RTC功能:", [("读取时间", "READ"), ("设置时间", "SET_TIME"), ("设置唤醒", "SET_WAKEUP")])
            self.eol_args.setVisible(True)
            if hasattr(self, "eol_args_label"):
                self.eol_args_label.setVisible(True)
            self.eol_args.setPlaceholderText("DATA:YYYYMMDDHHMMSS")
        elif "0xFF" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "读取项:", [
                ("读取第一唤醒源", "0x06"), 
                ("读取压力传感器", "0x0E"), 
                ("读取高边负载回采电压", "0x11")
            ])
            self._set_param_combo(self.eol_param2, self.eol_param2_label, "通道:", [
                ("通道0", "0"), ("通道1", "1"), ("通道2", "2"), ("通道3", "3"),
                ("通道4", "4"), ("通道5", "5"), ("通道6", "6"), ("通道7", "7"),
                ("通道8", "8"), ("通道9", "9"), ("通道10", "10"), ("通道11", "11"),
                ("通道12", "12"), ("通道13", "13"), ("通道14", "14"), ("通道15", "15")
            ])
        elif "0x0B" in op:
            self._set_param_combo(self.eol_param1, self.eol_param1_label, "霍尔通道:", [("通道1", "0x01"), ("通道2", "0x03")])

    def _split_params(self, params_str, is_args_level=False):
        values = {}
        if is_args_level:
            normalized = str(params_str).replace("；", "/").replace("，", "/").replace(",", "/").replace(";", "/")
        else:
            normalized = str(params_str).replace("；", "/").replace("，", "/")
        # 参数之间以 "/KEY:" 分隔，不能直接按 "/" 切分。
        # QFileDialog 在 Windows 下可能返回 D:/dir/app.exe，直接切分
        # 会把 HOST 错误截断为 D:。此规则同时兼容已保存的旧配方。
        parts = re.split(r"/(?=\s*[A-Za-z_][A-Za-z0-9_]*\s*[:：])", normalized)
        for part in parts:
            part = part.strip()
            if not part or ":" not in part:
                continue
            key, value = part.split(":", 1)
            values[key.strip().upper()] = value.strip()
        return values

    def _load_data(self, data):
        """全量还原：分类、设备、动作、策略及参数 (终极加固版)"""
        self.is_loading = True
        try:
            device = data.get('device', '')
            action = data.get('action', '')
            params_str = str(data.get('params', ''))
            step_type = data.get('type', '')
            device_step=decode_device_step(params_str)
            if device_step:
                key,spec,values=device_step
                group,label=STEP_DEVICES[key]
                self.category_combo.setCurrentText('设备操作');self.on_category_changed()
                self.sub_category_combo.setCurrentText(group);self.on_sub_category_changed()
                self.device_combo.setCurrentText(label);self.on_device_changed()
                self.action_combo.setCurrentText(spec.label);self.on_action_changed()
                self.device_step_form.configure(key,spec,values)
                self.cb_judgment.setChecked(data.get('is_judgment',False))
                self.fail_strategy_combo.setCurrentText(data.get('fail_strategy','失败停止'))
                return
            kv = self._split_params(params_str)
            
            # 1. 还原一级分类 (Category)
            category = "设备操作"
            if "CAN" in device or "UDS" in device or "报文" in device: category = "报文交互"
            elif "MES" in device or "MES" in step_type: category = "三方协议"
            # BUG-15修复: 统一用step_type和device字段判断，不再依赖kv字典的键名匹配
            elif "3.5HEOL" in device or "EOL" in step_type:
                eol_val = kv.get("EOL", action)
                if any(k in eol_val for k in ["EEPROM测试", "绝缘测试", "DTC诊断", "DTC故障清除", "DTU"]):
                    category = "特殊执行"
                else:
                    category = "三方协议"
            elif "等待" in device or "固定延时" in action or "变量操作" in device or "读取变量" in step_type or "公式计算" in device or "公式计算" in step_type: category = "通用交互"
            
            self.category_combo.setCurrentText(category)
            self.on_category_changed()

            # 2. 还原二级分类 (Sub-Category) - 仅限设备操作
            if category == "设备操作":
                sub_cat = "AFE"
                if "AFE" in device: sub_cat = "AFE"
                elif "继电器" in device or "Aging Board" in device or "Easy320" in device: sub_cat = "继电器"
                elif "HV Source" in device or "高压" in device: sub_cat = "高压源"
                elif "Simulator" in device or "模拟" in device: sub_cat = "模拟电池"
                elif "CA550" in device or "校准" in device: sub_cat = "校准源"
                elif "APP刷写" in device or "SG400" in device or "力捷丰" in device or "瑞萨" in device or "rfp-cli" in device: sub_cat = "下载器"
                elif "Power" in device or "电源" in device: sub_cat = "直流源"
                
                self.sub_category_combo.setCurrentText(sub_cat)
                self.on_sub_category_changed()

            # 3. 还原控制设备 (Device)
            # 使用模糊匹配，防止因为后缀不同导致失败
            for i in range(self.device_combo.count()):
                if self.device_combo.itemText(i) == device or device in self.device_combo.itemText(i):
                    self.device_combo.setCurrentIndex(i)
                    break
            self.on_device_changed()

            # 4. 还原功能动作 (Action)
            idx = self.action_combo.findText(action)
            if idx >= 0: self.action_combo.setCurrentIndex(idx)
            self.on_action_changed()
            
            # 回读页面必须恢复读取量，否则默认电压会在再次保存时覆盖电流。
            if self.param_stack.currentIndex() == 3:
                read_button = self.rb_curr if "电流" in params_str else self.rb_volt
                read_button.setChecked(True)
                self._update_read_units()
                saved_unit = configured_read_unit(params_str)
                if supports_read_units(device) and saved_unit:
                    if self.read_unit.findText(saved_unit) < 0:
                        raise ValueError(f"回读单位 {saved_unit!r} 与读取量不匹配")
                    self.read_unit.setCurrentText(saved_unit)

            # 5. 还原执行指令策略
            self.cb_judgment.setChecked(data.get('is_judgment', False))
            self.fail_strategy_combo.setCurrentText(data.get('fail_strategy', "失败停止"))

            # 6. 还原具体业务参数 (特征匹配法 + 异常隔离)
            import re
            
            # --- 电压/电流框 (通用) ---
            try:
                v_match = re.search(r'([\d.]+)V', params_str)
                if v_match:
                    val = float(v_match.group(1))
                    if hasattr(self, 'i_volt'): self.i_volt.setValue(val)
                    if hasattr(self, 'sim_batch_volt'): self.sim_batch_volt.setValue(val)
                
                a_match = re.search(r'([\d.]+)A', params_str)
                if a_match: 
                    if hasattr(self, 'i_curr'): self.i_curr.setValue(float(a_match.group(1)))
                
                ma_match = re.search(r'([\d.]+)mA', params_str)
                if ma_match:
                    val = float(ma_match.group(1))
                    if hasattr(self, 'sim_batch_curr'): self.sim_batch_curr.setValue(val)
                
                if "开启" in params_str or "ON" in params_str:
                    if hasattr(self, 'output_combo'): self.output_combo.setCurrentText("开启输出")
                    if hasattr(self, 'sim_batch_output'): self.sim_batch_output.setCurrentText("ON")
                elif "关闭" in params_str or "OFF" in params_str:
                    if hasattr(self, 'output_combo'): self.output_combo.setCurrentText("关闭输出")
                    if hasattr(self, 'sim_batch_output'): self.sim_batch_output.setCurrentText("OFF")
                    
                self.cb_volt.setChecked("V" in params_str)
                self.cb_curr.setChecked("A" in params_str)
                self.cb_output.setChecked("开启" in params_str or "关闭" in params_str or "ON" in params_str)
            except: pass

            # --- CAN 参数 (Page 1) ---
            try:
                if "ID" in kv: self.c_id.setText(kv["ID"])
                if "DATA" in kv: self.c_data.setText(kv["DATA"])
                if "WAIT_ID" in kv: self.c_wait_id.setText(kv["WAIT_ID"])
                if "TIMEOUT" in kv: self.c_timeout.setValue(int(float(kv["TIMEOUT"])))
                if "TYPE" in kv: self.c_type.setCurrentIndex(int(kv["TYPE"]))
                if "DLC" in kv: self.c_dlc.setValue(int(kv["DLC"]))
                if "CH" in kv: self.c_channel.setValue(int(kv["CH"]))
            except: pass

            # --- CA550 (Page 5) ---
            try:
                if "CA550" in device:
                    val_match = re.search(r'Val:([\d.-]+)', params_str)
                    if val_match: self.ca_val.setValue(float(val_match.group(1)))
                    if "RANGE" in kv: self.ca_range.setCurrentText(kv["RANGE"])
                    if "OUTPUT" in kv: self.ca_output_state.setCurrentText(kv["OUTPUT"])
                    # 精准匹配输出类型
                    for i in range(self.ca_type.count()):
                        t_text = self.ca_type.itemText(i).split(" ")[0]
                        if f"Type:{t_text}" in params_str or (t_text in params_str and "Type:" not in params_str):
                            self.ca_type.setCurrentIndex(i); break
            except: pass

            # --- 变量操作 (Page 9) ---
            try:
                if "VAR" in kv: self.var_name.setCurrentText(kv["VAR"])
            except: pass

            # --- 公式计算 (Page 10) ---
            try:
                if "公式计算" in step_type or "公式计算" in device or "FORMULA" in kv:
                    formula_val = ""
                    var_name = ""
                    if "FORMULA:" in params_str:
                        if "VAR:" in params_str:
                            if params_str.find("FORMULA:") < params_str.find("VAR:"):
                                f_part, v_part = params_str.split("VAR:", 1)
                                formula_val = f_part.replace("FORMULA:", "").strip()
                                var_name = v_part.strip()
                            else:
                                v_part, f_part = params_str.split("FORMULA:", 1)
                                var_name = v_part.replace("VAR:", "").strip()
                                formula_val = f_part.strip()
                        else:
                            formula_val = params_str.replace("FORMULA:", "").strip()
                    else:
                        formula_val = params_str.strip()
                    
                    formula_val = formula_val.strip(",").strip(";").strip("/").strip()
                    var_name = var_name.strip(",").strip(";").strip("/").strip()
                    self.formula_expr.setText(formula_val)
                    self.formula_var.setCurrentText(var_name)
            except: pass

            # --- 力捷丰 SG400 (Page 11) ---
            try:
                if "SG400" in device or "力捷丰" in device:
                    self._preload_downloader_projects()
                    proj_val = kv.get("PROJECT", "")
                    if proj_val:
                        idx = self.opteeq_project.findText(proj_val)
                        if idx >= 0:
                            self.opteeq_project.setCurrentIndex(idx)
                        else:
                            self.opteeq_project.setEditText(proj_val)
                    self.opteeq_timeout.setValue(int(float(kv.get("TIMEOUT", "300"))))
                    verify_diff = kv.get("VERIFY_DIFF", "0") in ("1", "TRUE", "YES")
                    self.cb_opteeq_verify_diff.setChecked(verify_diff)
                    if "VERIFY_SCOPE" in kv:
                        scope_val = kv.get("VERIFY_SCOPE", "")
                        s_idx = self.combo_opteeq_verify_scope.findText(scope_val)
                        if s_idx >= 0:
                            self.combo_opteeq_verify_scope.setCurrentIndex(s_idx)
                    if "ARCHIVE_REPORT" in kv:
                        self.cb_opteeq_archive_report.setChecked(kv.get("ARCHIVE_REPORT", "1") in ("1", "TRUE", "YES"))
                    self._update_opteeq_meta_preview()
            except: pass

            # --- 瑞萨 E2 (Page 15) ---
            try:
                if "瑞萨" in device or "rfp-cli" in device:
                    file_val = kv.get("FILE", "")
                    if file_val:
                        idx = self.e2_file.findText(file_val)
                        if idx >= 0:
                            self.e2_file.setCurrentIndex(idx)
                        else:
                            self.e2_file.setEditText(file_val)
                    if "DEVICE_FAMILY" in kv:
                        fam_idx = self.combo_e2_family.findText(kv["DEVICE_FAMILY"])
                        if fam_idx >= 0:
                            self.combo_e2_family.setCurrentIndex(fam_idx)
                    if "INTERFACE" in kv:
                        self.e2_interface.setText(kv["INTERFACE"])
                    if "SPEED" in kv:
                        self.e2_speed.setText(kv["SPEED"])
                    self.e2_timeout.setValue(int(float(kv.get("TIMEOUT", "300"))))
                    self.cb_e2_verify_diff.setChecked(kv.get("VERIFY_DIFF", "0") in ("1", "TRUE", "YES"))
            except: pass

            # --- UDS 顺序交互 (Page 12) ---
            try:
                if "UDS" in device:
                    self.uds_tx_id.setText(kv.get("TX_ID", "0x7E0"))
                    self.uds_rx_id.setText(kv.get("RX_ID", "0x7E8"))
                    self.uds_can_type.setCurrentIndex(int(kv.get("TYPE", "0")))
                    self.uds_timeout.setValue(int(float(kv.get("TIMEOUT", "1000"))))
                    self.uds_cycles.setValue(int(kv.get("CYCLES", "1")))
                    self.uds_interval.setValue(int(kv.get("INTERVAL", "1")))
                    encoded = kv.get("LIST64", "")
                    if encoded:
                        script = base64.urlsafe_b64decode(encoded).decode("utf-8")
                        for line in script.splitlines():
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            line_main, pipe_sep, rule_str = line.partition("|")
                            request, separator, expected = line_main.partition("=>")
                            self._append_uds_table_row("自定义", request.strip(), expected.strip() if separator else "", rule_str.strip())
            except: pass

            # --- APP 刷写 (Page 12) ---
            try:
                if "APP刷写" in device:
                    self.app_flash_program.setText(kv.get("HOST", kv.get("PROGRAM", "")))
                    self.app_flash_ini.setText(kv.get("INI", "config.ini"))
                    self.app_flash_section.setText(kv.get("SECTION", "App"))
                    self.app_flash_key.setText(kv.get("KEY", "addr"))
                    app_name = kv.get("APPNAME", "")
                    self.app_flash_name.setText("" if app_name == "APPname" else app_name)
                    self.app_flash_record.setText(kv.get("RECORD", os.path.join("Flash", "Record.txt")))
                    self.app_flash_timeout.setValue(int(float(kv.get("TIMEOUT", "300"))))
            except (TypeError, ValueError):
                pass

            # --- 延时参数 (Page 2) ---
            try:
                ms_match = re.search(r'(\d+)ms', params_str)
                if ms_match: self.w_time.setValue(int(ms_match.group(1)))
            except: pass

            # --- 继电器控制 (Page 4/8) ---
            try:
                if "继电器" in step_type or "Easy320" in device or "Aging Board" in device:
                    channels = params_str.split(",")
                    for c in channels:
                        try:
                            clean_c = re.sub(r'[^\d]', '', c) # 只保留数字
                            if clean_c:
                                c_idx = int(clean_c) - 1
                                if 0 <= c_idx < 32:
                                    if "Easy320" in device: self.easy320_checks[c_idx].setChecked(True)
                                    if "Aging Board" in device and c_idx < 22: self.aging_relay_checks[c_idx].setChecked(True)
                        except: pass
            except: pass

            # --- 3.5H EOL 协议 (Page 7) ---
            try:
                if "EOL" in step_type or "EOL" in kv:
                    eol_val = kv.get("EOL", action)
                    if eol_val == "DTU诊断": eol_val = "DTC诊断"
                    elif eol_val in ("DTU清除故障", "DTU清障"): eol_val = "DTC故障清除"
                    self.action_combo.setCurrentText(eol_val)
                    self.eol_op.setCurrentText(eol_val)
                    self.on_eol_op_changed()
                    if "TIMEOUT" in kv: self.eol_timeout.setValue(int(float(kv["TIMEOUT"])))
                    if "CH" in kv: self.eol_channel.setValue(int(float(kv["CH"])))
                    if "TX_ID" in kv: self.eol_tx_id.setText(kv["TX_ID"])
                    if "RX_ID" in kv: self.eol_rx_id.setText(kv["RX_ID"])
                    if "ARGS" in kv:
                        self.eol_args.setText(kv["ARGS"])
                        try:
                            args_str = kv["ARGS"]
                            args_kv = self._split_params(args_str, is_args_level=True)
                            if hasattr(self, "eol_fix_adjacent"):
                                is_enabled = args_kv.get("FIX_ADJACENT", "") in ("1", "true", "True", "ON", "on")
                                self.eol_fix_adjacent.setChecked(is_enabled)
                            if hasattr(self, "eol_compensate_random"):
                                is_comp = args_kv.get("COMPENSATE", "") in ("1", "true", "True", "ON", "on")
                                self.eol_compensate_random.setChecked(is_comp)
                        except: pass
                    
                    if "绝缘测试" in kv.get("EOL", action):
                        if "HV1" in kv: self.eol_hv1.setValue(float(kv["HV1"]))
                        if "R0" in kv: self.eol_r0.setValue(float(kv["R0"]))
                        if "R1" in kv: self.eol_r1.setValue(float(kv["R1"]))
                        if "R2" in kv: self.eol_r2.setValue(float(kv["R2"]))
                        if "R3" in kv: self.eol_r3.setValue(float(kv["R3"]))
                        if "R4" in kv: self.eol_r4.setValue(float(kv["R4"]))
                    else:
                        # 1. 优先尝试直接用全新通用 PARAM1 和 PARAM2 还原参数，毫无卡死可能！
                        if "PARAM1" in kv:
                            self._set_combo_by_value(self.eol_param1, kv["PARAM1"])
                        if "PARAM2" in kv:
                            self._set_combo_by_value(self.eol_param2, kv["PARAM2"])
                        if "PARAM3" in kv:
                            self.eol_param3.setValue(int(float(kv["PARAM3"])))
                        if "PARAM4" in kv:
                            self.eol_param4.setValue(int(float(kv["PARAM4"])))
                        if "DIFF_AMBIENT" in kv:
                            if hasattr(self, "eol_diff_ambient"):
                                self.eol_diff_ambient.setChecked(kv["DIFF_AMBIENT"] == "1")
                        if "CONVERT_TEMP" in kv or "TEMP_CONVERT" in kv:
                            if hasattr(self, "eol_temp_convert"):
                                self.eol_temp_convert.setChecked(kv.get("CONVERT_TEMP") == "1" or kv.get("TEMP_CONVERT") == "1")
                        
                    # 2. 兼容映射还原（包括 ADC, MODE, STATE, INDEX, LEVEL, PWM, NTC, HALL, OP, VAL 等历史字段）
                    p1_keys = ["ADC", "STATE", "INDEX", "PWM", "NTC", "HALL", "OP", "GPIO"]
                    p2_keys = ["MODE", "LEVEL", "VAL", "STATE_VAL", "INDEX"]
                    
                    for k in p1_keys:
                        if k in kv and not kv.get("PARAM1"):
                            self._set_combo_by_value(self.eol_param1, kv[k])
                            break
                    for k in p2_keys:
                        # 排除 OP/INDEX 混用（在 0x07、0x08 等操作中参数 2 被存为 INDEX，此时需排除做参数 1 的情况）
                        if k == "INDEX" and any(x in str(kv.get("EOL", "")) for x in ["0x04", "0x06"]):
                            continue
                        if k in kv and not kv.get("PARAM2"):
                            self._set_combo_by_value(self.eol_param2, kv[k])
                            break
            except: pass
        
        except Exception as e:
            print(f"Error in _load_data: {e}")
            self.btn_ok.setEnabled(False)
            QMessageBox.warning(self, "配方参数加载失败", f"无法恢复子工步参数：{e}\n请取消，避免覆盖原配方。")
        finally:
            self.is_loading = False
            self.on_var_changed()
            self.update()

    def on_var_changed(self):
        pass

    def on_category_changed(self, index=0):
        category = self.category_combo.currentText()
        self.sub_category_combo.setVisible(category == "设备操作")
        
        self.sub_category_combo.clear()
        self.device_combo.clear()
        
        if category == "设备操作":
            self.sub_category_combo.addItems(["直流源", "高压源", "模拟电池", "继电器", "校准源", "下载器", "测量仪器", "信号发生器", "示波器", "板卡"])
        elif category == "报文交互":
            self.device_combo.addItems(["CAN 交互", "UDS 交互列表"])
        elif category == "三方协议":
            self.device_combo.addItems(["3.5HEOL协议", "MES过站校验"])
        elif category == "通用交互":
            self.device_combo.addItems(["等待 (Wait)", "变量操作 (Variable)", "公式计算 (Calculate)"])
        elif category == "特殊执行":
            self.device_combo.addItems(["3.5HEOL协议"])
        
        if not self.is_loading:
            if category == "设备操作":
                self.on_sub_category_changed()
            else:
                self.on_device_changed()

    def on_sub_category_changed(self, index=0):
        if self.category_combo.currentText() != "设备操作": return
        
        sub_cat = self.sub_category_combo.currentText()
        self.device_combo.clear()
        
        if sub_cat == "继电器":
            self.device_combo.addItems(["Easy320 继电器 (Easy320)"])
        elif sub_cat == "高压源":
            self.device_combo.addItems(["NGI 高压源 (HV Source)"])
        elif sub_cat == "模拟电池":
            self.device_combo.addItems(["电池模拟器 (NGI 83624A Simulator)"])
        elif sub_cat == "校准源":
            self.device_combo.addItems(["CA550 校准仪 (CA550)"])
        elif sub_cat == "直流源":
            self.device_combo.addItems(["IT6722A 被测物供电电源 (IT6722A DUT Power)"])
        elif sub_cat == "下载器":
            self.device_combo.addItems(["力捷丰 SG400 下载器 (OpteeQ SG400)", "瑞萨 E2 仿真器 (rfp-cli)", "APP刷写"])
        for _,(group,label) in STEP_DEVICES.items():
            if group==sub_cat and self.device_combo.findText(label)<0:self.device_combo.addItem(label)
        
        if not self.is_loading:
            self.on_device_changed()

    def on_device_changed(self, index=0):
        self.action_combo.clear()
        device_text = self.device_combo.currentText()
        if not device_text: return
        key=device_key(device_text)
        if key in ('it6333b','dmm34461','dg2052','tbs2102b','insulation','resistor','relay','hv','hsd','dmm'):
            self.action_combo.addItems([a.label for a in STEP_ACTIONS[key]])
            if not self.is_loading:self.on_action_changed()
            return
        
        if "3.5HEOL" in device_text:
            category = self.category_combo.currentText()
            if category == "特殊执行":
                self.action_combo.addItems(["EEPROM测试", "绝缘测试", "DTC诊断", "DTC故障清除"])
            else:
                self.action_combo.addItems([
                    "0x03 绝缘控制读取", "0x04 GPIO控制读取", "0x05 PWM读取", "0x06 ADC读取",
                    "0x07 CSC控制读取", "0x07 CSC批量读取", "0x08 CRASH读取", "0x09 RTC控制读取", "0x10 NTC读取",
                    "0x0A EEPROM控制读取", "0x0B 霍尔电流读取", "0xFF 扩展指令"
                ])
        elif "UDS" in device_text:
            self.action_combo.addItems(["顺序发送并校验"])
        elif "MES" in device_text:
            self.action_combo.addItems(["moveInCheck过站校验"])
        elif "CAN" in device_text:
            self.action_combo.addItems(["发送指令", "交互/问答", "读取数据", "接收指定帧ID"])
        elif "等待" in device_text:
            self.action_combo.addItems(["固定延时"])
        elif "变量操作" in device_text:
            self.action_combo.addItems(["读取变量"])
        elif "公式计算" in device_text:
            self.action_combo.addItems(["公式计算"])
        elif "Easy320" in device_text:
            self.action_combo.addItems(["闭合勾选通道", "断开勾选通道", "全部断开"])
        elif "瑞萨" in device_text or "rfp-cli" in device_text:
            self.action_combo.addItems(["连接检测", "查询状态", "执行烧录工程"])
        elif "SG400" in device_text or "力捷丰" in device_text:
            self.action_combo.addItems(["连接检测", "查询状态", "执行烧录工程", "独立回读校验"])
        elif "APP刷写" in device_text:
            self.action_combo.addItems(["查找刷写文件", "启动刷写"])
        elif "CA550" in device_text:
            self.action_combo.addItems(["参数设置", "数据回读"])
        elif "HV Source" in device_text or "高压源" in device_text:
            self.action_combo.addItems(["设置参数", "回读数据", "清除保护"])
        else: # 电源类
            if "Simulator" in device_text:
                self.action_combo.addItems(["快捷批量配置", "回读数据"])
            else:
                self.action_combo.addItems(["设置参数", "回读数据"])
        
        if key:
            for spec in STEP_ACTIONS[key]:
                if self.action_combo.findText(spec.label)<0:self.action_combo.addItem(spec.label)
        if not self.is_loading:
            self.on_action_changed()

    def _update_read_units(self, checked=None):
        enabled = supports_read_units(self.device_combo.currentText())
        self.read_unit_row.setVisible(enabled)
        self.read_unit_help.setVisible(enabled)
        # Use the voltage button's checked state: Qt toggles it before selecting current.
        units = VOLTAGE_UNITS if self.rb_volt.isChecked() else CURRENT_UNITS
        previous = self.read_unit.currentText()
        self.read_unit.clear()
        self.read_unit.addItems(units)
        if previous in units:
            self.read_unit.setCurrentText(previous)

    def on_action_changed(self):
        device = self.device_combo.currentText()
        action = self.action_combo.currentText()
        key=device_key(device);spec=action_spec(key,action)
        if spec:
            self.device_step_form.configure(key,spec)
            self.param_stack.setCurrentWidget(self.device_step_form)
            return
        
        if "MES" in device:
            self.param_stack.setCurrentIndex(14)
        elif "3.5HEOL" in device:
            self.param_stack.setCurrentIndex(7)
            # 同步 action 到内部 eol_op 逻辑，并保持其隐藏
            self.eol_op.setCurrentText(action)
            self.on_eol_op_changed()
            self.eol_op.setVisible(False)
        elif "UDS" in device:
            self.param_stack.setCurrentIndex(13)
        elif "CAN" in device:
            self.param_stack.setCurrentIndex(1)
            # 动态显示/隐藏不需要的输入项
            c_layout = self.page_can.layout()
            if c_layout:
                fields_to_toggle = [self.c_data, self.c_wait_id, self.c_dlc]
                if action == "接收指定帧ID":
                    for field in fields_to_toggle:
                        field.hide()
                        lbl = c_layout.labelForField(field)
                        if lbl: lbl.hide()
                else:
                    for field in fields_to_toggle:
                        field.show()
                        lbl = c_layout.labelForField(field)
                        if lbl: lbl.show()
        elif "等待" in device:
            self.param_stack.setCurrentIndex(2)
        elif "变量操作" in device:
            self.param_stack.setCurrentIndex(9)
        elif "公式计算" in device:
            self.param_stack.setCurrentIndex(10)
        elif "同步屏障" in device:
            self.param_stack.setCurrentIndex(14)
        elif "继电器" in device or "Easy320" in device:
            if "全部" in action:
                self.param_stack.setCurrentIndex(14)
            else:
                self.param_stack.setCurrentIndex(4)
        elif "瑞萨" in device or "rfp-cli" in device:
            if "执行烧录工程" in action:
                self.param_stack.setCurrentIndex(15)
            else:
                self.param_stack.setCurrentIndex(14)
        elif "SG400" in device or "力捷丰" in device:
            self.cb_opteeq_verify_diff.setVisible('回读校验' not in action)
            if "执行烧录工程" in action or '回读校验' in action:
                self.param_stack.setCurrentIndex(11)
                if self.opteeq_project.count() == 0:
                    self._preload_downloader_projects()
            else:
                self.param_stack.setCurrentIndex(14)
        elif "APP刷写" in device:
            self.param_stack.setCurrentIndex(12)
            is_find = "查找" in action
            self.app_flash_program_label.setText("上位机路径:")
            for widget in (self.app_flash_program_label, self.app_flash_program,
                           self.btn_app_flash_program, self.btn_app_flash_dir):
                widget.setVisible(True)
            for widget in (self.app_flash_ini_label,
                           self.app_flash_ini, self.app_flash_section_label,
                           self.app_flash_section, self.app_flash_key_label,
                           self.app_flash_key, self.app_flash_name_label,
                           self.app_flash_name, self.app_flash_path_help):
                widget.setVisible(is_find)
            # Record.txt 由上位机路径自动解析，不再要求人工填写。
            self.app_flash_record_label.setVisible(False)
            self.app_flash_record.setVisible(False)
            for widget in (self.app_flash_timeout_label, self.app_flash_timeout,
                           self.app_flash_record_help):
                widget.setVisible(not is_find)
        elif "CA550" in device:
            if "参数设置" in action:
                self.param_stack.setCurrentIndex(5)
            elif "回读" in action:
                self.param_stack.setCurrentIndex(3)
            else:
                self.param_stack.setCurrentIndex(14)
        elif "设置参数" in action or "快捷批量配置" in action:
            if "Simulator" in device:
                self.param_stack.setCurrentIndex(6)
            else:
                self.param_stack.setCurrentIndex(0)
                # 动态调整范围
                if "AFE" in device:
                    self.i_volt.setRange(0, 100)
                    # 2# 和 3# AFE 电源电流限制为 12A，1# 为 36A
                    if "2#" in device or "3#" in device:
                        self.i_curr.setRange(0, 12)
                    else:
                        self.i_curr.setRange(0, 36)
                    self.i_curr.setSuffix(" A")
                elif "control power" in device.lower() or "控制板" in device:
                    self.i_volt.setRange(0, 30)
                    self.i_curr.setRange(0, 40)
                    self.i_curr.setSuffix(" A")
                elif "IT6722A" in device or "DUT Power" in device or "主机板" in device or "Main Power" in device:
                    self.i_volt.setRange(0, 80)
                    self.i_curr.setRange(0, 20)
                    self.i_curr.setSuffix(" A")
                elif "控制板" in device or "Control Power" in device:
                    self.i_volt.setRange(0, 30)
                    self.i_curr.setRange(0, 40)
                    self.i_curr.setSuffix(" A")
                elif "Simulator" in device: # 虽然现在模拟器用Page 6，但以防万一Page 0也被选中
                    self.i_volt.setRange(0, 15)
                    self.i_curr.setRange(0, 5)
                    self.i_curr.setSuffix(" A")
                else: # NGI 或其它
                    self.i_volt.setRange(0, 1000)
                    self.i_curr.setRange(0, 500)
                    self.i_curr.setSuffix(" A")
        elif "回读" in action or "读取数据" in action:
            self.param_stack.setCurrentIndex(3)
        else: # 全局操作等
            self.param_stack.setCurrentIndex(14) # 显式展示空参数占位区
            
        if self.param_stack.currentIndex() == 3:
            self._update_read_units()
        self.on_var_changed()

    def _reload_uds_templates_combo(self):
        """重新加载常用 DID 字典/模板下拉框"""
        if not hasattr(self, "combo_uds_templates"):
            return
        self.combo_uds_templates.blockSignals(True)
        self.combo_uds_templates.clear()
        self.combo_uds_templates.addItem("📋 选择常用 BMS 诊断读取模板 (一键预填)...", None)
        
        if not hasattr(self, "uds_dict_mgr"):
            from core.uds_dictionary import UDSDictionaryManager
            self.uds_dict_mgr = UDSDictionaryManager()
            
        items = self.uds_dict_mgr.get_all_items()
        for item in items:
            name = item.get("name", "未命名")
            did = item.get("did", "")
            label = f"{name} ({did})" if did else name
            self.combo_uds_templates.addItem(label, item)

        if hasattr(self, "uds_simple_template"):
            self.uds_simple_template.blockSignals(True)
            self.uds_simple_template.clear()
            self.uds_simple_template.addItem("请选择或搜索诊断项目...", None)
            for item in items:
                name = item.get("name", "未命名")
                did = item.get("did", "")
                self.uds_simple_template.addItem(f"{name} ({did})" if did else name, item)
            self.uds_simple_template.setCurrentIndex(0)
            self.uds_simple_template.blockSignals(False)
            
        self.combo_uds_templates.blockSignals(False)

    def _on_uds_simple_template_selected(self, idx: int):
        """简易模式选择模板后，自动填入高级字段并展示最终执行效果。"""
        if idx <= 0:
            self.lbl_uds_simple_preview.setText("选择项目后，系统会自动配置请求、响应校验、数据换算和结果变量。")
            return
        item = self.uds_simple_template.currentData()
        if not isinstance(item, dict):
            return
        from core.uds_dictionary import build_simple_uds_config
        try:
            config = build_simple_uds_config(item)
        except ValueError as exc:
            self.lbl_uds_simple_preview.setText(str(exc))
            return

        template_idx = self.combo_uds_templates.findData(item)
        if template_idx >= 0:
            self.combo_uds_templates.setCurrentIndex(template_idx)
        self.uds_request.setText(config["request"])
        # 仅校验稳定的服务/DID前缀，测量数据允许随真实状态变化。
        self.uds_expected.setText(config["expected"])
        unit_text = f" {config['unit']}" if config["unit"] else ""
        variable_text = config["variable"] or "不保存变量"
        self.lbl_uds_simple_preview.setText(
            f"请求 {config['request']}  → 校验 {config['expected']}  → "
            f"结果写入 {variable_text}{unit_text}"
        )

    def _add_simple_uds_item(self):
        """把简易模式当前项目加入既有 UDS 顺序执行列表。"""
        if self.uds_simple_template.currentIndex() <= 0:
            QMessageBox.information(self, "简易配置", "请先选择一个诊断项目。")
            return
        self.add_uds_row()
        self.lbl_uds_simple_preview.setText("已加入执行列表，可继续选择下一个诊断项目。")

    def _on_uds_proto_fmt_changed(self, idx: int):
        """协议规范切换 (0: 标准ISO 14229 偏移3, 1: 厂商私有含长度 偏移4, 2: 自定义)"""
        if idx == 0:
            self.uds_extract_offset.setValue(3)
        elif idx == 1:
            self.uds_extract_offset.setValue(4)
        if hasattr(self, "uds_byte_grid"):
            off = self.uds_extract_offset.value()
            length = self.uds_extract_len.value()
            self.uds_byte_grid.set_selection(off, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _on_uds_template_selected(self, idx: int):
        """选中常用 DID 模板后，一键自动填充请求、期望、偏移、长度、模式、系数、单位与变量名"""
        if idx <= 0:
            return
        item = self.combo_uds_templates.currentData()
        if not item or not isinstance(item, dict):
            return
            
        req = item.get("request", "").strip()
        exp = item.get("expected", "").strip()
        if req:
            self.uds_request.setText(req)
        if exp:
            self.uds_expected.setText(exp)
        elif req and hasattr(self, "_uds_positive_response_prefix"):
            self.uds_expected.setText(self._uds_positive_response_prefix(req))
            
        mode = item.get("mode", "NONE")
        if mode == "ASCII":
            self.uds_extract_mode.setCurrentIndex(1)
        elif mode in ("CALC", "NUMERIC"):
            self.uds_extract_mode.setCurrentIndex(2)
            endian = item.get("endian", "big")
            self.uds_extract_endian.setCurrentIndex(1 if endian == "little" else 0)
            self.uds_extract_signed.setChecked(bool(item.get("signed", False)))
            self.uds_extract_scale.setValue(float(item.get("scale", 1.0)))
            self.uds_extract_offset_val.setValue(float(item.get("offset_val", 0.0)))
            self.uds_extract_unit.setText(item.get("unit", ""))
        elif mode == "FORMULA":
            self.uds_extract_mode.setCurrentIndex(3)
            endian = item.get("endian", "big")
            self.uds_extract_endian.setCurrentIndex(1 if endian == "little" else 0)
            self.uds_extract_formula.setText(item.get("formula", "val"))
            self.uds_extract_unit.setText(item.get("unit", ""))
        else:
            self.uds_extract_mode.setCurrentIndex(0)
            
        offset = int(item.get("offset", 3))
        length = int(item.get("length", 0))
        self.uds_extract_offset.setValue(offset)
        self.uds_extract_len.setValue(length)
        
        if offset == 3 and self.uds_proto_fmt.currentIndex() != 0:
            self.uds_proto_fmt.blockSignals(True)
            self.uds_proto_fmt.setCurrentIndex(0)
            self.uds_proto_fmt.blockSignals(False)
        elif offset == 4 and self.uds_proto_fmt.currentIndex() != 1:
            self.uds_proto_fmt.blockSignals(True)
            self.uds_proto_fmt.setCurrentIndex(1)
            self.uds_proto_fmt.blockSignals(False)
            
        var_name = item.get("var_name", "")
        if var_name:
            self.uds_extract_var.setText(var_name)
            
        self._sync_byte_grid_from_pdu()
        if hasattr(self, "uds_byte_grid"):
            self.uds_byte_grid.set_selection(offset, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _on_save_current_as_template(self):
        """将当前配置的 DID 提取规则保存为自定义模板"""
        req = self.uds_request.text().strip().upper()
        if not req:
            QMessageBox.warning(self, "提示", "请先输入请求 PDU (如 22 01 01)")
            return
            
        parts = req.split()
        did_str = " ".join(parts[1:3]) if len(parts) >= 3 else req
        default_name = f"BMS 自定义项 ({did_str})"
        
        name, ok = QInputDialog.getText(self, "保存为模板", "请输入模板名称:", text=default_name)
        if not ok or not name.strip():
            return
            
        mode_idx = self.uds_extract_mode.currentIndex()
        mode_map = {0: "NONE", 1: "ASCII", 2: "CALC", 3: "FORMULA"}
        mode = mode_map.get(mode_idx, "NONE")
        
        item_dict = {
            "name": name.strip(),
            "did": did_str,
            "request": req,
            "expected": self.uds_expected.text().strip().upper(),
            "mode": mode,
            "offset": self.uds_extract_offset.value(),
            "length": self.uds_extract_len.value(),
            "endian": "little" if "小端" in self.uds_extract_endian.currentText() else "big",
            "signed": self.uds_extract_signed.isChecked(),
            "scale": self.uds_extract_scale.value(),
            "offset_val": self.uds_extract_offset_val.value(),
            "formula": self.uds_extract_formula.text().strip(),
            "unit": self.uds_extract_unit.text().strip(),
            "var_name": self.uds_extract_var.text().strip(),
        }
        
        if not hasattr(self, "uds_dict_mgr"):
            from core.uds_dictionary import UDSDictionaryManager
            self.uds_dict_mgr = UDSDictionaryManager()
            
        self.uds_dict_mgr.add_or_update_item(item_dict)
        self._reload_uds_templates_combo()
        
        for i in range(self.combo_uds_templates.count()):
            if name.strip() in self.combo_uds_templates.itemText(i):
                self.combo_uds_templates.setCurrentIndex(i)
                break
        QMessageBox.information(self, "保存成功", f"模板【{name.strip()}】已成功存入本地字典库！")

    def _on_import_did_csv(self):
        """从外部 CSV 字典批量导入 DID 配置"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 DID 字典 CSV 表格", "", "CSV 表格 (*.csv);;所有文件 (*.*)"
        )
        if not path:
            return
        if not hasattr(self, "uds_dict_mgr"):
            from core.uds_dictionary import UDSDictionaryManager
            self.uds_dict_mgr = UDSDictionaryManager()
            
        count = self.uds_dict_mgr.import_from_csv(path)
        self._reload_uds_templates_combo()
        QMessageBox.information(self, "导入完成", f"成功从 CSV 导入 {count} 条 DID 模板！")

    def _on_live_sniff_clicked(self):
        """在线抓取 (Sniff)：向真实硬件发送请求并抓取真实回包，瞬间生成真实方块"""
        req_str = self.uds_request.text().strip().upper()
        if not req_str:
            QMessageBox.warning(self, "提示", "请先输入请求 PDU (例如 22 01 01)")
            return
            
        can_tool = None
        if self.device_manager:
            can_tool = getattr(self.device_manager, "pcan", None) or getattr(self.device_manager, "can", None)
        resp_hex = None
        if can_tool and getattr(can_tool, "is_connected", False):
            try:
                req_bytes = bytes.fromhex(req_str)
                client = can_tool.create_uds_client(
                    tx_id=int(self.uds_tx_id.text().strip(), 0),
                    rx_id=int(self.uds_rx_id.text().strip(), 0),
                    can_fd=self.uds_can_type.currentIndex() > 0,
                    brs=self.uds_can_type.currentIndex() == 2,
                    timeout=max(0.001, self.uds_timeout.value() / 1000.0),
                )
                response = client.request(req_bytes[0], req_bytes[1:])
                if response.positive and response.raw:
                    resp_hex = response.raw.hex(" ").upper()
                elif not response.positive:
                    raise RuntimeError(response.error or response.nrc_name or "ECU 返回否定响应")
            except Exception as e:
                QMessageBox.warning(self, "嗅探失败", f"实机通信发生异常: {e}\n将使用仿真数据生成方块。")
                
        if not resp_hex:
            prefix = self._uds_positive_response_prefix(req_str)
            off = self.uds_extract_offset.value()
            length = self.uds_extract_len.value()
            target_len = max(off + length, 8)
            mock_bytes = bytearray.fromhex(prefix) if prefix else bytearray([0x62, 0x01, 0x01])
            while len(mock_bytes) < target_len:
                mock_bytes.append(0x12 if len(mock_bytes) % 2 == 0 else 0x34)
            resp_hex = mock_bytes.hex(" ").upper()
            
        self.uds_expected.setText(resp_hex)
        self._sync_byte_grid_from_pdu()
        QMessageBox.information(self, "抓取成功", f"已成功获取响应数据报文：\n{resp_hex}\n下方方块已自动生成，请直接滑动拖拽选区！")

    def _on_spinbox_offset_or_len_changed(self):
        """手动调节偏移量或长度 SpinBox 时，反向同步方块的高亮选区与全息预览"""
        if hasattr(self, "uds_byte_grid"):
            off = self.uds_extract_offset.value()
            length = self.uds_extract_len.value()
            self.uds_byte_grid.set_selection(off, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _sync_byte_grid_from_pdu(self):
        """解析 PDU 并在方块上展示，若数据不足自动进行虚拟补齐 (Auto-Mock Padding)"""
        text = self.uds_expected.text().strip() or self.uds_request.text().strip()
        if hasattr(self, "uds_byte_grid"):
            tokens = [t for t in text.replace(",", " ").split() if t]
            off = self.uds_extract_offset.value()
            length = self.uds_extract_len.value()
            
            if length > 0 and (off + length) > len(tokens):
                min_needed = off + length
            elif len(tokens) <= 3 and tokens:
                min_needed = max(off + length, 8)
            else:
                min_needed = len(tokens)
            
            if tokens and len(tokens) < min_needed:
                padded = list(tokens)
                while len(padded) < min_needed:
                    padded.append("00")
                display_hex = " ".join(padded)
            else:
                display_hex = text
                
            self.uds_byte_grid.set_hex_string(display_hex)
            if length > 0 or off > 0:
                self.uds_byte_grid.set_selection(off, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _on_byte_grid_range_selected(self, offset: int, length: int, chunk: bytes):
        self.uds_extract_offset.blockSignals(True)
        self.uds_extract_len.blockSignals(True)
        self.uds_extract_offset.setValue(offset)
        self.uds_extract_len.setValue(length)
        self.uds_extract_offset.blockSignals(False)
        self.uds_extract_len.blockSignals(False)
        self._refresh_byte_grid_preview(offset, length, chunk)

    def _refresh_byte_grid_preview(self, offset=None, length=None, chunk=None):
        if not hasattr(self, "uds_byte_grid"):
            return
        if offset is None or length is None or chunk is None:
            offset, length, chunk = self.uds_byte_grid.get_selected_range()

        if length <= 0 or not chunk:
            self.lbl_byte_range.setText("【拖拽选区】 未选择")
            self.lbl_byte_hex.setText("Hex: --")
            self.lbl_byte_int.setText("大端整数: --")
            self.lbl_byte_phys.setText("换算物理值: --")
            return

        end_idx = offset + length - 1
        self.lbl_byte_range.setText(f"【拖拽选区】 Byte [{offset} ~ {end_idx}] (偏移={offset}, 长度={length})")
        hex_str = chunk.hex(" ").upper()
        self.lbl_byte_hex.setText(f"Hex: {hex_str}")

        endian = "little" if "小端" in self.uds_extract_endian.currentText() else "big"
        is_signed = self.uds_extract_signed.isChecked()
        raw_int = None
        if len(chunk) <= 8:
            try:
                raw_int = int.from_bytes(chunk, byteorder=endian, signed=is_signed)
                self.lbl_byte_int.setText(f"整数({endian}): {raw_int}")
            except Exception:
                self.lbl_byte_int.setText("整数: 异常")
        else:
            self.lbl_byte_int.setText("整数: 长字节(超64位)")

        # 换算物理值预览
        mode_idx = self.uds_extract_mode.currentIndex()
        if mode_idx == 1: # ASCII
            ascii_text = chunk.decode("utf-8", errors="ignore").replace("\x00", "").strip()
            self.lbl_byte_phys.setText(f"ASCII: \"{ascii_text}\"")
        elif mode_idx == 2 and raw_int is not None: # CALC
            scale = self.uds_extract_scale.value()
            off_val = self.uds_extract_offset_val.value()
            unit = self.uds_extract_unit.text().strip()
            phys = round(raw_int * scale + off_val, 4)
            unit_str = f" {unit}" if unit else ""
            self.lbl_byte_phys.setText(f"物理值: {phys}{unit_str}")
        elif mode_idx == 3 and raw_int is not None: # FORMULA
            expr = self.uds_extract_formula.text().strip() or "val"
            unit = self.uds_extract_unit.text().strip()
            try:
                phys = round(float(eval(expr, {"__builtins__": None}, {"val": raw_int, "round": round, "abs": abs})), 4)
                unit_str = f" {unit}" if unit else ""
                self.lbl_byte_phys.setText(f"公式值: {phys}{unit_str}")
            except Exception:
                self.lbl_byte_phys.setText("公式计算异常")
        else:
            if raw_int is not None:
                self.lbl_byte_phys.setText(f"原始数值: {raw_int}")
            else:
                self.lbl_byte_phys.setText("换算物理值: --")

    def _on_uds_extract_mode_changed(self, idx):
        # 0: 无提取, 1: ASCII, 2: CALC, 3: FORMULA
        is_none = (idx == 0)
        is_calc = (idx == 2)
        is_formula = (idx == 3)

        self.uds_extract_offset.setEnabled(not is_none)
        self.uds_extract_len.setEnabled(not is_none)
        self.uds_extract_var.setEnabled(not is_none)
        self.uds_ext_detail_widget.setVisible(is_calc or is_formula)

        self.lbl_ext_scale.setVisible(is_calc)
        self.uds_extract_scale.setVisible(is_calc)
        self.lbl_ext_offset.setVisible(is_calc)
        self.uds_extract_offset_val.setVisible(is_calc)
        self.lbl_ext_formula.setVisible(is_formula)
        self.uds_extract_formula.setVisible(is_formula)

        self._refresh_byte_grid_preview()

    def _get_uds_extract_rule_string(self):
        idx = self.uds_extract_mode.currentIndex()
        if idx == 0:
            return ""
        from core.uds_parser import format_uds_rule_string
        offset = self.uds_extract_offset.value()
        length = self.uds_extract_len.value()
        var_name = self.uds_extract_var.text().strip()

        if idx == 1:
            return format_uds_rule_string({"mode": "ASCII", "offset": offset, "length": length, "var_name": var_name})
        elif idx == 2:
            endian = "little" if "小端" in self.uds_extract_endian.currentText() else "big"
            signed = self.uds_extract_signed.isChecked()
            scale = self.uds_extract_scale.value()
            offset_val = self.uds_extract_offset_val.value()
            unit = self.uds_extract_unit.text().strip()
            return format_uds_rule_string({
                "mode": "CALC", "offset": offset, "length": length, "endian": endian,
                "signed": signed, "scale": scale, "offset_val": offset_val, "unit": unit, "var_name": var_name
            })
        elif idx == 3:
            endian = "little" if "小端" in self.uds_extract_endian.currentText() else "big"
            formula = self.uds_extract_formula.text().strip() or "val"
            unit = self.uds_extract_unit.text().strip()
            return format_uds_rule_string({
                "mode": "FORMULA", "offset": offset, "length": length, "endian": endian,
                "formula": formula, "unit": unit, "var_name": var_name
            })
        return ""

    def _set_uds_extract_from_rule_string(self, rule_str):
        if not rule_str or rule_str == "--":
            self.uds_extract_mode.setCurrentIndex(0)
            self.uds_extract_var.clear()
            self._on_uds_extract_mode_changed(0)
            if hasattr(self, "uds_byte_grid"):
                self.uds_byte_grid.set_selection(0, 0, emit_signal=False)
            return

        from core.uds_parser import parse_uds_rule_string
        rule = parse_uds_rule_string(rule_str)
        if not rule:
            self.uds_extract_mode.setCurrentIndex(0)
            self._on_uds_extract_mode_changed(0)
            return

        mode = rule.get("mode", "NONE")
        off = int(rule.get("offset", 3))
        length = int(rule.get("length", 0))
        self.uds_extract_offset.setValue(off)
        self.uds_extract_len.setValue(length)
        self.uds_extract_var.setText(rule.get("var_name", ""))

        if mode == "ASCII":
            self.uds_extract_mode.setCurrentIndex(1)
        elif mode in ("CALC", "NUMERIC"):
            self.uds_extract_mode.setCurrentIndex(2)
            self.uds_extract_endian.setCurrentIndex(1 if rule.get("endian") == "little" else 0)
            self.uds_extract_signed.setChecked(bool(rule.get("signed", False)))
            self.uds_extract_scale.setValue(float(rule.get("scale", 1.0)))
            self.uds_extract_offset_val.setValue(float(rule.get("offset_val", 0.0)))
            self.uds_extract_unit.setText(rule.get("unit", ""))
        elif mode == "FORMULA":
            self.uds_extract_mode.setCurrentIndex(3)
            self.uds_extract_endian.setCurrentIndex(1 if rule.get("endian") == "little" else 0)
            self.uds_extract_formula.setText(rule.get("formula", ""))
            self.uds_extract_unit.setText(rule.get("unit", ""))
        else:
            self.uds_extract_mode.setCurrentIndex(0)

        self._on_uds_extract_mode_changed(self.uds_extract_mode.currentIndex())
        if hasattr(self, "uds_byte_grid"):
            self.uds_byte_grid.set_selection(off, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _append_uds_table_row(self, name, request, expected, rule_str=""):
        row = self.uds_table.rowCount()
        self.uds_table.insertRow(row)
        rule_display = rule_str.strip() if rule_str.strip() else "--"
        values = [name, request.upper(), expected.upper(), "校验" if expected else "不校验", rule_display]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (3, 4):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column == 4 and rule_display != "--":
                item.setToolTip(f"UDS提取规则: {rule_display}")
            self.uds_table.setItem(row, column, item)
        self.uds_table.selectRow(row)

    def on_uds_service_changed(self, service):
        code = service.split(" ", 1)[0]
        if code == "自定义":
            return
        previous = self.uds_request.text().strip().split()
        known_codes = {self.uds_service.itemText(i).split(" ", 1)[0]
                       for i in range(self.uds_service.count() - 1)}
        if not previous or previous[0].upper() in known_codes:
            tail = previous[1:] if previous else []
            self.uds_request.setText(" ".join([code] + tail))

    @staticmethod
    def _uds_positive_response_prefix(request_text):
        """根据 ISO 14229 常用服务的肯定响应回显规则生成校验前缀。"""
        try:
            request = [int(token, 16) for token in request_text.replace(",", " ").split()]
        except ValueError:
            return ""
        if not request or not 0 <= request[0] <= 0xBF:
            return ""
        sid = request[0]
        positive = [(sid + 0x40) & 0xFF]
        # value = 需要从请求中回显的 SID 后字节数。None = 仅校验肯定响应 SID。
        echo_lengths = {
            0x10: 1, 0x11: 1, 0x14: 0, 0x19: 1, 0x22: 2, 0x23: 0,
            0x27: 1, 0x28: 1, 0x2A: 0, 0x2C: 3, 0x2E: 2, 0x2F: 3,
            0x31: 3, 0x34: 0, 0x36: 1, 0x37: 0, 0x3D: 0, 0x3E: 1,
            0x83: 1, 0x84: 0, 0x85: 1, 0x86: 1, 0x87: 1,
        }
        echo_length = echo_lengths.get(sid, 0)
        echoed = request[1:1 + echo_length]
        # 子功能的 bit7 是“抑制肯定响应”位，肯定响应回显时应清零。
        if echoed and sid in {0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x83, 0x85, 0x86, 0x87}:
            echoed[0] &= 0x7F
        return " ".join(f"{value:02X}" for value in positive + echoed)

    def on_uds_request_changed(self, request_text):
        if not hasattr(self, "uds_auto_response") or not self.uds_auto_response.isChecked():
            return
        self.uds_expected.setText(self._uds_positive_response_prefix(request_text))

    def add_uds_row(self):
        request = self.uds_request.text().strip()
        expected = self.uds_expected.text().strip()
        if not request:
            return
        rule_str = self._get_uds_extract_rule_string()
        self._append_uds_table_row(self.uds_service.currentText(), request, expected, rule_str)
        self.uds_request.clear()
        self.uds_expected.clear()

    def load_uds_row(self, row, _column=0):
        if row < 0:
            return
        name_item = self.uds_table.item(row, 0)
        request_item = self.uds_table.item(row, 1)
        expected_item = self.uds_table.item(row, 2)
        rule_item = self.uds_table.item(row, 4)
        if name_item:
            index = self.uds_service.findText(name_item.text())
            self.uds_service.setCurrentIndex(index if index >= 0 else self.uds_service.count() - 1)
        self.uds_request.setText(request_item.text() if request_item else "")
        self.uds_expected.setText(expected_item.text() if expected_item else "")
        rule_str = rule_item.text() if rule_item else ""
        self._set_uds_extract_from_rule_string(rule_str)

    def update_uds_row(self):
        row = self.uds_table.currentRow()
        if row < 0:
            self.add_uds_row()
            return
        request = self.uds_request.text().strip().upper()
        expected = self.uds_expected.text().strip().upper()
        if not request:
            return
        rule_str = self._get_uds_extract_rule_string()
        rule_display = rule_str.strip() if rule_str.strip() else "--"
        values = [self.uds_service.currentText(), request, expected, "校验" if expected else "不校验", rule_display]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (3, 4):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column == 4 and rule_display != "--":
                item.setToolTip(f"UDS提取规则: {rule_display}")
            self.uds_table.setItem(row, column, item)
        self.uds_table.selectRow(row)

    def delete_uds_row(self):
        row = self.uds_table.currentRow()
        if row >= 0:
            self.uds_table.removeRow(row)

    def clear_uds_rows(self):
        self.uds_table.setRowCount(0)

    def move_uds_row(self, offset):
        row = self.uds_table.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.uds_table.rowCount():
            return
        values = [self.uds_table.item(row, column).text() if self.uds_table.item(row, column) else ""
                  for column in range(self.uds_table.columnCount())]
        self.uds_table.removeRow(row)
        self.uds_table.insertRow(target)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in (3, 4):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column == 4 and values[4] != "--":
                item.setToolTip(f"UDS提取规则: {values[4]}")
            self.uds_table.setItem(target, column, item)
        self.uds_table.selectRow(target)

    def _uds_script(self):
        lines = []
        for row in range(self.uds_table.rowCount()):
            request_item = self.uds_table.item(row, 1)
            expected_item = self.uds_table.item(row, 2)
            rule_item = self.uds_table.item(row, 4)
            request = request_item.text().strip() if request_item else ""
            expected = expected_item.text().strip() if expected_item else ""
            rule_str = rule_item.text().strip() if rule_item else ""
            if request:
                line = f"{request} => {expected}" if expected else request
                if rule_str and rule_str != "--":
                    line = f"{line} | {rule_str}"
                lines.append(line)
        return "\n".join(lines)

    def get_data(self):
        device = self.device_combo.currentText()
        action = self.action_combo.currentText()
        key=device_key(device);spec=action_spec(key,action)
        if spec:
            values=self.device_step_form.values()
            summary='，'.join(f'{field.label}={values[field.key]}' for field in spec.fields if field.key!='variable')
            return {'device':device,'action':action,
                    'params':encode_device_step(key,spec.key,values),
                    'type':'读取仪表' if spec.read else '设置仪表',
                    'is_judgment':self.cb_judgment.isChecked(),
                    'fail_strategy':self.fail_strategy_combo.currentText(),
                    'name':f'{device}-{action} ({summary})'}
        idx = self.param_stack.currentIndex()
        
        params = []
        step_type = "设置仪表"
        
        if "MES" in device:
            step_type = "MES过站校验"
        elif "同步屏障" in device:
            step_type = "同步屏障"
            params.append("Barrier")
        elif "瑞萨" in device or "rfp-cli" in device:
            if "烧录" in action or "执行烧录工程" in action:
                step_type = "设置仪表"
                params.append(f"FILE:{self.e2_file.currentText().strip()}")
                params.append(f"DEVICE_FAMILY:{self.combo_e2_family.currentText().strip()}")
                if self.e2_interface.text().strip():
                    params.append(f"INTERFACE:{self.e2_interface.text().strip()}")
                if self.e2_speed.text().strip():
                    params.append(f"SPEED:{self.e2_speed.text().strip()}")
                params.append(f"TIMEOUT:{self.e2_timeout.value()}")
                if self.cb_e2_verify_diff.isChecked():
                    params.append("VERIFY_DIFF:1")
            elif "连接" in action or "连接检测" in action:
                step_type = "连接检测"
            elif "查询" in action or "查询状态" in action:
                step_type = "查询状态"
            else:
                step_type = action
        elif "SG400" in device or "力捷丰" in device:
            if "烧录" in action or '回读校验' in action:
                step_type = "设置仪表"
                params.append(f"PROJECT:{self.opteeq_project.currentText().strip()}")
                params.append(f"TIMEOUT:{self.opteeq_timeout.value()}")
                if self.cb_opteeq_verify_diff.isChecked() or '回读校验' in action:
                    params.append("VERIFY_DIFF:1")
                    params.append(f"VERIFY_SCOPE:{self.combo_opteeq_verify_scope.currentText().strip()}")
                    if self.cb_opteeq_archive_report.isChecked():
                        params.append("ARCHIVE_REPORT:1")
            elif "连接" in action or "连接检测" in action:
                step_type = "连接检测"
            elif "查询" in action or "查询状态" in action:
                step_type = "查询状态"
            else:
                step_type = action
        elif idx == 14: # 空参数占位页面
            if "全部" in action:
                step_type = "继电器控制"
                if "开启" in action: params.append("开启")
                elif "关闭" in action: params.append("关闭")
                else: params.append("--")
            else:
                step_type = action if action else "通用操作"
        elif idx == 0: # 设置页面
            if self.cb_volt.isChecked(): params.append(f"{self.i_volt.value()}V")
            if self.cb_curr.isChecked(): params.append(f"{self.i_curr.value()}A")
            if self.cb_output.isChecked(): params.append(self.output_combo.currentText())
        elif idx == 3: # 读取页面
            step_type = "读取仪表"
            if self.rb_volt.isChecked(): params.append("读取电压")
            if self.rb_curr.isChecked(): params.append("读取电流")
            if supports_read_units(device):
                params.append(f"UNIT:{self.read_unit.currentText()}")
        elif idx == 4: # Easy320 / Relay
            step_type = "继电器控制"
            selected = [str(i+1) for i, cb in enumerate(self.easy320_checks) if cb.isChecked()]
            params.append(",".join(selected))
        elif idx == 8: # Aging Board Relay
            step_type = "继电器控制"
            selected = [str(i+1) for i, cb in enumerate(self.aging_relay_checks) if cb.isChecked()]
            params.append(",".join(selected))
        elif idx == 1: # CAN
            if action == "接收指定帧ID":
                step_type = "CAN接收"
                params.append(f"ID:{self.c_id.text().strip()}")
                params.append(f"TYPE:{self.c_type.currentIndex()}")
                params.append(f"CH:{self.c_channel.value()}")
                params.append(f"TIMEOUT:{self.c_timeout.value()}")
            else:
                step_type = "CAN发送" if "发送" in action else "CAN交互"
                params.append(f"ID:{self.c_id.text().strip()}")
                params.append(f"DATA:{self.c_data.text().strip()}")
                if "发送" not in action:
                    params.append(f"WAIT_ID:{self.c_wait_id.text().strip()}")
                    params.append(f"TIMEOUT:{self.c_timeout.value()}")
                params.append(f"TYPE:{self.c_type.currentIndex()}")
                params.append(f"DLC:{self.c_dlc.value()}")
                params.append(f"CH:{self.c_channel.value()}")
        elif idx == 7: # 3.5H EOL 协议
            step_type = "3.5HEOL协议"
            eol_action = self.eol_op.currentText()
            params.append(f"EOL:{eol_action}")
            
            if "绝缘测试" in eol_action:
                params.append(f"HV1:{self.eol_hv1.value()}")
                params.append(f"R0:{self.eol_r0.value()}")
                params.append(f"R1:{self.eol_r1.value()}")
                params.append(f"R2:{self.eol_r2.value()}")
                params.append(f"R3:{self.eol_r3.value()}")
                params.append(f"R4:{self.eol_r4.value()}")
            else:
                # --- 终极加固通用参数保存：完美覆盖 0x03~0xFF 所有3.5H EOL 协议 ---
                p1_val = self._combo_value(self.eol_param1)
                p2_val = self._combo_value(self.eol_param2)
                args_val = self.eol_args.text().strip()
                
                if p1_val: params.append(f"PARAM1:{p1_val}")
                if p2_val: params.append(f"PARAM2:{p2_val}")
                if "0x07" in eol_action:
                    params.append(f"PARAM3:{self.eol_param3.value()}")
                    params.append(f"PARAM4:{self.eol_param4.value()}")
                if "0x10" in eol_action:
                    if hasattr(self, "eol_diff_ambient") and self.eol_diff_ambient.isChecked():
                        params.append(f"DIFF_AMBIENT:1")
                    if hasattr(self, "eol_temp_convert") and self.eol_temp_convert.isChecked():
                        params.append(f"CONVERT_TEMP:1")
                if "0x07 CSC批量读取" in eol_action and hasattr(self, "eol_fix_adjacent"):
                    args_kv = self._split_params(args_val, is_args_level=True)
                    if self.eol_fix_adjacent.isChecked():
                        args_kv["FIX_ADJACENT"] = "1"
                    else:
                        args_kv.pop("FIX_ADJACENT", None)
                    if hasattr(self, "eol_compensate_random"):
                        if self.eol_compensate_random.isChecked():
                            args_kv["COMPENSATE"] = "1"
                        else:
                            args_kv.pop("COMPENSATE", None)
                    args_val = ",".join(f"{k}:{v}" for k, v in args_kv.items())
                if args_val: params.append(f"ARGS:{args_val}")
                
                # 兼容老版本历史配方的字段格式，额外写入特定键名，确保老引擎读取无误
                if "0x06" in eol_action:
                    params.append(f"ADC:{p1_val}")
                    params.append(f"MODE:{p2_val}")
                elif "0x03" in eol_action:
                    params.append(f"STATE:{p1_val}")
                    params.append(f"VAL:{p2_val}")
                elif "0x04" in eol_action:
                    params.append(f"INDEX:{p1_val}")
                    params.append(f"LEVEL:{p2_val}")
                elif "0x05" in eol_action:
                    params.append(f"PWM:{p1_val}")
                    params.append(f"MODE:{p2_val}")
                elif "0x10" in eol_action:
                    params.append(f"NTC:{p1_val}")
                    params.append(f"NTC_TYPE:{p2_val}")
                elif "0x0B" in eol_action:
                    params.append(f"HALL:{p1_val}")
                elif "0x07" in eol_action or "0x08" in eol_action or "0xFF" in eol_action:
                    params.append(f"OP:{p1_val}")
                    params.append(f"INDEX:{p2_val}")
            
            # 通用配置参数
            params.append(f"TIMEOUT:{self.eol_timeout.value()}")
            params.append(f"CH:{self.eol_channel.value()}")
            params.append(f"TYPE:{self.eol_can_type.currentIndex()}")
            params.append(f"DLC:{self.eol_dlc.value()}")
            params.append(f"TX_ID:{self.eol_tx_id.text().strip()}")
            params.append(f"RX_ID:{self.eol_rx_id.text().strip()}")
        elif idx == 9: # 变量操作
            step_type = "读取变量"
            params.append(f"VAR:{self.var_name.currentText().strip()}")
        elif idx == 10: # 公式计算
            step_type = "公式计算"
            params.append(f"FORMULA:{self.formula_expr.text().strip()}")
            var_txt = self.formula_var.currentText().strip()
            if var_txt:
                params.append(f"VAR:{var_txt}")
        elif idx == 11: # 力捷丰 SG400
            step_type = "设置仪表"
            params.append(f"PROJECT:{self.opteeq_project.currentText().strip()}")
            params.append(f"TIMEOUT:{self.opteeq_timeout.value()}")
            if self.cb_opteeq_verify_diff.isChecked():
                params.append("VERIFY_DIFF:1")
                params.append(f"VERIFY_SCOPE:{self.combo_opteeq_verify_scope.currentText().strip()}")
                if self.cb_opteeq_archive_report.isChecked():
                    params.append("ARCHIVE_REPORT:1")
        elif idx == 15: # 瑞萨 E2 (rfp-cli)
            step_type = "设置仪表"
            params.append(f"FILE:{self.e2_file.currentText().strip()}")
            params.append(f"DEVICE_FAMILY:{self.combo_e2_family.currentText().strip()}")
            if self.e2_interface.text().strip():
                params.append(f"INTERFACE:{self.e2_interface.text().strip()}")
            if self.e2_speed.text().strip():
                params.append(f"SPEED:{self.e2_speed.text().strip()}")
            params.append(f"TIMEOUT:{self.e2_timeout.value()}")
            if self.cb_e2_verify_diff.isChecked():
                params.append("VERIFY_DIFF:1")
        elif idx == 13: # UDS 顺序交互列表
            step_type = "CAN交互"
            uds_text = self._uds_script()
            encoded = base64.urlsafe_b64encode(uds_text.encode("utf-8")).decode("ascii")
            params.append(f"TX_ID:{self.uds_tx_id.text().strip()}")
            params.append(f"RX_ID:{self.uds_rx_id.text().strip()}")
            params.append(f"TYPE:{self.uds_can_type.currentIndex()}")
            params.append(f"TIMEOUT:{self.uds_timeout.value()}")
            params.append(f"CYCLES:{self.uds_cycles.value()}")
            params.append(f"INTERVAL:{self.uds_interval.value()}")
            params.append(f"LIST64:{encoded}")
        elif idx == 12: # APP 刷写
            if "查找" in action:
                step_type = "APP刷写配置"
                params.append(f"HOST:{self.app_flash_program.text().strip()}")
                params.append(f"INI:{self.app_flash_ini.text().strip()}")
                params.append(f"SECTION:{self.app_flash_section.text().strip()}")
                params.append(f"KEY:{self.app_flash_key.text().strip()}")
                params.append(f"APPNAME:{self.app_flash_name.text().strip()}")
            else:
                step_type = "APP刷写"
                params.append(f"HOST:{self.app_flash_program.text().strip()}")
                params.append(f"TIMEOUT:{self.app_flash_timeout.value()}")
        elif idx == 2: # 等待
            step_type = "等待"
            params.append(f"{self.w_time.value()}ms")
        elif idx == 5: # CA550
            step_type = "校准仪设置"
            params.append(f"Type:{self.ca_type.currentText().split(' ')[0]}")
            params.append(f"Val:{self.ca_val.value()}")
            params.append(f"Range:{self.ca_range.currentText()}")
            params.append(f"Output:{self.ca_output_state.currentText()}")
        elif idx == 6: # 模拟器批量页面
            params.append(f"{self.sim_batch_volt.value()}V")
            params.append(f"{self.sim_batch_curr.value()}mA")
            params.append(f"{self.sim_batch_output.currentText()}")
            params.append(f"Range:{self.sim_batch_range.currentText().split(' ')[0]}")
            params.append(f"CH:{self.sim_batch_channels.text()}")
        elif "全部" in action:
            if "开启" in action: params.append("开启")
            elif "关闭" in action: params.append("关闭")
            else: params.append("--")

        params_str = " / ".join(params) if params else "--"
        
        return {
            'device': device,
            'action': action,
            'params': params_str,
            'type': step_type,
            'is_judgment': self.cb_judgment.isChecked(),
            'fail_strategy': self.fail_strategy_combo.currentText(),
            'name': f"{device}-{action} ({params_str})"
        }

    def _adjust_opteeq_project_popup_width(self):
        """根据当前全部工程名称的最长像素宽度，自适应扩展下拉弹出框的最小宽度，彻底杜绝文本截断"""
        if not hasattr(self, "opteeq_project") or not self.opteeq_project.view():
            return
        fm = self.opteeq_project.fontMetrics()
        max_w = 0
        for i in range(self.opteeq_project.count()):
            t = self.opteeq_project.itemText(i)
            max_w = max(max_w, fm.horizontalAdvance(t))
            self.opteeq_project.setItemData(i, t, Qt.ToolTipRole)
        target_w = max(self.opteeq_project.width(), max_w + 60, 560)
        self.opteeq_project.view().setMinimumWidth(target_w)
        self.opteeq_project.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def _update_opteeq_meta_preview(self, *args):
        """选定工程后自动解析并呈现源程序元数据"""
        if not hasattr(self, "lbl_opteeq_meta_preview") or not hasattr(self, "opteeq_project"):
            return
        proj = self.opteeq_project.currentText().strip()
        if not proj or proj.startswith("（"):
            self.lbl_opteeq_meta_preview.setText("ℹ️ 源程序元数据: 未选定有效工程")
            return
        target_path = proj
        if not os.path.isabs(target_path):
            candidate = os.path.abspath(os.path.join("cache/projects", proj))
            if os.path.isfile(candidate):
                target_path = candidate
        if os.path.isfile(target_path):
            try:
                from core.burn_recorder import parse_opj_metadata
                meta = parse_opj_metadata(target_path)
                src_hex = meta.get("source_file", "--")
                chip = meta.get("chip_name", "--")
                crc = meta.get("opd_checksum") or meta.get("source_crc", "--")
                sectors = meta.get("sectors", [])
                opd_size = meta.get("opd_size", 0)
                if opd_size >= 1024 * 1024:
                    size_desc = f"{opd_size / 1024 / 1024:.2f} MB"
                elif opd_size > 0:
                    size_desc = f"{opd_size / 1024:.1f} KB"
                else:
                    size_desc = "未知"
                sec_desc = f"{len(sectors)}个分区" if sectors else "无物理扇区"
                if sectors:
                    sec_desc += f" (起始: {sectors[0]['start_hex']})"
                self.lbl_opteeq_meta_preview.setText(
                    f"✅ 源固件: {src_hex} | 程序大小: {size_desc} | 芯片: {chip} | 扇区: {sec_desc} | 基准CRC: {crc}"
                )
                return
            except Exception as e:
                self.lbl_opteeq_meta_preview.setText(f"ℹ️ 本地工程解析提示: {e}")
                return
        self.lbl_opteeq_meta_preview.setText(f"ℹ️ 脱机工程: {proj} (烧录时将自动拉取元数据与单片机回读比对)")

    def _preload_downloader_projects(self):
        """自动预装填已知与本地缓存的脱机工程"""
        existing_items = [self.opteeq_project.itemText(i) for i in range(self.opteeq_project.count())]
        
        cache_dir = os.path.abspath("cache/projects")
        found = []
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                if fname.lower().endswith((".opj", ".oprj")):
                    found.append(fname)

        programmer = getattr(self.device_manager, 'opteeq', None) if self.device_manager else None
        if programmer and getattr(programmer, 'is_connected', False):
            try:
                remote_files = programmer.get_project_list()
                if remote_files:
                    for rf in remote_files:
                        if rf not in found:
                            found.append(rf)
            except Exception:
                pass

        cur_text = self.opteeq_project.currentText().strip()
        merged = []
        for item in found + existing_items:
            if item and item not in merged and not item.startswith("（"):
                merged.append(item)
        
        if merged:
            self.opteeq_project.clear()
            self.opteeq_project.addItems(merged)
            self._adjust_opteeq_project_popup_width()
            if cur_text:
                idx = self.opteeq_project.findText(cur_text)
                if idx >= 0:
                    self.opteeq_project.setCurrentIndex(idx)
                else:
                    self.opteeq_project.setEditText(cur_text)

    def read_downloader_projects(self):
        """在线读取力捷丰 SG400 下载器脱机工程列表"""
        programmer = getattr(self.device_manager, 'opteeq', None) if self.device_manager else None
        
        if not programmer:
            try:
                from devices.opteeq_sg400 import OpteeQSG400
                programmer = OpteeQSG400()
            except Exception:
                pass

        if not programmer:
            QMessageBox.warning(self, "读取失败", "未找到力捷丰 SG400 下载器驱动或设备管理器。")
            return

        files = []
        err = ""
        try:
            if not programmer.is_connected:
                programmer.connect()
            files = programmer.get_project_list()
        except Exception as e:
            err = str(e)
            files = []

        if not files and programmer.last_error:
            err = programmer.last_error

        cache_dir = os.path.abspath("cache/projects")
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                if fname.lower().endswith((".opj", ".oprj")) and fname not in files:
                    files.append(fname)

        if files:
            cur_text = self.opteeq_project.currentText().strip()
            self.opteeq_project.clear()
            self.opteeq_project.addItems(files)
            self._adjust_opteeq_project_popup_width()
            idx = self.opteeq_project.findText(cur_text)
            if idx >= 0:
                self.opteeq_project.setCurrentIndex(idx)
            elif cur_text:
                self.opteeq_project.setEditText(cur_text)
            else:
                self.opteeq_project.setCurrentIndex(0)
            QMessageBox.information(self, "读取成功", f"成功读取到 {len(files)} 个工程文件:\n" + "\n".join(files[:10]) + ("\n..." if len(files) > 10 else ""))
        else:
            QMessageBox.warning(self, "读取结果", f"未能从下载器读取到脱机工程{f' ({err})' if err else '，请检查设备连线及网络状态'}。")

    def select_e2_project(self):
        cur_text = self.e2_file.currentText().strip()
        start_dir = cur_text if os.path.isfile(cur_text) or os.path.isdir(cur_text) else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择瑞萨E2烧录工程",
            start_dir,
            "瑞萨烧录工程 (*.rpj *.mot *.hex *.srec);;所有文件 (*.*)",
        )
        if path:
            norm_path = os.path.normpath(path)
            idx = self.e2_file.findText(norm_path)
            if idx < 0:
                self.e2_file.addItem(norm_path)
                idx = self.e2_file.count() - 1
            self.e2_file.setCurrentIndex(idx)

    def select_opteeq_project(self):
        cur_text = self.opteeq_project.currentText().strip()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择力捷丰烧录工程",
            cur_text,
            "OpteeQ工程 (*.oprj *.opj);;所有文件 (*.*)",
        )
        if path:
            norm_path = os.path.normpath(path)
            idx = self.opteeq_project.findText(norm_path)
            if idx < 0:
                self.opteeq_project.addItem(norm_path)
                idx = self.opteeq_project.count() - 1
            self.opteeq_project.setCurrentIndex(idx)
            self._adjust_opteeq_project_popup_width()

    def select_app_flash_program(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择上位机程序文件", self.app_flash_program.text().strip(),
            "上位机程序 (*.exe *.bat *.cmd *.py);;所有文件 (*.*)",
        )
        if path:
            self.app_flash_program.setText(os.path.normpath(path))

    def select_app_flash_directory(self):
        current = self.app_flash_program.text().strip()
        initial_dir = current if os.path.isdir(current) else os.path.dirname(current)
        path = QFileDialog.getExistingDirectory(
            self, "选择上位机所在目录", initial_dir or os.getcwd()
        )
        if path:
            self.app_flash_program.setText(os.path.normpath(path))
