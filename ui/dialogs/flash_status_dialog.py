# -*- coding: utf-8 -*-
"""刷写/下载监控弹窗：APP刷写、力捷丰 SG400、瑞萨 E2 的实时进度以非模态弹窗展示。"""

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
)


class FlashStatusDialog(QDialog):
    """非模态刷写监控弹窗。

    由 OverviewTab 持有一份实例：刷写开始时自动弹出，结束后保留最终结果，
    操作员可手动关闭；新一轮测试开始时自动复位隐藏。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("刷写 / 下载监控")
        self.setModal(False)
        self._state={}
        self._resize_timer=QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._render_after_resize)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.flash_status_panel = QFrame()
        self.flash_status_panel.setObjectName("flash_status_panel")
        self.flash_status_panel.setStyleSheet(
            "QFrame#flash_status_panel { background: #16213E; border: 1px solid #2D3748; border-radius: 6px; padding: 0px; }"
        )
        flash_layout = QVBoxLayout(self.flash_status_panel)
        flash_layout.setContentsMargins(10, 8, 10, 8)
        flash_layout.setSpacing(3)
        self.lbl_flash_status = QLabel("APP刷写: 待启动")
        self.lbl_flash_record = QLabel("Record: --")
        self.lbl_flash_can = QLabel("PCAN: 等待刷写监听")
        for label in (self.lbl_flash_status, self.lbl_flash_record, self.lbl_flash_can):
            label.setTextFormat(Qt.PlainText)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setStyleSheet("color: #CBD5E0; font-size: 12px; border: none; padding: 0px; margin: 0px; background: transparent;")
            flash_layout.addWidget(label)
        self.flash_progress_bar = QProgressBar()
        self.flash_progress_bar.setRange(0, 1000)
        self.flash_progress_bar.setValue(0)
        self.flash_progress_bar.setTextVisible(False)
        self.flash_progress_bar.setFixedHeight(6)
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
        layout.addWidget(self.flash_status_panel)

    def render_state(self, state):
        """按最新刷写状态刷新三行文案、颜色与进度条（逻辑与原底部面板一致）。"""
        self._state=dict(state or {})
        state = state or {}
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
        if 'received_frames' in state:
            interface = state.get('can_interface', 'CAN')
            total = state['received_frames']
            ids = state.get('monitor_ids', '')
            can_text = f'{interface}：总接收 {total} 帧；监听 {ids}；' + (f'相关报文 {age:.1f}秒前' if age is not None else ('有报文但监听ID未匹配' if total else '未收到总线报文'))
            observed = state.get('observed_ids', [])
            if age is None and observed:
                can_text += '；已见ID：' + ', '.join(observed[:8])
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

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._resize_timer.start(0)

    def hideEvent(self,event):
        self._resize_timer.stop()
        super().hideEvent(event)

    def _render_after_resize(self):
        self.render_state(self._state)
