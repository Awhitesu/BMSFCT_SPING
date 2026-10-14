from ui.device_task import DeviceTask
import os
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.burn_recorder import (
    compare_and_diff_data,
    extract_source_data_bytes,
    generate_source_address_data_image,
    parse_opj_metadata,
    record_burn_event,
)
from ui.dialogs.step_dialog import SmartProjectComboBox


class OpteeQTab(QWidget):
    """力捷丰 SG400 调试页：始终复用 DeviceManager 在启动自检中创建的唯一驱动实例。"""

    operation_finished = Signal(bool, str)
    log_message = Signal(str)
    remote_projects_loaded = Signal(list)
    export_finished = Signal(bool, str)
    metadata_loaded = Signal(dict)
    burn_record_updated = Signal(dict)

    def __init__(self, device_manager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self.programmer = device_manager.opteeq
        self._running = False
        self.preview_task=DeviceTask(self)
        self.preview_timer=QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._load_project_preview)
        self._preview_text=''

        self._has_auto_read = False
        self._current_meta = {}
        self._init_ui()
        self.operation_finished.connect(self._finish_operation)
        self.log_message.connect(self._append_log)
        self.remote_projects_loaded.connect(self._on_remote_projects_loaded)
        self.export_finished.connect(self._on_export_finished)
        self.metadata_loaded.connect(self._on_metadata_loaded)
        self.burn_record_updated.connect(self._on_burn_record_updated)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_connection_state)
        self.status_timer.start(1000)
        self.refresh_connection_state()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 1. 顶部紧凑公用状态栏 (单行 ~46px，彻底消除原 2 个大 GroupBox 的过度纵向挤压)
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

        lbl_title = QLabel("力捷丰 SG400 (CH1)")
        lbl_title.setStyleSheet("font-weight: bold; color: #F8FAFC; font-size: 15px;")
        header_layout.addWidget(lbl_title)

        sep1 = QLabel("|")
        sep1.setStyleSheet("color: #334155;")
        header_layout.addWidget(sep1)

        header_layout.addWidget(QLabel("IP:"))
        self.edit_ip = QLineEdit(self.programmer.ip)
        self.edit_ip.setReadOnly(True)
        self.edit_ip.setFixedWidth(115)
        header_layout.addWidget(self.edit_ip)

        header_layout.addWidget(QLabel("端口:"))
        self.edit_port = QLineEdit(str(self.programmer.port))
        self.edit_port.setReadOnly(True)
        self.edit_port.setFixedWidth(65)
        header_layout.addWidget(self.edit_port)

        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #334155;")
        header_layout.addWidget(sep2)

        header_layout.addWidget(QLabel("CH1 状态:"))
        self.lbl_device_state = QLabel("未查询")
        self.lbl_device_state.setStyleSheet("font-size:14px; font-weight:bold; color:#00E5FF;")
        header_layout.addWidget(self.lbl_device_state)

        self.btn_query = QPushButton("🔄 查询 CH1 状态")
        self.btn_query.setMinimumWidth(150)
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
        self.combo_project.lineEdit().setPlaceholderText("从下拉框选择下载器内置工程，或浏览/输入工程路径")
        self.combo_project.addItem("（点击右侧读取下载器工程列表）")
        self.combo_project.currentIndexChanged.connect(lambda _: self.refresh_connection_state())
        self.combo_project.editTextChanged.connect(lambda _: self.refresh_connection_state())
        self.combo_project.currentTextChanged.connect(self._on_project_selection_changed)
        proj_row.addWidget(self.combo_project, 1)

        self.btn_read_remote = QPushButton("🔄 读取下载器工程")
        self.btn_read_remote.setToolTip("在线重新查询下载器内部已存储的脱机工程列表 (/home/s2g/proj/)")
        self.btn_read_remote.clicked.connect(self.read_remote_projects)
        proj_row.addWidget(self.btn_read_remote)

        self.btn_browse = QPushButton("📂 本地文件")
        self.btn_browse.setToolTip("选择电脑本地的 .oprj 或 .opj 工程文件")
        self.btn_browse.clicked.connect(self.select_project)
        proj_row.addWidget(self.btn_browse)

        self.btn_export_remote = QPushButton("⬇️ 导出到本地")
        self.btn_export_remote.setToolTip("将选中的下载器内置工程保存到本地电脑")
        self.btn_export_remote.clicked.connect(self.export_remote_project)
        proj_row.addWidget(self.btn_export_remote)

        project_layout.addLayout(proj_row, 0, 1, 1, 2)

        project_layout.addWidget(QLabel("完成超时:"), 1, 0)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(1, 3600)
        self.spin_timeout.setValue(300)
        self.spin_timeout.setSuffix(" s")
        self.spin_timeout.setFixedWidth(120)
        project_layout.addWidget(self.spin_timeout, 1, 1)

        self.btn_run = QPushButton("🚀 执行 CH1 烧录")
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
        self.chk_deep_diff.setToolTip("烧录成功后从芯片物理回读数据并与源程序镜像进行 Byte-by-Byte 比对")
        ctrl_row.addWidget(self.chk_deep_diff)

        ctrl_row.addStretch()

        self.btn_readback_probe = QPushButton("🔬 回读自检")
        self.btn_readback_probe.setFixedHeight(30)
        self.btn_readback_probe.setToolTip(
            "不烧录，直接测试 readchip 回读通道：按 256B/4KB/16KB/64KB/整包 逐级回读，"
            "报告每档耗时与实收字节数，用于诊断回读链路"
        )
        self.btn_readback_probe.clicked.connect(self.run_readback_probe)
        ctrl_row.addWidget(self.btn_readback_probe)

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
        log_tip = QLabel("实时显示力捷丰 SG400 控制器通信事件、Lua 脚本回显与底层 /log/ch.log 工序")
        log_tip.setStyleSheet("color:#64748B; font-size:13px;")
        log_top_row.addWidget(log_tip)
        log_top_row.addStretch()

        self.btn_fetch_hw_log = QPushButton("🔄 拉取底层日志 (/log/ch.log)")
        self.btn_fetch_hw_log.setToolTip("从物理下载器内部拉取最近工序日志与 Vcc 供电状态")
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
        if not self.isVisible(): return
        connected = bool(self.programmer and self.programmer.is_connected)
        self.lbl_connection.setText("●  已连接" if connected else "●  未连接")
        color = "#22C55E" if connected else "#FF4D5A"
        self.lbl_connection.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:6px; "
            "padding:7px 14px; font-weight:bold;"
        )
        can_operate = connected and not self._running
        self.btn_query.setEnabled(can_operate)
        self.btn_run.setEnabled(can_operate)
        self.btn_read_remote.setEnabled(can_operate)
        self.btn_browse.setEnabled(can_operate)
        cur_text = self.combo_project.currentText().strip()
        has_valid_project = bool(cur_text and not cur_text.startswith("（"))
        self.btn_export_remote.setEnabled(can_operate and has_valid_project)

        # 设备在线且尚未自动加载时，触发一次自动获取下载器内置工程
        if connected and not self._has_auto_read and not self._running:
            self._has_auto_read = True
            QTimer.singleShot(100, self.read_remote_projects)
        elif not connected:
            self._has_auto_read = False

    def _require_initialized_connection(self):
        if self.programmer and self.programmer.is_connected:
            return True
        QMessageBox.warning(
            self, "SG400 未连接",
            "请先在“硬件管理中心”初始化力捷丰 SG400。\n"
            "调试页不会另建连接或自动重连。",
        )
        return False

    def select_project(self):
        current = self.combo_project.currentText().strip()
        start_dir = current if os.path.isfile(current) or os.path.isdir(current) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择力捷丰烧录工程", start_dir,
            "OpteeQ 工程 (*.oprj *.opj);;所有文件 (*)",
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
        self.log_message.emit("正在查询 SG400 CH1 状态…")

        def task():
            state = self.programmer.query_state()
            self.operation_finished.emit(state != "Lost", state)

        threading.Thread(target=task, daemon=True).start()

    def read_remote_projects(self):
        if not self._require_initialized_connection() or self._running:
            return
        self._running = True
        self.refresh_connection_state()
        self.log_message.emit("正在读取力捷丰下载器工程列表 (/home/s2g/proj/)…")

        def task():
            files = self.programmer.get_project_list()
            self.remote_projects_loaded.emit(files)

        threading.Thread(target=task, daemon=True).start()

    def _on_remote_projects_loaded(self, files: list):
        self._running = False
        prev_selection = self.combo_project.currentText().strip()
        self.combo_project.clear()
        if files:
            self.combo_project.addItems(files)
            for i in range(self.combo_project.count()):
                self.combo_project.setItemData(i, self.combo_project.itemText(i), Qt.ToolTipRole)
            self.log_message.emit(f"成功读取到 {len(files)} 个下载器内部工程: {', '.join(files)}")
            idx = self.combo_project.findText(prev_selection)
            if idx >= 0:
                self.combo_project.setCurrentIndex(idx)
            else:
                self.combo_project.setCurrentIndex(0)
        else:
            self.combo_project.addItem("（下载器内无脱机工程）")
            err = self.programmer.last_error if self.programmer else ""
            self.log_message.emit(f"未读取到下载器工程文件{f' ({err})' if err else ''}")
        self.refresh_connection_state()

    def export_remote_project(self):
        selected = self.combo_project.currentText().strip()
        if not selected or selected.startswith("（"):
            QMessageBox.information(self, "提示", "请先在下拉框选择有效的脱机工程文件。")
            return
        if not self._require_initialized_connection() or self._running:
            return
        default_name = os.path.basename(selected.replace("\\", "/"))
        local_path, _ = QFileDialog.getSaveFileName(
            self, "导出下载器工程到本地", default_name,
            "OpteeQ 工程 (*.opj *.oprj);;所有文件 (*)",
        )
        if not local_path:
            return
        self._running = True
        self.refresh_connection_state()
        self.log_message.emit(f"开始从下载器导出工程: {selected} -> {local_path}…")

        def task():
            ok = self.programmer.download_project_file(selected, local_path)
            err = self.programmer.last_error if not ok else ""
            self.export_finished.emit(ok, local_path if ok else err)

        threading.Thread(target=task, daemon=True).start()

    def _on_export_finished(self, success: bool, result: str):
        self._running = False
        self.refresh_connection_state()
        if success:
            self.log_message.emit(f"工程导出成功: {result}")
            QMessageBox.information(self, "导出成功", f"工程文件已成功保存到本地:\n{result}")
        else:
            self.log_message.emit(f"工程导出失败: {result}")
            QMessageBox.warning(self, "导出失败", f"导出工程文件失败:\n{result}")

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
            local_path = text
            if not os.path.isfile(local_path):
                cache_dir = os.path.abspath("cache/projects")
                os.makedirs(cache_dir, exist_ok=True)
                local_path = os.path.join(cache_dir, text)
                if not os.path.isfile(local_path) and self.programmer:
                    self.programmer.download_project_file(text, local_path)

            if os.path.isfile(local_path):
                meta = parse_opj_metadata(local_path)
                meta["project_name"] = text
                meta["project_path"] = local_path
                self.metadata_loaded.emit(meta)
                img_dir = os.path.abspath("logs/burn_records/images")
                os.makedirs(img_dir, exist_ok=True)
                img_path = os.path.join(img_dir, f"{os.path.basename(text)}_image.txt")
                generate_source_address_data_image(local_path, img_path)

        self.preview_task.run(task,lambda *_:None)

    def _on_metadata_loaded(self, meta: dict):
        if meta.get("project_name") != self.combo_project.currentText().strip():return
        if meta.get('project_name') != self.combo_project.currentText().strip():
            return
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
        # 运行结束重新绑定当前工程，不能使用异步预览留下的旧元数据。
        import tempfile
        from core.burn_recorder import verify_project_readback
        with tempfile.TemporaryDirectory(prefix='sg400_project_') as folder:
            path = project
            if not os.path.isfile(path):
                path = os.path.join(folder, os.path.basename(project))
                if not self.programmer.download_project_file(project, path):
                    if success and deep_diff:
                        return False, '校验失败：无法获取本次执行工程'
            self._current_meta = parse_opj_metadata(path)
            self._current_meta['project_path'] = path
            return self._record_bound_burn(project, success, state, duration, deep_diff)

    def _record_bound_burn(self, project, success, state, duration, deep_diff):
        from core.burn_recorder import verify_project_readback
        act_crc = "未知"
        hw_log = ""
        try:
            act_crc = self.programmer.get_checksum() or "未知"
        except Exception as e:
            act_crc = f"获取失败({e})"

        try:
            hw_log = self.programmer.get_recent_log(60)
        except Exception:
            hw_log = ""

        diagnosis = "烧录成功完成" if success else f"烧录异常: {state}"
        if "Sync failed" in hw_log:
            diagnosis = "DAP时钟同步失败，疑似芯片未供电(Vcc=0mv)或引脚接触不良"
        elif "Init FAIL" in hw_log:
            diagnosis = "芯片初始化失败"
        elif "Vcc = 0" in hw_log:
            diagnosis = "检测到实测供电 Vcc=0mV"

        diff_details = None
        source_bytes, read_bytes, start_addr = b"", b"", 0
        # 仅当烧录成功、勾选了逐字节校验、且当前工程具备有效的物理扇区时才触发硬件回读
        has_sectors = bool(self._current_meta.get("valid") and self._current_meta.get("sectors"))
        if success and deep_diff:
            try:
                source_bytes, read_bytes, start_addr, diff_details = verify_project_readback(
                    self.programmer, project, self._current_meta['project_path'])
                if not diff_details['is_match']:
                    success = False
                    diagnosis = f"物理回读比对不一致({diff_details['mismatch_count']} 字节)"
                    state = diagnosis
            except Exception as exc:
                success = False
                diagnosis = state = f'回读校验失败: {exc}'
            if not read_bytes:
                self.log_message.emit(
                    f"物理回读未完成：{diagnosis}"
                )

        try:
            res = record_burn_event({
                "sn": "调试",
                "project_name": project,
                "source_file": self._current_meta.get("source_file", "未知"),
                "chip_name": self._current_meta.get("chip_name", "未知"),
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
                "readback_bytes": read_bytes,
                "base_address": start_addr,
            })
            res["act_crc"] = act_crc
            self.burn_record_updated.emit(res)
        except Exception as e:
            self.log_message.emit(f"生成留痕记录事件失败: {e}")
        return success, state

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
        if res.get("csv_error"):
            self._append_log(f"⚠️ {res['csv_error']}")

    def fetch_hardware_log(self):
        if not self._require_initialized_connection() or self._running:
            return
        self.log_message.emit("正在拉取力捷丰下载器底层日志 (/log/ch.log)…")

        def task():
            hw_log = self.programmer.get_recent_log(80)
            if hw_log:
                self.log_message.emit("------ [下载器底层工序与供电日志 /log/ch.log] ------\n" + hw_log)
            else:
                self.log_message.emit("未能拉取到底层日志或日志为空")

        threading.Thread(target=task, daemon=True).start()

    def run_readback_probe(self):
        """回读自检：不烧录，逐级测试 readchip 通道的耗时与实收字节数"""
        if not self._require_initialized_connection() or self._running:
            return
        project = self.combo_project.currentText().strip()
        if not project or project.startswith("（"):
            QMessageBox.warning(self, "工程未选择", "请先在下拉框选择下载器工程。")
            return
        meta = self._current_meta or {}
        sectors = meta.get("sectors") or []
        start_addr = sectors[0].get("start_addr", 0xA0000000) if sectors else 0xA0000000
        opd_size = meta.get("opd_size", 0)
        levels = [256, 4096, 16384, 65536]
        if opd_size > 0 and opd_size not in levels:
            levels.append(opd_size)

        self._running = True
        self.refresh_connection_state()
        self.log_message.emit(
            f"【回读自检】开始 (工程: {project}, 起始: 0x{start_addr:08X})，请保持目标供电…"
        )

        def task():
            try:
                for size in levels:
                    timeout_ms = min(60000, max(8000, size // 10))
                    t0 = time.monotonic()
                    data = self.programmer.read_chip(project, start_addr, size, timeout_ms=timeout_ms)
                    elapsed = time.monotonic() - t0
                    if data:
                        head = data[:16].hex(" ").upper()
                        kind = "IntelHEX文本" if data.lstrip()[:1] == b":" else "原始二进制"
                        self.log_message.emit(
                            f"【回读自检】{size}B @0x{start_addr:08X} → ✅ 实收 {len(data)} 字节, "
                            f"耗时 {elapsed:.1f}s, 格式: {kind}, 首16字节: {head}"
                        )
                    else:
                        err = getattr(self.programmer, "last_error", "") or "无数据"
                        self.log_message.emit(
                            f"【回读自检】{size}B @0x{start_addr:08X} → ❌ 实收 0 字节, "
                            f"耗时 {elapsed:.1f}s ({err})"
                        )
                self.log_message.emit("【回读自检】完成")
            finally:
                self.operation_finished.emit(True, "回读自检完成")

        threading.Thread(target=task, daemon=True).start()

    def run_project(self):
        if not self._require_initialized_connection() or self._running:
            return
        project = self.combo_project.currentText().strip()
        if not project or project.startswith("（"):
            QMessageBox.warning(self, "工程未选择", "请先在下拉框选择下载器工程，或点击【📂 本地文件】。")
            return
        if not project.lower().endswith((".oprj", ".opj")):
            QMessageBox.warning(self, "工程文件错误", "请选择 .oprj 或 .opj 烧录工程。")
            return
        is_remote_name = project.lower().endswith(".opj") and not os.path.isabs(project)
        if not is_remote_name and not os.path.isfile(project):
            QMessageBox.warning(self, "工程文件不存在", f"本地工程文件不存在:\n{project}")
            return
        timeout = self.spin_timeout.value()
        deep_diff_enabled = self.chk_deep_diff.isChecked()
        self._running = True
        self.refresh_connection_state()
        self.log_message.emit(f"开始执行 CH1 烧录工程: {project}")
        start_time = time.monotonic()

        def task():
            success = False
            state = "Lost"
            duration = 0.0
            try:
                if not self.programmer.run_project(project):
                    state = self.programmer.last_error or "工程启动失败"
                    return
                deadline = time.monotonic() + timeout
                last_state = None
                while time.monotonic() < deadline:
                    state = self.programmer.query_state()
                    if state != last_state:
                        self.log_message.emit(f"CH1 状态: {state}")
                        last_state = state
                    if state == "Pass":
                        success = True
                        duration = time.monotonic() - start_time
                        return
                    if state in ("Error", "Lost"):
                        success = False
                        duration = time.monotonic() - start_time
                        return
                    time.sleep(0.2)
                state = "Timeout"
                duration = time.monotonic() - start_time
            except Exception as e:
                state = f"执行异常: {e}"
            finally:
                # 无论发生何种情况，必须确保留痕安全执行且 operation_finished 必定触发，释放按钮锁
                try:
                    success, state = self._record_burn_and_emit(project, success, state, duration, deep_diff=deep_diff_enabled)
                except Exception as log_err:
                    self.log_message.emit(f"留痕归档异常(已兜底隔离): {log_err}")
                    if deep_diff_enabled:
                        success, state = False, f'校验异常: {log_err}'
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
