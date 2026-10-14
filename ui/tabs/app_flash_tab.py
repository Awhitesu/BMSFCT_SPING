import os
import glob
import time
import configparser
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)


class AppFlashTab(QWidget):
    """
    APP 固件刷写独立调试页 (精准对齐 LabVIEW 触发刷写真实工业逻辑)
    业务流：
    1. 上位机路径 (可为 .exe 或所在目录)，同级读取 config.ini (纯只读，绝不写)；
    2. 读取指定段名 (默认 App) 与指定键名 (默认 addr)，自动清洗去除外层引号 (如 "Slate SR" -> Slate SR)；
    3. 在同级 Flash 根目录下定位与键值同名的子目录 (Flash/<键值>/)，检索 *.s19/*.hex 提取首个固件作为 APP Name；
    4. 向同级 Flash/Record.txt 写入 Start，正式开启底层刷写并留痕；
    5. 支持单步独立调试与一键全流程自动化执行。
    """

    def __init__(self, device_manager=None, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------
        # 1. 上位机路径与 INI 配置只读区域
        # -------------------------------------------------------------
        group_ini = QGroupBox("1. 上位机路径与 INI 配置 (同级只读)")
        layout_ini = QGridLayout(group_ini)
        layout_ini.setSpacing(8)

        # 上位机路径 (支持选择程序文件或所在文件夹)
        layout_ini.addWidget(QLabel("上位机路径:"), 0, 0)
        default_base_path = os.path.abspath(os.getcwd())
        self.edit_base_path = QLineEdit(default_base_path)
        layout_ini.addWidget(self.edit_base_path, 0, 1, 1, 2)

        self.btn_browse_file = QPushButton("📄 浏览文件")
        self.btn_browse_file.clicked.connect(self._on_browse_file)
        layout_ini.addWidget(self.btn_browse_file, 0, 3)

        self.btn_browse_dir = QPushButton("📂 浏览目录")
        self.btn_browse_dir.clicked.connect(self._on_browse_dir)
        layout_ini.addWidget(self.btn_browse_dir, 0, 4)

        # INI 相对路径 (默认同级 config.ini)
        layout_ini.addWidget(QLabel("INI 文件名/路径:"), 1, 0)
        self.edit_ini_rel_path = QLineEdit("config.ini")
        layout_ini.addWidget(self.edit_ini_rel_path, 1, 1)

        # Section 与 Key (默认 App / addr)
        layout_ini.addWidget(QLabel("Section (段名):"), 1, 2)
        self.edit_section = QLineEdit("App")
        layout_ini.addWidget(self.edit_section, 1, 3)

        layout_ini.addWidget(QLabel("Key (键名):"), 2, 0)
        self.edit_key = QLineEdit("addr")
        layout_ini.addWidget(self.edit_key, 2, 1)

        # 读取结果展示
        layout_ini.addWidget(QLabel("读取清洗值:"), 2, 2)
        self.edit_read_result = QLineEdit()
        self.edit_read_result.setReadOnly(True)
        self.edit_read_result.setPlaceholderText("读取到的键值 (已剔除引号)")
        self.edit_read_result.setStyleSheet("font-weight:bold; color:#10B981; background-color:#0F172A;")
        layout_ini.addWidget(self.edit_read_result, 2, 3)

        # 读取配置按钮
        self.btn_read_ini = QPushButton("📖 读取配置 (只读)")
        self.btn_read_ini.setStyleSheet("background-color:#1E3A8A; color:#FFFFFF; font-weight:bold; height:32px;")
        self.btn_read_ini.clicked.connect(self.read_config)
        layout_ini.addWidget(self.btn_read_ini, 2, 4)

        main_layout.addWidget(group_ini)

        # -------------------------------------------------------------
        # 中间区域：左侧 固件检索 与 右侧 Record 写入 / 一键开启刷写
        # -------------------------------------------------------------
        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(10)

        # 2. 固件扫描与提取面板
        group_firmware = QGroupBox("2. 目标固件检索 (Flash/<键值>/*.s19/*.hex)")
        layout_firmware = QVBoxLayout(group_firmware)
        layout_firmware.setSpacing(8)

        row_flash_dir = QHBoxLayout()
        row_flash_dir.addWidget(QLabel("Flash 子目录:"))
        self.edit_target_flash_dir = QLineEdit()
        self.edit_target_flash_dir.setReadOnly(True)
        self.edit_target_flash_dir.setPlaceholderText("自动解析同级 Flash/<键值> 物理目录")
        row_flash_dir.addWidget(self.edit_target_flash_dir)
        self.btn_scan_firmware = QPushButton("🔍 检索固件")
        self.btn_scan_firmware.clicked.connect(self.scan_firmware)
        row_flash_dir.addWidget(self.btn_scan_firmware)
        layout_firmware.addLayout(row_flash_dir)

        row_app_name = QHBoxLayout()
        row_app_name.addWidget(QLabel("APP Name (识别首选):"))
        self.edit_app_name = QLineEdit()
        self.edit_app_name.setReadOnly(True)
        self.edit_app_name.setStyleSheet("font-weight:bold; font-size:14px; color:#38BDF8; background-color:#0F172A;")
        row_app_name.addWidget(self.edit_app_name)
        layout_firmware.addLayout(row_app_name)

        # 固件表格
        self.table_firmware = QTableWidget()
        self.table_firmware.setColumnCount(3)
        self.table_firmware.setHorizontalHeaderLabels(["固件文件名", "格式", "大小 (KB)"])
        self.table_firmware.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_firmware.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_firmware.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout_firmware.addWidget(self.table_firmware)

        mid_layout.addWidget(group_firmware, stretch=3)

        # 3. 刷写开启 (Flash/Record.txt 写入 Start) 与 一键执行面板
        group_record = QGroupBox("3. 开启刷写 (向 Flash/Record.txt 写入 Start)")
        layout_record = QVBoxLayout(group_record)
        layout_record.setSpacing(8)

        row_record_path = QHBoxLayout()
        row_record_path.addWidget(QLabel("Record 相对路径:"))
        self.edit_record_rel_path = QLineEdit(os.path.join("Flash", "Record.txt"))
        row_record_path.addWidget(self.edit_record_rel_path)
        layout_record.addLayout(row_record_path)

        row_record_btn = QHBoxLayout()
        self.btn_write_start = QPushButton("✍️ 写入 Start 开启刷写")
        self.btn_write_start.setStyleSheet("background-color:#D97706; color:#FFFFFF; font-weight:bold;")
        self.btn_write_start.clicked.connect(self.write_start_to_record)
        row_record_btn.addWidget(self.btn_write_start)

        self.btn_read_record = QPushButton("👁️ 预览 Record")
        self.btn_read_record.clicked.connect(self.preview_record)
        row_record_btn.addWidget(self.btn_read_record)
        layout_record.addLayout(row_record_btn)

        self.edit_record_preview = QTextEdit()
        self.edit_record_preview.setReadOnly(True)
        self.edit_record_preview.setPlaceholderText("Record.txt 最近内容预览...")
        layout_record.addWidget(self.edit_record_preview)

        # 核心大按钮：一键触发刷写
        self.btn_trigger_full = QPushButton("🚀 一键开启刷写 (完整 LabVIEW 流程)")
        self.btn_trigger_full.setStyleSheet(
            "background-color:#059669; color:#FFFFFF; font-size:15px; font-weight:bold; height:42px; border-radius:6px;"
        )
        self.btn_trigger_full.clicked.connect(self.trigger_full_flow)
        layout_record.addWidget(self.btn_trigger_full)

        mid_layout.addWidget(group_record, stretch=2)
        main_layout.addLayout(mid_layout, stretch=2)

        # -------------------------------------------------------------
        # 4. 操作日志监控
        # -------------------------------------------------------------
        group_log = QGroupBox("4. 操作监控与执行日志")
        layout_log = QVBoxLayout(group_log)
        layout_log.setSpacing(6)

        row_log_head = QHBoxLayout()
        row_log_head.addWidget(QLabel("实时日志:"))
        row_log_head.addStretch()
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.clear_log)
        row_log_head.addWidget(self.btn_clear_log)
        layout_log.addLayout(row_log_head)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color:#020617; color:#E2E8F0; font-family:Consolas, monospace; font-size:12px;")
        layout_log.addWidget(self.txt_log)

        main_layout.addWidget(group_log, stretch=1)

    # -----------------------------------------------------------------
    # 路径解析工具函数
    # -----------------------------------------------------------------
    def _get_host_dir(self) -> str:
        """获取上位机文件所在的同级物理目录"""
        base = self.edit_base_path.text().strip()
        if not base:
            base = os.getcwd()
        # 若 base 是具体文件或带后缀，取其所在目录
        if os.path.isfile(base) or os.path.splitext(base)[1]:
            return os.path.normpath(os.path.dirname(base))
        return os.path.normpath(base)

    def _get_abs_path(self, rel_or_abs: str) -> str:
        """根据上位机同级目录解析物理路径"""
        if os.path.isabs(rel_or_abs):
            return os.path.normpath(rel_or_abs)
        host_dir = self._get_host_dir()
        return os.path.normpath(os.path.join(host_dir, rel_or_abs))

    def _on_browse_file(self):
        cur = self.edit_base_path.text().strip()
        init_dir = self._get_host_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择上位机程序文件", init_dir,
            "所有文件 (*.*);;可执行程序 (*.exe *.bat *.cmd);;LabVIEW/脚本 (*.vi *.py *.ini)"
        )
        if file_path:
            self.edit_base_path.setText(os.path.normpath(file_path))
            self._log(f"上位机程序文件已更新: {file_path}")

    def _on_browse_dir(self):
        cur = self.edit_base_path.text().strip()
        init_dir = self._get_host_dir()
        dir_path = QFileDialog.getExistingDirectory(self, "选择上位机同级目录", init_dir)
        if dir_path:
            self.edit_base_path.setText(os.path.normpath(dir_path))
            self._log(f"上位机目录已更新: {dir_path}")

    def _log(self, msg: str):
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.txt_log.append(f"[{now}] {msg}")

    def clear_log(self):
        self.txt_log.clear()

    # -----------------------------------------------------------------
    # 业务槽函数
    # -----------------------------------------------------------------
    def read_config(self) -> str:
        """同级只读 config.ini 中 [Section] -> Key，并剔除外层引号"""
        ini_path = self._get_abs_path(self.edit_ini_rel_path.text().strip())
        section = self.edit_section.text().strip()
        key = self.edit_key.text().strip()

        self._log(f"读取同级配置文件: {ini_path}")
        if not os.path.exists(ini_path):
            self._log(f"❌ 错误: 配置文件不存在 -> {ini_path}")
            QMessageBox.warning(self, "读取失败", f"配置文件不存在:\n{ini_path}")
            self.edit_read_result.clear()
            return ""

        try:
            config = configparser.ConfigParser()
            config.read(ini_path, encoding="utf-8")
            
            # 大小写不敏感查找 Section
            matched_sec = None
            for s in config.sections():
                if s.strip().lower() == section.lower():
                    matched_sec = s
                    break

            if not matched_sec:
                self._log(f"❌ 错误: 配置文件未找到 Section [{section}]")
                QMessageBox.warning(self, "读取失败", f"未找到 Section [{section}]")
                self.edit_read_result.clear()
                return ""

            # 大小写不敏感查找 Key
            matched_key = None
            for k in config.options(matched_sec):
                if k.strip().lower() == key.lower():
                    matched_key = k
                    break

            if not matched_key:
                self._log(f"❌ 错误: Section [{matched_sec}] 中未找到 Key '{key}'")
                QMessageBox.warning(self, "读取失败", f"Section [{matched_sec}] 中未找到 Key '{key}'")
                self.edit_read_result.clear()
                return ""

            raw_val = config.get(matched_sec, matched_key)
            # 剥离外层双引号、单引号与首尾空白
            clean_val = raw_val.strip(' \t\r\n"\'')
            self.edit_read_result.setText(clean_val)
            self._log(f"✅ 读取成功: [{matched_sec}] -> {matched_key} = {raw_val} -> 清洗值: '{clean_val}'")
            return clean_val
        except Exception as e:
            self._log(f"❌ 读取异常: {e}")
            QMessageBox.critical(self, "读取异常", f"读取 config.ini 异常: {e}")
            self.edit_read_result.clear()
            return ""

    def scan_firmware(self) -> str:
        """在同级 Flash/<键值>/ 目录下检索 *.s19 与 *.hex 并输出 APP Name"""
        # 1. 优先获取当前配置键值
        config_val = self.edit_read_result.text().strip()
        if not config_val:
            config_val = self.read_config()

        if not config_val:
            self._log("⚠️ 请先成功读取配置段与键名，以确定 Flash 目标子目录！")
            return ""

        # 2. 定位同级 Flash/<键值> 目录
        host_dir = self._get_host_dir()
        target_flash_dir = os.path.normpath(os.path.join(host_dir, "Flash", config_val))
        self.edit_target_flash_dir.setText(target_flash_dir)
        self._log(f"定位目标 Flash 子目录: {target_flash_dir}")

        self.table_firmware.setRowCount(0)
        self.edit_app_name.clear()

        if not os.path.exists(target_flash_dir):
            self._log(f"⚠️ 警告: Flash 目标子目录不存在 -> {target_flash_dir}")
            return ""

        # 3. 检索 *.s19, *.S19, *.hex, *.HEX
        patterns = ["*.s19", "*.S19", "*.hex", "*.HEX"]
        all_files = []
        for p in patterns:
            for fpath in glob.glob(os.path.join(target_flash_dir, p)):
                if fpath not in all_files:
                    all_files.append(fpath)

        self._log(f"检索到固件文件: 共 {len(all_files)} 个")

        for fpath in all_files:
            fname = os.path.basename(fpath)
            ext = os.path.splitext(fname)[1].lower()
            size_kb = f"{os.path.getsize(fpath) / 1024:.2f}"
            row = self.table_firmware.rowCount()
            self.table_firmware.insertRow(row)
            self.table_firmware.setItem(row, 0, QTableWidgetItem(fname))
            self.table_firmware.setItem(row, 1, QTableWidgetItem(ext))
            self.table_firmware.setItem(row, 2, QTableWidgetItem(size_kb))

        if all_files:
            first_file = os.path.basename(all_files[0])
            self.edit_app_name.setText(first_file)
            self._log(f"🎯 提取第 1 个固件 (APP Name): {first_file}")
            return first_file
        else:
            self._log(f"⚠️ 未在子目录 '{config_val}' 下找到任何 .s19 或 .hex 固件文件")
            return ""

    def write_start_to_record(self) -> bool:
        """向同级 Flash/Record.txt 写入 Start 开启刷写"""
        record_rel = self.edit_record_rel_path.text().strip() or os.path.join("Flash", "Record.txt")
        record_path = self._get_abs_path(record_rel)
        os.makedirs(os.path.dirname(record_path), exist_ok=True)

        self._log(f"正在向 Record 写入 'Start' 开启刷写: {record_path}")
        try:
            # 写入 Start
            with open(record_path, "w", encoding="utf-8") as f:
                f.write("Start\n")
            self._log(f"✅ 成功向 {record_path} 写入 'Start'，已开启刷写！")
            self.preview_record()
            return True
        except Exception as e:
            self._log(f"❌ 写入 Record.txt 失败: {e}")
            QMessageBox.critical(self, "写入异常", f"写入 Record.txt 异常: {e}")
            return False

    def preview_record(self):
        """预览 Record.txt 的最新内容"""
        record_rel = self.edit_record_rel_path.text().strip() or os.path.join("Flash", "Record.txt")
        record_path = self._get_abs_path(record_rel)
        if not os.path.exists(record_path):
            self.edit_record_preview.setText(f"文件不存在: {record_path}")
            return

        try:
            with open(record_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                self.edit_record_preview.setText(content)
        except Exception as e:
            self.edit_record_preview.setText(f"读取异常: {e}")

    def trigger_full_flow(self):
        """🚀 一键开启刷写 (完整 LabVIEW 流程)"""
        self._log("================ 🚀 开始执行一键触发刷写全流程 ================")
        start_t = time.time()

        # 1. 只读同级 config.ini
        self._log("▶ 步骤 1/3: 只读同级 config.ini 读取目标键值...")
        config_val = self.read_config()
        if not config_val:
            self._log("❌ 流程中止: 未能从 config.ini 读取到有效键值！")
            return

        # 2. 定位 Flash/<键值> 检索首个固件并输出 APP Name
        self._log(f"▶ 步骤 2/3: 在 Flash/{config_val}/ 目录下扫描 *.hex / *.s19 固件...")
        app_name = self.scan_firmware()

        # 3. 向 Flash/Record.txt 写入 Start 开启刷写
        self._log("▶ 步骤 3/3: 向同级 Flash/Record.txt 写入 'Start' 开启刷写...")
        ok_start = self.write_start_to_record()

        elapsed = time.time() - start_t
        if ok_start:
            self._log(f"🎉 刷写已正式开启! 耗时: {elapsed:.3f} 秒, 目标固件: {app_name or '无'}")
            QMessageBox.information(
                self, "开启成功",
                f"已成功开启刷写流程!\n\n"
                f"目标子目录: Flash/{config_val}/\n"
                f"识别固件 (APP Name): {app_name or '未检测到固件文件'}\n"
                f"触发指令: 已写入 Start 到 Flash/Record.txt\n"
                f"总耗时: {elapsed:.3f} s"
            )
        else:
            self._log(f"⚠️ 开启刷写失败, 耗时: {elapsed:.3f} 秒")
