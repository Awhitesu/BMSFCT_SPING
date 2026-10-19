from ui.device_task import DeviceTask
import os
import threading
import time
import zlib

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.burn_recorder import (
    record_burn_event,
)
from ui.dialogs.step_dialog import SmartProjectComboBox


class RenesasE2Tab(QWidget):
    """瑞萨 E2 (rfp-cli) 调试页：始终复用 DeviceManager 在启动自检中创建的唯一驱动实例。"""

    operation_finished = Signal(bool, str)
    log_message = Signal(str)
    metadata_loaded = Signal(dict)
    burn_record_updated = Signal(dict)

    DEVICE_FAMILIES = ["RA", "RX", "RL78", "RH850", "RE"]

    def __init__(self, device_manager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self.programmer = device_manager.e2_rfp
        self._running = False
        self.preview_task=DeviceTask(self)
        self.presence_task=DeviceTask(self)
        self.preview_timer=QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._load_project_preview)
        self._preview_text=''

        self._current_meta = {}
        self._init_ui()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)
        self.operation_finished.connect(self._finish_operation)
        self.log_message.connect(self._append_log)
        self.metadata_loaded.connect(self._on_metadata_loaded)
        self.burn_record_updated.connect(self._on_burn_record_updated)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.poll_presence)
        self.status_timer.start(1000)
        self.refresh_connection_state()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 1. 顶部紧凑公用状态栏 (单行 ~46px)
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #151e2e;
                border: 1px solid #1f2937;
                border-radius: 8px;
                padding: 4px 10px;
            }
        """)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 4, 10, 4)
        header_layout.setSpacing(12)

        lbl_title = QLabel("瑞萨 E2 (rfp-cli)")
        lbl_title.setStyleSheet("font-weight: bold; color: #F8FAFC; font-size: 15px;")
        header_layout.addWidget(lbl_title)

        sep1 = QLabel("|")
        sep1.setStyleSheet("color: #334155;")
        header_layout.addWidget(sep1)

        header_layout.addWidget(QLabel("rfp-cli:"))
        self.edit_cli_path = QLineEdit(str(getattr(self.programmer, "rfp_cli_path", "") or "自动搜索安装目录"))
        self.edit_cli_path.setReadOnly(True)
        self.edit_cli_path.setFixedWidth(200)
        self.edit_cli_path.setToolTip(str(getattr(self.programmer, "rfp_cli_path", "")))
        header_layout.addWidget(self.edit_cli_path)

        header_layout.addWidget(QLabel("器件家族:"))
        self.combo_family = QComboBox()
        self.combo_family.addItems(self.DEVICE_FAMILIES)
        cur_family = str(getattr(self.programmer, "device_family", "RH850") or "RH850")
        idx = self.combo_family.findText(cur_family)
        self.combo_family.setCurrentIndex(idx if idx >= 0 else self.combo_family.findText("RH850"))
        self.combo_family.currentTextChanged.connect(self._save_conn_params)
        header_layout.addWidget(self.combo_family)

        header_layout.addWidget(QLabel("接口:"))
        self.edit_interface = QLineEdit(str(getattr(self.programmer, "interface", "") or ""))
        self.edit_interface.setFixedWidth(90)
        self.edit_interface.setPlaceholderText("如 FINE/CSI")
        self.edit_interface.editingFinished.connect(self._save_conn_params)
        header_layout.addWidget(self.edit_interface)

        header_layout.addWidget(QLabel("速率:"))
        self.edit_speed = QLineEdit(str(getattr(self.programmer, "speed", "") or ""))
        self.edit_speed.setFixedWidth(90)
        self.edit_speed.setPlaceholderText("如 1000000")
        self.edit_speed.editingFinished.connect(self._save_conn_params)
        header_layout.addWidget(self.edit_speed)

        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #334155;")
        header_layout.addWidget(sep2)

        header_layout.addWidget(QLabel("状态:"))
        self.lbl_device_state = QLabel("未查询")
        self.lbl_device_state.setStyleSheet("font-size:14px; font-weight:bold; color:#00E5FF;")
        header_layout.addWidget(self.lbl_device_state)

        self.btn_query = QPushButton("🔄 检测E2")
        self.btn_query.setToolTip("检测实体E2的USB在位状态及上次操作结果；不连接或复位目标MCU。")
        self.btn_query.setMinimumWidth(120)
        self.btn_query.clicked.connect(self.query_state)
        header_layout.addWidget(self.btn_query)

        header_layout.addStretch()

        self.lbl_connection = QLabel()
        self.lbl_connection.setMinimumWidth(110)
        self.lbl_connection.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.lbl_connection)

        layout.addWidget(header_frame)

        # 2. 本 TAB 内二级子 TAB (QTabWidget)
        self.sub_tabs = QTabWidget()

        # ==================== 子 Tab 1: 烧录主控与留痕防呆 ====================
        tab_burn = QWidget()
        tab_burn_layout = QVBoxLayout(tab_burn)
        tab_burn_layout.setContentsMargins(12, 12, 12, 12)
        tab_burn_layout.setSpacing(12)

        project_group = QGroupBox("烧录工程控制")
        project_layout = QGridLayout(project_group)
        project_layout.setContentsMargins(14, 14, 14, 12)
        project_layout.setSpacing(10)

        project_layout.addWidget(QLabel("烧录工程:"), 0, 0)
        proj_row = QHBoxLayout()
        self.combo_project = SmartProjectComboBox()
        self.combo_project.setEditable(True)
        self.combo_project.lineEdit().setPlaceholderText("从下拉框选择最近使用的工程，或浏览/输入本地工程文件路径")
        self.combo_project.addItem("（点击右侧浏览本地工程文件）")
        self.combo_project.currentIndexChanged.connect(lambda _: self.refresh_connection_state())
        self.combo_project.editTextChanged.connect(lambda _: self.refresh_connection_state())
        self.combo_project.currentTextChanged.connect(self._on_project_selection_changed)
        proj_row.addWidget(self.combo_project, 1)

        self.btn_browse = QPushButton("📂 浏览文件")
        self.btn_browse.setToolTip("选择电脑本地的 .rpj / .mot / .hex / .srec 工程文件")
        self.btn_browse.clicked.connect(self.select_project)
        proj_row.addWidget(self.btn_browse)

        project_layout.addLayout(proj_row, 0, 1, 1, 2)

        project_layout.addWidget(QLabel("完成超时:"), 1, 0)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(1, 3600)
        self.spin_timeout.setValue(300)
        self.spin_timeout.setSuffix(" s")
        self.spin_timeout.setFixedWidth(120)
        project_layout.addWidget(self.spin_timeout, 1, 1)

        self.btn_run = QPushButton("🚀 执行烧录")
        self.btn_run.setProperty("buttonRole", "primary")
        self.btn_run.setFixedHeight(34)
        self.btn_run.clicked.connect(self.run_project)
        project_layout.addWidget(self.btn_run, 1, 2)

        tab_burn_layout.addWidget(project_group)

        # 烧录留痕与源程序防呆比对面板
        audit_group = QGroupBox("📋 烧录留痕与源程序防呆比对 (全息追溯)")
        audit_layout = QVBoxLayout(audit_group)
        audit_layout.setContentsMargins(14, 14, 14, 12)
        audit_layout.setSpacing(12)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(24)
        info_grid.setVerticalSpacing(10)

        info_grid.addWidget(QLabel("源程序文件:"), 0, 0)
        self.lbl_audit_source_file = QLabel("—")
        self.lbl_audit_source_file.setStyleSheet("color:#38BDF8; font-weight:bold; font-size:14px;")
        info_grid.addWidget(self.lbl_audit_source_file, 0, 1)

        info_grid.addWidget(QLabel("目标芯片:"), 0, 2)
        self.lbl_audit_chip = QLabel("—")
        self.lbl_audit_chip.setStyleSheet("color:#F1F5F9; font-size:14px;")
        info_grid.addWidget(self.lbl_audit_chip, 0, 3)

        info_grid.addWidget(QLabel("基准校验码:"), 1, 0)
        self.lbl_audit_exp_crc = QLabel("—")
        self.lbl_audit_exp_crc.setStyleSheet("color:#FBBF24; font-family:monospace; font-weight:bold; font-size:15px;")
        info_grid.addWidget(self.lbl_audit_exp_crc, 1, 1)

        info_grid.addWidget(QLabel("实测校验码:"), 1, 2)
        self.lbl_audit_act_crc = QLabel("未烧录")
        self.lbl_audit_act_crc.setStyleSheet("color:#94A3B8; font-family:monospace; font-weight:bold; font-size:15px;")
        info_grid.addWidget(self.lbl_audit_act_crc, 1, 3)

        info_grid.addWidget(QLabel("物理扇区范围:"), 2, 0)
        self.lbl_audit_sectors = QLabel("—")
        self.lbl_audit_sectors.setStyleSheet("color:#CBD5E1; font-size:13px;")
        info_grid.addWidget(self.lbl_audit_sectors, 2, 1)

        info_grid.addWidget(QLabel("比对结论:"), 2, 2)
        self.lbl_audit_status = QLabel("待烧录")
        self.lbl_audit_status.setStyleSheet("color:#94A3B8; font-weight:bold; font-size:15px;")
        info_grid.addWidget(self.lbl_audit_status, 2, 3)

        audit_layout.addLayout(info_grid)

        ctrl_row = QHBoxLayout()
        self.chk_deep_diff = QCheckBox("启用物理回读逐字节校验 (Byte-by-Byte Diff)")
        self.chk_deep_diff.setChecked(True)
        self.chk_deep_diff.setToolTip("烧录成功后经 rfp-cli 从芯片物理回读数据并进行 Byte-by-Byte 比对")
        ctrl_row.addWidget(self.chk_deep_diff)

        ctrl_row.addStretch()

        self.btn_open_records = QPushButton("📁 打开留痕目录 (CSV流水账 / 全息报告)")
        self.btn_open_records.setFixedHeight(30)
        self.btn_open_records.setToolTip("打开本地存放 CSV 流水账与全息报告的文件夹 (logs/burn_records)")
        self.btn_open_records.clicked.connect(self.open_records_folder)
        ctrl_row.addWidget(self.btn_open_records)

        audit_layout.addLayout(ctrl_row)

        self.txt_audit_summary = QTextEdit()
        self.txt_audit_summary.setReadOnly(True)
        self.txt_audit_summary.setMaximumHeight(85)
        self.txt_audit_summary.setPlaceholderText("烧录与留痕报告全息摘要将在此实时更新...")
        self.txt_audit_summary.setStyleSheet("background-color:#0b1120; border:1px solid #1e293b; color:#94A3B8; font-size:12px;")
        audit_layout.addWidget(self.txt_audit_summary)

        tab_burn_layout.addWidget(audit_group)
        tab_burn_layout.addStretch()

        self.sub_tabs.addTab(tab_burn, "🚀 烧录控制与防呆比对")

        # ==================== 子 Tab 2: 运行与底层调试日志 ====================
        tab_log = QWidget()
        tab_log_layout = QVBoxLayout(tab_log)
        tab_log_layout.setContentsMargins(12, 12, 12, 12)
        tab_log_layout.setSpacing(10)

        log_top_row = QHBoxLayout()
        log_tip = QLabel("实时显示 rfp-cli 命令行 stdout/stderr 原文与瑞萨 E2 驱动操作日志")
        log_tip.setStyleSheet("color:#64748B; font-size:13px;")
        log_top_row.addWidget(log_tip)
        log_top_row.addStretch()

        self.btn_fetch_hw_log = QPushButton("🔄 拉取驱动日志缓冲")
        self.btn_fetch_hw_log.setToolTip("拉取驱动内部环形日志缓冲（rfp-cli 最近输出）")
        self.btn_fetch_hw_log.clicked.connect(self.fetch_hardware_log)
        log_top_row.addWidget(self.btn_fetch_hw_log)

        btn_clear = QPushButton("🗑️ 清空日志")
        btn_clear.clicked.connect(lambda: self.log_output.clear())
        log_top_row.addWidget(btn_clear)

        tab_log_layout.addLayout(log_top_row)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.document().setMaximumBlockCount(1000)
        tab_log_layout.addWidget(self.log_output, 1)

        self.sub_tabs.addTab(tab_log, "📜 运行与底层调试日志")

        layout.addWidget(self.sub_tabs, 1)

    def refresh_connection_state(self):
        ready = bool(self.programmer and self.programmer.tool_ready)
        present = bool(ready and self.programmer.is_connected)
        checked = bool(self.programmer and self.programmer.presence_checked)
        text = 'E2已识别' if present else ('E2未连接' if checked else '连接未验证')
        self.lbl_connection.setText('●  ' + text if ready else '●  工具未就绪')
        self.lbl_connection.setToolTip(
            "Windows已识别到可用E2 USB设备；目标MCU连接未验证。烧录、校验和回读分别以实际执行结果为准。"
            if present else (str(getattr(self.programmer, 'presence_error', '') or '请先初始化工具并检测E2的USB连接。'))
        )
        color = "#FFC107" if present else "#FF4D5A"
        self.lbl_connection.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:6px; "
            "padding:7px 14px; font-weight:bold;"
        )
        can_operate = present and not self._running
        self.btn_query.setEnabled(ready and not self._running)
        self.btn_run.setEnabled(can_operate)
        self.btn_browse.setEnabled(not self._running)

    def poll_presence(self):
        if (not self.isVisible() or self._running or self.presence_task.isRunning()
                or not self.programmer or not self.programmer.tool_ready):
            return
        self.presence_task.run(self.programmer.refresh_presence,
                               lambda _ok, _value: self.refresh_connection_state())

    def _save_conn_params(self):
        if not self.programmer:
            return
        family = self.combo_family.currentText().strip()
        interface = self.edit_interface.text().strip()
        speed = self.edit_speed.text().strip()
        self.programmer.device_family = family
        self.programmer.interface = interface
        self.programmer.speed = speed
        db = getattr(self.device_manager, "db_manager", None)
        if db:
            try:
                db.save_sys_config({
                    "e2_device_family": family,
                    "e2_interface": interface,
                    "e2_speed": speed,
                })
            except Exception:
                pass

    def _require_initialized_connection(self):
        if self.programmer and self.programmer.tool_ready:
            return True
        QMessageBox.warning(
            self, "瑞萨烧录工具未就绪",
            "请先在“硬件管理中心”初始化本机 rfp-cli 工具。\n"
            "调试页不会另建目标连接或自动重连。",
        )
        return False

    def select_project(self):
        current = self.combo_project.currentText().strip()
        start_dir = current if os.path.isfile(current) or os.path.isdir(current) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择瑞萨E2烧录工程", start_dir,
            "瑞萨烧录工程 (*.rpj *.mot *.hex *.srec);;所有文件 (*)",
        )
        if path:
            norm_path = os.path.normpath(path)
            idx = self.combo_project.findText(norm_path)
            if idx < 0:
                self.combo_project.insertItem(0, norm_path)
                self.combo_project.setCurrentIndex(0)
            else:
                self.combo_project.setCurrentIndex(idx)

    def query_state(self):
        if not self._require_initialized_connection() or self._running:
            return
        self._running = True
        self.refresh_connection_state()
        self.log_message.emit("正在检测E2 USB在位状态；目标连接不做启动检测…")

        def task():
            state = self.programmer.query_state()
            display = "E2 USB已识别（目标连接未验证）" if state == "Ready" else state
            if state in ("Pass", "Error"):
                display = f"上次操作：{state}（目标连接未验证）"
            if state == 'Lost':
                display = self.programmer.last_error or 'E2未连接'
            self.operation_finished.emit(state not in ("Lost", "Error"), display)

        threading.Thread(target=task, daemon=True).start()

    def open_records_folder(self):
        folder = os.path.abspath("logs/burn_records")
        os.makedirs(folder, exist_ok=True)
        try:
            os.startfile(folder)
        except Exception as e:
            self._append_log(f"打开留痕目录失败: {e}")

    def _on_project_selection_changed(self,text):
        self._preview_text=str(text).strip()
        self.preview_timer.start(300)

    def _load_project_preview(self):
        if self.preview_task.isRunning():
            self.preview_timer.start(300);return
        text=self._preview_text
        family=self.combo_family.currentText() if hasattr(self,'combo_family') else ''

        text = str(text).strip()
        if not text or text.startswith("（"):
            return

        def task():
            if os.path.isfile(text):
                try:
                    with open(text, "rb") as f:
                        data = f.read()
                    meta = {
                        "valid": True,
                        "source_file": os.path.basename(text),
                        "chip_name": family,
                        "opd_checksum": f"0x{zlib.crc32(data) & 0xFFFFFFFF:08X}",
                        "summary_address_range": f"文件大小 {len(data):,} 字节",
                        "sectors": [],
                        "project_name": text,
                        "project_path": text,
                    }
                    self.metadata_loaded.emit(meta)
                except Exception as e:
                    self.log_message.emit(f"解析工程文件失败: {e}")

        self.preview_task.run(task,lambda *_:None)

    def _on_metadata_loaded(self, meta: dict):
        if meta.get("project_name") != self.combo_project.currentText().strip():return
        self._current_meta = meta
        if meta.get("valid"):
            self.lbl_audit_source_file.setText(meta.get("source_file", "—"))
            self.lbl_audit_chip.setText(meta.get("chip_name", "—"))
            self.lbl_audit_exp_crc.setText(meta.get("opd_checksum", "—"))
            self.lbl_audit_sectors.setText(meta.get("summary_address_range", "—"))
            self.lbl_audit_act_crc.setText("未烧录")
            self.lbl_audit_status.setText("待烧录")
            self.lbl_audit_status.setStyleSheet("color:#94A3B8; font-weight:bold;")

    def _record_burn_and_emit(self, project: str, success: bool, state: str, duration: float, deep_diff: bool = True):
        act_crc = "未知"
        hw_log = ""
        try:
            act_crc = self.programmer.get_checksum() or "未知"
        except Exception as e:
            act_crc = f"获取失败({e})"

        try:
            hw_log = self.programmer.get_recent_log(max_lines=60)
        except Exception:
            hw_log = ""

        diagnosis = "烧录成功完成" if success else f"烧录异常: {state}"
        if "[Error]" in hw_log:
            diagnosis = "rfp-cli 报错，详见底层日志"
        elif "timeout" in hw_log.lower() or "超时" in hw_log:
            diagnosis = "rfp-cli 执行超时，疑似芯片未供电或引脚接触不良"

        diff_details = None
        source_bytes = readback_bytes = b""
        base_address = 0
        if success and deep_diff:
            try:
                from core.renesas_verify import verify_readback
                source_bytes, readback_bytes, base_address, diff_details = verify_readback(self.programmer, project)
                success = diff_details['is_match']
                diagnosis = "源程序与回读数据逐字节一致" if success else "源程序与回读数据不一致"
            except Exception as exc:
                success = False
                diagnosis = f"逐字节校验未完成: {exc}"
                self.log_message.emit(diagnosis)

        try:
            res = record_burn_event({
                "sn": "DUT",
                "project_name": project,
                "source_file": self._current_meta.get("source_file", "未知"),
                "chip_name": self._current_meta.get("chip_name", self.combo_family.currentText()),
                "address_range": self._current_meta.get("summary_address_range", "未知"),
                "expected_checksum": self._current_meta.get("opd_checksum", "未知"),
                "actual_checksum": act_crc,
                "vcc_mv": self._current_meta.get("vcc_mv", 3300),
                "duration_sec": duration,
                "result": "PASS" if success else "FAIL",
                "diagnosis": diagnosis,
                "hardware_log": hw_log,
                "diff_details": diff_details,
                "source_bytes": source_bytes,
                "readback_bytes": readback_bytes,
                "base_address": base_address,
            })
            res["act_crc"] = act_crc
            self.burn_record_updated.emit(res)
        except Exception as e:
            self.log_message.emit(f"生成留痕记录事件失败: {e}")
        return success, (state if success else diagnosis)

    def _on_burn_record_updated(self, res: dict):
        match = res.get("match", False)
        match_str = res.get("match_str", "—")
        act_crc = res.get("act_crc", "—")
        self.lbl_audit_act_crc.setText(act_crc)
        self.lbl_audit_status.setText(match_str)
        self.lbl_audit_status.setStyleSheet(
            f"color:{'#22C55E' if match else '#FF4D5A'}; font-weight:bold; font-size:15px;"
        )
        report_path = res.get("report_path", "")
        diag = res.get("diagnosis", "")
        summary_text = (
            f"【留痕归档完成】{time.strftime('%Y-%m-%d %H:%M:%S')} | 比对结论: {match_str}\n"
            f"基准校验码: {self._current_meta.get('opd_checksum', '—')} | 芯片实测: {act_crc}\n"
            f"诊断说明: {diag}\n"
            f"全息报告: {report_path}"
        )
        self.txt_audit_summary.setPlainText(summary_text)
        self._append_log(f"【烧录全息留痕】结论: {match_str} | 全息报告: {report_path}")

    def fetch_hardware_log(self):
        if not self.programmer:
            return
        self.log_message.emit("正在拉取瑞萨 E2 驱动日志缓冲…")

        def task():
            hw_log = self.programmer.get_recent_log(max_lines=80)
            if hw_log:
                self.log_message.emit("------ [rfp-cli 最近输出与驱动操作日志] ------\n" + hw_log)
            else:
                self.log_message.emit("驱动日志缓冲为空")

        threading.Thread(target=task, daemon=True).start()

    def run_project(self):
        if not self._require_initialized_connection() or self._running:
            return
        project = self.combo_project.currentText().strip()
        if not project or project.startswith("（"):
            QMessageBox.warning(self, "工程未选择", "请先在下拉框选择工程，或点击【📂 浏览文件】。")
            return
        if not project.lower().endswith((".rpj", ".mot", ".hex", ".srec")):
            QMessageBox.warning(self, "工程文件错误", "请选择 .rpj / .mot / .hex / .srec 烧录工程。")
            return
        if not os.path.isfile(project):
            QMessageBox.warning(self, "工程文件不存在", f"本地工程文件不存在:\n{project}")
            return
        timeout = self.spin_timeout.value()
        deep_diff_enabled = self.chk_deep_diff.isChecked()
        self._running = True
        self.refresh_connection_state()
        self.log_message.emit(f"开始执行瑞萨 E2 烧录工程: {project}")
        start_time = time.monotonic()

        def task():
            success = False
            state = "Lost"
            duration = 0.0
            try:
                success = bool(self.programmer.run_project(
                    project, timeout_s=timeout,
                    progress_cb=lambda line: self.log_message.emit(line),
                ))
                duration = time.monotonic() - start_time
                state = "Pass" if success else (self.programmer.last_error or "Error")
            except Exception as e:
                state = f"执行异常: {e}"
                duration = time.monotonic() - start_time
            finally:
                # 无论发生何种情况，必须确保留痕安全执行且 operation_finished 必定触发，释放按钮锁
                try:
                    verified = self._record_burn_and_emit(project, success, state, duration, deep_diff=deep_diff_enabled)
                    if isinstance(verified, tuple):
                        success, state = verified
                except Exception as log_err:
                    self.log_message.emit(f"留痕归档异常(已兜底隔离): {log_err}")
                self.operation_finished.emit(success, state)

        threading.Thread(target=task, daemon=True).start()

    def _finish_operation(self, success, result):
        self._running = False
        self.lbl_device_state.setText(result)
        self.lbl_device_state.setStyleSheet(
            f"font-size:20px; font-weight:bold; color:{'#22C55E' if success else '#FF4D5A'};"
        )
        self._append_log(f"{'PASS' if success else 'FAIL'}: {result}")
        self.refresh_connection_state()

    def _append_log(self, message):
        self.log_output.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def shutdown(self):
        self.status_timer.stop()
        self.preview_timer.stop()
        self.preview_task.shutdown()
        self.presence_task.shutdown()
