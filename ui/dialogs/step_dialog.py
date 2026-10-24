import base64
import json

import os
import re

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QComboBox, QPushButton, QDoubleSpinBox, 
                               QSpinBox, QStackedWidget, QWidget, QFormLayout, QFrame, 
                                QCheckBox, QScrollArea, QGridLayout, QFileDialog, QInputDialog,
                               QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QListView, QGroupBox, QTabWidget, QApplication, QSizePolicy)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator, QShortcut, QKeySequence
from core.measurement_units import CURRENT_UNITS, VOLTAGE_UNITS, configured_read_unit, supports_read_units
from ui.widgets.byte_grid_widget import ByteGridWidget
from core.device_steps import DEVICES as STEP_DEVICES, ACTIONS as STEP_ACTIONS, device_key, action_spec, encode as encode_device_step, decode as decode_device_step
from ui.widgets.device_step_form import DeviceStepForm
from ui.widgets.eliding_label import ElidingLabel


def _resolve_resource_stylesheet(stylesheet):
    import sys
    from pathlib import Path
    root=Path(sys._MEIPASS) if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
    prefix=(root/'resources').as_posix().replace('"','%22')+'/'
    return re.sub(r'url\(resources/([^)]*)\)',lambda match:'url("'+prefix+match.group(1)+'")',stylesheet)

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
    def __init__(self, parent=None, step_data=None, device_manager=None, test_item_name=None):
        super().__init__(parent)
        self.device_manager = device_manager or getattr(parent, 'device_manager', None)
        self.is_loading = False # 状态位：标记是否处于数据加载还原中
        self._legacy_simulator_data = None
        self._uds_load_error = ''
        self._uds_edit_item = None
        self.setWindowTitle("编辑指令 (子工步)")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        available=self.screen().availableGeometry()
        self.resize(min(1050,int(available.width()*.9)),min(820,int(available.height()*.9)))
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
                border: 2px solid white;
                border-radius: 4px;
                background-color: #1A1A2E;
            }
            QCheckBox::indicator:checked {
                background-color: white;
                border: 2px solid white;
                image: url(resources/checkbox_white_checked.svg);
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
        self.setStyleSheet(self.styleSheet() + """
            QAbstractItemView::indicator { width: 20px; height: 20px; }
            QAbstractItemView::indicator:unchecked { background: #1A1A2E; border: 2px solid white; border-radius: 4px; }
            QAbstractItemView::indicator:checked { image: url(resources/checkbox_white_checked.svg); }
            QGroupBox::indicator { width: 20px; height: 20px; border: 2px solid white; border-radius: 4px; background: #1A1A2E; }
            QGroupBox::indicator:checked { image: url(resources/checkbox_white_checked.svg); background: white; }
            QTabWidget::pane { border: 1px solid #334155; }
            QTabBar::tab { background: #16213E; color: white; padding: 9px 14px; }
            QTabBar::tab:selected { background: #0F3460; border-top: 2px solid white; }
        """)
        
        main_layout = QVBoxLayout(self)
        self.setStyleSheet(_resolve_resource_stylesheet(self.styleSheet()))
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)
        
        # 1. 设备与大类选择 (分类整理)
        top_frame = QFrame(self)
        self.device_selection_frame = top_frame
        top_layout = QFormLayout(top_frame)
        self.device_selection_form = top_layout
        top_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        top_layout.setSpacing(6)
        
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
        binding = getattr(self.parent(), 'recipe_can_binding', None)
        self.recipe_can_binding = binding() if callable(binding) else None
        self.can_binding_label = QLabel()
        self.can_physical_channel = QComboBox()
        channel = (self.recipe_can_binding or {}).get('channel', '')
        self.can_binding_label.setText('ZLG CANFDNET-400U（配方绑定）' if channel.startswith('ZLG_CAN')
                                       else ('PCAN_USBBUS1（配方绑定）' if channel else '跟随运行配方的CAN卡'))
        if (self.recipe_can_binding or {}).get('enabled') is False:
            self.can_binding_label.setText('不使用 CAN（请先在配方中选择 CAN 卡）')
            self.can_physical_channel.setEnabled(False)
        self._legacy_can_channel = int(channel.removeprefix('ZLG_CAN')) if channel.startswith('ZLG_CAN') else 1
        self._can_maximum = 4 if channel.startswith('ZLG_CAN') or not channel else 1
        self._can_enabled = set(range(1, self._can_maximum+1))
        zlg = (self.recipe_can_binding or {}).get('zlg')
        if isinstance(zlg, dict) and len(zlg.get('channels', [])) == 4:
            self._can_enabled = {i+1 for i,row in enumerate(zlg['channels']) if row['enabled']}
        for physical in range(1, self._can_maximum+1):
            self.can_physical_channel.addItem(f'CH{physical}', physical)
            if physical not in self._can_enabled:
                self.can_physical_channel.setItemText(physical-1, f'CH{physical}（配方未启用）')
                self.can_physical_channel.model().item(physical-1).setEnabled(False)
        self.can_physical_channel.setCurrentIndex(self.can_physical_channel.findData(min(self._can_enabled)) if self._can_enabled else -1)
        if channel == 'PCAN_USBBUS1':
            self.can_physical_channel.setToolTip('PCAN_USBBUS1只有CH1；运行时始终使用配方绑定设备。')
        self.can_binding_title = QLabel('配方CAN卡:')
        self.can_channel_title = QLabel('物理通道:')
        top_layout.addRow(self.can_binding_title, self.can_binding_label)
        top_layout.addRow(self.can_channel_title, self.can_physical_channel)
        self.can_send_channels = QWidget()
        send_layout = QHBoxLayout(self.can_send_channels);send_layout.setContentsMargins(0,0,0,0)
        self.can_send_all = QCheckBox('全部已启用通道')
        send_layout.addWidget(self.can_send_all)
        self.can_send_checks = {}
        for physical in range(1, self._can_maximum+1):
            check = QCheckBox(f'CH{physical}')
            check.setEnabled(physical in self._can_enabled)
            if physical not in self._can_enabled:check.setToolTip('当前配方未启用此通道')
            check.setChecked(physical == self.can_physical_channel.currentData())
            check.toggled.connect(self._update_send_channel_checks)
            self.can_send_checks[physical] = check;send_layout.addWidget(check)
        send_layout.addStretch()
        self.can_send_all.toggled.connect(self._toggle_all_can_channels)
        self.can_send_title = QLabel('发送通道（可多选）:')
        # A long multi-channel heading must not widen every form label.
        self.can_send_row = QWidget()
        channel_row_layout = QHBoxLayout(self.can_send_row)
        channel_row_layout.setContentsMargins(0,0,0,0)
        channel_row_layout.setSpacing(6)
        channel_row_layout.addWidget(self.can_send_title)
        channel_row_layout.addWidget(self.can_send_channels,1)
        top_layout.addRow(self.can_send_row)
        
        main_layout.addWidget(top_frame)
        
        # 分割线
        line = QFrame(self)
        self.device_separator = line
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #0F3460;")
        main_layout.addWidget(line)
        
        # 2. 滚动区域 (包裹动态参数和策略区)
        self.main_scroll = QScrollArea(self)
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.NoFrame)
        self.main_scroll.setObjectName('stepMainScroll')
        self.main_scroll.setStyleSheet('QScrollArea#stepMainScroll { background-color: transparent; }')
        
        self.main_scroll_content = QWidget() # 容器 Widget
        self.scroll_layout = QVBoxLayout(self.main_scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(12)
        
        # 动态参数配置区
        from ui.widgets.compact_step_layout import CurrentPageStack
        self.param_stack = CurrentPageStack(self)
        self.param_stack.currentChanged.connect(lambda *_:self.param_stack.updateGeometry())
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
        self.c_action_help=QLabel();self.c_action_help.setWordWrap(True);c_form.addRow(self.c_action_help)
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
        self.c_rx_format=QComboBox();self.c_rx_format.addItems(['自动（按响应ID）','标准帧','扩展帧'])
        self.c_rx_prefix=QLineEdit();self.c_rx_prefix.setPlaceholderText('可选，例如 06；仅接收以这些字节开头的回复')
        c_form.addRow('响应帧格式：',self.c_rx_format)
        c_form.addRow('响应开头匹配：',self.c_rx_prefix)
        c_form.addRow("CAN 类型:", self.c_type)
        c_form.addRow("DLC:", self.c_dlc)
        self.c_channel.hide()  # Legacy CH is retained for file compatibility only.
        c_form.addRow("超时:", self.c_timeout)
        from ui.widgets.can_parser_form import CanParserForm
        self.can_parser = CanParserForm()
        self.can_parser.capture_requested.connect(self._capture_can_response)
        c_form.addRow(self.can_parser)

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
        read_radio_style = _resolve_resource_stylesheet('''
            QRadioButton { spacing:8px; background:transparent; }
            QRadioButton::indicator { width:20px; height:20px; border:0; background:transparent; }
            QRadioButton::indicator:unchecked { border:0; image:url(resources/radio_read_unchecked.svg); }
            QRadioButton::indicator:checked { border:0; image:url(resources/radio_read_checked.svg); }
        ''')
        self.rb_volt.setStyleSheet(read_radio_style)
        self.rb_curr.setStyleSheet(read_radio_style)
        
        self.read_group.addButton(self.rb_volt)
        self.read_group.addButton(self.rb_curr)
        
        read_choices = QHBoxLayout()
        read_choices.addWidget(self.rb_volt)
        read_choices.addWidget(self.rb_curr)
        read_choices.addStretch()
        read_layout.addLayout(read_choices)
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
        self.page_easy320.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.page_easy320.setObjectName('relayStepPage')
        self.page_easy320.setStyleSheet('QWidget#relayStepPage { background:#0f172a; }')
        e_layout = QVBoxLayout(self.page_easy320)
        
        scroll = QScrollArea()
        scroll.setObjectName('relayPointScroll')
        scroll.setStyleSheet('''
            QScrollArea#relayPointScroll { background:#0f172a; border:0; }
            QScrollArea#relayPointScroll QScrollBar:vertical { background:#0f172a; width:10px; margin:0; }
            QScrollArea#relayPointScroll QScrollBar::handle:vertical { background:#475569; border-radius:4px; min-height:24px; }
            QScrollArea#relayPointScroll QScrollBar::add-line:vertical,
            QScrollArea#relayPointScroll QScrollBar::sub-line:vertical { height:0; }
            QScrollArea#relayPointScroll QScrollBar::add-page:vertical,
            QScrollArea#relayPointScroll QScrollBar::sub-page:vertical { background:#0f172a; }
        ''')
        scroll.viewport().setObjectName('relayPointViewport')
        scroll.viewport().setStyleSheet('QWidget#relayPointViewport { background:#0f172a; }')
        scroll_content = QWidget()
        scroll_content.setObjectName('relayScrollContent')
        scroll_content.setStyleSheet('QWidget#relayScrollContent { background:#0f172a; }')
        grid = QVBoxLayout(scroll_content)
        self.easy320_checks = []
        from core.easy320_channels import configured_channels
        try:
            self.easy320_channels = configured_channels(self.device_manager)
            self.easy320_config_error = ''
        except (ValueError, KeyError, TypeError) as exc:
            self.easy320_channels = []
            self.easy320_config_error = f'继电器导入配置无效：{exc}'
        self.easy320_missing_channels = set()
        from ui.widgets.relay_combination_form import RelayCombinationForm
        self.relay_combination_form = RelayCombinationForm(self.easy320_channels)
        self.relay_combination_form.mixed_requested.connect(lambda: self.action_combo.setCurrentText('组合设置ON/OFF'))
        grid.addWidget(self.relay_combination_form)
        self.easy320_checks = self.relay_combination_form.checks
        scroll.setWidget(scroll_content)
        scroll.setWidgetResizable(True)
        
        self.easy320_channel_tip = QLabel(self.easy320_config_error or '选择导入表中的DO通道（多选，八进制编号）：')
        self.easy320_channel_tip.setWordWrap(True)
        e_layout.addWidget(self.easy320_channel_tip)
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
        self.sim_batch_curr.setRange(0, 1000); self.sim_batch_curr.setDecimals(1); self.sim_batch_curr.setValue(1000.0)
        self.sim_batch_curr.setSuffix(" mA")
        self.sim_batch_output = QComboBox()
        self.sim_batch_output.addItems(["ON", "OFF"])
        self.sim_batch_range = QComboBox()
        for label, value in [('0–1000 mA', 'HIGH'), ('0–1 mA', 'LOW'), ('AUTO', 'AUTO')]:
            self.sim_batch_range.addItem(label, value)
        self.sim_batch_range.currentIndexChanged.connect(self._update_sim_batch_current_limit)
        self.sim_batch_channels = QLineEdit("ALL")
        
        sim_batch_layout.addRow("设定电压 (V):", self.sim_batch_volt)
        sim_batch_layout.addRow("输出限流 (mA):", self.sim_batch_curr)
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
        self.eol_channel.hide()
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
        self.eol_temp_convert.setEnabled(False)
        self.eol_temp_convert.setToolTip("\u5df2\u505c\u7528\uff0c\u751f\u4ea7\u6d4b\u8bd5\u4fdd\u7559\u539f\u59cb\u5b9e\u6d4b\u503c")
        self.eol_temp_convert.setStyleSheet("color: #00E5FF;")
        self.eol_temp_convert.setVisible(False)
        temp_opts_layout.addWidget(self.eol_temp_convert)
        temp_opts_layout.addStretch()

        eol_form.addRow("", temp_opts_layout)
        
        self.eol_fix_adjacent = QCheckBox("相邻电芯异常值修正 (之和为4.5~5.5V)")
        self.eol_fix_adjacent.setEnabled(False)
        self.eol_fix_adjacent.setToolTip("\u5df2\u505c\u7528\uff0c\u751f\u4ea7\u6d4b\u8bd5\u4fdd\u7559\u539f\u59cb\u5b9e\u6d4b\u503c")
        self.eol_fix_adjacent.setStyleSheet("color: #00E5FF;")
        self.eol_fix_adjacent.setVisible(False)
        eol_form.addRow("", self.eol_fix_adjacent)
        
        self.eol_compensate_random = QCheckBox("开启异常电压随机补偿")
        self.eol_compensate_random.setEnabled(False)
        self.eol_compensate_random.setToolTip("\u5df2\u505c\u7528\uff0c\u751f\u4ea7\u6d4b\u8bd5\u4fdd\u7559\u539f\u59cb\u5b9e\u6d4b\u503c")
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

        # Reserved page index
        # Preserve subsequent page indices while removing the obsolete page.
        self.param_stack.addWidget(QWidget())  # reserved index 8

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
        self.cb_opteeq_verify_diff.setStyleSheet("color: #e6ebf2; font-weight: bold; font-size: 13px;")
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
        self.cb_opteeq_archive_report.setStyleSheet("color: #e2e8f0; font-size: 12px;")
        verify_sub_layout.addRow("留痕归档:", self.cb_opteeq_archive_report)

        self.lbl_opteeq_meta_preview = QLabel("ℹ️ 源程序元数据: 切换或选定工程后自动探测物理扇区与基准CRC")
        self.lbl_opteeq_meta_preview.setStyleSheet("color: #94a3b8; font-size: 12px; font-style: italic;")
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
            "color:#38BDF8; background-color:#0f172a; border:1px solid #1e3a5f; "
            "border-radius:5px; padding:8px;"
        )
        self.app_flash_record_help = QLabel(
            "路径选择说明：请选择与查找刷写文件工步相同的上位机程序或所在目录。\n"
            "系统会自动定位到 上位机目录\\Flash\\Record.txt，写入 Start 后监控 "
            "FLASH / PASS / FAIL。"
        )
        self.app_flash_record_help.setWordWrap(True)
        self.app_flash_record_help.setStyleSheet(
            "color:#38BDF8; background-color:#0f172a; border:1px solid #1e3a5f; "
            "border-radius:5px; padding:8px;"
        )
        self.app_flash_record = QLineEdit(os.path.join("Flash", "Record.txt"))
        self.app_flash_record.setPlaceholderText("Record.txt 的绝对路径")
        self.app_flash_timeout = QSpinBox()
        self.app_flash_timeout.setRange(1, 86400)
        self.app_flash_timeout.setValue(300)
        self.app_flash_timeout.setSuffix(" s")
        self.app_flash_tx_id = QLineEdit('0x7E4')
        self.app_flash_rx_id = QLineEdit('0x7EC')
        self.app_flash_tx_label = QLabel('刷写请求 ID（HEX）：')
        self.app_flash_rx_label = QLabel('刷写响应 ID（HEX）：')
        self.app_flash_monitor_help = QLabel('被动监听，不发送刷写请求。请填写外部刷写上位机实际使用的请求/响应 ID；与普通报文监控共用接收线程。ID 不匹配时只能显示 Record 状态。')
        self.app_flash_monitor_help.setWordWrap(True)
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
        app_flash_layout.addRow(self.app_flash_tx_label, self.app_flash_tx_id)
        app_flash_layout.addRow(self.app_flash_rx_label, self.app_flash_rx_id)
        app_flash_layout.addRow(self.app_flash_monitor_help)
        self.param_stack.addWidget(self.page_app_flash) # 12

        # --- 页面 12: UDS 服务交互列表 ---
        self.page_uds = QWidget()
        self.page_uds.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.page_uds.setStyleSheet("""
            QCheckBox::indicator, QGroupBox::indicator, QAbstractItemView::indicator {
                width: 20px; height: 20px; background: transparent;
                border: 2px solid white; border-radius: 4px;
            }
            QCheckBox::indicator:checked, QGroupBox::indicator:checked, QAbstractItemView::indicator:checked {
                image: url(resources/checkbox_green_tick.svg);
                background: transparent; border: none;
            }
            QCheckBox::indicator:unchecked:hover, QGroupBox::indicator:unchecked:hover,
            QAbstractItemView::indicator:unchecked:hover { background: transparent; border: 2px solid white; }
        """)
        self.page_uds.setStyleSheet(_resolve_resource_stylesheet(self.page_uds.styleSheet()))
        uds_layout = QVBoxLayout(self.page_uds)
        uds_link_row = QFormLayout()
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
            uds_link_row.addRow(label + ":", widget)
        uds_layout.addLayout(uds_link_row)
        from ui.widgets.security_access_form import SecurityAccessForm
        self.uds_security = SecurityAccessForm()
        uds_layout.addWidget(self.uds_security)
        self.uds_unlock_add = QPushButton('添加 DLL 解锁到交互列表（放在会话切换之后、读写之前）')
        self.uds_unlock_add.clicked.connect(self._add_security_unlock_row)
        uds_layout.addWidget(self.uds_unlock_add)

        uds_edit_row = QFormLayout()
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
        self.uds_expected.setPlaceholderText("选填：额外响应校验；读取VIN等数据时可留空")
        self.uds_result_expected = QLineEdit()
        self.uds_result_expected.setPlaceholderText('选填：ASCII字符串、HEX字节或计算后数值，留空不校验提取结果')
        self.uds_sync_expected = QCheckBox('跟随在线抓取选区更新期望结果')
        self.uds_sync_expected.setChecked(True)
        self.uds_sync_expected.setToolTip('仅使用真实抓取的响应填写；取消后可手动修改或清空期望结果。')
        self.uds_request.textChanged.connect(self.on_uds_request_changed)
        self.uds_request.textChanged.connect(lambda: self._sync_byte_grid_from_pdu())
        self.uds_expected.textChanged.connect(lambda: self._sync_byte_grid_from_pdu())
        self.uds_add_btn = QPushButton("添加到列表")
        self.uds_add_btn.clicked.connect(self.add_uds_row)
        uds_edit_row.addRow("诊断服务：", self.uds_service)
        uds_edit_row.addRow("请求 PDU：", self.uds_request)
        uds_edit_row.addRow("报文前缀校验（HEX，选填）：", self.uds_expected)
        uds_edit_row.addRow("额外期望结果（解析后）：", self.uds_result_expected)
        uds_edit_row.addRow(self.uds_sync_expected)
        uds_layout.addLayout(uds_edit_row)

        uds_rule_row = QHBoxLayout()
        self.uds_auto_response = QCheckBox("自动填写控制服务响应（读取服务留空）")
        self.uds_auto_response.setChecked(True)
        self.uds_auto_response.toggled.connect(lambda checked: checked and self.on_uds_request_changed(self.uds_request.text()))
        uds_rule_row.addWidget(self.uds_auto_response)
        response_tip = QLabel("按响应前缀校验；?? 表示任意字节。读取 VIN 等数据时可留空。")
        response_tip.setWordWrap(True)
        self.uds_auto_response.setToolTip(response_tip.text())
        uds_rule_row.addStretch()
        uds_layout.addLayout(uds_rule_row)

        # --- 新增: UDS 响应数据提取与数值计算配置卡片 (可视化拖拽选区增强) ---
        self.uds_extract_group = QGroupBox()
        self.uds_extract_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #334155;
                border-radius: 6px;
                margin-top: 0px;
                padding-top: 0px;
                background-color: #0f172a;
            }
        """)
        uds_ext_vbox = QVBoxLayout(self.uds_extract_group)
        uds_ext_vbox.setSpacing(5)
        uds_ext_vbox.setContentsMargins(10, 8, 10, 8)

        self.btn_live_sniff = QPushButton("📡 在线抓取 (Sniff)")
        self.btn_live_sniff.setToolTip("通过 PCAN 向真实被测 BMS 发送当前请求并抓取真实回包，瞬间生成方块")
        self.btn_live_sniff.setStyleSheet("background-color: #7C3AED; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 11px;")
        self.btn_live_sniff.clicked.connect(self._on_live_sniff_clicked)

        # 1. 可视化交互式拖拽字节流组件 (Byte Blocks)
        self.uds_byte_grid = ByteGridWidget(self)
        self.uds_byte_grid.scroll_area.setFixedHeight(96)
        self.uds_byte_grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.uds_byte_grid.customContextMenuRequested.connect(self._uds_selection_menu)
        self.uds_byte_grid.range_selected.connect(self._on_byte_grid_range_selected)
        uds_ext_vbox.addWidget(self.uds_byte_grid)

        # 2. 实时全息预览栏 (选区、Hex、大端整数、物理换算值)
        self.uds_preview_frame = QFrame()
        self.uds_preview_frame.setObjectName('udsSelectionPreview')
        self.uds_preview_frame.setStyleSheet("""
            QFrame#udsSelectionPreview {
                background-color: #090E17;
                border: 1px dashed #0284C7;
                border-radius: 6px;
            }
        """)
        prev_layout = QHBoxLayout(self.uds_preview_frame)
        prev_layout.setContentsMargins(8, 4, 8, 4)
        prev_layout.setSpacing(10)

        self.lbl_byte_range = ElidingLabel("【拖拽选区】 未选择")
        self.lbl_byte_range.setStyleSheet("font-size: 14px; color: #38BDF8; font-weight: bold; border: 0; background: transparent;")
        self.lbl_byte_hex = ElidingLabel("Hex: --")
        self.lbl_byte_hex.setStyleSheet("font-size: 14px; color: #A78BFA; font-family: Consolas, monospace; border: 0; background: transparent;")
        self.lbl_byte_int = ElidingLabel("大端整数: --")
        self.lbl_byte_int.setStyleSheet("font-size: 14px; color: #34D399; border: 0; background: transparent;")
        self.lbl_byte_phys = ElidingLabel("换算物理值: --")
        self.lbl_byte_phys.setStyleSheet("font-size: 14px; color: #fbbf24; border: 0; background: transparent;")

        prev_layout.addWidget(self.lbl_byte_range,1)
        prev_layout.addWidget(self.lbl_byte_hex,1)
        prev_layout.addWidget(self.lbl_byte_int,1)
        prev_layout.addWidget(self.lbl_byte_phys,1)
        uds_ext_vbox.addWidget(self.uds_preview_frame)

        # 4. 第一行: 提取模式、起始偏移、截取长度、写入变量名
        self.uds_parameter_widget = QWidget()
        parameter_layout = QVBoxLayout(self.uds_parameter_widget)
        parameter_layout.setContentsMargins(0, 0, 0, 0)
        parameter_layout.setSpacing(8)
        row1_ext = QGridLayout()
        row1_ext.setContentsMargins(0, 0, 0, 0)
        row1_ext.setHorizontalSpacing(8)
        row1_ext.setVerticalSpacing(6)
        self.uds_extract_mode = QComboBox()
        self.uds_extract_mode.addItems(["无数据提取", "ASCII 字符串解码 (如 VIN/版本号)", "数值计算 CALC (原始值 * 比例 + 偏移)", "自定义公式 FORMULA (如 (val*0.05)+12)"])
        self.uds_extract_mode.addItem('HEX 十六进制字节输出')
        self.uds_extract_mode.addItem('BCD 十进制数字串（保留前导零）')
        self.uds_extract_mode.setStyleSheet("background-color: #1e293b; color: #e6ebf2;")
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

        parameter_labels=[]
        for index,(title,field) in enumerate((
                ('当前段解析格式：',self.uds_extract_mode),
                ('起始字节偏移（从0开始）：',self.uds_extract_offset),
                ('提取长度（0表示到末尾）：',self.uds_extract_len),
                ('结果变量名（选填）：',self.uds_extract_var))):
            label=QLabel(title)
            parameter_labels.append(label)
            row,column=divmod(index,2)
            row1_ext.addWidget(label,row,column*2)
            row1_ext.addWidget(field,row,column*2+1)
        parameter_layout.addLayout(row1_ext)

        # 第二行: 数值计算专属控件 (端序、有符号、比例、偏移量、单位、自定义公式)
        self.uds_ext_detail_widget = QWidget()
        row2_ext = QGridLayout(self.uds_ext_detail_widget)
        row2_ext.setContentsMargins(0, 0, 0, 0)
        row2_ext.setHorizontalSpacing(8)
        row2_ext.setVerticalSpacing(6)

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

        self.uds_extract_formula = QLineEdit()
        self.uds_extract_formula.setPlaceholderText("公式，如 (val * 0.05) + 12")

        # 标签引用方便切换
        self.lbl_ext_endian = QLabel("端序：")
        self.lbl_ext_scale = QLabel("比例：")
        self.lbl_ext_offset = QLabel("偏移量：")
        self.lbl_ext_unit = QLabel("单位：")
        self.lbl_ext_formula = QLabel("公式：")

        endian_options = QHBoxLayout()
        endian_options.setContentsMargins(0,0,0,0)
        endian_options.setSpacing(8)
        endian_options.addWidget(self.lbl_ext_endian)
        endian_options.addWidget(self.uds_extract_signed,0,Qt.AlignVCenter)
        endian_options.addStretch()
        row2_ext.addLayout(endian_options,0,0)
        row2_ext.addWidget(self.uds_extract_endian,0,1)
        row2_ext.addWidget(self.lbl_ext_unit,0,2)
        row2_ext.addWidget(self.uds_extract_unit,0,3)
        row2_ext.addWidget(self.lbl_ext_scale,1,0)
        row2_ext.addWidget(self.uds_extract_scale,1,1)
        row2_ext.addWidget(self.lbl_ext_offset,1,2)
        row2_ext.addWidget(self.uds_extract_offset_val,1,3)
        row2_ext.addWidget(self.lbl_ext_formula,2,0)
        row2_ext.addWidget(self.uds_extract_formula,2,1,1,3)

        # 上下两块使用相同的四列尺寸，模式切换和窗口缩放时保持对齐。
        parameter_labels.extend((self.lbl_ext_endian, self.lbl_ext_scale,
                                 self.lbl_ext_offset, self.lbl_ext_unit, self.lbl_ext_formula))
        label_width=max(label.sizeHint().width() for label in parameter_labels)
        for label in parameter_labels:
            label.setFixedWidth(label_width)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_ext_endian.setFixedWidth(self.lbl_ext_endian.sizeHint().width())
        self.uds_extract_signed.setFixedHeight(28)
        for grid in (row1_ext,row2_ext):
            grid.setColumnMinimumWidth(0,label_width)
            grid.setColumnMinimumWidth(2,label_width)
            grid.setColumnMinimumWidth(1,120)
            grid.setColumnMinimumWidth(3,120)
            grid.setColumnStretch(1,1)
            grid.setColumnStretch(3,1)
        for field in (self.uds_extract_mode, self.uds_extract_offset, self.uds_extract_len,
                      self.uds_extract_var, self.uds_extract_endian, self.uds_extract_scale,
                      self.uds_extract_offset_val, self.uds_extract_unit, self.uds_extract_formula):
            control=type(field).__name__
            field.setStyleSheet(f'{control} {{ '+field.styleSheet()+' padding:2px 4px; min-height:22px; max-height:22px; }')
            field.setFixedHeight(28)
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
            if isinstance(field,QComboBox):
                field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)

        parameter_layout.addWidget(self.uds_ext_detail_widget)
        parameter_band=QHBoxLayout()
        parameter_band.addWidget(self.uds_parameter_widget,3)
        capture_actions=QHBoxLayout()
        capture_actions.addStretch()
        capture_actions.addWidget(self.btn_live_sniff)
        parameter_band.addLayout(capture_actions,2)
        uds_ext_vbox.addLayout(parameter_band)
        from ui.widgets.uds_segments_widget import UDSSegmentsWidget
        self.uds_segments = UDSSegmentsWidget(self)
        uds_ext_vbox.addWidget(self.uds_segments)
        self.uds_segments.changed.connect(self._refresh_byte_grid_preview)
        self.uds_segments.enabled.toggled.connect(self._on_uds_multi_toggled)
        self.uds_segments.selected_rule.connect(self._load_uds_segment_for_edit)
        self.uds_segments.append_requested.connect(self._add_uds_segment)
        self.uds_segments.replace_requested.connect(lambda: self._add_uds_segment(replace=True))
        for signal in (self.uds_extract_scale.valueChanged, self.uds_extract_offset_val.valueChanged,
                       self.uds_extract_formula.textChanged, self.uds_extract_endian.currentIndexChanged,
                       self.uds_extract_signed.toggled):
            signal.connect(self._update_uds_expected_result)
        self.uds_sync_expected.toggled.connect(self._update_uds_expected_result)
        uds_layout.addWidget(self.uds_extract_group)
        self._on_uds_extract_mode_changed(0)

        self.uds_table = QTableWidget(0, 7)
        self.uds_table.setHorizontalHeaderLabels(["名称", "请求PDU", "报文前缀（HEX）", "前缀校验", "数据提取/计算", "期望结果（解析后）", "最终输出"])
        self.uds_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.uds_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.uds_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.uds_table.customContextMenuRequested.connect(self._uds_context_menu)
        self.uds_delete_shortcut = QShortcut(QKeySequence('Delete'), self.uds_table)
        self.uds_delete_shortcut.setContext(Qt.WidgetShortcut)
        self.uds_delete_shortcut.activated.connect(self.delete_uds_row)
        self.uds_table.cellClicked.connect(self._on_uds_row_clicked)
        self.uds_table.itemChanged.connect(self._on_uds_table_item_changed)
        self.uds_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.uds_table.setToolTip("单击行进入参数编辑；Ctrl/Shift可多选，右键可编辑或删除。最终输出复选框只切换输出行。")
        self.uds_table.horizontalHeader().setStretchLastSection(True)
        self.uds_table.setColumnWidth(0, 100)
        self.uds_table.setColumnWidth(1, 130)
        self.uds_table.setColumnWidth(2, 120)
        self.uds_table.setColumnWidth(3, 60)
        self.uds_table.setColumnWidth(4, 200)
        self.uds_table.setColumnWidth(5, 140)
        self.uds_table.setColumnWidth(6, 80)
        uds_layout.addWidget(self.uds_table, 1)

        uds_control_row = QHBoxLayout()
        for text, handler in (("删除", self.delete_uds_row), ("上移", lambda: self.move_uds_row(-1)),
                              ("下移", lambda: self.move_uds_row(1)), ("清空", self.clear_uds_rows)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            if text == '删除':
                self.uds_delete_btn = button
                button.setText('删除选中行')
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
        uds_control_row.addWidget(self.uds_add_btn)
        uds_layout.insertLayout(uds_layout.indexOf(self.uds_table), uds_control_row)
        self._organize_uds_pages(uds_layout, uds_link_row, uds_edit_row, uds_rule_row, uds_control_row)
        from ui.widgets.uds_sequence_workbench import UDSSequenceWorkbench
        self.uds_workbench = UDSSequenceWorkbench(self)
        self.uds_workbench.install()
        self.param_stack.addWidget(self.page_uds) # 13

        # --- 页面 14: 空参数占位页面 (针对无需配置额外参数的动作) ---
        self.page_empty = QWidget()
        empty_layout = QVBoxLayout(self.page_empty)
        empty_layout.setContentsMargins(15, 25, 15, 25)
        self.lbl_empty_param_tip = QLabel("ℹ️ 当前动作无需配置额外参数")
        self.lbl_empty_param_tip.setAlignment(Qt.AlignCenter)
        self.lbl_empty_param_tip.setStyleSheet("color: #64748b; font-size: 14px; font-weight: bold; padding: 25px 0;")
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
        self.cb_e2_verify_diff.setStyleSheet("color: #e6ebf2; font-weight: bold; font-size: 13px;")

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
        from ui.widgets.simulator_read_form import SimulatorReadForm
        self.simulator_read_form = SimulatorReadForm(self.device_manager)
        self.param_stack.addWidget(self.simulator_read_form)
        self.legacy_simulator_notice = QLabel()
        self.legacy_simulator_notice.setWordWrap(True)
        self.param_stack.addWidget(self.legacy_simulator_notice)
        from ui.widgets.dbc_forms import DBCStepForm
        dbc_provider=getattr(self.parent(),'recipe_dbc_asset',None)
        self.dbc_form=DBCStepForm(dbc_provider() if callable(dbc_provider) else None,test_item_name=test_item_name)
        self.param_stack.addWidget(self.dbc_form)


        # 3. 策略与判定设置
        policy_frame = QFrame()
        policy_layout = QFormLayout(policy_frame)
        self.fail_strategy_combo = QComboBox()
        self.fail_strategy_combo.addItems(["失败停止", "忽略继续", "重试3次"])
        policy_layout.addRow("指令执行失败策略:", self.fail_strategy_combo)
        
        self.cb_judgment = QCheckBox("结果输出并参与最终判定")
        self.cb_judgment.setStyleSheet("color: #00E5FF; font-weight: bold;")
        policy_layout.addRow("", self.cb_judgment)

        from ui.widgets.read_until_form import ReadUntilForm
        self.read_until = ReadUntilForm()
        self.scroll_layout.addWidget(self.read_until)
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
        from ui.widgets.uds_field_write_form import UDSFieldWriteForm
        self.uds_field_write_form=UDSFieldWriteForm(self)
        self.param_stack.addWidget(self.uds_field_write_form)
        from ui.widgets.can_periodic_form import CANPeriodicForm
        self.can_periodic_form=CANPeriodicForm(self)
        self.param_stack.addWidget(self.can_periodic_form)
        from ui.widgets.variable_step_form import VariableStepForm
        self.variable_step_form=VariableStepForm(self)
        self.param_stack.addWidget(self.variable_step_form)
        self.category_combo.currentIndexChanged.connect(self.on_category_changed)
        self.sub_category_combo.currentIndexChanged.connect(self.on_sub_category_changed)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        self.action_combo.currentIndexChanged.connect(self.on_action_changed)
        # Activating the already-selected action explicitly replaces a legacy step.
        self.action_combo.activated.connect(lambda *_: self.on_action_changed() if self._legacy_simulator_data else None)
        self.eol_op.currentTextChanged.connect(self.on_eol_op_changed)
        self.var_name.currentTextChanged.connect(self.on_var_changed)
        
        # 初始状态：如果有传入数据则执行加载，否则执行默认初始化
        if step_data:
            self._load_data(step_data)
        else:
            self.on_category_changed(0)
            self.on_eol_op_changed()
        from ui.widgets.compact_step_layout import compact_combos
        compact_combos(self)
        self.scroll_layout.setAlignment(Qt.AlignTop)

    def accept(self):
        from core.can_step_channels import is_can_step
        if is_can_step(dict(device=self.device_combo.currentText())):
            try:self._selected_physical_channel(self._is_plain_can_send())
            except ValueError as exc:
                QMessageBox.warning(self, '通道参数错误', str(exc));return
        if self.param_stack.currentWidget() is self.variable_step_form:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'变量参数错误',str(exc));return
        if self.param_stack.currentWidget() is self.can_periodic_form:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'周期报文参数错误',str(exc));return
        if self.uds_field_write_form.task.isRunning():return
        if self.param_stack.currentWidget() is self.uds_field_write_form:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'三字段写入参数错误',str(exc));return
        if self.param_stack.currentWidget() is self.dbc_form:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'DBC工步参数错误',str(exc));return
            if (self.recipe_can_binding or {}).get('enabled') is False:
                QMessageBox.warning(self,'未绑定CAN卡','DBC读取需要先选择配方CAN卡。');return
        if self.uds_workbench.task.isRunning():
            return
        if self.param_stack.currentIndex() == 13:
            if self._uds_edit_item is not None and not self.save_uds_edited_row(show_list=False):
                return
            try:
                self.get_data()
                self.uds_security.config()
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, '安全访问配置错误', str(exc))
                return
        if self.param_stack.currentIndex() == 12 and '查找' not in self.action_combo.currentText():
            try:
                self._app_flash_monitor_ids()
            except ValueError as exc:
                QMessageBox.warning(self, '刷写监听参数错误', str(exc))
                return
        try:
            self.read_until.config()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, '范围等待参数错误', str(exc))
            return
        if self.param_stack.currentIndex() in (1, 7, 13) and (self.recipe_can_binding or {}).get('enabled') is False:
            QMessageBox.warning(self, '未绑定 CAN 卡', '当前配方选择了“不使用 CAN”，请先在配方中选择 CAN 卡。')
            return
        if self.param_stack.currentIndex() == 1 and self.can_parser.isVisible() and self.can_parser.enabled.isChecked():
            try:
                self.can_parser.config()
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self, '解析参数错误', str(exc))
                return
        key=device_key(self.device_combo.currentText())
        spec=action_spec(key,self.action_combo.currentText())
        if spec:
            try:self.get_data()
            except (ValueError,TypeError) as exc:
                QMessageBox.warning(self,'参数错误',str(exc));return
        super().accept()

    def reject(self):
        if hasattr(self, 'uds_workbench') and self.uds_workbench.task.isRunning():
            self.uds_workbench.cancel.set()
            return
        if hasattr(self, 'uds_workbench'):
            self.uds_workbench.stop()
        super().reject()

    def closeEvent(self, event):
        if hasattr(self, 'uds_workbench') and self.uds_workbench.task.isRunning():
            self.uds_workbench.cancel.set()
            event.ignore()
            return
        if hasattr(self, 'uds_workbench'):
            self.uds_workbench.stop()
        super().closeEvent(event)

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
            self.eol_args.setPlaceholderText("可选参数，格式：参数名:值")
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
        from core.can_step_channels import explicit_step
        data = explicit_step(data, self._legacy_can_channel)
        self._legacy_simulator_data = None
        self.is_loading = True
        try:
            device = data.get('device', '')
            action = data.get('action', '')
            params_str = str(data.get('params', ''))
            step_type = data.get('type', '')
            device_step=decode_device_step(params_str)
            if ((device_step and device_step[0] == 'simulator' and device_step[1].key != 'current_batch')
                    or (not device_step and device_key(device) == 'simulator'
                        and (step_type == '读取仪表' or action == '回读数据'))):
                self._load_legacy_simulator(data, device_step)
                return
            if device_step:
                key,spec,values=device_step
                group,label=STEP_DEVICES[key]
                self.category_combo.setCurrentText('设备操作');self.on_category_changed()
                self.sub_category_combo.setCurrentText(group);self.on_sub_category_changed()
                self.device_combo.setCurrentText(label);self.on_device_changed()
                self.action_combo.setCurrentText(spec.label);self.on_action_changed()
                if key == 'simulator' and spec.key == 'current_batch':
                    self.simulator_read_form.load(values)
                else:
                    self.device_step_form.configure(key,spec,values)
                from core.read_until import settings
                self.read_until.load(settings(params_str))
                self.cb_judgment.setChecked(data.get('is_judgment',False) if spec.key != 'current_batch' else False)
                self.fail_strategy_combo.setCurrentText(data.get('fail_strategy','失败停止'))
                return
            kv = self._split_params(params_str)
            if 'VARSTEP64' in kv:
                from core.variable_steps import decode
                c=decode(params_str)
                self.category_combo.setCurrentText('通用交互');self.on_category_changed()
                self.device_combo.setCurrentText('写入变量' if c['operation']=='write' else '读取变量');self.on_device_changed();self.on_action_changed()
                self.variable_step_form.load(c)
                self.cb_judgment.setChecked(data.get('is_judgment',False))
                self.fail_strategy_combo.setCurrentText(data.get('fail_strategy','失败停止'))
                return
            if 'CANPER64' in kv:
                from core.can_periodic import decode
                c=decode(params_str)
                self.category_combo.setCurrentText('报文交互');self.on_category_changed()
                self.device_combo.setCurrentText('CAN 交互');self.on_device_changed()
                self.action_combo.setCurrentText('启动周期报文列表' if c['operation']=='start' else '停止周期报文列表')
                self.on_action_changed();self.can_periodic_form.load(c)
                if 'PCH' in kv:self._load_physical_selection(kv['PCH'])
                self.cb_judgment.setChecked(data.get('is_judgment',False))
                self.fail_strategy_combo.setCurrentText(data.get('fail_strategy','失败停止'))
                return
            if 'WRITE64' in kv:
                from core.uds_field_write import DEVICE,decode
                self.category_combo.setCurrentText('报文交互');self.on_category_changed()
                self.device_combo.setCurrentText(DEVICE);self.on_device_changed();self.on_action_changed()
                self.uds_field_write_form.load(decode(kv['WRITE64']))
                if 'PCH' in kv:self._load_physical_selection(kv['PCH'])
                self.cb_judgment.setChecked(data.get('is_judgment',False))
                self.fail_strategy_combo.setCurrentText(data.get('fail_strategy','失败停止'))
                return
            if 'SEC64' in kv:
                from core.seedkey import decode
                self.uds_security.load(decode(kv['SEC64']))
            if 'PCH' in kv:
                from core.can_step_channels import is_plain_send
                self._load_physical_selection(kv['PCH'], is_plain_send(data))
            
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
                elif "继电器" in device or "Easy320" in device: sub_cat = "继电器"
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
            if device_key(device) == 'simulator' and action == '快捷批量配置':
                idx = self.action_combo.findText('快捷配置')
            if idx >= 0: self.action_combo.setCurrentIndex(idx)
            self.on_action_changed()
            if self.param_stack.currentWidget() is self.page_sim_batch:
                range_index = self.sim_batch_range.findData(kv.get('RANGE', 'HIGH').upper())
                self.sim_batch_range.setCurrentIndex(max(0, range_index))
                self._update_sim_batch_current_limit()
                self.sim_batch_channels.setText(kv.get('CH', 'ALL'))
            if self.param_stack.currentWidget() is self.dbc_form:
                self.dbc_form.load(params_str)
                from core.read_until import settings
                self.read_until.load(settings(params_str))
            
            # 回读页面必须恢复读取量，否则默认电压会在再次保存时覆盖电流。
            if self.param_stack.currentIndex() == 3:
                from core.read_until import settings
                self.read_until.load(settings(params_str))
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
                self.c_rx_format.setCurrentIndex(('auto','standard','extended').index(kv.get('RX_FORMAT','auto')))
                self.c_rx_prefix.setText(kv.get('RX_PREFIX',''))
                if "TIMEOUT" in kv: self.c_timeout.setValue(int(float(kv["TIMEOUT"])))
                if "TYPE" in kv: self.c_type.setCurrentIndex(int(kv["TYPE"]))
                if "DLC" in kv: self.c_dlc.setValue(int(kv["DLC"]))
                if "CH" in kv: self.c_channel.setValue(int(kv["CH"]))
                if "PARSE" in kv: self.can_parser.load(kv["PARSE"])
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
                    self._uds_load_error = ''
                    self.uds_tx_id.setText(kv.get("TX_ID", "0x7E0"))
                    self.uds_rx_id.setText(kv.get("RX_ID", "0x7E8"))
                    self.uds_can_type.setCurrentIndex(int(kv.get("TYPE", "0")))
                    self.uds_timeout.setValue(int(float(kv.get("TIMEOUT", "1000"))))
                    self.uds_cycles.setValue(int(kv.get("CYCLES", "1")))
                    self.uds_interval.setValue(int(kv.get("INTERVAL", "1")))
                    encoded = kv.get("LIST64", "")
                    self.uds_table.setRowCount(0)
                    from core.uds_sequence import settings_from
                    self.uds_workbench.load_config(settings_from(kv.get('UDSCFG64', '')))
                    if encoded:
                        script = base64.urlsafe_b64decode(encoded).decode("utf-8")
                        for line in script.splitlines():
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            line_main, pipe_sep, rule_str = line.partition("|")
                            request, separator, expected = line_main.partition("=>")
                            self._append_uds_table_row("自定义", request.strip(), expected.strip() if separator else "", rule_str.strip())
                        from core.uds_result_config import decode
                        requests = [line.strip() for line in script.splitlines() if line.strip() and not line.strip().startswith('#')]
                        metadata = decode(kv.get('UDSMETA64', ''), requests)
                        for row, config in enumerate(metadata):
                            self.uds_table.item(row, 5).setText(config.get('expected', ''))
                            self.uds_table.item(row, 6).setCheckState(Qt.Checked if config.get('output') else Qt.Unchecked)
                            self._set_uds_row_options(row, config)
                            self.uds_table.item(row,0).setCheckState(Qt.Checked if config.get('enabled',True) else Qt.Unchecked)
                            if config.get('name'):self.uds_table.item(row,0).setText(config['name'])
            except Exception as exc:
                self._uds_load_error = str(exc)

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
                    self.app_flash_tx_id.setText(kv.get('FLASH_TX_ID', '0x7E4'))
                    self.app_flash_rx_id.setText(kv.get('FLASH_RX_ID', '0x7EC'))
            except (TypeError, ValueError):
                pass

            # --- 延时参数 (Page 2) ---
            try:
                ms_match = re.search(r'(\d+)ms', params_str)
                if ms_match: self.w_time.setValue(int(ms_match.group(1)))
            except: pass

            # --- 继电器控制 (Page 4/8) ---
            try:
                if "继电器" in step_type or "Easy320" in device:
                    from core.relay_combination import decode as decode_relays
                    combination = decode_relays(params_str)
                    if combination is not None:
                        self.action_combo.setCurrentText('组合设置ON/OFF')
                        self.relay_combination_form.load(combination)
                        channels = []
                    else:
                        channels = params_str.split(",")
                    for c in channels:
                        try:
                            clean_c = re.sub(r'[^\d]', '', c) # 只保留数字
                            if clean_c:
                                c_idx = int(clean_c) - 1
                                if "Easy320" in device:
                                    match = next((cb for cb in self.easy320_checks if cb.property('coil_index') == c_idx), None)
                                    if match is not None: match.setChecked(True)
                                    else:
                                        self.easy320_missing_channels.add(c_idx)
                                        self.easy320_channel_tip.setText('原工步包含当前导入表中不存在的DO通道：' + ', '.join(f'DO{i:o}' for i in sorted(self.easy320_missing_channels)) + '。请重新选择通道并核对工步。')
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

    def _update_sim_batch_current_limit(self, *_):
        low = self.sim_batch_range.currentData() == 'LOW'
        self.sim_batch_curr.setRange(1 if low else 0, 1 if low else 1000)
        self.sim_batch_curr.setEnabled(not low)
        self.sim_batch_curr.setToolTip('0–1 mA 量程的输出限流固定为 1 mA' if low else '输出限流范围：0–1000 mA')

    def _load_legacy_simulator(self, data, decoded):
        """Keep historical single-channel operations intact until explicitly replaced."""
        self.category_combo.setCurrentText('设备操作'); self.on_category_changed()
        self.sub_category_combo.setCurrentText('模拟电池'); self.on_sub_category_changed()
        self.device_combo.setCurrentText(STEP_DEVICES['simulator'][1]); self.on_device_changed()
        self.action_combo.setCurrentText('回读数据' if decoded is None or decoded[1].read else '快捷配置')
        self.on_action_changed()
        notice = '历史工步按原参数保留；重新选择“功能动作”可改用新格式。'
        if decoded:
            _, spec, values = decoded
            self.device_step_form.configure('simulator', spec, values)
            self.device_step_form.help_label.setText(f'历史工步：{spec.label}。{notice}')
            self.param_stack.setCurrentWidget(self.device_step_form)
        else:
            kind = '电流' if '电流' in str(data.get('params', '')) else '电压'
            self.legacy_simulator_notice.setText(f'历史单通道读取{kind}。{notice}')
            self.param_stack.setCurrentWidget(self.legacy_simulator_notice)
        self._legacy_simulator_data = dict(data)
        self.read_until.hide()
        self.cb_judgment.setEnabled(True)
        self.cb_judgment.setChecked(data.get('is_judgment', False))
        self.fail_strategy_combo.setCurrentText(data.get('fail_strategy', '失败停止'))

    def on_category_changed(self, index=0):
        category = self.category_combo.currentText()
        self.device_selection_form.setRowVisible(self.sub_category_combo, category == "设备操作")
        
        self.sub_category_combo.clear()
        self.device_combo.clear()
        
        if category == "设备操作":
            self.sub_category_combo.addItems(["直流源", "高压源", "模拟电池", "继电器", "校准源", "下载器", "测量仪器", "信号发生器", "示波器", "板卡"])
        elif category == "报文交互":
            self.device_combo.addItems(["CAN 交互", "UDS 交互列表", "CANDBC 交互", "UDS 2E 三字段写入"])
        elif category == "三方协议":
            self.device_combo.addItems(["3.5HEOL协议", "MES过站校验"])
        elif category == "通用交互":
            self.device_combo.addItems(["等待 (Wait)", "写入变量", "读取变量", "变量操作 (Variable)", "公式计算 (Calculate)"])
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

    def _is_plain_can_send(self):
        return ('CAN' in self.device_combo.currentText().upper()
                and self.action_combo.currentText() == '发送指令')

    def _update_send_channel_checks(self):
        for physical,check in self.can_send_checks.items():
            check.setEnabled((physical in self._can_enabled or check.isChecked()) and not self.can_send_all.isChecked())

    def _toggle_all_can_channels(self, checked):
        if checked:
            self._can_custom_selection = {ch for ch,check in self.can_send_checks.items() if check.isChecked()}
        selected = self._can_enabled if checked else self._can_custom_selection
        for physical,check in self.can_send_checks.items():
            check.blockSignals(True);check.setChecked(physical in selected);check.blockSignals(False)
        self._update_send_channel_checks()

    def _update_can_channel_controls(self):
        can_related = any(word in self.device_combo.currentText().upper() for word in ('CAN','UDS','EOL'))
        multiple = self._is_plain_can_send()
        self.device_selection_form.setRowVisible(self.can_binding_label,can_related)
        self.device_selection_form.setRowVisible(self.can_physical_channel,can_related and not multiple)
        self.device_selection_form.setRowVisible(self.can_send_row,can_related and multiple)
        for control in (self.can_send_title,self.can_send_channels):control.setVisible(can_related and multiple)

    def _selected_physical_channel(self, multiple=False):
        from core.can_step_channels import resolve_channels
        if (self.recipe_can_binding or {}).get('enabled') is False:raise ValueError('请先在配方中选择CAN卡')
        if multiple:
            token = 'ALL' if self.can_send_all.isChecked() else ','.join(str(ch) for ch,check in self.can_send_checks.items() if check.isChecked())
        else:token = str(self.can_physical_channel.currentData() or '')
        if not token:raise ValueError('请至少选择一个已启用的物理通道')
        resolve_channels('PCH:'+token, maximum=self._can_maximum, enabled=self._can_enabled, multiple=multiple)
        return token

    def _load_physical_selection(self, token, multiple=False):
        from core.can_step_channels import resolve_channels
        if multiple and str(token).upper() == 'ALL':
            self.can_send_all.setChecked(True);return
        # Keep a disabled/out-of-card saved selection visible; saving requires the user to correct it.
        channels = resolve_channels('PCH:'+str(token), maximum=max(4,self._can_maximum), multiple=multiple)
        if multiple:
            self.can_send_all.setChecked(False)
            for physical in channels:
                if physical not in self.can_send_checks:
                    check = QCheckBox(f'CH{physical}（不属于当前CAN卡，请重选）')
                    check.setEnabled(False);check.toggled.connect(self._update_send_channel_checks)
                    self.can_send_checks[physical] = check
                    self.can_send_channels.layout().insertWidget(self.can_send_channels.layout().count()-1,check)
            for physical,check in self.can_send_checks.items():check.setChecked(physical in channels)
            self._update_send_channel_checks()
        if len(channels) == 1:
            physical = channels[0];index = self.can_physical_channel.findData(physical)
            if index < 0:
                self.can_physical_channel.addItem(f'CH{physical}（不属于当前配方CAN卡，请重选）',physical)
                index = self.can_physical_channel.count()-1
            self.can_physical_channel.setCurrentIndex(index)

    def on_device_changed(self, index=0):
        if not self.is_loading:self._legacy_simulator_data = None
        text = self.device_combo.currentText()
        self._update_can_channel_controls()
        self.action_combo.clear()
        device_text = self.device_combo.currentText()
        if not device_text: return
        if device_text in ('写入变量','读取变量'):
            self.action_combo.addItem(device_text)
            if not self.is_loading:self.on_action_changed()
            return
        if device_text == 'UDS 2E 三字段写入':
            self.action_combo.addItem('写入三字段 ASCII 并校验响应')
            if not self.is_loading:self.on_action_changed()
            return
        key=device_key(device_text)
        if key == 'simulator':
            self.action_combo.addItems(['快捷配置', '回读数据'])
            if not self.is_loading:self.on_action_changed()
            return
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
        elif "CANDBC" in device_text:
            self.action_combo.addItems(['读取DBC信号'])
        elif "UDS" in device_text:
            self.action_combo.addItems(["顺序发送并校验"])
        elif "MES" in device_text:
            self.action_combo.addItems(["moveInCheck过站校验"])
        elif "CAN" in device_text:
            self.action_combo.addItems(["发送指令", "交互/问答", "读取数据", "接收指定帧ID"])
            self.action_combo.addItems(['启动周期报文列表','停止周期报文列表'])
            self.action_combo.setItemData(0,'仅发送，不等待或解析回复',Qt.ToolTipRole)
            self.action_combo.setItemData(1,'发送请求，等待指定ID的回复并解析',Qt.ToolTipRole)
        elif "等待" in device_text:
            self.action_combo.addItems(["固定延时"])
        elif "变量操作" in device_text:
            self.action_combo.addItems(["读取变量"])
        elif "公式计算" in device_text:
            self.action_combo.addItems(["公式计算"])
        elif "Easy320" in device_text:
            self.action_combo.addItems(["组合设置ON/OFF", "闭合勾选通道", "断开勾选通道", "全部断开"])
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
        self._update_can_channel_controls()
        if not self.is_loading:self._legacy_simulator_data = None
        if hasattr(self, 'cb_judgment'):self.cb_judgment.setEnabled(True)
        if hasattr(self, 'read_until'):
            key = device_key(self.device_combo.currentText())
            spec = action_spec(key, self.action_combo.currentText())
            action = self.action_combo.currentText()
            self.read_until.setVisible(bool(spec.read and spec.key not in ('state', 'waveform')) if spec else (any(word in action for word in ('读取', '回读')) and any(x in self.device_combo.currentText().lower() for x in ('simulator', '模拟器', 'it6722', 'dut', 'ngi', '高压源', 'ca550'))))
        device = self.device_combo.currentText()
        action = self.action_combo.currentText()
        if device in ('写入变量','读取变量'):
            self.variable_step_form.set_operation('write' if device=='写入变量' else 'read')
            self.param_stack.setCurrentWidget(self.variable_step_form)
            return
        if action in ('启动周期报文列表','停止周期报文列表'):
            self.can_periodic_form.set_operation('start' if action=='启动周期报文列表' else 'stop')
            self.param_stack.setCurrentWidget(self.can_periodic_form)
            return
        if device == 'UDS 2E 三字段写入':
            self.param_stack.setCurrentWidget(self.uds_field_write_form)
            return
        key=device_key(device);spec=action_spec(key,action)
        if key == 'simulator':
            self.read_until.hide()
            if action == '回读数据':
                self.param_stack.setCurrentWidget(self.simulator_read_form)
                self.simulator_read_form.update_preview()
                self.cb_judgment.setChecked(False)
                self.cb_judgment.setEnabled(False)
            else:
                self.param_stack.setCurrentIndex(6)
            return
        if spec:
            self.device_step_form.configure(key,spec)
            self.param_stack.setCurrentWidget(self.device_step_form)
            return
        
        if 'CANDBC' in device:
            self.param_stack.setCurrentWidget(self.dbc_form)
            self.dbc_form.ensure_loaded()
            self.read_until.setVisible(True)
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
            self.c_action_help.setText('仅发送：不等待回复，也不解析返回数据。' if '发送' in action else
                ('仅接收：等待指定ID的报文并解析，不发送请求。' if action=='接收指定帧ID' else
                 '请求应答：发送请求 → 等待指定响应ID → 匹配回复 → 输出解析结果。ID统一按十六进制填写。'))
            self.can_parser.setVisible("发送" not in action)
            for field in (self.c_rx_format,self.c_rx_prefix):
                field.setVisible('发送' not in action)
                self.page_can.layout().labelForField(field).setVisible('发送' not in action)
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
        elif "继电器" in device or "Easy320" in device:
            self.relay_combination_form.set_legacy(None if action == '组合设置ON/OFF' else '闭合' in action)
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
                           self.app_flash_record_help, self.app_flash_tx_label, self.app_flash_tx_id,
                           self.app_flash_rx_label, self.app_flash_rx_id, self.app_flash_monitor_help):
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

    def _organize_uds_pages(self, root, link, editor, response, controls):
        """Separate the workflow without changing the persisted UDS fields."""
        for layout in (link, editor, response, controls):
            root.removeItem(layout)
        for widget in (self.uds_security, self.uds_unlock_add, self.uds_extract_group, self.uds_table):
            root.removeWidget(widget)
        from ui.widgets.compact_step_layout import CurrentPageTabs
        self.uds_tabs = CurrentPageTabs()
        self.uds_tabs.setMinimumHeight(440)
        root.addWidget(self.uds_tabs, 1)
        pages = []
        descriptions = [
            ('1 连接参数', '先确认上方配方 CAN 卡和物理通道，再填写请求 ID、响应 ID、帧类型与超时。此处设置供整个交互列表共用。'),
            ('2 安全解锁（可选）', '不需要解锁可直接下一步。需要时勾选安全访问，选择 DLL 并配置参数，再添加解锁行；在执行列表中将其放在会话切换之后、受保护读写之前。'),
            ('3 请求与解析', '选择服务并填写请求 PDU。只校验响应可不提取；读取 VIN 等内容请选择 ASCII。可手输偏移、长度，也可在线抓取后选择字节。完成后到下一页添加。'),
            ('4 执行列表', '点击右上方“添加到列表”加入请求；单击已有行返回第3页修改并保存回原行。Ctrl/Shift可多选，右键可编辑或删除。勾选某一行“最终输出”时只显示该行最后一轮结果，其他行仍执行并校验。期望结果留空不额外比较；不勾输出则兼容原有汇总方式。最后点击“确认指令”保存。'),
        ]
        for title, description in descriptions:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setSpacing(12)
            tip = QLabel(description)
            tip.setWordWrap(True)
            tip.setStyleSheet('color: #e2e8f0; padding: 8px; background: #16213E; border-radius: 4px;')
            layout.addWidget(tip)
            scroll.setWidget(content)
            self.uds_tabs.addTab(scroll, title)
            pages.append(layout)
        pages[0].addLayout(link)
        pages[0].addStretch()
        pages[1].addWidget(self.uds_security)
        pages[1].addWidget(self.uds_unlock_add)
        pages[1].addStretch()
        pages[2].addLayout(editor)
        pages[2].addLayout(response)
        self.uds_edit_actions = QWidget()
        edit_actions = QHBoxLayout(self.uds_edit_actions)
        edit_actions.setContentsMargins(0, 0, 0, 0)
        self.uds_save_edit = QPushButton('保存修改并返回列表')
        self.uds_save_edit.setAutoDefault(False)
        self.uds_save_edit.clicked.connect(lambda: self.save_uds_edited_row())
        self.uds_cancel_edit = QPushButton('取消修改／新增请求')
        self.uds_cancel_edit.setAutoDefault(False)
        self.uds_cancel_edit.clicked.connect(self.cancel_uds_edit)
        edit_actions.addStretch()
        edit_actions.addWidget(self.uds_save_edit)
        edit_actions.addWidget(self.uds_cancel_edit)
        self.uds_edit_actions.hide()
        pages[2].addWidget(self.uds_edit_actions)
        pages[2].addWidget(self.uds_extract_group)
        pages[2].addStretch()
        header = QHBoxLayout()
        header.addWidget(QLabel('按执行顺序排列的 UDS 请求'))
        header.addStretch()
        controls.removeWidget(self.uds_add_btn)
        header.addWidget(self.uds_add_btn)
        pages[3].addLayout(header)
        pages[3].addLayout(controls)
        self.uds_table.setMinimumHeight(220)
        pages[3].addWidget(self.uds_table, 1)
        navigation = QHBoxLayout()
        self.uds_previous = QPushButton('上一步')
        self.uds_next = QPushButton('下一步')
        for button in (self.uds_previous, self.uds_next):
            button.setAutoDefault(False)
            button.setStyleSheet('QPushButton { color: white; background: #16213E; border: 1px solid #64748b; border-radius: 4px; padding: 7px 16px; } QPushButton:disabled { color: #94a3b8; border-color: #334155; }')
        self.uds_previous.clicked.connect(lambda: self.uds_tabs.setCurrentIndex(self.uds_tabs.currentIndex()-1))
        self.uds_next.clicked.connect(lambda: self.uds_tabs.setCurrentIndex(self.uds_tabs.currentIndex()+1))
        navigation.addWidget(self.uds_previous)
        navigation.addStretch()
        navigation.addWidget(self.uds_next)
        root.addLayout(navigation)
        self.uds_tabs.currentChanged.connect(self._update_uds_navigation)
        self.uds_request.textChanged.connect(self._update_uds_navigation)
        self._update_uds_navigation()

    def _update_uds_navigation(self, *_):
        page = self.uds_tabs.currentIndex()
        self.uds_previous.setEnabled(page > 0)
        self.uds_next.setEnabled(page < 3)
        self.uds_next.setText('下一步：数据解析' if page == 2 else '下一步')
        editing = self._uds_edit_row()
        if editing >= 0:
            self.uds_add_btn.setText('保存本行修改')
        else:
            self.uds_add_btn.setText('添加到列表')

    def _capture_signature(self,uds=False):
        driver=getattr(self.device_manager,'pcan',None)
        common=(id(driver),id(getattr(driver,'bus',None)),getattr(driver,'channel',None),
            getattr(driver,'nominal_bitrate',None),getattr(driver,'data_bitrate',None),getattr(driver,'fd_enabled',None),
            json.dumps(self.recipe_can_binding or {},sort_keys=True),self.can_physical_channel.currentData(),
            self.category_combo.currentText(),self.device_combo.currentText(),self.action_combo.currentText())
        if uds:
            return common+(self.uds_request.text(),self.uds_tx_id.text(),self.uds_rx_id.text(),
                self.uds_can_type.currentIndex(),self.uds_timeout.value(),id(self._uds_edit_item))
        return common+(self.c_id.text(),self.c_wait_id.text(),self.c_data.text(),self.c_type.currentIndex(),
            self.c_dlc.value(),self.c_timeout.value(),self.c_rx_format.currentIndex(),self.c_rx_prefix.text())

    def _capture_can_response(self):
        try:
            binding=self.recipe_can_binding or {}
            if binding.get('enabled') is False:raise ValueError('请先在配方中绑定CAN设备')
            driver=getattr(self.device_manager,'pcan',None)
            if driver is None or not driver.is_connected:raise ValueError('请先连接配方指定的CAN设备')
            expected=binding.get('channel','')
            if expected and expected.startswith('ZLG_CAN')!=str(driver.channel).startswith('ZLG_CAN'):
                raise ValueError('当前连接设备与配方不一致，请先应用配方配置')
            physical=int(self._selected_physical_channel())
            if physical != 1 or str(driver.channel).startswith('ZLG_CAN'):
                from devices.can_channel_view import CANChannelView
                driver=CANChannelView(driver,physical)
            receive_only=self.action_combo.currentText()=='接收指定帧ID'
            from core.can_frame_config import can_id,response_matcher
            tx=can_id(self.c_id.text());rx=tx if receive_only else can_id(self.c_wait_id.text())
            matcher=response_matcher(rx,('auto','standard','extended')[self.c_rx_format.currentIndex()],self.c_rx_prefix.text().strip())
            if not all(0<=i<=0x1FFFFFFF for i in (tx,rx)):raise ValueError('CAN ID超出有效范围')
            data=bytes.fromhex(self.c_data.text())
            channel=self.c_channel.value();kind=self.c_type.currentIndex();dlc=self.c_dlc.value();timeout=self.c_timeout.value()/1000
            signature=self._capture_signature()
            from ui.device_task import DeviceTask
            if not hasattr(self,'_can_capture_task'):self._can_capture_task=DeviceTask(self)
            if self._can_capture_task.isRunning():return
            def capture():
                if receive_only:
                    import time
                    return driver.wait_for_message(rx,predicate=matcher,timeout=timeout,since_time=time.time())
                return driver.send_and_wait_response(channel,tx,kind,dlc,data,rx,timeout,matcher=matcher)
            def complete(ok,result):
                self.can_parser.capture.setEnabled(True)
                if signature!=self._capture_signature():
                    QMessageBox.information(self,'抓取结束','设备或请求参数已修改，本次响应未覆盖当前样例，请重新抓取。');return
                if not ok or not result:
                    QMessageBox.warning(self,'抓取失败',str(result) if not ok else '等待响应超时');return
                self.can_parser.enabled.setChecked(True)
                self.can_parser.sample.setText(bytes(result['data']).hex(' ').upper())
            self.can_parser.capture.setEnabled(False)
            if not self._can_capture_task.run(capture,complete):self.can_parser.capture.setEnabled(True)
        except Exception as exc:
            QMessageBox.warning(self,'无法抓取',str(exc))

    def _on_live_sniff_clicked(self):
        if (self.recipe_can_binding or {}).get('enabled') is False:
            QMessageBox.warning(self, '未绑定 CAN 卡', '当前配方不使用 CAN，不能发送在线请求。请先在配方中选择 CAN 卡。')
            return
        """在线抓取 (Sniff)：向真实硬件发送请求并抓取真实回包，瞬间生成真实方块"""
        req_str = self.uds_request.text().strip().upper()
        if req_str == '@SECURITY':
            QMessageBox.information(self, 'DLL安全解锁', '此行为自动Seed/Key解锁，不能用普通PDU嗅探。请在CAN调试的“安全访问 DLL”分页执行，或运行当前UDS工步。')
            return
        if not req_str:
            QMessageBox.warning(self, "提示", "请先输入请求 PDU (例如 22 01 01)")
            return
            
        can_tool = None
        if self.device_manager:
            can_tool = getattr(self.device_manager, "pcan", None) or getattr(self.device_manager, "can", None)
        resp_hex = None
        if can_tool and getattr(can_tool, "is_connected", False):
            try:
                binding = self.recipe_can_binding or {}
                expected = binding.get('channel', '')
                if expected and (str(can_tool.channel).startswith('ZLG_CAN') != expected.startswith('ZLG_CAN')):
                    raise ValueError('当前已连接CAN卡与配方不一致，请先应用配方CAN配置')
                physical = int(self._selected_physical_channel())
                if physical != 1 or str(can_tool.channel).startswith('ZLG_CAN'):
                    from devices.can_channel_view import CANChannelView
                    can_tool = CANChannelView(can_tool, physical)
                req_bytes = bytes.fromhex(req_str)
                client = can_tool.create_uds_client(
                    tx_id=int(self.uds_tx_id.text().strip(), 0),
                    rx_id=int(self.uds_rx_id.text().strip(), 0),
                    can_fd=self.uds_can_type.currentIndex() > 0,
                    brs=self.uds_can_type.currentIndex() == 2,
                    timeout=max(0.001, self.uds_timeout.value() / 1000.0),
                )
                from ui.device_task import DeviceTask
                signature=self._capture_signature(uds=True)
                if not hasattr(self,'_uds_capture_task'):self._uds_capture_task=DeviceTask(self)
                if self._uds_capture_task.isRunning():return
                def capture():
                    response=client.request(req_bytes[0],req_bytes[1:])
                    if not response.positive:raise RuntimeError(response.error or response.nrc_name or 'ECU返回否定响应')
                    if not response.raw:raise RuntimeError('未收到有效响应数据')
                    return response.raw.hex(' ').upper()
                def complete(ok,value):
                    self.btn_live_sniff.setEnabled(True);self.btn_live_sniff.setText('📡 在线抓取 (Sniff)')
                    if not ok:
                        QMessageBox.warning(self,'抓取失败',str(value));return
                    if signature!=self._capture_signature(uds=True):
                        QMessageBox.information(self,'抓取结束','设备、通道或请求参数已修改，本次响应未覆盖当前解析样例，请重新抓取。');return
                    self._uds_response_preview=value
                    self._sync_byte_grid_from_pdu()
                self.btn_live_sniff.setEnabled(False);self.btn_live_sniff.setText('正在抓取…')
                if not self._uds_capture_task.run(capture,complete):
                    self.btn_live_sniff.setEnabled(True);self.btn_live_sniff.setText('📡 在线抓取 (Sniff)')
                return
            except Exception as e:
                QMessageBox.warning(self, "嗅探失败", f"实机通信发生异常: {e}")
                return
                
        if not resp_hex:
            QMessageBox.warning(self, '未抓取到响应', '请确认配方CAN连接和请求参数。没有真实响应时不会生成期望结果。')
            return
            
        self._uds_response_preview = resp_hex
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
        """Only display response bytes; never fabricate padding or use TX as RX."""
        text = getattr(self, '_uds_response_preview', '')
        if not text and not hasattr(self,'uds_workbench'):
            text = self.uds_expected.text().strip()
        if hasattr(self, "uds_byte_grid"):
            tokens = [t for t in text.replace(",", " ").split() if t]
            off = self.uds_extract_offset.value()
            length = self.uds_extract_len.value()
            
            display_hex = text
                
            self.uds_byte_grid.set_hex_string(display_hex)
            if length > 0 or off > 0:
                self.uds_byte_grid.set_selection(off, length, emit_signal=False)
            self._refresh_byte_grid_preview()

    def _uds_selection_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        offset, length, chunk = self.uds_byte_grid.get_selected_range()
        if not chunk:
            return
        menu = QMenu(self.uds_byte_grid)
        for label, index in [('HEX', 4), ('ASCII', 1), ('BCD', 5), ('数值计算', 2), ('自定义公式', 3)]:
            menu.addAction('选区设为 ' + label, lambda checked=False, i=index: self.uds_extract_mode.setCurrentIndex(i))
        menu.exec(self.uds_byte_grid.mapToGlobal(pos))

    def _on_byte_grid_range_selected(self, offset: int, length: int, chunk: bytes):
        self.uds_extract_offset.blockSignals(True)
        self.uds_extract_len.blockSignals(True)
        self.uds_extract_offset.setValue(offset)
        self.uds_extract_len.setValue(length)
        self.uds_extract_offset.blockSignals(False)
        self.uds_extract_len.blockSignals(False)
        self._refresh_byte_grid_preview(offset, length, chunk)

    def _update_uds_expected_result(self, *_):
        if self.is_loading or not hasattr(self, 'uds_sync_expected') or not self.uds_sync_expected.isChecked():
            return
        preview = getattr(self, '_uds_response_preview', '')
        if not preview:
            return
        from core.uds_parser import parse_uds_rule_string, parse_uds_response_data
        rule = parse_uds_rule_string(self._get_uds_extract_rule_string())
        result = parse_uds_response_data(bytes.fromhex(preview), rule)
        if result.get('success'):
            self.uds_result_expected.setText(str(result['value']))
            self.lbl_byte_phys.setText(str(result['display']))
        else:
            self.uds_result_expected.clear()
        self.uds_result_expected.setToolTip('来自当前真实响应和提取规则；可取消同步后修改。' if result.get('success') else str(result.get('error', '')))

    def _refresh_byte_grid_preview(self, offset=None, length=None, chunk=None):
        self._update_uds_expected_result()
        if hasattr(self, 'uds_segments'):
            self.uds_segments.refresh(bytes.fromhex(getattr(self, '_uds_response_preview', '') or ''))
            self.uds_byte_grid.set_segment_ranges(self.uds_segments.rules if self.uds_segments.enabled.isChecked() else [])
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
        from core.uds_parser import parse_uds_rule_string, parse_uds_response_data
        rule = parse_uds_rule_string(self._get_uds_extract_rule_string())
        if rule:
            if rule['mode'] == 'COMBINE':
                result = parse_uds_response_data(bytes.fromhex(getattr(self, '_uds_response_preview', '') or ''), rule)
            else:
                rule.update(offset=0, length=0)
                result = parse_uds_response_data(chunk, rule)
            if result.get('success'):
                display = ('ASCII: ' + json.dumps(result['value'], ensure_ascii=False)
                           if rule['mode'] == 'ASCII' else str(result['display']))
                self.lbl_byte_phys.setText(display)
            else:
                self.lbl_byte_phys.setText(str(result.get('error', '--')))
        else:
            self.lbl_byte_phys.setText(str(raw_int) if raw_int is not None else '--')

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

    def _on_uds_multi_toggled(self, enabled):
        if enabled and not self.is_loading and not self.uds_segments.rules and self.uds_extract_mode.currentIndex():
            self._add_uds_segment()

    def _add_uds_segment(self, index=None, replace=False):
        if index is not None:
            self.uds_extract_mode.setCurrentIndex(index)
        if not self.uds_segments.enabled.isChecked():
            return
        from core.uds_parser import parse_uds_rule_string
        rule = parse_uds_rule_string(self._get_uds_single_extract_rule_string())
        if not rule:
            QMessageBox.warning(self, '解析分段', '请先选择当前段的解析格式、起始位置和长度。')
            return
        rule.pop('var_name', None)
        try:
            if replace:
                self.uds_segments.replace(rule)
            else:
                self.uds_segments.append(rule)
        except ValueError as exc:
            QMessageBox.warning(self, '解析分段', str(exc))

    def _load_uds_segment_for_edit(self, rule):
        from core.uds_parser import format_uds_rule_string
        var_name = self.uds_extract_var.text()
        self._set_uds_single_extract_from_rule_string(format_uds_rule_string(rule))
        self.uds_extract_var.setText(var_name)

    def _get_uds_extract_rule_string(self):
        if hasattr(self, 'uds_segments') and self.uds_segments.enabled.isChecked() and self.uds_segments.rules:
            from core.uds_parser import format_uds_rule_string
            return format_uds_rule_string(self.uds_segments.config(self.uds_extract_var.text().strip()))
        return self._get_uds_single_extract_rule_string()

    def _set_uds_extract_from_rule_string(self, rule_str):
        from core.uds_parser import parse_uds_rule_string, format_uds_rule_string
        rule = parse_uds_rule_string(rule_str)
        was_loading = self.is_loading
        self.is_loading = True
        try:
            combined = bool(rule and rule.get('mode') == 'COMBINE')
            self.uds_segments.enabled.setChecked(combined or not rule_str or rule_str == '--')
            self.uds_segments.load(rule if combined else {})
            if combined:
                self._set_uds_single_extract_from_rule_string(format_uds_rule_string(rule['segments'][0]))
                self.uds_extract_var.setText(rule.get('var_name', ''))
            else:
                self._set_uds_single_extract_from_rule_string(rule_str)
        finally:
            self.is_loading = was_loading
        self._refresh_byte_grid_preview()

    def _get_uds_single_extract_rule_string(self):
        idx = self.uds_extract_mode.currentIndex()
        if idx == 0:
            return ""
        from core.uds_parser import format_uds_rule_string
        offset = self.uds_extract_offset.value()
        length = self.uds_extract_len.value()
        var_name = self.uds_extract_var.text().strip()

        if idx == 1:
            return format_uds_rule_string({"mode": "ASCII", "offset": offset, "length": length, "var_name": var_name})
        elif idx == 4:
            return format_uds_rule_string({'mode':'HEX','offset':offset,'length':length,'var_name':var_name})
        elif idx == 5:
            return format_uds_rule_string({'mode':'BCD','offset':offset,'length':length,'var_name':var_name})
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

    def _set_uds_single_extract_from_rule_string(self, rule_str):
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
        elif mode == 'HEX':
            self.uds_extract_mode.setCurrentIndex(4)
        elif mode == 'BCD':
            self.uds_extract_mode.setCurrentIndex(5)
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

    def _uds_rule_text(self, item):
        return (item.data(Qt.UserRole + 50) or item.text()) if item else ''

    def _set_uds_rule_item(self, item, rule_text):
        from core.uds_parser import parse_uds_rule_string
        item.setData(Qt.UserRole + 50, rule_text)
        parsed = parse_uds_rule_string(rule_text)
        if parsed and parsed['mode'] == 'COMBINE':
            summary = ' + '.join(f"{r['mode']}[{r['offset']}:{r['length']}]" for r in parsed['segments'])
            item.setText(f"组合{len(parsed['segments'])}段：{summary}")
            item.setToolTip(summary)
        else:
            item.setText(rule_text or '--')
            item.setToolTip(rule_text)

    def _append_uds_table_row(self, name, request, expected, rule_str="", expected_result="", *, row=None):
        if row is None:
            row = self.uds_table.rowCount()
        self.uds_table.insertRow(row)
        rule_display = rule_str.strip() if rule_str.strip() else "--"
        values = [name, request.upper(), expected.upper(), "校验" if expected else "不校验", rule_display, expected_result]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
            if column == 3:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if column == 4:
                self._set_uds_rule_item(item, rule_display)
            self.uds_table.setItem(row, column, item)
        output = QTableWidgetItem()
        output.setFlags((output.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        output.setCheckState(Qt.Unchecked)
        if request.strip() == '@SECURITY':
            output.setFlags(output.flags() & ~Qt.ItemIsEnabled)
        output.setToolTip('勾选此行作为本工步唯一最终输出；不勾选时兼容原有汇总方式。')
        self.uds_table.setItem(row, 6, output)
        self._set_uds_row_options(row, {})
        self.uds_table.selectRow(row)
        return row

    def _set_uds_row_options(self, row, options):
        options={key:options.get(key,default) for key,default in (('address','default'),('suppress',False),('check',True),('response_sample',''))}
        self.uds_table.item(row,0).setData(Qt.UserRole,options)
        self.uds_table.item(row,3).setText('校验' if options['check'] and self.uds_table.item(row,2).text().strip() else '不校验')
        for col,value in ((7,{'default':'跟随默认','physical':'物理地址','functional':'功能地址'}[options['address']]),
                          (8,'是' if options['suppress'] else '否'),(9,'是' if options['check'] else '否')):
            item=QTableWidgetItem(value);item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.uds_table.setItem(row,col,item)

    def _uds_row_metadata(self):
        rows=[]
        for row in range(self.uds_table.rowCount()):
            if self.uds_table.item(row,1).text().strip():
                rows.append(dict(self.uds_table.item(row,0).data(Qt.UserRole) or {},
                    expected=self.uds_table.item(row,5).text(),output=self.uds_table.item(row,6).checkState()==Qt.Checked,
                    enabled=self.uds_table.item(row,0).checkState()==Qt.Checked,name=self.uds_table.item(row,0).text()))
        return rows

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

    @staticmethod
    def _uds_is_read_request(request_text):
        try:
            return int(request_text.strip().split()[0], 16) in (0x19, 0x22, 0x23, 0x24)
        except (ValueError, IndexError):
            return False

    def on_uds_request_changed(self, request_text):
        self._uds_response_preview = ''
        if hasattr(self,'uds_workbench'):
            self.uds_workbench.response_sample.clear()
        if hasattr(self, 'uds_result_expected'):
            self.uds_result_expected.clear()
        suppress=getattr(getattr(self,'uds_workbench',None),'suppress',None)
        if suppress is not None and suppress.isChecked():
            self.uds_expected.clear()
            return
        if not hasattr(self, "uds_auto_response") or not self.uds_auto_response.isChecked():
            return
        self.uds_expected.setText('' if self._uds_is_read_request(request_text) else self._uds_positive_response_prefix(request_text))

    def add_uds_row(self):
        if self._uds_edit_item is not None:
            return self.save_uds_edited_row()
        request = self.uds_request.text().strip()
        expected = self.uds_expected.text().strip()
        if not request:
            return
        rule_str = self._get_uds_extract_rule_string()
        expected_result = self.uds_result_expected.text()
        if expected_result and not rule_str:
            QMessageBox.warning(self, '期望结果', '请先选择ASCII、数值计算或公式模式，或清空期望结果。')
            return
        case=getattr(self,'_uds_case_name',None)
        name=case[1] if case and case[0]==request else self.uds_service.currentText()
        current = self.uds_table.currentRow()
        row = current + 1 if self.uds_table.selectionModel().hasSelection() else self.uds_table.rowCount()
        self._append_uds_table_row(name, request, expected, rule_str, expected_result, row=row)
        self._set_uds_row_options(row, self.uds_workbench.row_options())
        self.uds_request.clear()
        self.uds_expected.clear()

    def _uds_edit_row(self):
        from shiboken6 import isValid
        item = self._uds_edit_item
        return item.row() if item is not None and isValid(item) and item.tableWidget() is self.uds_table else -1

    def cancel_uds_edit(self):
        self._uds_edit_item = None
        self.uds_edit_actions.hide()
        self.uds_request.clear()
        self.uds_expected.clear()
        self.uds_result_expected.clear()
        if hasattr(self,'uds_workbench'):self.uds_workbench.load_options({})
        self._update_uds_navigation()

    def save_uds_edited_row(self, *, show_list=True):
        row = self._uds_edit_row()
        if row < 0:
            QMessageBox.warning(self, '无法保存', '原行已删除，请重新选择需要编辑的行。')
            self.cancel_uds_edit()
            return False
        request = self.uds_request.text().strip().upper()
        expected = self.uds_expected.text().strip().upper()
        rule = self._get_uds_extract_rule_string()
        result = self.uds_result_expected.text()
        if not request or request == '@SECURITY' or (result and not rule):
            QMessageBox.warning(self, '参数不完整', '请填写请求PDU；期望结果需配置提取规则。DLL解锁请在安全解锁页配置。')
            return False
        name = self.uds_service.currentText()
        if name == '自定义':
            name = self.uds_table.item(row, 0).text()
        for column, value in enumerate([name, request, expected, '校验' if expected else '不校验', rule or '--', result]):
            if column == 4:
                self._set_uds_rule_item(self.uds_table.item(row, column), value)
            else:
                self.uds_table.item(row, column).setText(value)
        self._set_uds_row_options(row,self.uds_workbench.row_options())
        self.cancel_uds_edit()
        self.uds_table.selectRow(row)
        if show_list:
            self.uds_tabs.setCurrentIndex(2)
        return True

    def _on_uds_row_clicked(self, row, column):
        if column in (0,6) or QApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return
        self.load_uds_row(row, column)

    def load_uds_row(self, row, _column=0):
        if row < 0 or row >= self.uds_table.rowCount():
            return
        if self._uds_edit_item is not None:
            if self._uds_edit_row() == row:
                self.uds_tabs.setCurrentIndex(2)
                return
            if not self.save_uds_edited_row(show_list=False):
                return
        name_item = self.uds_table.item(row, 0)
        request_item = self.uds_table.item(row, 1)
        if request_item and request_item.text().strip() == '@SECURITY':
            self.uds_tabs.setCurrentIndex(1)
            return
        if request_item and request_item.text().strip().startswith('@DELAY '):
            value,ok=QInputDialog.getInt(self,'修改延时','延时（ms）',int(request_item.text().split()[1]),0,3600000)
            if ok:
                request_item.setText(f'@DELAY {value}');name_item.setText(f'延时 {value}ms')
            return
        expected_item = self.uds_table.item(row, 2)
        rule_item = self.uds_table.item(row, 4)
        if name_item:
            index = self.uds_service.findText(name_item.text())
            self.uds_service.setCurrentIndex(index if index >= 0 else self.uds_service.count() - 1)
        self.uds_request.setText(request_item.text() if request_item else "")
        self.uds_expected.setText(expected_item.text() if expected_item else "")
        rule_str = self._uds_rule_text(rule_item)
        self._set_uds_extract_from_rule_string(rule_str)
        item = self.uds_table.item(row, 5)
        self.uds_result_expected.setText(item.text() if item else '')
        self.uds_workbench.load_options(name_item.data(Qt.UserRole) or {})
        self._uds_edit_item = name_item
        self.uds_edit_actions.show()
        self.uds_table.selectRow(row)
        self.uds_tabs.setCurrentIndex(2)
        self._update_uds_navigation()

    def _on_uds_table_item_changed(self, item):
        if item.column() == 6 and item.checkState() == Qt.Checked:
            self.uds_table.blockSignals(True)
            try:
                for row in range(self.uds_table.rowCount()):
                    other = self.uds_table.item(row, 6)
                    if row != item.row() and other:
                        other.setCheckState(Qt.Unchecked)
            finally:
                self.uds_table.blockSignals(False)
        if item.column() == 2:
            status = self.uds_table.item(item.row(), 3)
            if status is not None:
                status.setText("校验" if item.text().strip() else "不校验")
        elif item.column() == 4:
            item.setToolTip(f"UDS提取规则: {item.text()}" if item.text().strip() != "--" else "")

    def delete_uds_row(self):
        rows = sorted({index.row() for index in self.uds_table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            self.uds_table.removeRow(row)
        if self._uds_edit_item is not None and self._uds_edit_row() < 0:
            self.cancel_uds_edit()
        self._update_uds_navigation()
        if rows and self.uds_table.rowCount():
            self.uds_table.selectRow(min(min(rows), self.uds_table.rowCount()-1))

    def _uds_context_menu(self, position):
        from PySide6.QtWidgets import QMenu
        index = self.uds_table.indexAt(position)
        if index.isValid() and not self.uds_table.selectionModel().isRowSelected(index.row()):
            self.uds_table.selectRow(index.row())
        menu = QMenu(self.uds_table)
        edit = menu.addAction('编辑选中行', lambda: self.load_uds_row(self.uds_table.currentRow()))
        edit.setEnabled(len(self.uds_table.selectionModel().selectedRows()) == 1)
        delete = menu.addAction('删除选中行', self.delete_uds_row)
        delete.setEnabled(bool(self.uds_table.selectionModel().selectedRows()))
        clear = menu.addAction('清空列表', self.clear_uds_rows)
        clear.setEnabled(self.uds_table.rowCount() > 0)
        menu.exec(self.uds_table.viewport().mapToGlobal(position))

    def clear_uds_rows(self):
        self.cancel_uds_edit()
        self.uds_table.setRowCount(0)
        self._uds_load_error = ''

    def move_uds_row(self, offset):
        row = self.uds_table.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.uds_table.rowCount():
            return
        items = [self.uds_table.takeItem(row, column) for column in range(self.uds_table.columnCount())]
        self.uds_table.blockSignals(True)
        self.uds_table.removeRow(row)
        self.uds_table.insertRow(target)
        for column, item in enumerate(items):
            if item is not None:
                self.uds_table.setItem(target, column, item)
        self.uds_table.blockSignals(False)
        self.uds_table.selectRow(target)
        self._update_uds_navigation()

    def _uds_script(self):
        lines = []
        for row in range(self.uds_table.rowCount()):
            request_item = self.uds_table.item(row, 1)
            expected_item = self.uds_table.item(row, 2)
            rule_item = self.uds_table.item(row, 4)
            request = request_item.text().strip() if request_item else ""
            expected = expected_item.text().strip() if expected_item else ""
            rule_str = self._uds_rule_text(rule_item).strip()
            if request:
                line = f"{request} => {expected}" if expected else request
                if rule_str and rule_str != "--":
                    line = f"{line} | {rule_str}"
                lines.append(line)
        return "\n".join(lines)

    def get_data(self):
        from core.read_until import attach
        device = self.device_combo.currentText()
        action = self.action_combo.currentText()
        if self._legacy_simulator_data and device_key(device) == 'simulator':
            result = dict(self._legacy_simulator_data)
            decoded = decode_device_step(result.get('params', ''))
            if decoded:
                key, spec, old_values = decoded
                values = self.device_step_form.values()
                if values != old_values:
                    from core.read_until import settings
                    result['params'] = attach(encode_device_step(key, spec.key, values), settings(result['params']))
                    summary = '，'.join(f'{field.label}={values[field.key]}' for field in spec.fields if field.key != 'variable')
                    result['name'] = f'{device}-{spec.label} ({summary})'
            result.update(is_judgment=self.cb_judgment.isChecked(), fail_strategy=self.fail_strategy_combo.currentText())
            return result
        if device_key(device) == 'simulator' and action == '回读数据':
            values = self.simulator_read_form.values()
            return dict(device=device, action=action, params=encode_device_step('simulator', 'current_batch', values),
                        type='读取仪表', is_judgment=False, fail_strategy=self.fail_strategy_combo.currentText(),
                        name=f'电池模拟器-批量读取电流（{values["selection"]}，每通道{values["count"]}次，'
                             f'{"最大值" if values["mode"] == "max" else "平均值"}）')
        if device in ('写入变量','读取变量'):
            return dict(device=device,action=action,params=self.variable_step_form.params(),type='读取变量',
                is_judgment=self.cb_judgment.isChecked(),fail_strategy=self.fail_strategy_combo.currentText(),
                name=device+'：'+self.variable_step_form.key.currentText())
        if action in ('启动周期报文列表','停止周期报文列表'):
            c=self.can_periodic_form.config()
            return dict(device=device,action=action,
                params=self.can_periodic_form.params()+f' / PCH:{self._selected_physical_channel()}',
                type='CAN交互',is_judgment=self.cb_judgment.isChecked(),fail_strategy=self.fail_strategy_combo.currentText(),
                name=action+'：'+c['name'])
        if device == 'UDS 2E 三字段写入':
            return dict(device=device,action=action,
                params=self.uds_field_write_form.params()+f' / PCH:{self._selected_physical_channel()}',
                type='CAN交互',is_judgment=self.cb_judgment.isChecked(),
                fail_strategy=self.fail_strategy_combo.currentText(),name='UDS 2E 三字段写入')
        if 'CANDBC' in device:
            from core.dbc_signals import encode_step
            config=self.dbc_form.config()
            read_wait=self.read_until.config()
            if read_wait and (config.get('group') or config.get('group_role')):
                raise ValueError('采样组使用固定快照，请使用测试项上下限判定，不支持范围等待')
            if read_wait and config['output']=='choice':raise ValueError('枚举文字输出不能等待数值范围，请取消范围等待或改为物理数值')
            params=attach(encode_step(config),read_wait)
            params+=f' / PCH:{self._selected_physical_channel()}'
            return dict(device=device,action=action,params=params,type='CANDBC交互',
                        is_judgment=self.cb_judgment.isChecked(),fail_strategy=self.fail_strategy_combo.currentText(),
                        name=f'CANDBC {config["message"]}.{config["signal"]}')
        key=device_key(device);spec=action_spec(key,action)
        if spec:
            values=self.device_step_form.values()
            summary='，'.join(f'{field.label}={values[field.key]}' for field in spec.fields if field.key!='variable')
            return {'device':device,'action':action,
                    'params':attach(encode_device_step(key,spec.key,values), self.read_until.config() if spec.read else None),
                    'type':'读取仪表' if spec.read else '设置仪表',
                    'is_judgment':self.cb_judgment.isChecked(),
                    'fail_strategy':self.fail_strategy_combo.currentText(),
                    'name':f'{device}-{action} ({summary})'}
        idx = self.param_stack.currentIndex()
        
        params = []
        step_type = "设置仪表"
        
        if "MES" in device:
            step_type = "MES过站校验"
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
            if action == '组合设置ON/OFF':
                from core.relay_combination import encode as encode_relays
                params.append(encode_relays(self.relay_combination_form.config()))
            else:
                selected = [str(cb.property('coil_index')+1) for cb in self.easy320_checks if cb.isChecked()]
                params.append(",".join(selected))
        elif idx == 1: # CAN
            from core.can_frame_config import can_id,response_matcher
            frame_id=can_id(self.c_id.text())
            if "发送" not in action:
                rx=frame_id if action=='接收指定帧ID' else can_id(self.c_wait_id.text())
                frame_format=('auto','standard','extended')[self.c_rx_format.currentIndex()]
                prefix=self.c_rx_prefix.text().strip()
                response_matcher(rx,frame_format,prefix)
                params.append(f'RX_FORMAT:{frame_format}')
                if prefix:params.append(f'RX_PREFIX:{bytes.fromhex(prefix).hex(" ").upper()}')
                parser = self.can_parser.encoded()
                if parser:
                    params.append(f"PARSE:{parser}")
            if action == "接收指定帧ID":
                step_type = "CAN接收"
                params.append(f"ID:0x{frame_id:X}")
                params.append(f"TYPE:{self.c_type.currentIndex()}")
                params.append(f"CH:{self.c_channel.value()}")
                params.append(f"TIMEOUT:{self.c_timeout.value()}")
            else:
                step_type = "CAN发送" if "发送" in action else "CAN交互"
                params.append(f"ID:0x{frame_id:X}")
                params.append(f"DATA:{self.c_data.text().strip()}")
                if "发送" not in action:
                    params.append(f"WAIT_ID:0x{rx:X}")
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
                        params.append("DIFF_AMBIENT:1")
                if "0x07 CSC批量读取" in eol_action and hasattr(self, "eol_fix_adjacent"):
                    args_kv = self._split_params(args_val, is_args_level=True)
                    args_kv.pop("FIX_ADJACENT", None)
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
            if self._uds_load_error:
                raise ValueError('UDS配置未能完整载入：' + self._uds_load_error + '；请检查原配方或清空列表重建。')
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
            from core.uds_result_config import encode as encode_results, decode as validate_results
            metadata = self._uds_row_metadata()
            result_config = encode_results(metadata)
            validate_results(result_config, uds_text.splitlines())
            params.append(f'UDSMETA64:{result_config}')
            from core.uds_sequence import encode_settings,validate_sequence
            sequence_settings=self.uds_workbench.config()
            validate_sequence(uds_text.splitlines(),metadata,sequence_settings)
            params.append('UDSCFG64:' + encode_settings(sequence_settings))
            security = self.uds_security.config()
            if security:
                from core.seedkey import encode
                params.append('SEC64:' + encode(security))
            elif any(row=='@SECURITY' and config.get('enabled',True) for row,config in zip(uds_text.splitlines(),metadata)):
                raise ValueError('DLL解锁行需要配置安全访问DLL')
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
                tx_id, rx_id = self._app_flash_monitor_ids()
                params.append(f'FLASH_TX_ID:0x{tx_id:X}')
                params.append(f'FLASH_RX_ID:0x{rx_id:X}')
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
            params.append(f"Range:{self.sim_batch_range.currentData()}")
            params.append(f"CH:{self.sim_batch_channels.text()}")
        elif "全部" in action:
            if "开启" in action: params.append("开启")
            elif "关闭" in action: params.append("关闭")
            else: params.append("--")

        if idx in (1, 7, 13):
            # CAN, EOL and UDS use one recipe-bound adapter and an explicit physical channel.
            physical = self._selected_physical_channel(self._is_plain_can_send())
            params.append(f'PCH:{physical}')
        params_str = " / ".join(params) if params else "--"
        if idx == 3:
            params_str = attach(params_str, self.read_until.config())
        
        return {
            'device': device,
            'action': action,
            'params': params_str,
            'type': step_type,
            'is_judgment': self.cb_judgment.isChecked(),
            'fail_strategy': self.fail_strategy_combo.currentText(),
            'name': f"{device}-{action} ({params_str})"
        }

    def _add_security_unlock_row(self):
        try:
            if self.uds_security.config() is None:
                raise ValueError('请先勾选并配置安全访问DLL')
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '安全访问配置错误', str(exc))
            return
        self._append_uds_table_row('DLL安全解锁', '@SECURITY', '')

    def _app_flash_monitor_ids(self):
        try:
            tx_id = int(self.app_flash_tx_id.text().strip(), 16)
            rx_id = int(self.app_flash_rx_id.text().strip(), 16)
        except ValueError as exc:
            raise ValueError('刷写请求和响应 ID 必须为十六进制，例如 0x7E4。') from exc
        if not all(0 <= value <= 0x1FFFFFFF for value in (tx_id, rx_id)) or tx_id == rx_id:
            raise ValueError('刷写 ID 应在 0–0x1FFFFFFF 范围内，且请求和响应 ID 不能相同。')
        return tx_id, rx_id

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
