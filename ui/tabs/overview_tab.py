import os
import sys
import json
import datetime
import re
import time
from typing import List, Dict, Any

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QMessageBox,
                               QLabel, QTreeWidget, QTreeWidgetItem, QGroupBox,
                               QFileDialog, QFrame, QHeaderView, QAbstractItemView, QLineEdit, QInputDialog,
                               QProgressBar, QSizePolicy)
from PySide6.QtCore import Qt, Slot, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics

from core.engine import TestStep, SubStep, StepType, SubStepType, SubStepFailStrategy
from core.debug_trace import trace
from core.measurement_units import configured_read_unit, supports_read_units


class OverviewTab(QWidget):
    """
    全屏单通道测试主界面 (Full-Page Single Channel Read-Only Test Main View)
    仅用于选择配方、展示测试序列只读流与实时运行高亮打分
    所有序列的增删改查编辑均在【工步与配方配置】 Tab 页中进行
    """

    plc_signal_received = Signal(int, object)

    def __init__(self, engine=None, db_manager=None):
        super().__init__()
        self.engine = engine
        self.db_manager = db_manager

        self.current_recipe_name = ""
        self.current_recipe_path = ""
        self.current_recipe_data = {}
        self.channel_id = 1
        self.is_testing = False
        self._confirmed_barcode = ""
        self._last_plc_signal = 0
        self._flash_state = {}
        self._flash_ignore_before = time.monotonic()
        self._flash_started_at = 0.0

        # 统计数据 (默认归零)
        self.total_count = 0
        self.pass_count = 0
        self.fail_count = 0

        # 计时器
        self.elapsed_seconds = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_timer_tick)

        # PLC 开始/停止测试信号 500ms 轮询定时器 (方案 A)
        self._plc_polling = False
        self.plc_signal_timer = QTimer(self)
        self.plc_signal_timer.setInterval(500)
        self.plc_signal_timer.timeout.connect(self.poll_plc_start_stop_signal)
        self.plc_signal_received.connect(self._handle_plc_signal)
        if self.engine:
            self.engine.channel_test_finished.connect(self.on_worker_test_finished)
        self.plc_signal_timer.start()

        self._init_ui()
        if self.engine and hasattr(self.engine, "flash_status_changed"):
            self.engine.flash_status_changed.connect(self.on_flash_status_changed)
        self.load_default_or_first_recipe()

    def _on_timer_tick(self):
        self.elapsed_seconds += 1
        mins = self.elapsed_seconds // 60
        secs = self.elapsed_seconds % 60
        self.lbl_timer.setText(f"{mins:02d}:{secs:02d}")

    def _create_kpi_card(self, title: str, val_text: str, val_color: str, width: int = 110, height: int = 56) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setFixedSize(width, height)
        card.setStyleSheet("""
            QFrame {
                background-color: #16213E;
                border: 1px solid #2D3748;
                border-radius: 6px;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(6, 4, 6, 4)
        c_layout.setSpacing(1)
        c_layout.setAlignment(Qt.AlignCenter)

        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500; border: none; background: transparent;")

        lbl_val = QLabel(val_text)
        lbl_val.setAlignment(Qt.AlignCenter)
        lbl_val.setStyleSheet(f"color: {val_color}; font-size: 20px; font-weight: bold; border: none; background: transparent; font-family: 'Segoe UI', Arial, sans-serif;")

        c_layout.addWidget(lbl_title)
        c_layout.addWidget(lbl_val)
        return card, lbl_val

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # -------------------------------------------------------------
        # 1. 顶部测试控制工具栏
        # -------------------------------------------------------------
        self.top_title_frame = QFrame(self)
        self.top_title_frame.setStyleSheet("""
            QFrame {
                background-color: #1A1A2E;
                border: 1px solid #2D2D4A;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        top_title_layout = QHBoxLayout(self.top_title_frame)
        top_title_layout.setContentsMargins(12, 4, 12, 4)

        top_title_layout.addWidget(QLabel("🎯 选择测试配方:"))
        self.combo_recipe = QComboBox(self.top_title_frame)
        self.combo_recipe.setStyleSheet("""
            QComboBox {
                background-color: #16213E;
                color: #FFD700;
                border: 1px solid #0F3460;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                font-weight: bold;
                min-width: 240px;
            }
        """)
        # activated 只在操作员主动选择时触发；程序初始化/刷新下拉框不触发 PLC 写入。
        self.combo_recipe.activated.connect(self.on_recipe_combo_changed)
        top_title_layout.addWidget(self.combo_recipe)

        top_title_layout.addStretch()

        # -------------------------------------------------------------
        # 2. 中部核心分割区域 (左侧 75% 测试表格 + 右侧 25% 状态/控制面板)
        # -------------------------------------------------------------
        content_layout = QHBoxLayout()
        content_layout.setSpacing(10)

        # ====== 左侧测试序列列表 (7列标头) ======
        self.step_tree = QTreeWidget(self)
        self.step_tree.setHeaderLabels(["序号", "名称", "下限", "上限", "测试值", "单位", "判定"])
        
        # 禁用拖拽与直接双击编辑，确保测试运行主界面只读
        self.step_tree.setDragEnabled(False)
        self.step_tree.setAcceptDrops(False)
        self.step_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)

        header = self.step_tree.header()
        header.setMinimumSectionSize(58)
        header.setStretchLastSection(False)
        # 列宽仅依据顶层测试项，展开子工步不会挤占上下限和测试值。
        header.setSectionResizeMode(QHeaderView.Interactive)

        self.step_tree.setIndentation(14)
        self.step_tree.setColumnWidth(1, 180)
        self.step_tree.setColumnWidth(2, 110)
        self.step_tree.setColumnWidth(3, 110)
        self.step_tree.setColumnWidth(4, 220)
        self.step_tree.setColumnWidth(6, 90)

        self.step_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1A1A2E;
                color: #E0E0E0;
                border: 1px solid #2D2D4A;
                border-radius: 6px;
                font-size: 14px;
                gridline-color: #2D2D4A;
            }
            QTreeWidget::item { padding: 6px; border-bottom: 1px solid #252540; }
            QTreeWidget::item:selected { background-color: #0F3460; color: #00E5FF; font-weight: bold; }
            QTreeWidget::item:hover { background-color: #25254A; }
            QHeaderView::section {
                background-color: #252540;
                color: #00E5FF;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid #3E3E5C;
                padding: 6px;
                text-align: center;
            }
        """)

        content_layout.addWidget(self.step_tree, 75)

        # ====== 右侧大屏状态与控制面板 ======
        self.right_panel = QFrame(self)
        self.right_panel.setStyleSheet("""
            QFrame {
                background-color: #16213E;
                border: 1px solid #2D2D4A;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setSpacing(12)

        # 配方选择器移至右侧状态面板顶部，避免占用测试序列表格上方空间
        top_title_layout.setContentsMargins(6, 2, 6, 2)
        self.combo_recipe.setMinimumWidth(0)
        right_layout.addWidget(self.top_title_frame)

        # 1. 配方/产品型号卡片
        self.lbl_product_model = QLabel("1 - AT-SR3.5X(PAT35X-)")
        self.lbl_product_model.setStyleSheet("""
            QLabel {
                color: #00E5FF;
                font-size: 15px;
                font-weight: bold;
                background-color: #0F3460;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        self.lbl_product_model.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_product_model)

        # 2. 巨型计时器
        self.lbl_timer = QLabel("00:00")
        self.lbl_timer.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                font-size: 42px;
                font-weight: bold;
                font-family: 'Consolas', 'Courier New', monospace;
                background-color: #1A1A2E;
                border: 1px solid #3E3E5C;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        self.lbl_timer.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_timer)

        # 3. 巨型 PASS/FAIL 总判定看板
        self.lbl_big_status = QLabel("READY")
        self.lbl_big_status.setStyleSheet("""
            QLabel {
                background-color: #6C757D;
                color: #FFFFFF;
                font-size: 38px;
                font-weight: bold;
                border-radius: 6px;
                padding: 15px;
            }
        """)
        self.lbl_big_status.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_big_status)

        right_layout.addStretch()

        # 4. SN 码扫码输入框
        sn_layout = QHBoxLayout()
        lbl_sn = QLabel("SN")
        lbl_sn.setStyleSheet("color: #E0E0E0; font-size: 16px; font-weight: bold;")
        self.txt_sn = QLineEdit()
        self.txt_sn.setPlaceholderText("请输入或扫描SN条码...")
        self.txt_sn.setReadOnly(True)
        self.txt_sn.setStyleSheet("""
            QLineEdit {
                background-color: #1A1A2E;
                color: #00FF66;
                border: 1px solid #0F3460;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        self.txt_sn.textChanged.connect(self._validate_sn_with_ai_rule)
        sn_layout.addWidget(lbl_sn)
        sn_layout.addWidget(self.txt_sn)
        right_layout.addLayout(sn_layout)

        # 5. 大控制按钮: 开始 & 停止
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_run_test = QPushButton("开始")
        self.btn_run_test.setMinimumHeight(48)
        self.btn_run_test.setStyleSheet("""
            QPushButton {
                background-color: #28A745;
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #34CE57; }
        """)
        self.btn_run_test.clicked.connect(self.request_scan_and_start)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setMinimumHeight(48)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #DC3545;
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #E64A5A; }
        """)
        self.btn_stop.clicked.connect(self.stop_single_channel_test)

        btn_layout.addWidget(self.btn_run_test)
        btn_layout.addWidget(self.btn_stop)
        right_layout.addLayout(btn_layout)

        content_layout.addWidget(self.right_panel, 25)
        main_layout.addLayout(content_layout, 1)

        # -------------------------------------------------------------
        # 3. 底部生产统计数据栏 (全等高 56px 卡片 + 状态监控 + 右侧卡片)
        # -------------------------------------------------------------
        self.stats_frame = QFrame(self)
        self.stats_frame.setStyleSheet("""
            QFrame#stats_frame {
                background-color: #1A1A2E;
                border: 1px solid #2D2D4A;
                border-radius: 8px;
            }
        """)
        self.stats_frame.setObjectName("stats_frame")
        stats_layout = QHBoxLayout(self.stats_frame)
        stats_layout.setContentsMargins(12, 8, 12, 8)
        stats_layout.setSpacing(10)

        # 4 组大数字 KPI 卡片 (严格 56px 等高)
        self.card_stat_total, self.lbl_stat_total_val = self._create_kpi_card("生产总数", "0", "#00E5FF", 110, 56)
        self.card_stat_pass, self.lbl_stat_pass_val = self._create_kpi_card("合格数", "0", "#00FF66", 110, 56)
        self.card_stat_fail, self.lbl_stat_fail_val = self._create_kpi_card("不合格", "0", "#FF3366", 110, 56)
        self.card_stat_rate, self.lbl_stat_rate_val = self._create_kpi_card("合格率", "0.00%", "#FFD700", 115, 56)

        # 兼顾代码向后兼容性
        self.lbl_stat_total = self.lbl_stat_total_val
        self.lbl_stat_pass = self.lbl_stat_pass_val
        self.lbl_stat_fail = self.lbl_stat_fail_val
        self.lbl_stat_rate = self.lbl_stat_rate_val

        stats_layout.addWidget(self.card_stat_total)
        stats_layout.addWidget(self.card_stat_pass)
        stats_layout.addWidget(self.card_stat_fail)
        stats_layout.addWidget(self.card_stat_rate)

        # 5. 数据清零按钮 (严格 56px 等高卡片按钮，管理员激活，操作员置灰)
        self.btn_reset_stats = QPushButton("🔄 数据清零\n(仅管理员)")
        self.btn_reset_stats.setFixedSize(110, 56)
        self.btn_reset_stats.setCursor(Qt.PointingHandCursor)
        self.btn_reset_stats.setToolTip("需要管理员权限执行统计清零")
        self.btn_reset_stats.setStyleSheet("""
            QPushButton {
                background-color: #241D35;
                color: #FFAA33;
                border: 1px solid #FF9900;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
                line-height: 1.3;
            }
            QPushButton:hover {
                background-color: #33264A;
                border: 1px solid #FFB84D;
                color: #FFCC66;
            }
            QPushButton:pressed {
                background-color: #1A1426;
            }
            QPushButton:disabled {
                background-color: #1A1A28;
                color: #555566;
                border: 1px dashed #3D3D4E;
                font-size: 12px;
                font-weight: normal;
            }
        """)
        self.btn_reset_stats.clicked.connect(self.on_reset_stats_clicked)
        stats_layout.addWidget(self.btn_reset_stats)

        # 6. APP 刷写监控面板 (严格 56px 等高)
        self.flash_status_panel = QFrame()
        self.flash_status_panel.setObjectName("flash_status_panel")
        self.flash_status_panel.setFixedHeight(84)
        self.flash_status_panel.setMinimumWidth(260)
        self.flash_status_panel.setStyleSheet(
            "QFrame#flash_status_panel { background: #16213E; border: 1px solid #2D3748; border-radius: 6px; padding: 0px; }"
        )
        flash_layout = QVBoxLayout(self.flash_status_panel)
        flash_layout.setContentsMargins(8, 2, 8, 2)
        flash_layout.setSpacing(1)
        self.lbl_flash_status = QLabel("APP刷写: 待启动")
        self.lbl_flash_record = QLabel("Record: --")
        self.lbl_flash_can = QLabel("PCAN: 等待刷写监听")
        for label in (self.lbl_flash_status, self.lbl_flash_record, self.lbl_flash_can):
            label.setTextFormat(Qt.PlainText)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setStyleSheet("color: #CBD5E0; font-size: 10px; border: none; padding: 0px; margin: 0px; background: transparent;")
            flash_layout.addWidget(label)
        self.flash_progress_bar = QProgressBar()
        self.flash_progress_bar.setRange(0, 1000)
        self.flash_progress_bar.setValue(0)
        self.flash_progress_bar.setTextVisible(False)
        self.flash_progress_bar.setFixedHeight(4)
        self.flash_progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #0F172A;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #00E5FF;
                border-radius: 2px;
            }
        """)
        flash_layout.addWidget(self.flash_progress_bar)
        stats_layout.addWidget(self.flash_status_panel, 1)

        # 右侧预留容器 (用于放入图2 缩小版用户卡片 & 老化次数)
        self.right_controls_layout = QHBoxLayout()
        self.right_controls_layout.setSpacing(10)
        stats_layout.addLayout(self.right_controls_layout)

        main_layout.addWidget(self.stats_frame)

    def _reset_flash_status(self):
        self._flash_ignore_before = time.monotonic()
        self._flash_started_at = 0.0
        self._flash_state = {}
        for item in getattr(self, 'current_recipe_data', {}).get('items', []):
            for sub in item.get('sub_steps', []):
                device = str(sub.get('device', '')).lower()
                action = str(sub.get('action', ''))
                kind = 'SG400' if any(name in device for name in ('sg400', 'opteeq', '力捷丰')) else 'E2' if any(name in device for name in ('e2_rfp', 'renesas', '瑞萨')) else None
                if kind and ('烧录' in action or '回读校验' in action):
                    from core.engine import ChannelWorker
                    values = ChannelWorker._parse_key_values(self, str(sub.get('params', '')))
                    self._flash_state = dict(kind=kind, phase='待启动', project=values.get('PROJECT', ''), active=False)
                    break
            if self._flash_state:
                break
        self._render_flash_status()

    def _stop_flash_display(self):
        self._flash_ignore_before = time.monotonic()
        if self._flash_state.get("active"):
            self._flash_state.update(active=False, result="STOPPED", phase="已停止监控，未确认上位机停止")
            self._render_flash_status()

    @Slot(int, object)
    def on_flash_status_changed(self, channel_id, status):
        if channel_id != self.channel_id:
            return
        started = status.get("started_at", 0.0)
        if started < max(self._flash_ignore_before, self._flash_started_at):
            return  # Ignore queued updates from a previous test or stopped flash.
        self._flash_started_at = started
        self._flash_state = dict(status)
        if not hasattr(self, '_sg_display_timer'):
            self._sg_display_timer = QTimer(self)
            self._sg_display_timer.setInterval(500)
            self._sg_display_timer.timeout.connect(self._render_flash_status)
        if status.get('kind') in ('SG400', 'E2') and status.get('active'):
            self._sg_display_timer.start()
        else:
            self._sg_display_timer.stop()
        self._render_flash_status()

    def _render_flash_status(self):
        state = self._flash_state
        result = state.get("result", "")
        active = state.get("active", False)
        percent = state.get("percent")
        title = "APP刷写：" + state.get("phase", "待启动")
        is_sg = state.get('kind') in ('SG400', 'E2')
        if is_sg:
            title = ("力捷丰 SG400：" if state['kind'] == 'SG400' else "瑞萨 E2：") + state.get("phase", "待启动")
        if percent is not None:
            title += f" | {percent:.1f}%" if is_sg else f" | 第{state.get('segment', 1)}段 {percent:.1f}%"
        elif active and state.get("segment", 0):
            title += " | 进度未知"
        if state and 'started_at' in state:
            elapsed = max(0, time.monotonic() - state['started_at']) if is_sg and active else state.get('elapsed', 0.0)
            title += f" | {elapsed:.1f}s"
            if is_sg and state.get('timeout'):
                title += f" / 超时上限 {state['timeout']:g}s"
        record = "Record：" + (state.get("record") or "--")
        if is_sg:
            record = "工程：" + os.path.basename(str(state.get('project', '')).replace('\\', '/'))
        if state.get("record_error"):
            record += " | " + state["record_error"]
        age = state.get("last_can_age")
        can_text = "PCAN：" + (f"最近相关报文 {age:.1f}秒前" if age is not None else "尚未收到刷写报文")
        if state.get("can_warning"):
            can_text += " | " + state["can_warning"]
        elif active and age is not None and age >= 3.0:
            can_text += " | 暂无新报文"
        if not state:
            can_text = "PCAN：等待刷写监听"
        if is_sg:
            can_text = ("结果：" + state.get('detail', result)) if result else "下载中显示滚动条，回读时显示实际百分比" if active else "等待执行下载或回读校验工步"
            if state['kind'] == 'E2' and not result:
                can_text = "执行中显示滚动条，阶段信息来自 rfp-cli" if active else "等待执行瑞萨下载工步"
        for label, text in ((self.lbl_flash_status, title), (self.lbl_flash_record, record), (self.lbl_flash_can, can_text)):
            label.setToolTip(text)
            label.setText(label.fontMetrics().elidedText(" ".join(text.splitlines()), Qt.ElideRight, max(40, label.width())))
        color = "#28A745" if result == "PASS" else "#FFC107" if result == "STOPPED" else "#DC3545" if result else "#00E5FF"
        self.lbl_flash_status.setStyleSheet(f"color: {color}; font-size: 12px; border: none; padding: 0px;")
        self.flash_progress_bar.setStyleSheet(
            f"QProgressBar {{ border: none; background: #252540; }} QProgressBar::chunk {{ background: {color}; }}"
        )
        if active and percent is None:
            self.flash_progress_bar.setRange(0, 0)  # Indeterminate, never a fabricated percent.
        else:
            self.flash_progress_bar.setRange(0, 1000)
            self.flash_progress_bar.setValue(1000 if result == "PASS" else int((percent or 0) * 10))

    def _fit_test_columns(self):
        """按测试项完整文本分配列宽；空间不足时允许横向滚动。"""
        tree = self.step_tree
        tree.ensurePolished()
        font = QFont(tree.font())
        font.setBold(True)  # 选中行加粗后也能完整显示。
        metrics = QFontMetrics(font)
        widths = [64, 180, 110, 110, 220, 58, 90]
        for row in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(row)
            for item in [parent] + [parent.child(i) for i in range(parent.childCount())]:
                for col in range(tree.columnCount()):
                    text = item.text(col)
                    if item.parent() is None or col != 1:
                        item.setToolTip(col, text)
                    width = max((metrics.horizontalAdvance(line) for line in text.splitlines()), default=0) + 28
                    # 树形缩进占用序号列宽，子工步还要预留第二级缩进。
                    if col == tree.treePosition():
                        width += tree.indentation() * (2 if item.parent() is not None else 1)
                    widths[col] = max(widths[col], width)
        widths[1] = min(widths[1], 240)
        # 剩余空间优先留给测试值，长文件名不受名称列影响。
        widths[4] += max(0, tree.viewport().width() - sum(widths))
        for col, width in enumerate(widths):
            tree.setColumnWidth(col, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "step_tree"):
            QTimer.singleShot(0, self._fit_test_columns)
        if hasattr(self, "flash_status_panel"):
            QTimer.singleShot(0, self._render_flash_status)

    def _validate_sn_with_ai_rule(self, text=""):
        sn_rule = str(self.current_recipe_data.get("sn_regex", "")).strip() if hasattr(self, "current_recipe_data") and self.current_recipe_data else ""
        sn_text = text.strip() if text else self.txt_sn.text().strip()
        if not sn_rule or not sn_text:
            self.txt_sn.setStyleSheet("""
                QLineEdit {
                    background-color: #1A1A2E;
                    color: #00FF66;
                    border: 1px solid #0F3460;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 13px;
                    font-weight: bold;
                }
            """)
            return

        import re
        try:
            if re.match(sn_rule, sn_text):
                self.txt_sn.setStyleSheet("""
                    QLineEdit {
                        background-color: #112211;
                        color: #00FF66;
                        border: 2px solid #28A745;
                        border-radius: 4px;
                        padding: 6px;
                        font-size: 13px;
                        font-weight: bold;
                    }
                """)
            else:
                self.txt_sn.setStyleSheet("""
                    QLineEdit {
                        background-color: #2A1111;
                        color: #FFD700;
                        border: 2px solid #DC3545;
                        border-radius: 4px;
                        padding: 6px;
                        font-size: 13px;
                        font-weight: bold;
                    }
                """)
        except Exception:
            pass

    def embed_right_controls(self, user_btn: QWidget, aging_lbl: QWidget):
        """供 MainWindow 嵌入右下角缩小控件的接口"""
        if user_btn:
            self.right_controls_layout.addWidget(user_btn)
        if aging_lbl:
            self.right_controls_layout.addWidget(aging_lbl)

    # -----------------------------------------------------------------
    # 配方加载与只读树结构展示
    # -----------------------------------------------------------------
    def load_default_or_first_recipe(self):
        """加载 recipes 目录下的首个配方文件"""
        recipe_dir = None
        if self.db_manager and hasattr(self.db_manager, "get_recipe_dir"):
            recipe_dir = self.db_manager.get_recipe_dir()
        if not recipe_dir or not os.path.exists(recipe_dir):
            recipe_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "recipes")

        if os.path.exists(recipe_dir):
            files = [f for f in os.listdir(recipe_dir) if f.endswith(".json")]
            if files:
                self.update_recipes(files)
                first_path = os.path.join(recipe_dir, files[0])
                # 启动时只加载默认配方到界面，不产生 PLC 写操作。
                self.load_recipe_from_file(first_path, sync_plc=False)

    def update_recipes(self, recipes_list: List[str]):
        """供 MainWindow 调用的接口，刷新配方下拉框"""
        try:
            current_selection = self.combo_recipe.currentText()
            self.combo_recipe.blockSignals(True)
            self.combo_recipe.clear()
            for r in recipes_list:
                clean_name = r.replace(".json", "")
                filename = r if r.endswith(".json") else f"{r}.json"
                self.combo_recipe.addItem(clean_name, filename)
            if current_selection:
                idx = self.combo_recipe.findText(current_selection)
                if idx >= 0:
                    self.combo_recipe.setCurrentIndex(idx)
            self.combo_recipe.blockSignals(False)
            # 列表刷新属于程序动作，只刷新界面，不写 PLC。
            index = self.combo_recipe.currentIndex()
            if index >= 0:
                self.on_recipe_combo_changed(index, sync_plc=False)
        except (RuntimeError, AttributeError) as e:
            import logging
            logging.warning(f"OverviewTab.update_recipes 忽略控件状态异常: {e}")

    def on_recipe_combo_changed(self, index: int, sync_plc: bool = True):
        """切换配方。只有用户触发的下拉选择才同步 PLC。"""
        try:
            recipe_filename = self.combo_recipe.currentData()
            if not recipe_filename:
                recipe_filename = self.combo_recipe.currentText()
            if not recipe_filename:
                return

            if not recipe_filename.endswith(".json"):
                recipe_filename += ".json"
        except (RuntimeError, AttributeError) as e:
            import logging
            logging.warning(f"OverviewTab.on_recipe_combo_changed 忽略控件状态异常: {e}")
            return

        file_path = None
        if self.db_manager and hasattr(self.db_manager, "get_recipe_dir"):
            candidate = os.path.join(self.db_manager.get_recipe_dir(), recipe_filename)
            if os.path.exists(candidate):
                file_path = candidate

        if not file_path:
            recipes_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "recipes")
            candidate = os.path.join(recipes_dir, recipe_filename)
            if os.path.exists(candidate):
                file_path = candidate

        if file_path and os.path.exists(file_path):
            self.load_recipe_from_file(file_path, sync_plc=sync_plc)

    def load_recipe_from_file(self, file_path: str, sync_plc: bool = False):
        """解析 JSON 配方文件并生成 7 列只读树状节点"""
        if not os.path.exists(file_path):
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.current_recipe_path = file_path
            self.current_recipe_name = data.get("name", os.path.basename(file_path))
            self.current_recipe_data = data
            self.lbl_product_model.setText(f"1 - {self.current_recipe_name}")
            self.step_tree.clear()

            items = data.get("items", [])
            for idx, item in enumerate(items, 1):
                item_node = QTreeWidgetItem(self.step_tree)
                item_node.setText(0, str(idx))                             # 序号
                item_node.setText(1, item.get("name", "未命名测试项"))      # 名称
                item_node.setText(2, str(item.get("min", "--")))          # 下限
                item_node.setText(3, str(item.get("max", "--")))          # 上限
                item_node.setText(4, "--")                                 # 测试值
                item_node.setText(5, item.get("unit", "NULL"))             # 单位
                item_node.setText(6, "待测试")                             # 判定
                item_node.setData(0, Qt.UserRole, {"type": "item_node", "raw_data": item})

                # 挂载子工步
                sub_steps = item.get("sub_steps", [])
                for sub_idx, sub in enumerate(sub_steps, 1):
                    sub_node = QTreeWidgetItem(item_node)
                    sub_node.setText(0, f"{idx}.{sub_idx}")
                    sub_node.setText(1, sub.get("action") or "子工步")
                    params_str = str(sub.get("params", ""))
                    sub_node.setToolTip(1, f"{sub.get('device', '')}\n{params_str}")
                    sub_node.setText(2, "--")
                    sub_node.setText(3, "--")
                    sub_node.setText(4, "--")
                    sub_node.setText(5, sub.get("unit", "NULL"))
                    if sub.get("type") == "读取仪表" and supports_read_units(sub.get("device", "")):
                        sub_node.setText(5, configured_read_unit(params_str) or ("A" if "电流" in params_str else "V"))
                    sub_node.setText(6, "待测试")
                    sub_node.setData(0, Qt.UserRole, {"type": "sub_step_node", "raw_data": sub})

            self.step_tree.collapseAll()
            self._fit_test_columns()
            self._reset_flash_status()

            if sync_plc:
                # 仅当操作员在下拉框中主动选择配方时下发并回读校验。
                recipe_number = int(data.get("recipe_number", 0))
                self.sync_recipe_number_to_plc(recipe_number)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"加载配方文件失败: {str(e)}")

    def sync_recipe_number_to_plc(self, recipe_number: int):
        """主界面选择测试配方时，下发配方号给PLC保持寄存器地址0，并回读对比校验"""
        manager = getattr(self.engine, "device_manager", None)
        if manager is None:
            QMessageBox.warning(self, "PLC下发失败", "设备管理器未初始化，无法下发配方号给PLC！")
            return

        plc = getattr(manager, "easy320", None)
        if plc is None:
            QMessageBox.warning(self, "PLC下发失败", "未找到 Easy320 PLC 驱动实例，无法下发配方号！")
            return

        # 检查连接状态，未连接则尝试自动连接一次
        if not getattr(plc, "is_connected", False):
            try:
                if not plc.connect():
                    QMessageBox.warning(
                        self, "PLC通讯告警",
                        f"PLC 未连接且尝试自动连接失败，无法下发配方号【{recipe_number}】！\n请检查 PLC 网络连通性与 IP/端口配置。"
                    )
                    return
            except Exception as e:
                QMessageBox.warning(
                    self, "PLC通讯告警",
                    f"连接 PLC 发生异常: {e}\n无法下发配方号【{recipe_number}】！"
                )
                return

        register = 0
        # 1. 写入保持寄存器地址 0 (FC06)
        try:
            write_ok = plc.write_register(register, recipe_number, verify=False)
            if not write_ok:
                QMessageBox.warning(
                    self, "PLC写入失败",
                    f"向 PLC 保持寄存器地址 {register} 写入配方号【{recipe_number}】失败！"
                )
                return
        except Exception as e:
            QMessageBox.warning(
                self, "PLC写入异常",
                f"向 PLC 写入配方号发生异常: {e}"
            )
            return

        # 2. 回读保持寄存器地址 0 (FC03)
        try:
            read_vals = plc.read_register(address=register, count=1)
            if not read_vals:
                QMessageBox.warning(
                    self, "PLC回读失败",
                    f"配方号【{recipe_number}】已下发至 PLC 地址 {register}，但回读该寄存器无响应/失败！"
                )
                return
            read_val = read_vals[0]
        except Exception as e:
            QMessageBox.warning(
                self, "PLC回读异常",
                f"回读 PLC 寄存器地址 {register} 发生异常: {e}"
            )
            return

        # 3. 对比校验
        if read_val != recipe_number:
            QMessageBox.warning(
                self, "PLC配方校验不一致",
                f"⚠️ PLC 配方号数据不一致！\n\n"
                f"下发配方号: {recipe_number}\n"
                f"PLC回读值: {read_val}\n\n"
                f"请检查 PLC 寄存器状态或重新选择配方！"
            )

    def serialize_tree_to_dict(self) -> Dict[str, Any]:
        """从只读 QTreeWidget 反向提取当前配方数据以供 TestEngine 运行"""
        items_list = []
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            item_info = item_node.data(0, Qt.UserRole)
            raw_item = item_info.get("raw_data", {}) if item_info else {}
            items_list.append(raw_item)

        return {
            "name": self.current_recipe_name or "SingleChannelRecipe",
            "recipe_number": self.current_recipe_data.get("recipe_number", 0),
            "plc_recipe_register": 0,
            "can_config": self.current_recipe_data.get("can_config"),
            "items": items_list
        }

    # -----------------------------------------------------------------
    # 单通道测试引擎控制与运行联动
    # -----------------------------------------------------------------
    def request_scan_and_start(self):
        """弹出扫码窗口；只有确认取得有效条码后才允许启动测试。"""
        if self.is_testing:
            QMessageBox.information(self, "提示", "当前测试正在运行，请勿重复启动。")
            return False

        barcode, accepted = QInputDialog.getText(
            self,
            "请扫码",
            "请使用扫码枪扫描产品 SN（扫码后按回车）：",
            QLineEdit.Normal,
            "",
        )
        barcode = barcode.strip()
        if not accepted:
            return False
        if not barcode:
            QMessageBox.warning(self, "扫码失败", "未读取到产品 SN，不能开始测试。")
            return False

        self._confirmed_barcode = barcode
        self.txt_sn.setText(barcode)
        return self.start_single_channel_test()

    def start_single_channel_test(self):
        """启动单通道测试序列"""
        if self.is_testing:
            return False
        if not self.engine:
            QMessageBox.warning(self, "错误", "底层测试引擎 TestEngine 未连接!")
            return False

        sn_barcode = self.txt_sn.text().strip()
        if not sn_barcode or sn_barcode != self._confirmed_barcode:
            QMessageBox.warning(self, "请先扫码", "必须先在扫码弹窗中扫描产品 SN，才能开始测试。")
            return False

        sn_rule = str(self.current_recipe_data.get("sn_regex", "")).strip()
        if sn_rule:
            try:
                if not re.match(sn_rule, sn_barcode):
                    self.lbl_big_status.setText("SN ERROR")
                    self.lbl_big_status.setStyleSheet("background-color: #DC3545; color: white; font-size: 34px; font-weight: bold; border-radius: 6px; padding: 15px;")
                    QMessageBox.warning(self, "AI 条码规则拦截", f"❌ 输入的 SN 条码【{sn_barcode}】不符合当前配方的 AI 规则！\n\n当前配方规则: {sn_rule}\n请核对并重新扫描正确的条码！")
                    self._confirmed_barcode = ""
                    return False
            except re.error as exc:
                QMessageBox.warning(self, "条码规则错误", f"当前配方的 SN 正则表达式无效：{exc}")
                return False

        recipe_dict = self.serialize_tree_to_dict()
        items = recipe_dict.get("items", [])
        if not items:
            QMessageBox.warning(self, "警告", "当前测试序列为空!")
            return False

        ready, message = self._apply_recipe_hardware(recipe_dict)
        if not ready:
            QMessageBox.critical(self, "配方硬件配置失败", message)
            return False

        # 重置 UI 看板与计时器
        self._reset_flash_status()
        self.elapsed_seconds = 0
        self.lbl_timer.setText("00:00")
        self.timer.start()

        self.lbl_big_status.setText("TESTING")
        self.lbl_big_status.setStyleSheet("""
            QLabel {
                background-color: #00E5FF;
                color: #000000;
                font-size: 38px;
                font-weight: bold;
                border-radius: 6px;
                padding: 15px;
            }
        """)

        # 重置树状态
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            item_node.setText(4, "--")
            item_node.setText(6, "等待")
            item_node.setBackground(6, QColor("#1A1A2E"))
            for j in range(item_node.childCount()):
                sub_node = item_node.child(j)
                sub_node.setText(4, "--")
                sub_node.setText(6, "等待")
                sub_node.setBackground(6, QColor("#1A1A2E"))

        self.engine.begin_batch_start()
        try:
            try:
                success = self.engine.start_channel_test(
                    self.channel_id,
                    items,
                    sn_barcode=sn_barcode,
                    recipe_name=recipe_dict.get("name", self.current_recipe_name),
                )
            except Exception as exc:
                success = False
                QMessageBox.critical(self, "测试启动异常", f"创建测试线程失败：{exc}")
        finally:
            self.engine.end_batch_start()

        if success:
            self.is_testing = True
            worker = self.engine.workers.get(self.channel_id)
            if worker:
                worker.step_started.connect(self.on_worker_step_started)
                worker.sub_step_finished.connect(self.on_worker_sub_step_finished)
                worker.step_finished.connect(self.on_worker_step_finished)
        else:
            self.timer.stop()
            self.lbl_big_status.setText("ERROR")
            self.lbl_big_status.setStyleSheet("background-color: #DC3545; color: white; font-size: 38px; font-weight: bold; border-radius: 6px; padding: 15px;")
            QMessageBox.critical(self, "错误", "启动单通道测试引擎失败!")
        return bool(success)

    def _apply_recipe_hardware(self, recipe_dict):
        """在测试启动前应用配方绑定的 CAN 参数，并下发 PLC 配方号。"""
        manager = getattr(self.engine, "device_manager", None)
        if manager is None:
            return False, "设备管理器未初始化"

        can_config = recipe_dict.get("can_config")
        mode = None
        if can_config:
            pcan = getattr(manager, "pcan", None)
            if pcan is None:
                return False, "配方已绑定 CAN 卡，但 USB PCAN-FD 驱动未初始化"
            try:
                mode = can_config.get("mode", "CAN-FD + BRS")
                pcan.disconnect()
                pcan.configure(
                    channel=can_config.get("channel", "PCAN_USBBUS1"),
                    nominal_bitrate=int(can_config.get("nominal_bitrate", 500000)),
                    data_bitrate=int(can_config.get("data_bitrate", 2000000)),
                    fd_enabled=mode != "经典 CAN",
                )
                pcan.default_can_type = {
                    "经典 CAN": pcan.CAN_TYPE_CLASSIC,
                    "CAN-FD": pcan.CAN_TYPE_FD,
                    "CAN-FD + BRS": pcan.CAN_TYPE_FD_BRS,
                }.get(mode, pcan.CAN_TYPE_FD_BRS)
                if not pcan.connect():
                    return False, f"CAN卡连接失败: {pcan.last_error or 'PCAN无响应'}"
            except Exception as exc:
                return False, f"CAN配方参数应用失败: {exc}"

        # PLC 配方号只在操作员主动切换配方时下发，开始测试不重复写入。
        can_summary = f"CAN={can_config.get('channel', 'PCAN_USBBUS1')} {mode}" if can_config else "无CAN配置"
        return True, can_summary

    def stop_single_channel_test(self):
        self._stop_flash_display()
        """强制停止单通道测试"""
        if self.engine:
            self.engine.stop_test(self.channel_id)
            self.is_testing = False
            self.timer.stop()
            self.lbl_big_status.setText("STOPPED")
            self.lbl_big_status.setStyleSheet("background-color: #FFC107; color: black; font-size: 34px; font-weight: bold; border-radius: 6px; padding: 15px;")

    def stop_selected_tests(self):
        """MainWindow 兼容接口"""
        self.stop_single_channel_test()

    def reset_all_screen_channels(self):
        """重置主界面测试序列状态"""
        self.stop_single_channel_test()
        self.elapsed_seconds = 0
        self._reset_flash_status()
        self.lbl_timer.setText("00:00")
        self.lbl_big_status.setText("READY")
        self.lbl_big_status.setStyleSheet("background-color: #6C757D; color: white; font-size: 38px; font-weight: bold; border-radius: 6px; padding: 15px;")
        
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            item_node.setText(4, "--")
            item_node.setText(6, "等待")
            item_node.setBackground(6, QColor("#1A1A2E"))
            for j in range(item_node.childCount()):
                sub_node = item_node.child(j)
                sub_node.setText(4, "--")
                sub_node.setText(6, "等待")
                sub_node.setBackground(6, QColor("#1A1A2E"))

    def stop_monitor(self):
        if hasattr(self, 'plc_signal_timer') and self.plc_signal_timer.isActive():
            self.plc_signal_timer.stop()

    # -----------------------------------------------------------------
    # PLC 保持寄存器地址 1 开始/停止测试信号轮询 (方案 A)
    # -----------------------------------------------------------------
    def poll_plc_start_stop_signal(self):
        """每 500ms 轮询 PLC 保持寄存器地址 1：读 1 开始测试，读 2 停止测试，读完立即向地址 1 写 0"""
        if self._plc_polling:
            return
        manager = getattr(self.engine, "device_manager", None)
        if not manager:
            return
        plc = getattr(manager, "easy320", None)
        if not plc or not getattr(plc, "is_connected", False):
            return

        import threading
        self._plc_polling = True

        def fetch():
            try:
                val_list = plc.read_register(address=1, count=1)
                val = val_list[0] if val_list else 0
                self.plc_signal_received.emit(int(val), plc)
            except Exception as e:
                import logging
                logging.warning(f"OverviewTab 轮询 PLC 地址 1 异常: {e}")
            finally:
                self._plc_polling = False

        threading.Thread(target=fetch, daemon=True).start()

    def _handle_plc_signal(self, val: int, plc):
        """主线程响应 PLC 地址 1 信号：1=弹窗扫码，2=停止测试；两者均立即清零。"""
        import threading
        if val == 0:
            self._last_plc_signal = 0
            return
        if val not in (1, 2) or val == self._last_plc_signal:
            return
        self._last_plc_signal = val

        # 先复位 PLC，避免扫码弹窗期间 500ms 轮询重复消费同一个命令。
        threading.Thread(
            target=lambda: plc.write_register(1, 0, verify=False),
            daemon=True,
        ).start()

        if val == 1:
            self.request_scan_and_start()
        elif val == 2:
            if self.is_testing:
                self.stop_single_channel_test()

    # -----------------------------------------------------------------
    # Worker 信号响应与表格打分刷新 (7列映射)
    # -----------------------------------------------------------------
    @Slot(int, int, str)
    def on_worker_step_started(self, channel_id: int, step_idx: int, step_name: str):
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            item_node.setText(6, "RUNNING")
            item_node.setBackground(6, QColor("#00E5FF"))
            item_node.setForeground(6, QColor("#000000"))

    @Slot(int, int, int, str, object)
    def on_worker_sub_step_finished(self, channel_id: int, step_idx: int, sub_idx: int, status: str, result: object):
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            if sub_idx < item_node.childCount():
                sub_node = item_node.child(sub_idx)
                result_text = str(result) if result is not None else "Done"
                sub_node.setText(4, result_text)
                sub_node.setToolTip(4, result_text)
                if getattr(result, "unit", ""):
                    sub_node.setText(5, result.unit)
                res_pass = "PASS" in status or "成功" in status or "OK" in status
                sub_node.setText(6, "PASS" if res_pass else "FAIL")
                bg_color = QColor("#28A745") if res_pass else QColor("#DC3545")
                sub_node.setBackground(6, bg_color)
                sub_node.setForeground(6, QColor("#FFFFFF"))

    @Slot(int, int, str, bool, object)
    def on_worker_step_finished(self, channel_id: int, step_idx: int, name: str, is_pass: bool, val: object):
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            res_text = "PASS" if is_pass else "FAIL"
            item_node.setText(4, str(val) if val is not None else "Done")
            if getattr(val, "unit", ""):
                item_node.setText(5, val.unit)
            item_node.setText(6, res_text)
            bg_color = QColor("#28A745") if is_pass else QColor("#DC3545")
            item_node.setBackground(6, bg_color)
            item_node.setForeground(6, QColor("#FFFFFF"))
            self._fit_test_columns()

    def _update_stats_display(self):
        """刷新界面生产统计 4 组大数字卡片"""
        pass_rate = (self.pass_count / self.total_count * 100) if self.total_count > 0 else 0.0
        if hasattr(self, 'lbl_stat_total_val'):
            self.lbl_stat_total_val.setText(str(self.total_count))
            self.lbl_stat_pass_val.setText(str(self.pass_count))
            self.lbl_stat_fail_val.setText(str(self.fail_count))
            self.lbl_stat_rate_val.setText(f"{pass_rate:.2f}%")

    def reset_statistics(self):
        """重置生产统计数据归零"""
        self.total_count = 0
        self.pass_count = 0
        self.fail_count = 0
        self._update_stats_display()

    def on_reset_stats_clicked(self):
        """点击数据清零按钮 (带二次确认与权限保障)"""
        if hasattr(self, 'btn_reset_stats') and not self.btn_reset_stats.isEnabled():
            QMessageBox.warning(self, "权限不足", "只有管理员模式可以执行数据清零！")
            return
        res = QMessageBox.question(
            self,
            "确认清零",
            "【管理员确认】\n是否确认清空当前生产统计数据（生产总数、合格数、不合格数、合格率）？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res == QMessageBox.Yes:
            self.reset_statistics()
            QMessageBox.information(self, "提示", "生产统计数据已成功清零！")

    @Slot(int, bool)
    def on_worker_test_finished(self, channel_id: int, status: bool):
        self.is_testing = False
        self.timer.stop()

        # 更新生产统计
        self.total_count += 1
        if status:
            self.pass_count += 1
        else:
            self.fail_count += 1
        self._update_stats_display()

        # 更新右侧巨型 Total Status 看板
        if status:
            self.lbl_big_status.setText("PASS")
            self.lbl_big_status.setStyleSheet("""
                QLabel {
                    background-color: #28A745;
                    color: #FFFFFF;
                    font-size: 46px;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 15px;
                }
            """)
        else:
            self.lbl_big_status.setText("FAIL")
            self.lbl_big_status.setStyleSheet("""
                QLabel {
                    background-color: #DC3545;
                    color: #FFFFFF;
                    font-size: 46px;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 15px;
                }
            """)

    def append_log(self, msg: str):
        pass

    def _convert_dict_to_test_steps(self, items: List[Dict]) -> List[TestStep]:
        """解析树节点或 JSON 字典为 core.engine.TestStep 结构"""
        steps = []
        for item in items:
            name = item.get("name", "未命名工步")
            mode_str = item.get("mode", "自定义工步")
            step_obj = TestStep(name=name, step_type=StepType.CUSTOM)
            step_obj.min_limit = item.get("min")
            step_obj.max_limit = item.get("max")
            for sub in item.get("sub_steps", []):
                sub_type_str = sub.get("type", "指令")
                sub_type = SubStepType.READ_INSTRUMENT
                if "CAN" in sub_type_str or "报文" in sub_type_str:
                    sub_type = SubStepType.CAN_SEND
                elif "EOL" in sub_type_str:
                    sub_type = SubStepType.EOL_PROTOCOL
                elif "等待" in sub_type_str:
                    sub_type = SubStepType.WAIT

                fail_strat_str = sub.get("fail_strategy", "失败停止")
                fail_strat = SubStepFailStrategy.STOP
                if fail_strat_str == "忽略继续": fail_strat = SubStepFailStrategy.CONTINUE
                elif fail_strat_str == "重试3次": fail_strat = SubStepFailStrategy.RETRY_3

                params = sub.get("params", {})
                if isinstance(params, str):
                    params = {"raw_params": params}

                sub_obj = SubStep(type=sub_type, params=params, fail_strategy=fail_strat)
                step_obj.add_sub_step(sub_obj)

            steps.append(step_obj)
        return steps
