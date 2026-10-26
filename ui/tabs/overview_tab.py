import os
import copy
import threading
from ui.device_task import DeviceTask
from core.single_channel_recipe import single_channel_items
from core.result_display import format_result, result_for_display

import json

import re
import time
from typing import List, Dict, Any

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QComboBox, QMessageBox, QLabel, QTreeWidget, QTreeWidgetItem, QFrame, QHeaderView, QAbstractItemView, QLineEdit, QInputDialog, QSizePolicy, QCheckBox, QPlainTextEdit
from PySide6.QtCore import Qt, Slot, Signal, QTimer, QCoreApplication
from PySide6.QtGui import QColor

from ui.dialogs.flash_status_dialog import FlashStatusDialog
from ui.widgets.test_status_delegate import TestStatusDelegate
from ui.widgets.test_sequence_tree import TestSequenceTree



from core.measurement_units import configured_read_unit, supports_read_units
from core.production_statistics import ProductionStatistics


class RecipeSelectionCombo(QComboBox):
    def setEnabled(self, enabled):
        owner=self.parent()
        while owner is not None and not hasattr(owner,'_can_debug_tests'):owner=owner.parent()
        allowed=owner is not None and getattr(owner,'_manual_recipe_allowed',owner._can_debug_tests())
        super().setEnabled(bool(enabled and allowed))


class OverviewTab(QWidget):
    """
    全屏单通道测试主界面 (Full-Page Single Channel Read-Only Test Main View)
    仅用于选择配方、展示测试序列只读流与实时运行高亮打分
    所有序列的增删改查编辑均在【工步与配方配置】 Tab 页中进行
    """

    run_activity_changed = Signal(bool)
    plc_signal_received = Signal(int, object)
    preparation_log = Signal(str)

    def __init__(self, engine=None, db_manager=None):
        super().__init__()
        # Closed pages must be destroyed by the GUI event loop, before a later
        # device action can collect their Python reference cycles on its thread.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.engine = engine
        self.db_manager = db_manager
        self.preparation_log.connect(self._on_preparation_log)

        self.current_recipe_name = ""
        self.current_recipe_path = ""
        self.current_recipe_data = {}
        self.channel_id = 1
        self.is_testing = False
        self.preparing = False
        self._waiting_board=None
        self._arrival_continue=None
        self._debug_requested_indices = None
        self._active_debug_run = False
        self._prepare_cancel = threading.Event()
        self._prepared_test_id = -1
        self._prepare_task = DeviceTask(self)
        self._plc_task = DeviceTask(self)
        self._recipe_match_task=DeviceTask(self)
        self._mes_upload_task = DeviceTask(self)
        self._mes_upload_queue = []
        self._mes_upload_active_path = None
        self._mes_upload_task.finished.connect(self._start_next_mes_upload)
        self._pending_plc_recipe = None
        self._plc_config_error = ""
        self._recipe_can_error = ''
        self._selected_app_flash_config = None
        self._closing = False
        self._column_fit_timer = QTimer(self)
        self._column_fit_timer.setSingleShot(True)
        self._column_fit_timer.timeout.connect(self._fit_test_columns)
        self._flash_render_timer = QTimer(self)
        self._flash_render_timer.setSingleShot(True)
        self._flash_render_timer.timeout.connect(self._render_flash_status)
        self._confirmed_barcode = ""
        self._scan_pending = False
        self._last_plc_signal = 0
        self._flash_state = {}
        self._flash_ignore_before = time.monotonic()
        self._flash_started_at = 0.0

        # 统计数据 (默认归零)
        self.total_count = 0
        self.pass_count = 0
        self.fail_count = 0
        self.production_statistics = ProductionStatistics()
        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
        from core.consecutive_ng import ConsecutiveNgAlarm
        self.ng_alarm = ConsecutiveNgAlarm((cfg or {}).get('ng_alarm_enabled', False),
                                          (cfg or {}).get('ng_alarm_threshold', 3))
        self._ng_alarm_box = None
        self.stats_deduplicate = bool((cfg or {}).get('stats_deduplicate_barcode', True))
        self._active_stats_barcode = ''
        self._stats_task = DeviceTask(self)
        self._stats_pending = False
        self._stats_reset_requested = False
        self._stats_reset_busy = False
        # 项目统计详情按钮由 MainWindow 注入。
        self.btn_stats_range = None
        self._stats_task.finished.connect(self._finish_statistics_refresh)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(60000)
        self._stats_timer.timeout.connect(self.refresh_statistics)

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
            if hasattr(self.engine,'log_message'):
                self.engine.log_message.connect(self.on_worker_log_message)
            self.engine.channel_test_finished.connect(self.on_worker_test_finished)
            self.engine.step_started.connect(self.on_worker_step_started)
            self.engine.variable_target_resolved.connect(self.on_variable_target_resolved)
            self.engine.sub_step_finished.connect(self.on_worker_sub_step_finished)
            self.engine.step_finished.connect(self.on_worker_step_finished)
            self.engine.run_retired.connect(self._on_run_retired)
            self.engine.cleanup_failed.connect(self._on_cleanup_failed)
        self.plc_signal_timer.start()

        self._init_ui()
        self.set_generic_mes_enabled((cfg or {}).get('generic_mes_enabled', False))
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)
        if self.engine and hasattr(self.engine, "flash_status_changed"):
            self.engine.flash_status_changed.connect(self.on_flash_status_changed)
        self.load_default_or_first_recipe()
        if self._persistent_statistics():
            self.refresh_statistics(reason='startup')
            self._stats_timer.start()

    def _persistent_statistics(self):
        return callable(getattr(type(self.db_manager),'load_project_statistics',None))

    def _statistics_project(self):
        return str(self.current_recipe_name or '')

    def _finish_statistics_refresh(self):
        if self._stats_pending and not self._closing:
            self._stats_pending=False
            self.refresh_statistics()

    def _set_stats_range_feedback(self, text, tooltip=None):
        """刷新项目统计详情按钮的反馈文案；按钮未注入时忽略。"""
        btn = getattr(self, 'btn_stats_range', None)
        if btn is None:
            return
        btn.setText(text)
        if tooltip is not None:
            btn.setToolTip(tooltip)

    def _audit_statistics(self,action,details):
        import logging
        evidence=dict(details,project=details.get('project',self._statistics_project()),deduplicate=self.stats_deduplicate)
        logging.info('STATISTICS %s %s',action,json.dumps(evidence,ensure_ascii=False))
        if callable(getattr(type(self.db_manager),'record_statistics_audit',None)):
            self.db_manager.record_statistics_audit(action,evidence)

    def refresh_statistics(self,reason='refresh'):
        if self._closing or not self._persistent_statistics():return
        if reason!='refresh':self._stats_refresh_reason=reason
        if self._stats_task.isRunning():
            self._stats_pending=True
            return
        project=self._statistics_project()
        if not project:
            self.production_statistics=ProductionStatistics();self._recalculate_statistics()
            return
        reset=self._stats_reset_requested
        reason=getattr(self,'_stats_refresh_reason','refresh')
        self._stats_refresh_reason='refresh'
        self._stats_reset_requested=False
        database=self.db_manager
        self._set_stats_range_feedback('正在读取统计…')
        def read():
            if reset:database.reset_project_statistics(project)
            rows=database.load_project_statistics(project)
            statistics=ProductionStatistics();statistics.restore(rows)
            # Lifetime project results are reduced outside the GUI thread.
            statistics.counts(True);statistics.counts(False);statistics.details()
            return statistics
        def completed(ok,rows):
            if reset:
                self._stats_reset_busy=False
                self.btn_run_test.setEnabled(not self._run_busy() and not self._closing)
                self.run_activity_changed.emit(self._run_busy())
            if project!=self._statistics_project():return
            if not ok:
                self._audit_statistics('read_failed',dict(project=project,reason=reason,error=str(rows)))
                self._set_stats_range_feedback('统计读取失败', str(rows))
                self.card_stat_total.setToolTip('统计读取失败，当前数字尚未更新：'+str(rows))
                if reset:QMessageBox.warning(self,'统计清零失败',str(rows))
                return
            if not self._stats_pending:
                self._set_stats_range_feedback('项目统计详情', '当前项目自上次清零以来的累计良率，不按时间重置。')
                previous=(self.total_count,self.pass_count,self.fail_count)
                counts=rows.counts(self.stats_deduplicate)
                context=(project,self.stats_deduplicate)
                decreased=counts[0]<previous[0]
                unexpected=decreased and not reset and context==getattr(self,'_stats_applied_context',None)
                self._audit_statistics('unexpected_decrease' if unexpected else 'snapshot',dict(reason='manual_reset' if reset else reason,previous=previous,current=counts))
                self._stats_applied_context=context
                self.production_statistics=rows
                self._recalculate_statistics()
                if unexpected:
                    self.card_stat_total.setToolTip(f'项目 {project} 的数量异常下降，已记录诊断信息。')
            if reset:QMessageBox.information(self,'提示',f'项目 {project} 的统计已清零，其他项目不受影响，历史测试记录保留。')
        self._stats_task.run(read,completed)

    def show_project_statistics(self):
        detail=self.production_statistics.details()
        QMessageBox.information(self,'项目统计详情',f'项目：{self._statistics_project() or "未选择"}\n'
            f'自上次清零以来累计：{detail["products"]}个产品，{detail["attempts"]}次测试，复测{detail["retests"]}次\n'
            f'首次通过率：{detail["first_pass_rate"]:.2f}%\n不按日期或班次重置；只计已完成的PASS/NG/FAIL。')

    def _run_busy(self):
        return (self.is_testing or self.preparing or self._stats_reset_busy
                or self._ng_alarm_box is not None or bool(getattr(self.engine, 'workers', {})))

    def set_generic_mes_enabled(self, enabled):
        self.ruineng_panel.setVisible(enabled is True)
        self._clear_mes_info()

    def _clear_mes_info(self):
        for field in self.mes_info_fields.values():
            field.setText('—')
            field.setToolTip('')

    def _show_mes_query_result(self, snapshot):
        result = snapshot.get('_generic_mes_result') or {}
        response = result.get('response')
        data = response.get('data') if isinstance(response, dict) else None
        if result:
            self.lbl_mes_upload_status.setText('查询通过' if result.get('passed') else '查询未通过')
            self.lbl_mes_upload_status.setToolTip(result.get('message', ''))
        if isinstance(data, dict) and str(data.get('uniqueId', '')).strip() == result.get('sn'):
            for key, field in self.mes_info_fields.items():
                field.setText(str(data.get(key, '—')))
                field.setToolTip(field.text())

    def enqueue_mes_upload(self, path):
        if path and path != self._mes_upload_active_path and path not in self._mes_upload_queue:
            self._mes_upload_queue.append(str(path))
        self._start_next_mes_upload()

    @Slot()
    def _start_next_mes_upload(self):
        if self._closing or self._mes_upload_task.isRunning() or not self._mes_upload_queue:
            return
        from core.mes_outbox import send_job
        path = self._mes_upload_queue.pop(0)
        self._mes_upload_active_path = path
        self.lbl_mes_upload_status.setText('MES上传中（不影响检测判定）')
        def completed(ok, value):
            self._mes_upload_active_path = None
            if self._closing:
                return
            passed = ok and value['passed']
            reason = value['message'] if ok else str(value)
            self.lbl_mes_upload_status.setText('MES上传成功' if passed else 'MES上传失败，待补传')
            self._log_test_event(f'[锐能MES上传] {"成功" if passed else "失败"} | {reason} | 文件：{path}')
            if not passed:
                self._show_mes_upload_error(reason, path)
        self._mes_upload_task.run(lambda: send_job(path, lambda: self._closing), completed)

    def _show_mes_upload_error(self, reason, path=None):
        detail = (f'原始数据已保留，可在软件设置中补传。\n文件：{path}' if path else
                  '数据文件未能保存，请检查磁盘空间及目录权限；无法保证重启后补传。')
        box = QMessageBox(QMessageBox.Warning, '锐能MES上传失败',
            f'{reason}\n\n检测PASS/FAIL保持不变。{detail}', QMessageBox.Ok, self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setModal(False)
        box.show()
        self._mes_upload_error_box = box

    def retry_pending_mes_uploads(self):
        from core.mes_outbox import pending_jobs
        if self._run_busy() or self._scan_pending or self._closing:
            QMessageBox.warning(self, '暂不能补传', '请先结束扫码、测试或等待到位。')
            return
        paths = pending_jobs(getattr(self.db_manager, 'db_path', None))
        if not paths:
            QMessageBox.information(self, '锐能MES补传', '没有待补传文件。')
            return
        for path in paths:
            self.enqueue_mes_upload(str(path))

    def _on_run_retired(self, channel):
        if channel != self.channel_id:
            return
        self.combo_recipe.setEnabled(not self._closing)
        self.run_activity_changed.emit(False)
        if self.lbl_big_status.text() == 'STOPPING':
            self.lbl_big_status.setText('STOPPED')

    def _on_cleanup_failed(self, channel, error):
        if channel == self.channel_id:
            self.lbl_big_status.setText('STOP ERROR')
            self.lbl_big_status.setToolTip(error)

    def begin_shutdown(self):
        self._closing = True
        self._column_fit_timer.stop()
        self._flash_render_timer.stop()
        self.step_tree.viewport().removeEventFilter(self)
        self.step_tree.stop_auto_scroll()
        self._waiting_board=None;self._arrival_continue=None
        if hasattr(self,'_sg_display_timer'):self._sg_display_timer.stop()
        if hasattr(self,'flash_dialog'):self.flash_dialog.hide()
        self._stats_timer.stop()
        self._stats_task.begin_shutdown()
        self._prepare_cancel.set()
        self._abort_prepared_record()
        self.stop_monitor()
        self._prepare_task.begin_shutdown()
        self._plc_task.begin_shutdown()
        self._recipe_match_task.begin_shutdown()
        self._mes_upload_task.begin_shutdown()

    def closeEvent(self, event):
        if not self._closing:
            self.begin_shutdown()
        # DeviceTask runtimes retain live workers independently of the page.
        # WA_DeleteOnClose posts GUI-owned deletion after this event returns.
        super().closeEvent(event)
        if event.isAccepted():
            app = QCoreApplication.instance()
            pages = getattr(app, '_retiring_overview_pages', None)
            if pages is None:
                pages = app._retiring_overview_pages = {}
            key = id(self)
            if key not in pages:
                # Keep the wrapper reachable until DeferredDelete is actually
                # handled, even when a caller only pumps processEvents().
                pages[key] = self
                self.destroyed.connect(lambda: pages.pop(key, None))

    def _on_timer_tick(self):
        self.elapsed_seconds += 1
        mins = self.elapsed_seconds // 60
        secs = self.elapsed_seconds % 60
        self.lbl_timer.setText(f"{mins:02d}:{secs:02d}")

    def _create_kpi_card(self, title: str, val_text: str, val_color: str, width: int = 110, height: int = 56) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setMinimumWidth(width)
        card.setFixedHeight(height)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.setStyleSheet("""
            QFrame {
                background-color: #16213E;
                border: 1px solid #2D3748;
                border-radius: 6px;
                padding: 0px;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(6, 4, 6, 4)
        c_layout.setSpacing(1)
        c_layout.setAlignment(Qt.AlignCenter)

        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500; border: none; padding: 0px; background: transparent;")

        lbl_val = QLabel(val_text)
        lbl_val.setAlignment(Qt.AlignCenter)
        lbl_val.setStyleSheet(f"color: {val_color}; font-size: 20px; font-weight: bold; border: none; padding: 0px; background: transparent; font-family: 'Segoe UI', Arial, sans-serif;")

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

        self.btn_select_recipe=QPushButton('🎯 选择测试配方')
        self.btn_select_recipe.clicked.connect(self.select_test_recipe)
        top_title_layout.addWidget(self.btn_select_recipe)
        self.combo_recipe = RecipeSelectionCombo(self.top_title_frame)
        self.combo_recipe.setPlaceholderText('请点击左侧按钮扫码选择配方')
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
        self.step_tree = TestSequenceTree(self)
        self.step_tree.setItemDelegateForColumn(6, TestStatusDelegate(self.step_tree))
        self.step_tree.setHeaderLabels(["序号", "名称", "下限", "上限", "测试值", "单位", "判定"])
        self.step_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.step_tree.setToolTip('Shift 连续选中多行，Ctrl 追加或取消选中；右键可批量勾选选中的测试项。')
        self.step_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.step_tree.customContextMenuRequested.connect(self._test_context_menu)

        # 禁用拖拽与直接双击编辑，确保测试运行主界面只读
        self.step_tree.setDragEnabled(False)
        self.step_tree.setAcceptDrops(False)
        self.step_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)

        header = self.step_tree.header()
        header.setMinimumSectionSize(1)
        header.setStretchLastSection(False)
        # 列宽仅依据顶层测试项，展开子工步不会挤占上下限和测试值。
        header.setSectionResizeMode(QHeaderView.Fixed)

        self.step_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.step_tree.setTextElideMode(Qt.ElideRight)
        self.step_tree.viewport().installEventFilter(self)
        self.step_tree.setMinimumWidth(0)
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

        # ====== 右侧测试状态与控制面板 ======
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
        right_layout.setSpacing(8)

        # 配方选择器移至右侧状态面板顶部，避免占用测试序列表格上方空间
        top_title_layout.setContentsMargins(6, 2, 6, 2)
        self.combo_recipe.setMinimumWidth(0)
        right_layout.addWidget(self.top_title_frame)

        # 1. 配方/产品型号卡片
        from ui.widgets.eliding_label import ElidingLabel
        self.lbl_product_model = ElidingLabel("1 - AT-SR3.5X(PAT35X-)")
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
                font-size: 38px;
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
                font-size: 34px;
                font-weight: bold;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        self.lbl_big_status.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_big_status)

        # READY下方的良率统计，四张卡片等宽填充右侧面板。
        self.card_stat_total, self.lbl_stat_total_val = self._create_kpi_card("生产总数", "0", "#00E5FF", 76, 64)
        self.card_stat_pass, self.lbl_stat_pass_val = self._create_kpi_card("合格数", "0", "#00FF66", 76, 64)
        self.card_stat_fail, self.lbl_stat_fail_val = self._create_kpi_card("不合格", "0", "#FF3366", 76, 64)
        self.card_stat_rate, self.lbl_stat_rate_val = self._create_kpi_card("合格率", "0.00%", "#FFD700", 76, 64)

        # 兼顾代码向后兼容性
        self.lbl_stat_total = self.lbl_stat_total_val
        self.lbl_stat_pass = self.lbl_stat_pass_val
        self.lbl_stat_fail = self.lbl_stat_fail_val
        self.lbl_stat_rate = self.lbl_stat_rate_val

        self.yield_stats_panel = QWidget(self.right_panel)
        yield_layout = QHBoxLayout(self.yield_stats_panel)
        yield_layout.setContentsMargins(0, 0, 0, 0)
        yield_layout.setSpacing(6)
        for card in (self.card_stat_total, self.card_stat_pass, self.card_stat_fail, self.card_stat_rate):
            yield_layout.addWidget(card, 1)
        right_layout.addWidget(self.yield_stats_panel)

        right_layout.addStretch()

        self.ruineng_panel = QFrame()
        self.ruineng_panel.setObjectName('ruineng_mes_info')
        self.ruineng_panel.setStyleSheet('''
            QFrame#ruineng_mes_info { background: #16213E; border: 1px solid #334155; border-radius: 5px; padding: 0; }
            QFrame#ruineng_mes_info QLabel { background: transparent; border: none; border-radius: 0; padding: 0; font-size: 12px; }
            QFrame#ruineng_mes_info QLineEdit { min-height: 20px; padding: 2px 4px; font-size: 12px; }
        ''')
        mes_grid = QGridLayout(self.ruineng_panel)
        mes_grid.setContentsMargins(8, 6, 8, 6)
        mes_grid.setHorizontalSpacing(6)
        title = QLabel('锐能MES')
        self.lbl_mes_upload_status = QLabel('等待扫码查询')
        self.lbl_mes_upload_status.setWordWrap(True)
        mes_grid.addWidget(title, 0, 0)
        mes_grid.addWidget(self.lbl_mes_upload_status, 0, 1, 1, 3)
        self.mes_info_fields = {}
        for index, (key, label) in enumerate((('checkTimes', '检测次数'), ('orderId', '工单号'),
                                               ('totalNum', '计划数量'), ('procNum', '已加工数量'))):
            row, column = index // 2 + 1, (index % 2) * 2
            field = QLineEdit('—')
            field.setReadOnly(True)
            field.setMinimumWidth(0)
            self.mes_info_fields[key] = field
            mes_grid.addWidget(QLabel(label), row, column)
            mes_grid.addWidget(field, row, column + 1)
        mes_grid.setColumnStretch(1, 1)
        mes_grid.setColumnStretch(3, 1)
        right_layout.addWidget(self.ruineng_panel)

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
        self.txt_sn.textChanged.connect(lambda _: self._clear_mes_info())
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
        # 3. 底部测试记录栏 (逐步骤滚动显示；刷写进度在独立弹窗中监控)
        # -------------------------------------------------------------
        # 刷写/下载监控弹窗 (APP刷写 / 力捷丰SG400 / 瑞萨E2)：刷写开始时自动弹出
        self.flash_dialog = FlashStatusDialog(self)
        self.flash_dialog.finished.connect(self._on_flash_dialog_closed)
        self._flash_dialog_suppressed = False

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

        # 4. 测试执行记录 (滚动显示每一步在干什么)
        self.test_log_view = QPlainTextEdit()
        self.test_log_view.setReadOnly(True)
        self.test_log_view.setFixedHeight(72)
        self.test_log_view.setPlaceholderText("测试记录：开始测试后按步骤滚动显示执行过程")
        self.test_log_view.setStyleSheet("""
            QPlainTextEdit {
                background: #16213E;
                color: #CBD5E0;
                border: 1px solid #2D3748;
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                padding: 4px;
            }
        """)
        stats_layout.addWidget(self.test_log_view, 1)

        main_layout.addWidget(self.stats_frame)

    def _log_test_event(self, message):
        """向底部测试记录栏追加一条带时间戳的记录。"""
        if not hasattr(self, 'test_log_view'):
            return
        stamp = time.strftime("%H:%M:%S")
        for line in str(message).splitlines() or [""]:
            self.test_log_view.appendPlainText(f"[{stamp}] {line}")

    @Slot(int, str)
    def on_worker_log_message(self, channel_id, message):
        if channel_id==self.channel_id and not self._closing:
            self._log_test_event(message)

    def _on_flash_dialog_closed(self, *_args):
        """刷写进行中操作员主动关闭弹窗后，本轮不再自动弹出。"""
        if self._flash_state.get("active"):
            self._flash_dialog_suppressed = True

    def _reset_flash_status(self):
        self._flash_ignore_before = time.monotonic()
        self._flash_started_at = 0.0
        self._flash_state = {}
        self._flash_dialog_suppressed = False
        self._flash_logged_result = None
        if hasattr(self, 'flash_dialog'):
            self.flash_dialog.hide()
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
        if self._closing or channel_id != self.channel_id:
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
        # 刷写开始：自动弹出监控窗口（操作员本轮主动关闭过则不再弹出）
        if status.get('active') and not self._flash_dialog_suppressed and not self.flash_dialog.isVisible():
            self.flash_dialog.show()
            self.flash_dialog.raise_()
        # 终态写入测试记录（同一结果只记一次）
        result = status.get('result')
        result_key=(started,status.get('kind'),status.get('project'),result)
        if result and result_key != getattr(self, '_flash_logged_result', None):
            self._flash_logged_result = result_key
            kind = status.get('kind') or 'APP刷写'
            detail = status.get('detail') or status.get('phase') or ''
            self._log_test_event(f"刷写[{kind}] 结束: {result}" + (f" | {detail}" if detail else ""))
        self._render_flash_status()

    @Slot()
    def _render_flash_status(self):
        """渲染最新的刷写状态到监控弹窗。"""
        if self._closing:
            return
        if hasattr(self, 'flash_dialog'):
            self.flash_dialog.render_state(self._flash_state)
            if not self._flash_state.get('active') or self._flash_state.get('result'):
                # hide() does not emit finished: an automatic close must not
                # suppress the popup for a later programming step.
                self.flash_dialog.hide()

    @Slot()
    def _fit_test_columns(self):
        """七列按指定比例铺满视口，始终不显示水平滚动条。"""
        if self._closing:
            return
        tree = self.step_tree
        tree.ensurePolished()
        # Exact requested proportions: 1 : 3 : 2 : 2 : 2 : 1 : 1.5.
        available = max(7, tree.viewport().width())
        weights = [2, 6, 4, 4, 4, 2, 3]
        total = sum(weights)
        fitted = [available * weight // total for weight in weights]
        remainder = available - sum(fitted)
        order = sorted(range(7), key=lambda col: -(available * weights[col] % total))
        for col in order[:remainder]:
            fitted[col] += 1
        for col, width in enumerate(fitted):
            if tree.columnWidth(col) != width:
                tree.setColumnWidth(col, width)

    def _update_test_row_tooltips(self, item, columns=range(7)):
        """只更新变化行的提示；子工步名称保留设备及参数说明。"""
        for col in columns:
            if item.parent() is not None and col == 1:
                continue
            text = item.text(col)
            if item.toolTip(col) != text:
                item.setToolTip(col, text)

    def eventFilter(self,watched,event):
        from PySide6.QtCore import QEvent
        if not self._closing and hasattr(self,'step_tree') and watched is self.step_tree.viewport() and event.type()==QEvent.Resize:
            self._column_fit_timer.start(0)
        return super().eventFilter(watched,event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._closing:
            return
        if hasattr(self, "step_tree"):
            self._column_fit_timer.start(0)
        if hasattr(self, "flash_dialog"):
            self._flash_render_timer.start(0)

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
            from core.recipe_matching import barcode_rule_matches
            if barcode_rule_matches(sn_rule, sn_text):
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

    # -----------------------------------------------------------------
    # 配方加载与只读树结构展示
    # -----------------------------------------------------------------
    def load_default_or_first_recipe(self):
        """启动时只刷新候选配方，等待手动选择或扫码匹配。"""
        recipe_dir = None
        if self.db_manager and hasattr(self.db_manager, "get_recipe_dir"):
            recipe_dir = self.db_manager.get_recipe_dir()
        if not recipe_dir or not os.path.exists(recipe_dir):
            recipe_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "recipes")

        files = [f for f in os.listdir(recipe_dir) if f.endswith(".json")] if os.path.exists(recipe_dir) else []
        self.update_recipes(files)

    def update_recipes(self, recipes_list: List[str], preserve_results: bool = False):
        if self._run_busy():
            return
        """供 MainWindow 调用的接口，刷新配方下拉框"""
        try:
            current_selection = self.combo_recipe.currentText()
            keep_loaded = preserve_results and bool(self.current_recipe_data)
            current_file = os.path.basename(self.current_recipe_path) if keep_loaded else ''
            self.combo_recipe.blockSignals(True)
            self.combo_recipe.clear()
            for r in recipes_list:
                clean_name = r.replace(".json", "")
                filename = r if r.endswith(".json") else f"{r}.json"
                self.combo_recipe.addItem(clean_name, filename)
            idx = self.combo_recipe.findText(current_selection) if self.current_recipe_name and current_selection else -1
            self.combo_recipe.setCurrentIndex(idx)
            self.combo_recipe.blockSignals(False)
            self.combo_recipe.setEnabled(not self._run_busy() and not self._closing)
            if keep_loaded:
                # Returning to this page only refreshes the project selector.
                # The displayed results belong to the last run, even if its file
                # was edited or removed while another tab was open.
                self.combo_recipe.blockSignals(True)
                self.combo_recipe.setCurrentIndex(self.combo_recipe.findData(current_file))
                self.combo_recipe.blockSignals(False)
                self._refresh_recipe_on_next_test = True
                return
            # 列表刷新属于程序动作，只刷新界面，不写 PLC。
            index = self.combo_recipe.currentIndex()
            if index >= 0 and self._can_debug_tests():
                self.on_recipe_combo_changed(index, sync_plc=False)
            elif index<0 or not self.current_recipe_name:
                self._clear_recipe_state()
        except (RuntimeError, AttributeError) as e:
            import logging
            logging.warning(f"OverviewTab.update_recipes 忽略控件状态异常: {e}")

    def on_recipe_combo_changed(self, index: int, sync_plc: bool = True):
        if self._run_busy() or not self._can_debug_tests():
            return
        """切换配方。只有用户触发的下拉选择才同步 PLC。"""
        try:
            if not 0<=index<self.combo_recipe.count():return
            recipe_filename = self.combo_recipe.itemData(index)
            if not recipe_filename:
                recipe_filename = self.combo_recipe.itemText(index)
            if not recipe_filename:
                return

            if not recipe_filename.endswith(".json"):
                recipe_filename += ".json"
        except (RuntimeError, AttributeError) as e:
            import logging
            logging.warning(f"OverviewTab.on_recipe_combo_changed 忽略控件状态异常: {e}")
            return

        if self.db_manager and hasattr(self.db_manager, "get_recipe_dir"):
            file_path = os.path.join(self.db_manager.get_recipe_dir(), recipe_filename)
        else:
            recipes_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "recipes")
            file_path = os.path.join(recipes_dir, recipe_filename)

        self.load_recipe_from_file(file_path, sync_plc=sync_plc)

    def _clear_recipe_state(self):
        self._refresh_recipe_on_next_test = False
        self.current_recipe_path = ''
        self.current_recipe_name = ''
        self.current_recipe_data = {}
        self._confirmed_barcode = ''
        self.step_tree.clear()
        self.lbl_product_model.setText('等待选择测试配方')
        self._reset_flash_status()
        self.production_statistics=ProductionStatistics();self._recalculate_statistics()
        self.refresh_statistics(reason='project_changed')

    def load_recipe_from_file(self, file_path: str, sync_plc: bool = False):
        if self._run_busy():
            return False
        """解析 JSON 配方文件并生成 7 列只读树状节点"""
        self._clear_recipe_state()
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = dict(data, items=single_channel_items(data.get("items", [])))

            self.current_recipe_path = file_path
            self.current_recipe_name = data.get("name", os.path.basename(file_path))
            self.current_recipe_data = data
            self.lbl_product_model.setText(f"1 - {self.current_recipe_name}")
            self.step_tree.clear()

            # Keep the saved recipe intact, but display and execute the same
            # active projection so worker row indexes stay aligned.
            items = []
            for saved_item in data.get("items", []):
                if str(saved_item.get("name", "")).strip().startswith("#"):
                    continue
                item = copy.deepcopy(saved_item)
                item["sub_steps"] = [sub for sub in item.get("sub_steps", [])
                                     if not str(sub.get("name", "")).strip().startswith("#")]
                items.append(item)
            for idx, item in enumerate(items, 1):
                item_node = QTreeWidgetItem(self.step_tree)
                item_node.setText(0, str(idx))                             # 序号
                item_node.setFlags(item_node.flags() | Qt.ItemIsUserCheckable)
                item_node.setCheckState(0, Qt.Unchecked)
                item_node.setText(1, item.get("name", "未命名测试项"))      # 名称
                item_node.setText(2, str(item.get("min", "--")))          # 下限
                if item.get('standard_type') == '变量':
                    item_node.setText(2, '待获取变量值')
                    item_node.setToolTip(2, f"期望值取自变量：{item.get('min', '')}；执行判定时显示实际键值")
                item_node.setText(3, str(item.get("max", "--")))          # 上限
                item_node.setText(4, "--")                                 # 测试值
                item_node.setText(5, item.get("unit", "NULL"))             # 单位
                item_node.setText(6, "待测试")                             # 判定
                item_node.setData(0, Qt.UserRole, {"type": "item_node", "raw_data": item})
                self._update_test_row_tooltips(item_node)

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
                    self._update_test_row_tooltips(sub_node)

            self.step_tree.collapseAll()
            self._fit_test_columns()
            self._reset_flash_status()
            self.refresh_statistics(reason='project_changed')

            if sync_plc:
                # 仅当操作员在下拉框中主动选择配方时下发并回读校验。
                recipe_number = int(data.get("recipe_number", 0))
                self.sync_recipe_number_to_plc(recipe_number)
                self.apply_selected_recipe_can(data)
            return True
        except Exception as e:
            self._clear_recipe_state()
            QMessageBox.warning(self, "错误", f"加载配方文件失败: {str(e)}")
            return False

    def sync_recipe_number_to_plc(self, recipe_number: int):
        self._pending_plc_recipe = int(recipe_number)
        self._plc_config_error = ""
        self._drain_plc_recipe()

    def _plc_enabled(self):
        manager = getattr(self.engine, 'device_manager', None)
        enabled = getattr(manager, 'is_enabled', None)
        return bool(enabled('easy320')) if callable(enabled) else True

    def apply_selected_recipe_can(self, recipe):
        """选择型号后按配方使能重启上位机、同步 INI，并应用 CAN 参数。"""
        if self._closing or self._prepare_task.isRunning():
            return False
        snapshot = copy.deepcopy(recipe)
        self._recipe_can_error = ''
        self.preparing = True
        self._prepare_cancel.clear()
        self.combo_recipe.setEnabled(False)
        self.lbl_big_status.setText('CONFIGURING')
        self.run_activity_changed.emit(True)
        def completed(ok, result):
            self.preparing = False
            self.combo_recipe.setEnabled(True)
            if self._closing or self._prepare_cancel.is_set():
                self._recipe_can_error = '配方参数应用已取消，请重新选择型号。'
            else:
                if '_synced_app_flash' in snapshot:
                    self._selected_app_flash_config = snapshot['_synced_app_flash']
                success = ok and result[0]
                self._recipe_can_error = '' if success else (str(result[1]) if ok else str(result))
                self.lbl_big_status.setText('CONFIG ERROR' if self._recipe_can_error else 'READY')
                self.lbl_big_status.setToolTip(self._recipe_can_error or str(result[1]))
                self._log_test_event('配方参数应用失败：' + self._recipe_can_error if self._recipe_can_error else '配方参数已应用：' + str(result[1]))
                if self._recipe_can_error:
                    QMessageBox.warning(self, '配方参数应用失败', self._recipe_can_error)
            self.run_activity_changed.emit(False)
        def apply_selection():
            if self._prepare_cancel.is_set():return False, '已取消'
            try:
                from core.recipe_app_flash import prepare_app_flash
                snapshot['_synced_app_flash'] = prepare_app_flash(
                    snapshot.get('app_flash'), self.preparation_log.emit, self._prepare_cancel.is_set)
            except Exception as exc:
                return False, f'APP刷写参数同步失败：{exc}'
            if self._prepare_cancel.is_set():return False, '已取消'
            return self._apply_recipe_hardware(snapshot)
        accepted = self._prepare_task.run(apply_selection, completed)
        if not accepted:
            self.preparing = False
            self.combo_recipe.setEnabled(True)
            self._recipe_can_error = '配方参数应用任务未启动，请重新选择型号。'
            self.run_activity_changed.emit(False)
        return accepted

    def _drain_plc_recipe(self):
        if self._closing:
            return
        if not self._plc_enabled():
            self._pending_plc_recipe = None
            self._plc_config_error = ''
            return
        if self._pending_plc_recipe is None:
            return
        if self._plc_task.isRunning():
            QTimer.singleShot(50, self._drain_plc_recipe)
            return
        number = self._pending_plc_recipe
        manager = getattr(self.engine, 'device_manager', None)
        plc = getattr(manager, 'easy320', None)
        def write_recipe():
            if plc is None:
                raise RuntimeError('PLC is not configured')
            if not plc.is_connected and not plc.connect():
                raise RuntimeError('PLC connection failed')
            if not plc.write_register(0, number, verify=False):
                raise RuntimeError('PLC recipe write failed')
            if plc.read_register(address=0, count=1) != [number]:
                raise RuntimeError('PLC recipe confirmation failed')
            return number
        def completed(ok, result):
            if not self._plc_enabled():
                self._pending_plc_recipe = None
                self._plc_config_error = ''
                return
            if self._pending_plc_recipe == number:
                self._pending_plc_recipe = None
                self._plc_config_error = '' if ok else str(result)
            if not ok:
                self.lbl_big_status.setText('PLC ERROR')
                self.lbl_big_status.setToolTip(str(result))
            QTimer.singleShot(0, self._drain_plc_recipe)
        self._plc_task.run(write_recipe, completed)

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
            "project_no": self.current_recipe_data.get('project_no',''),
            "model_name": self.current_recipe_data.get('model_name',''),
            "dbc": copy.deepcopy(self.current_recipe_data.get('dbc')),
            "app_flash": copy.deepcopy(self.current_recipe_data.get('app_flash')),
            "plc_recipe_register": 0,
            "can_config": self.current_recipe_data.get("can_config"),
            "items": items_list
        }

    # -----------------------------------------------------------------
    # 单通道测试引擎控制与运行联动
    # -----------------------------------------------------------------
    def request_scan_and_start(self, scan_only=False):
        """弹出扫码窗口；只有确认取得有效条码后才允许启动测试。"""
        if self._closing or self._scan_pending:
            return False
        if self._run_busy():
            QMessageBox.information(self, "提示", "当前测试正在运行，请勿重复启动。")
            return False

        self._scan_pending = True
        try:
            scanner=getattr(self,'serial_scanner',None)
            if scanner is not None and scanner.config.get('scanner_enabled',False):
                if not scanner.port.isOpen():
                    QMessageBox.warning(self,'扫码器未打开','请先在硬件管理中心连接扫码器，检查COM5及串口参数。');return False
                dialog=QInputDialog(self)
                dialog.setWindowTitle('请扫码')
                dialog.setLabelText('请扫描产品SN（串口扫码结果自动提交）：')
                dialog.setInputMode(QInputDialog.TextInput)
                def receive_barcode(value):
                    if not self._closing and not self._run_busy():
                        dialog.setTextValue(value);dialog.accept()
                scanner.barcode.connect(receive_barcode)
                try:
                    if not scanner.begin_scan(dialog):
                        QMessageBox.warning(self,'扫码器忙','请先结束扫码器调试任务。');return False
                    accepted=bool(dialog.exec());barcode=dialog.textValue()
                finally:
                    scanner.end_scan(dialog);scanner.barcode.disconnect(receive_barcode);dialog.deleteLater()
            else:
                barcode, accepted = QInputDialog.getText(
                self,
                "请扫码",
                "请使用扫码枪扫描产品 SN（扫码后按回车）：",
                QLineEdit.Normal,
                "",
            )
        finally:
            self._scan_pending = False
        if self._closing or self._run_busy():
            return False
        barcode = barcode.strip()
        if not accepted:
            return False
        if not barcode:
            QMessageBox.warning(self, "扫码失败", "未读取到产品 SN，不能开始测试。")
            return False

        if scan_only:return barcode

        self._confirmed_barcode = barcode
        self.txt_sn.setText(barcode)
        return self.start_single_channel_test()

    def set_recipe_selection_role(self,username,is_admin):
        identity=(username,bool(is_admin))
        changed=getattr(self,'_recipe_selection_identity',None)!=identity
        self._recipe_selection_identity=identity
        self._manual_recipe_allowed=bool(is_admin)
        if changed:self._recipe_selection_reset_pending=not is_admin
        if getattr(self,'_recipe_selection_reset_pending',False) and not self._run_busy():
            self._clear_recipe_state();self.combo_recipe.setCurrentIndex(-1);self.txt_sn.clear()
            self._recipe_selection_reset_pending=False
        self.combo_recipe.setEnabled(not self._run_busy() and not self._closing)
        self.btn_select_recipe.setEnabled(not self._run_busy() and not self._scan_pending and not self._closing)
        self.btn_select_recipe.setToolTip('手动选择测试配方' if is_admin else '启动扫码器，按条码规则识别配方')

    def select_test_recipe(self):
        if self._closing or self._run_busy() or self._scan_pending:return False
        if self._can_debug_tests():
            self.combo_recipe.showPopup();return True
        self._clear_recipe_state();self.combo_recipe.setCurrentIndex(-1);self.txt_sn.clear()
        barcode=self.request_scan_and_start(scan_only=True)
        if not barcode:return False
        from core.recipe_matching import match_recipe_files
        root=self.db_manager.get_recipe_dir()
        entries=tuple((i,self.combo_recipe.itemData(i)) for i in range(self.combo_recipe.count()))
        identity=getattr(self,'_recipe_selection_identity',None)
        self._scan_pending=True;self.btn_select_recipe.setEnabled(False);self.btn_select_recipe.setText('正在匹配配方…')
        task=self._recipe_match_task
        def completed(ok,result):
            self._scan_pending=False;self.btn_select_recipe.setText('🎯 选择测试配方')
            self.btn_select_recipe.setEnabled(not self._closing and not self._run_busy())
            if self._closing or self._run_busy() or identity!=getattr(self,'_recipe_selection_identity',None):return
            if entries!=tuple((i,self.combo_recipe.itemData(i)) for i in range(self.combo_recipe.count())):
                QMessageBox.warning(self,'配方列表已变化','匹配期间配方列表已变化，请重新扫码。');return
            if not ok:
                QMessageBox.warning(self,'配方规则检查失败',str(result));return
            self._apply_recipe_match_result(result)
        if not task.run(lambda:match_recipe_files(root,entries,barcode,cancelled=task.thread.isInterruptionRequested),completed):
            self._scan_pending=False;self.btn_select_recipe.setEnabled(True);self.btn_select_recipe.setText('🎯 选择测试配方');return False
        return True

    def _apply_recipe_match_result(self,result):
        matches,errors=result
        if errors:
            QMessageBox.warning(self,'配方规则检查失败','以下配方无法检查，为避免错误匹配，本次不加载：\n'+'\n'.join(errors));return False
        if len(matches)>1:
            QMessageBox.warning(self,'条码匹配多个配方','本次不加载配方，请联系管理员处理重复规则：\n'+'\n'.join(f'{name}\n规则：{rule}' for _,_,name,rule,_ in matches));return False
        if not matches:
            QMessageBox.warning(self,'未匹配到配方','该条码没有匹配的配方，请联系管理员检查条码规则。');return False
        index,path,_,_,checksum=matches[0]
        import hashlib
        try:
            with open(path,'rb') as stream:current=hashlib.sha256(stream.read()).hexdigest()
            if current!=checksum:raise ValueError('匹配期间配方已修改，请重新扫码')
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'配方已变化',str(exc));return False
        if not self.load_recipe_from_file(path,sync_plc=True):return False
        self.combo_recipe.setCurrentIndex(index)
        self._confirmed_barcode='';self.txt_sn.clear()
        return True

    def _can_debug_tests(self):
        from core.user_session import get_current_user
        return bool(self.db_manager and self.db_manager.get_user_role(get_current_user()) == 'admin')

    def _test_context_menu(self, pos):
        if self._run_busy() or not self._can_debug_tests():
            return
        from ui.widgets.sequence_context_menu import SequenceContextMenu
        self.step_tree.stop_auto_scroll()
        menu = SequenceContextMenu(self)
        selected = self._selected_debug_item_indices()
        for text,mode in [('选择','selected'),('取消选择','selected_none')]:
            action=menu.addAction(text,lambda checked=False,mode=mode:self._select_debug_items(mode))
            action.setEnabled(bool(selected))
        menu.addSeparator()
        checked = [i for i in range(self.step_tree.topLevelItemCount())
                   if self.step_tree.topLevelItem(i).checkState(0) == Qt.Checked]
        action = menu.addAction('勾选测试', lambda: self._debug_test_items(checked))
        action.setEnabled(bool(checked))
        menu.addSeparator()
        for text, mode in [('全选', 'all'), ('全不选', 'none'), ('反选', 'invert'), ('只选失败项', 'failed')]:
            menu.addAction(text, lambda checked=False, mode=mode: self._select_debug_items(mode))
        menu.addSeparator()
        menu.addAction('展开全部', self.step_tree.expandAll)
        menu.addAction('折叠全部', self.step_tree.collapseAll)
        menu.popup(self.step_tree.viewport().mapToGlobal(pos))

    def _debug_item_executable(self, node):
        data = node.data(0, Qt.UserRole) or {}
        raw = data.get('raw_data', {})
        return not str(raw.get('name', node.text(1))).strip().startswith('#') and not raw.get('skip_runtime', False)

    def _selected_debug_item_indices(self):
        indices=set()
        for node in self.step_tree.selectedItems():
            while node.parent() is not None:node=node.parent()
            index=self.step_tree.indexOfTopLevelItem(node)
            if index>=0:indices.add(index)
        return sorted(indices)

    def _select_debug_items(self, mode):
        if self._run_busy() or not self._can_debug_tests():
            return
        if mode not in ('all', 'none', 'invert', 'failed', 'selected', 'selected_none'):
            raise ValueError('未知调试选择操作')
        indices=(self._selected_debug_item_indices() if mode in ('selected','selected_none')
                 else range(self.step_tree.topLevelItemCount()))
        for index in indices:
            node = self.step_tree.topLevelItem(index)
            selected = (mode in ('all','selected') or (mode == 'invert' and node.checkState(0) != Qt.Checked)
                        or (mode == 'failed' and node.text(6) == 'FAIL'))
            node.setCheckState(0, Qt.Checked if selected and self._debug_item_executable(node) else Qt.Unchecked)

    def _debug_test_items(self, indices):
        if self._run_busy() or not self._can_debug_tests():
            return False
        if not indices:
            return False
        self._debug_requested_indices = set(indices)
        try:
            return self.start_single_channel_test()
        finally:
            self._debug_requested_indices = None

    def _refresh_recipe_for_new_test(self, debug_run=False):
        if not getattr(self, '_refresh_recipe_on_next_test', False):
            return True
        path = self.current_recipe_path
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                data = json.load(stream)
            data = dict(data, items=single_channel_items(data.get('items', [])))
            new_number = int(data.get('recipe_number', 0))
            old_number = int(self.current_recipe_data.get('recipe_number', 0))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            QMessageBox.warning(self, '配方无法加载', f'上次结果已保留，本次未开始测试。请检查或重新选择配方：\n{exc}')
            return False
        if data == self.current_recipe_data:
            self._refresh_recipe_on_next_test = False
            return True
        barcode, confirmed = self.txt_sn.text(), self._confirmed_barcode
        if not self.load_recipe_from_file(path, sync_plc=False):
            return False
        self.txt_sn.setText(barcode)
        self._confirmed_barcode = confirmed
        if debug_run:
            QMessageBox.information(self, '配方已更新', '配方内容已修改，已载入最新配置。请重新勾选要调试的测试项，避免按旧序号执行。')
            return False
        if new_number != old_number:
            self.sync_recipe_number_to_plc(new_number)
        return True

    def start_single_channel_test(self):
        """启动单通道测试序列"""
        if self._closing or self._run_busy():
            return False
        if not self.engine:
            QMessageBox.warning(self, "错误", "底层测试引擎 TestEngine 未连接!")
            return False

        debug_run = self._debug_requested_indices is not None
        if debug_run and not self._can_debug_tests():
            return False
        if not self._refresh_recipe_for_new_test(debug_run):
            return False
        sn_barcode = self.txt_sn.text().strip()
        if not debug_run and (not sn_barcode or sn_barcode != self._confirmed_barcode):
            QMessageBox.warning(self, "请先扫码", "必须先在扫码弹窗中扫描产品 SN，才能开始测试。")
            return False

        sn_rule = str(self.current_recipe_data.get("sn_regex", "")).strip()
        if sn_rule and not debug_run:
            try:
                from core.recipe_matching import barcode_rule_matches
                if not barcode_rule_matches(sn_rule, sn_barcode):
                    self.lbl_big_status.setText("SN ERROR")
                    self.lbl_big_status.setStyleSheet("background-color: #DC3545; color: white; font-size: 34px; font-weight: bold; border-radius: 6px; padding: 15px;")
                    QMessageBox.warning(self, "AI 条码规则拦截", f"❌ 输入的 SN 条码【{sn_barcode}】不符合当前配方的 AI 规则！\n\n当前配方规则: {sn_rule}\n请核对并重新扫描正确的条码！")
                    self._confirmed_barcode = ""
                    return False
            except Exception as exc:
                QMessageBox.warning(self, "条码规则错误", f"当前配方的 SN 正则表达式无效：{exc}")
                return False

        recipe_dict = self.serialize_tree_to_dict()
        items = recipe_dict.get("items", [])
        if not items or not any(not str(item.get('name', '')).strip().startswith('#')
                                and not item.get('skip_runtime', False) for item in items):
            QMessageBox.warning(self, "警告", "当前测试序列为空!")
            return False

        if not self._plc_enabled():
            self._pending_plc_recipe = None
            self._plc_config_error = ''
        elif self._pending_plc_recipe is not None or self._plc_config_error:
            QMessageBox.warning(self, "Preparation", "PLC recipe confirmation is pending")
            return False
        if self._recipe_can_error:
            QMessageBox.warning(self, '配方参数未就绪', self._recipe_can_error)
            return False
        from core.recipe_app_flash import validate_app_flash
        try:
            selected_app_flash = validate_app_flash(recipe_dict.get('app_flash'))
        except ValueError as exc:
            QMessageBox.warning(self, 'APP刷写参数无效', str(exc))
            return False
        if selected_app_flash is not None and selected_app_flash != self._selected_app_flash_config:
            QMessageBox.warning(self, 'APP刷写参数未同步', '请在测试界面重新选择型号，成功同步当前配方的 config.ini 后再开始测试。')
            return False
        snapshot = copy.deepcopy(recipe_dict)
        snapshot['_debug_run'] = self._debug_requested_indices is not None
        if snapshot['_debug_run']:
            service=getattr(self.engine,'debug_periodic_can',None)
            snapshot['_active_debug_periodic']=service.active_names() if service is not None and isinstance(service.groups,dict) else []
        if snapshot['_debug_run']:
            if not self._can_debug_tests():
                return False
            for index, item in enumerate(snapshot['items']):
                if index not in self._debug_requested_indices:
                    item['skip_runtime'] = True
            if not any(not item.get('skip_runtime') and not str(item.get('name', '')).startswith('#') for item in snapshot['items']):
                QMessageBox.warning(self, '无法调试', '勾选项中没有可执行的测试项。')
                return False
        from core.user_session import get_current_user
        snapshot["operator_name"] = get_current_user()
        try:
            from core import system_config
            config = {} if debug_run else (self.db_manager.load_sys_config() if self.db_manager else system_config.load())
            snapshot['_wait_board']=not debug_run and config.get('test_start_mode','board_first')=='scan_first'
            if snapshot['_wait_board']:
                plc=getattr(getattr(self.engine,'device_manager',None),'easy320',None)
                if not plc or not plc.is_connected:raise ValueError('先扫码模式需要先连接PLC，才能等待板子到位信号')
            from core.mes_precheck import validate_settings
            validate_settings(config)
            from core.generic_mes import load_runtime_settings, validate_recipe
            snapshot['_generic_mes'] = load_runtime_settings(config, getattr(self.db_manager, 'db_path', None))
            if snapshot['_generic_mes']:
                validate_recipe(snapshot['_generic_mes'], snapshot['items'])
            snapshot['_mes_precheck'] = {key: config[key] for key in (
                'mes_precheck_enabled', 'mes_url', 'mes_equipment_no', 'mes_order_no', 'mes_timeout') if key in config}
        except Exception as exc:
            QMessageBox.critical(self, 'MES设置读取失败，禁止测试', str(exc))
            return False
        self.preparing = True
        self.run_activity_changed.emit(True)
        self._prepare_cancel.clear()
        self.combo_recipe.setEnabled(False)
        self._clear_mes_info()
        if snapshot['_generic_mes']:
            self.lbl_mes_upload_status.setText('正在查询条码')
        self.lbl_big_status.setText("MES CHECK" if snapshot['_mes_precheck'].get('mes_precheck_enabled') or snapshot['_generic_mes'] else "PREPARING")
        def prepared(ok, result):
            self.preparing = False
            if self._prepare_cancel.is_set() or self._closing:
                self._abort_prepared_record()
                self._on_run_retired(self.channel_id)
                return
            self._show_mes_query_result(snapshot)
            if not ok or not result[0]:
                self.combo_recipe.setEnabled(True)
                self.run_activity_changed.emit(False)
                self.lbl_big_status.setText("ERROR")
                reason = str(result[1]) if ok else str(result)
                self._log_test_event(reason)
                self._confirmed_barcode = ''
                QMessageBox.critical(self, '测试未启动', reason)
                return
            if snapshot.get('_wait_board'):
                self.preparing=True
                self._waiting_board=(snapshot,sn_barcode)
                self._arrival_continue=lambda:self._prepare_task.run(lambda:self._prepare_run(snapshot,sn_barcode),prepared)
                self.lbl_big_status.setText('等待板子到位')
                self._log_test_event('扫码及MES检查通过，等待PLC到位信号；尚未开始测试')
                return
            self._launch_prepared_test(snapshot, sn_barcode)
        accepted = self._prepare_task.run(lambda: self._prepare_run(snapshot, sn_barcode), prepared)
        if not accepted:
            self.preparing = False
            self.combo_recipe.setEnabled(True)
            self.run_activity_changed.emit(False)
            self.lbl_big_status.setText("ERROR")
        return accepted

    def _prepare_run(self, snapshot, sn_barcode):
        from core.recipe_can_requirement import validate_recipe_can
        manager = getattr(getattr(self, 'engine', None), 'device_manager', None)
        enabled = getattr(manager, 'is_enabled', None)
        validate_recipe_can(snapshot, hardware_enabled=bool(enabled('pcan')) if callable(enabled) else True)
        from core.mes_precheck import check_before_test
        if snapshot.get('_mes_already_checked'):
            passed,reason=True,'MES已在扫码后通过，本次不重复请求'
        elif snapshot.get('_debug_run', False):
            passed, reason = True, '调试运行：跳过扫码和MES过站检查，不计入生产统计'
            self.preparation_log.emit(reason)
        else:
            passed, reason = check_before_test(snapshot.get('_mes_precheck', {}), sn_barcode,
                snapshot.get('operator_name', ''), self._prepare_cancel.is_set, self.preparation_log.emit)
        if not snapshot.get('_mes_already_checked') and not snapshot.get('_debug_run', False) and snapshot.get('_mes_precheck', {}).get('mes_precheck_enabled'):
            snapshot['_mes_precheck_result'] = dict(passed=passed, message=reason, sn=sn_barcode)
        if not passed:
            return False, 'MES过站检查未通过，禁止测试：\n' + reason
        if not snapshot.get('_mes_already_checked') and not snapshot.get('_debug_run', False) and snapshot.get('_generic_mes'):
            from core.generic_mes import check_before_test as check_ruineng
            result = check_ruineng(snapshot['_generic_mes'], sn_barcode,
                self._prepare_cancel.is_set, self.preparation_log.emit)
            snapshot['_generic_mes_result'] = dict(passed=result.passed, message=result.message,
                                                    response=result.response, sn=sn_barcode)
            if not result.passed:
                return False, '锐能MES检查未通过，禁止测试：\n' + result.message
        if self._prepare_cancel.is_set():
            return False, '已取消'
        if snapshot.get('_wait_board'):
            snapshot['_mes_already_checked']=True
            return True,'等待板子到位'
        ready, message = self._apply_recipe_hardware(snapshot)
        if not ready or self._prepare_cancel.is_set():
            return False, message
        if self.db_manager and not snapshot.get('_debug_run', False):
            if callable(getattr(type(self.db_manager),'start_traced_test',None)):
                self._prepared_test_id = self.db_manager.start_traced_test(self.channel_id,f'CH-{self.channel_id}',sn_barcode,snapshot,operator=snapshot['operator_name'])
            else:
                self._prepared_test_id = self.db_manager.start_new_test(self.channel_id, f'CH-{self.channel_id}',sn_barcode,snapshot.get('name','ManualTest'),operator=snapshot['operator_name'])
        if self._prepare_cancel.is_set():
            self._abort_prepared_record()
            return False, 'Cancelled'
        return True, message

    @Slot(str)
    def _on_preparation_log(self, message):
        if not self._closing:
            self._log_test_event(message)
            if self.preparing and message.startswith('[上位机准备]'):
                self.lbl_big_status.setToolTip(message)

    def _abort_prepared_record(self):
        identity = self._prepared_test_id
        self._prepared_test_id = -1
        if identity != -1 and self.db_manager:
            self.db_manager.abort_prepared_test(identity)

    def _launch_prepared_test(self, recipe_dict, sn_barcode):
        if self._prepare_cancel.is_set() or self._closing:
            return False
        items = recipe_dict['items']
        self._active_debug_run = bool(recipe_dict.get('_debug_run', False))
        self._active_stats_barcode = sn_barcode
        # 重置 UI 看板与计时器
        self._reset_flash_status()
        self.elapsed_seconds = 0
        self.lbl_timer.setText("00:00")
        self.timer.start()

        self.lbl_big_status.setText("DEBUG" if self._active_debug_run else "TESTING")
        self.lbl_big_status.setStyleSheet("""
            QLabel {
                background-color: #FFC107;
                color: #000000;
                font-size: 34px;
                font-weight: bold;
                border-radius: 6px;
                padding: 12px;
            }
        """)

        # 重置树状态
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            raw = (item_node.data(0, Qt.UserRole) or {}).get('raw_data', {})
            if raw.get('standard_type') == '变量':
                item_node.setText(2, '待获取变量值')
                item_node.setToolTip(2, f"期望值取自变量：{raw.get('min', '')}")
            item_node.setText(4, "--")
            item_node.setText(6, "等待")
            item_node.setBackground(6, QColor("#1A1A2E"))
            item_node.setForeground(6, QColor("#E0E0E0"))
            self._update_test_row_tooltips(item_node, (4, 6))
            for j in range(item_node.childCount()):
                sub_node = item_node.child(j)
                sub_node.setText(4, "--")
                sub_node.setText(6, "等待")
                sub_node.setBackground(6, QColor("#1A1A2E"))
                sub_node.setForeground(6, QColor("#E0E0E0"))
                self._update_test_row_tooltips(sub_node, (4, 6))

        if self._active_debug_run:
            self.step_tree.stop_auto_scroll()
            first_index = next((index for index, item in enumerate(items)
                                if not item.get('skip_runtime')
                                and not str(item.get('name', '')).strip().startswith('#')), None)
            if first_index is not None and first_index < self.step_tree.topLevelItemCount():
                self.step_tree.show_running_item(self.step_tree.topLevelItem(first_index), animated=False)
        else:
            self.step_tree.scrollToTop()

        # 清空并开始新的测试记录
        if hasattr(self, 'test_log_view'):
            self.test_log_view.clear()
        recipe_label = recipe_dict.get("name", self.current_recipe_name) or "未命名配方"
        self._log_test_event(f"开始测试 | 配方: {recipe_label} | SN: {sn_barcode or '--'}")
        if self._active_debug_run:
            self._log_test_event('【调试运行】仅执行指定测试项，不计入生产统计与连续不良报警；前置依赖请自行确认。')
        if recipe_dict.get('_mes_precheck_result'):
            self._log_test_event('[MES前置过站] PASS | ' + str(recipe_dict['_mes_precheck_result']['message']))

        try:
            success = self.engine.start_channel_test(
                self.channel_id, items, test_id=self._prepared_test_id, sn_barcode=sn_barcode,
                recipe_name=recipe_dict.get("name", self.current_recipe_name), operator_name=recipe_dict["operator_name"],
                app_flash_config=recipe_dict.get('app_flash'),
                **({'generic_mes_config': recipe_dict['_generic_mes']} if recipe_dict.get('_generic_mes') else {}),
                **({'debug_run': True} if self._active_debug_run else {}))
        except Exception as exc:
            success = False
            QMessageBox.critical(self, "Test start failed", str(exc))

        if success:
            self._prepared_test_id = -1
            self.is_testing = True
        else:
            self._abort_prepared_record()
            self.timer.stop()
            self.combo_recipe.setEnabled(True)
            self.run_activity_changed.emit(False)
            self.lbl_big_status.setText("ERROR")
            self.lbl_big_status.setStyleSheet("background-color: #DC3545; color: white; font-size: 38px; font-weight: bold; border-radius: 6px; padding: 15px;")
            QMessageBox.critical(self, "错误", "启动单通道测试引擎失败!")
        return bool(success)

    def _apply_recipe_hardware(self, recipe_dict):
        """在测试启动前应用配方绑定的 CAN 参数，并下发 PLC 配方号。"""
        if self._prepare_cancel.is_set():
            return False, 'Cancelled'
        manager=getattr(self.engine,'device_manager',None)
        monitor=getattr(manager,'dbc_monitor',None)
        if monitor is not None and monitor.asset and (monitor.asset['sha256']!=(recipe_dict.get('dbc') or {}).get('sha256') or monitor.context_key!=recipe_dict.get('name')):
            monitor.stop()
        from core.recipe_can_requirement import validate_recipe_can
        try:
            from core.can_step_channels import migrate_recipe_channels
            migrate_recipe_channels(recipe_dict)
            validate_recipe_can(recipe_dict)
            from core.dbc_signals import rebind_recipe_dbc
            rebind_recipe_dbc(recipe_dict)
        except ValueError as exc:
            return False, str(exc)
        manager = getattr(self.engine, "device_manager", None)
        if manager is None:
            return False, "设备管理器未初始化"

        can_config = recipe_dict.get("can_config")
        enabled = getattr(manager, 'is_enabled', None)
        if callable(enabled) and not enabled('pcan'):
            self._applied_recipe_can = None
            if monitor is not None:
                monitor.stop()
            return True, '硬件管理中心未启用 CAN，已跳过 CAN 参数应用和 DBC 监控'
        mode = None
        if can_config and can_config.get('enabled') is not False:
            pcan = getattr(manager, "pcan", None)
            if pcan is None:
                return False, "配方已绑定 CAN 卡，但 USB PCAN-FD 驱动未初始化"
            try:
                if callable(getattr(type(pcan), 'check_bus_health', None)):
                    pcan.check_bus_health()
                requested_key = json.dumps(can_config, sort_keys=True)
                current_signature = (getattr(pcan, 'bus', None), getattr(pcan, 'channel', None),
                    getattr(pcan, 'nominal_bitrate', None), getattr(pcan, 'data_bitrate', None), getattr(pcan, 'fd_enabled', None))
                cached = getattr(self, '_applied_recipe_can', None)
                if (str(can_config.get('channel', '')).startswith(('PCAN_', 'ZLG_CAN')) and
                        getattr(pcan, 'is_connected', False) is True and
                        cached == (requested_key, current_signature)):
                    OverviewTab._apply_recipe_dbc(manager,recipe_dict)
                    return True, 'CAN配置与已连接配方一致，保留连接'
                mode = can_config.get("mode", "CAN-FD + BRS")
                if mode not in ('经典 CAN', 'CAN-FD', 'CAN-FD + BRS'):
                    return False, 'CAN模式无效'
                zlg_config = None
                if can_config.get('zlg') is not None and str(can_config.get('channel', '')).startswith('ZLG_CAN'):
                    from core.zlg_recipe_config import validate_zlg_config
                    zlg_config = validate_zlg_config(can_config['zlg'])
                if pcan.disconnect() is False:
                    return False, '旧CAN连接未确认释放，未应用配方参数'
                if self._prepare_cancel.is_set():
                    return False, 'Cancelled'
                if zlg_config:
                    pcan.zlg_ip, pcan.zlg_port = zlg_config['ip'], int(zlg_config['port'])
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
                if self._prepare_cancel.is_set():
                    return False, 'Cancelled'
                if not pcan.connect():
                    return False, f"CAN卡连接失败: {pcan.last_error or 'PCAN无响应'}"
                if zlg_config:
                    if self._prepare_cancel.is_set():
                        pcan.disconnect()
                        return False, 'Cancelled'
                    try:
                        with pcan.lock:
                            pcan.bus.configure_channels(zlg_config['channels'], False)
                    except Exception:
                        pcan.disconnect()
                        raise
                self._applied_recipe_can = (requested_key, (getattr(pcan, 'bus', None), getattr(pcan, 'channel', None),
                    getattr(pcan, 'nominal_bitrate', None), getattr(pcan, 'data_bitrate', None), getattr(pcan, 'fd_enabled', None)))
            except Exception as exc:
                return False, f"CAN配方参数应用失败: {exc}"

        # PLC 配方号只在操作员主动切换配方时下发，开始测试不重复写入。
        if self._prepare_cancel.is_set():return False,'Cancelled'
        try:OverviewTab._apply_recipe_dbc(manager,recipe_dict)
        except Exception as exc:return False,f'DBC配置失败：{exc}'
        can_summary = f"CAN={can_config.get('channel', 'PCAN_USBBUS1')} {mode}" if can_config else "无CAN配置"
        return True, can_summary

    @staticmethod
    def _apply_recipe_dbc(manager,recipe):
        monitor=getattr(manager,'dbc_monitor',None)
        if monitor is not None:
            if recipe.get('dbc') and (not recipe.get('can_config') or recipe['can_config'].get('enabled') is False):
                monitor.stop();raise ValueError('已导入DBC，请选择配方CAN卡；没有DBC的项目可移除DBC')
            monitor.configure(recipe.get('dbc'),getattr(manager,'pcan',None),context_key=recipe.get('name'))

    def stop_single_channel_test(self):
        if self._waiting_board is not None:
            self._waiting_board=None;self._arrival_continue=None
            self.preparing=False;self._confirmed_barcode=''
            self.combo_recipe.setEnabled(True);self.run_activity_changed.emit(False)
        self._prepare_cancel.set()
        self._stop_flash_display()
        self._log_test_event("■ 测试被手动停止")
        """强制停止单通道测试"""
        if self.engine:
            self.engine.stop_test(self.channel_id)
            self.is_testing = False
            self.timer.stop()
            self.lbl_big_status.setText("STOPPING" if self.engine.workers or self._prepare_task.isRunning() else "STOPPED")
            self.lbl_big_status.setStyleSheet("background-color: #FFC107; color: black; font-size: 34px; font-weight: bold; border-radius: 6px; padding: 15px;")



    def stop_monitor(self):
        if hasattr(self, 'plc_signal_timer') and self.plc_signal_timer.isActive():
            self.plc_signal_timer.stop()

    # -----------------------------------------------------------------
    # PLC 保持寄存器地址 1 开始/停止测试信号轮询 (方案 A)
    # -----------------------------------------------------------------
    def poll_plc_start_stop_signal(self):
        if self._closing or self._pending_plc_recipe is not None or self._plc_task.isRunning():
            return
        manager = getattr(self.engine, 'device_manager', None)
        plc = getattr(manager, 'easy320', None)
        if not plc or not getattr(plc, 'is_connected', False):
            return
        def fetched(ok, values):
            if ok and values:
                self._handle_plc_signal(int(values[0]), plc)
        self._plc_task.run(lambda: plc.read_register(address=1, count=1), fetched)

    def _handle_plc_signal(self, val: int, plc):
        if self._closing or val not in (1, 2):
            return
        if self._plc_task.isRunning():
            QTimer.singleShot(0, lambda: self._handle_plc_signal(val, plc))
            return
        if val == self._last_plc_signal:
            return
        self._last_plc_signal = val
        def cleared(ok, acknowledged):
            self._last_plc_signal = 0
            if not ok or not acknowledged:
                self.lbl_big_status.setToolTip('PLC command reset failed; command will be retried')
                return
            action = self._on_board_arrival if val == 1 else self.stop_single_channel_test
            QTimer.singleShot(0, action)
        self._plc_task.run(lambda: plc.write_register(1, 0, verify=False), cleared)

    def _on_board_arrival(self):
        if self._closing:return
        if self._waiting_board is not None:
            if self._prepare_cancel.is_set():return
            snapshot,sn=self._waiting_board
            self._waiting_board=None
            resume=self._arrival_continue;self._arrival_continue=None
            if self._confirmed_barcode!=sn or self.txt_sn.text().strip()!=sn:
                self.stop_single_channel_test();return
            snapshot['_wait_board']=False
            self.lbl_big_status.setText('PREPARING')
            if resume and resume():return
            self.preparing=False;self.run_activity_changed.emit(False)
            self.combo_recipe.setEnabled(True);self.lbl_big_status.setText('ERROR')
            return
        from core import system_config
        cfg=self.db_manager.load_sys_config() if self.db_manager else system_config.load()
        if (cfg or {}).get('test_start_mode','board_first')=='scan_first':
            self._log_test_event('收到到位信号，但没有已通过扫码/MES的待测条码，不启动测试')
            return
        self.request_scan_and_start()

    @Slot(int, int, str, object)
    def on_variable_target_resolved(self, channel_id, step_idx, key, value):
        if channel_id != self.channel_id or not 0 <= step_idx < self.step_tree.topLevelItemCount():
            return
        row = self.step_tree.topLevelItem(step_idx)
        row.setText(2, '变量未赋值' if value is None else str(value))
        row.setToolTip(2, f'变量键名：{key}\n本次判定期望值：{value!r}')

    @Slot(int, int, str)
    def on_worker_step_started(self, channel_id: int, step_idx: int, step_name: str):
        if channel_id!=self.channel_id or step_idx<0:return
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            item_node.setText(6, "RUNNING")
            item_node.setBackground(6, QColor("#FFC107"))
            item_node.setForeground(6, QColor("#000000"))
            self._update_test_row_tooltips(item_node, (6,))
            self.step_tree.show_running_item(item_node)
        self._log_test_event(f"▶ 步骤{step_idx + 1} 开始: {step_name}")

    @Slot(int, int, int, str, object)
    def on_worker_sub_step_finished(self, channel_id: int, step_idx: int, sub_idx: int, status: str, result: object):
        if channel_id!=self.channel_id or step_idx<0 or sub_idx<0:return
        result_text = str(result) if result is not None else "Done"
        self._log_test_event(f"  · 步骤{step_idx + 1} 子项{sub_idx + 1}: {status} | {result_text}")
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            if sub_idx < item_node.childCount():
                sub_node = item_node.child(sub_idx)
                item_data = (item_node.data(0, Qt.UserRole) or {}).get('raw_data', {})
                display_value = result_for_display(result, item_data)
                result_text = format_result(display_value, item_data) if result is not None else "Done"
                sub_node.setText(4, result_text)
                sub_node.setToolTip(4, result_text)
                if getattr(display_value, "unit", ""):
                    sub_node.setText(5, display_value.unit)
                res_pass = "PASS" in status or "成功" in status or "OK" in status
                skipped = '跳过' in status
                sub_node.setText(6, 'SKIP' if skipped else ("PASS" if res_pass else "FAIL"))
                bg_color = QColor('#475569') if skipped else (QColor("#28A745") if res_pass else QColor("#DC3545"))
                sub_node.setBackground(6, bg_color)
                sub_node.setForeground(6, QColor("#FFFFFF" if skipped else "#000000"))
                self._update_test_row_tooltips(sub_node, (4, 5, 6))

    @Slot(int, int, str, bool, object)
    def on_worker_step_finished(self, channel_id: int, step_idx: int, name: str, is_pass: bool, val: object):
        if channel_id != self.channel_id or step_idx < 0:
            return
        if step_idx < self.step_tree.topLevelItemCount():
            item_node = self.step_tree.topLevelItem(step_idx)
            skipped = isinstance(val, str) and val == '跳过'
            res_text = 'SKIP' if skipped else ("PASS" if is_pass else "FAIL")
            item_data = (item_node.data(0, Qt.UserRole) or {}).get('raw_data', {})
            display_value = result_for_display(val, item_data)
            item_node.setText(4, format_result(display_value, item_data) if val is not None else "Done")
            if getattr(display_value, "unit", ""):
                item_node.setText(5, display_value.unit)
            item_node.setText(6, res_text)
            bg_color = QColor('#475569') if skipped else (QColor("#28A745") if is_pass else QColor("#DC3545"))
            item_node.setBackground(6, bg_color)
            item_node.setForeground(6, QColor("#FFFFFF" if skipped else "#000000"))
            self._update_test_row_tooltips(item_node, (4, 5, 6))
        val_text = str(val) if val is not None else ""
        self._log_test_event(f"{'✔' if is_pass else '✘'} 步骤{step_idx + 1} 结束: {name} => {'PASS' if is_pass else 'FAIL'}" + (f" | {val_text}" if val_text else ""))

    def _update_stats_display(self):
        """刷新界面生产统计 4 组大数字卡片"""
        pass_rate = (self.pass_count / self.total_count * 100) if self.total_count > 0 else 0.0
        if hasattr(self, 'lbl_stat_total_val'):
            self.lbl_stat_total_val.setText(str(self.total_count))
            self.lbl_stat_pass_val.setText(str(self.pass_count))
            self.lbl_stat_fail_val.setText(str(self.fail_count))
            self.lbl_stat_rate_val.setText(f"{pass_rate:.2f}%")

    def set_stats_deduplicate(self, enabled):
        """由软件设置页写入条码去重开关；返回是否生效（保存失败时调用方负责回滚界面）。"""
        enabled = bool(enabled)
        if self.db_manager:
            cfg = self.db_manager.load_sys_config() or {}
            cfg['stats_deduplicate_barcode'] = enabled
            if not self.db_manager.save_sys_config(cfg):
                QMessageBox.warning(self, '保存失败', '统计设置未保存，已保留原统计方式。')
                return False
        self.stats_deduplicate = enabled
        self._audit_statistics('deduplication_changed',dict(enabled=enabled,previous=(self.total_count,self.pass_count,self.fail_count)))
        self._recalculate_statistics()
        return True

    def _recalculate_statistics(self):
        self.total_count, self.pass_count, self.fail_count = self.production_statistics.counts(self.stats_deduplicate)
        self._update_stats_display()
        detail=self.production_statistics.details()
        text=f'项目：{self._statistics_project() or "未选择"}，自上次清零以来累计\n首次通过率：{detail["first_pass_rate"]:.2f}%；复测：{detail["retests"]}次；测试：{detail["attempts"]}次\n数据库：{getattr(self.db_manager,"db_path","当前会话")}'
        for card in (self.card_stat_total,self.card_stat_pass,self.card_stat_fail,self.card_stat_rate):card.setToolTip(text)

    def reset_statistics(self):
        """重置生产统计数据归零"""
        if self._persistent_statistics():
            if self._closing:return
            if not self._statistics_project():
                QMessageBox.warning(self,'无法清零','请先选择需要清零的项目。');return
            if self._run_busy():
                QMessageBox.warning(self,'无法清零','请等待当前测试结束后清零。');return
            self._stats_reset_requested=True
            self._audit_statistics('reset_requested',dict(previous=(self.total_count,self.pass_count,self.fail_count)))
            self._stats_reset_busy=True
            self.btn_run_test.setEnabled(False)
            self.run_activity_changed.emit(True)
            self.refresh_statistics()
            return
        self.production_statistics.clear()
        self.total_count = 0
        self.pass_count = 0
        self.fail_count = 0
        self._update_stats_display()

    def on_reset_stats_clicked(self):
        if not self._can_debug_tests():
            QMessageBox.warning(self,'权限不足','只有管理员可以清零生产统计。');return
        """数据清零请求（由软件设置页按钮触发；按钮按角色置灰，带二次确认）"""
        res = QMessageBox.question(
            self,
            "确认清零",
            f"【管理员确认】\n是否清零项目【{self._statistics_project()}】的生产统计？\n其他项目不受影响，历史测试记录保留。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res == QMessageBox.Yes:
            self.reset_statistics()
            if not self._persistent_statistics():QMessageBox.information(self, "提示", "生产统计数据已成功清零！")

    @Slot(int, bool)
    def on_worker_test_finished(self, channel_id: int, status: bool):
        if channel_id != self.channel_id:
            return
        self.is_testing = False
        self.timer.stop()
        self._log_test_event(f"■ 测试结束: {'PASS' if status else 'FAIL'}")

        # 更新生产统计
        worker = self.engine.workers.get(channel_id) if self.engine else None
        if worker and getattr(worker, 'mes_upload_job', None):
            self.enqueue_mes_upload(worker.mes_upload_job)
        elif worker and getattr(worker, 'mes_upload_error', ''):
            self._show_mes_upload_error(worker.mes_upload_error)
        barcode = worker.sn_barcode if worker else self._active_stats_barcode
        run_id = worker.execution_id if worker else None
        if worker and getattr(worker, 'record_error', ''):
            self.lbl_big_status.setText("RECORD ERROR")
            self.lbl_big_status.setToolTip(worker.record_error)
            return
        debug_run = getattr(worker, 'debug_run', False) is True if worker else self._active_debug_run
        if debug_run:
            self._log_test_event('【调试结束】本次结果不计入生产统计。')
        elif self._persistent_statistics():
            self.refresh_statistics()
        else:
            self.production_statistics.record(barcode, status, run_id)
            self._recalculate_statistics()

        # 更新右侧巨型 Total Status 看板
        if status:
            self.lbl_big_status.setText("PASS")
            self.lbl_big_status.setStyleSheet("""
                QLabel {
                    background-color: #28A745;
                    color: #000000;
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
                    color: #000000;
                    font-size: 46px;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 15px;
                }
            """)
        if worker and run_id and not debug_run:
            cancelled = getattr(worker, 'cancel_event', None)
            stopped = cancelled is not None and cancelled.is_set() is True
            if not stopped and self.ng_alarm.record(status, barcode, run_id):
                self._show_consecutive_ng_alarm(barcode)
        if debug_run:
            self.lbl_big_status.setText('DEBUG PASS' if status else 'DEBUG FAIL')

    def _show_consecutive_ng_alarm(self, barcode):
        if self._closing:
            return
        text = (f'已连续出现 {self.ng_alarm.count} 片不良，达到设定阈值 {self.ng_alarm.threshold} 片。\n'
                f'当前条码：{barcode or "未提供"}\n请检查产品、治具及设备状态。')
        self._log_test_event('[连续不良报警] ' + text)
        box = QMessageBox(QMessageBox.Warning, '连续不良报警', text, QMessageBox.Ok, self)
        box.setWindowModality(Qt.ApplicationModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        self._ng_alarm_box = box
        box.finished.connect(self._on_ng_alarm_closed)
        box.open()

    def _on_ng_alarm_closed(self, _result):
        self._ng_alarm_box = None
        if not self._closing:
            self.run_activity_changed.emit(self._run_busy())
