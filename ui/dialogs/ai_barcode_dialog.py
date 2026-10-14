import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QGroupBox, QMessageBox, QFrame, QWidget
)
from PySide6.QtCore import Qt


class AiBarcodeRuleDialog(QDialog):
    """
    AI 条码样本推导与测试专用对话框
    工作流：
    1. 扫码枪连续扫描 2~5 个真实条码样本（或手工输入添加）。
    2. 实时自动对比分析最长公共前缀 (LCP)、最长公共后缀 (LCS) 及中间变化号段字符集。
    3. 实时展现推导的正则表达式与特征分析拆解。
    4. 允许输入任意测试条码实时验证比对。
    5. 点击“确认应用规则到配方”即可自动将正则表达式填入配方设置。
    """
    def __init__(self, parent=None, initial_regex="", default_samples=None):
        super().__init__(parent)
        self.setWindowTitle("🤖 AI 条码样本推导与测试规则引擎")
        self.setFixedSize(760, 680)
        self.setStyleSheet("""
            QDialog {
                background-color: #1A1A2E;
                color: #E0E0E0;
                font-family: 'Segoe UI', Microsoft YaHei, sans-serif;
            }
            QLabel {
                color: #E0E0E0;
            }
            QLineEdit {
                background-color: #16213E;
                color: #00FF66;
                border: 1px solid #0F3460;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            QLineEdit:focus {
                border: 1px solid #00E5FF;
            }
            QPushButton {
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 13px;
            }
        """)

        self.generated_regex = initial_regex or "^.+$"
        self.samples = list(default_samples or [
            "SCUx03XP010010B126091",
            "SCUx03XP010010B126092",
            "SCUx03XP020015B126093"
        ])

        self._init_ui()
        self._recalculate_ai_rule()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(12)

        # -------------------------------------------------------------
        # 1. 顶部导引标题
        # -------------------------------------------------------------
        title_label = QLabel("🎯 智能样本条码录入与规则推导")
        title_label.setStyleSheet("font-size: 17px; font-weight: bold; color: #00E5FF;")
        main_layout.addWidget(title_label)

        desc_label = QLabel("提示：请使用扫码枪连续扫描 2~5 个真实产品条码，AI 将自动对比分析识别固定前缀、固定后缀与变化号段！")
        desc_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        desc_label.setWordWrap(True)
        main_layout.addWidget(desc_label)

        # -------------------------------------------------------------
        # 2. 扫码录入工具栏
        # -------------------------------------------------------------
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.scan_input = QLineEdit()
        self.scan_input.setPlaceholderText("请用扫码枪扫描条码，或在此输入后按回车...")
        self.scan_input.returnPressed.connect(self._on_add_sample_from_input)

        btn_add = QPushButton("➕ 添加样本")
        btn_add.setStyleSheet("background-color: #0F3460; color: white;")
        btn_add.clicked.connect(self._on_add_sample_from_input)

        btn_delete = QPushButton("🗑️ 删除选中")
        btn_delete.setStyleSheet("background-color: #5A5A5A; color: white;")
        btn_delete.clicked.connect(self._on_delete_selected_sample)

        btn_clear = QPushButton("🧹 清空所有")
        btn_clear.setStyleSheet("background-color: #8B0000; color: white;")
        btn_clear.clicked.connect(self._on_clear_all_samples)

        input_layout.addWidget(self.scan_input, 3)
        input_layout.addWidget(btn_add)
        input_layout.addWidget(btn_delete)
        input_layout.addWidget(btn_clear)
        main_layout.addLayout(input_layout)

        # -------------------------------------------------------------
        # 3. 样本列表展示框
        # -------------------------------------------------------------
        self.samples_list_widget = QListWidget()
        self.samples_list_widget.setFixedHeight(130)
        self.samples_list_widget.setStyleSheet("""
            QListWidget {
                background-color: #16213E;
                color: #00FF66;
                border: 1px solid #2D2D4A;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Consolas', monospace;
                font-size: 13px;
                font-weight: bold;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #202D54;
            }
            QListWidget::item:selected {
                background-color: #0F3460;
                color: #00E5FF;
            }
        """)
        self._refresh_samples_list_ui()
        main_layout.addWidget(self.samples_list_widget)

        # -------------------------------------------------------------
        # 4. 实时 AI 推导看板
        # -------------------------------------------------------------
        ai_group = QGroupBox("🤖 AI 实时推导规则与特征分析看板")
        ai_group.setStyleSheet("""
            QGroupBox {
                background-color: #16213E;
                border: 1px solid #0F3460;
                border-radius: 6px;
                margin-top: 6px;
                padding: 10px;
                font-weight: bold;
                color: #00E5FF;
            }
        """)
        ai_layout = QVBoxLayout(ai_group)
        ai_layout.setSpacing(8)

        regex_title_layout = QHBoxLayout()
        regex_lbl_tag = QLabel("推导正则表达式:")
        regex_lbl_tag.setStyleSheet("color: #FFD700; font-size: 13px; font-weight: bold;")
        self.lbl_regex_result = QLabel("^.+$")
        self.lbl_regex_result.setStyleSheet("color: #00FF66; font-size: 16px; font-weight: bold; font-family: 'Consolas', monospace;")
        regex_title_layout.addWidget(regex_lbl_tag)
        regex_title_layout.addWidget(self.lbl_regex_result, 1)
        ai_layout.addLayout(regex_title_layout)

        self.lbl_analysis_details = QLabel("前缀: 无 | 后缀: 无 | 变化号段: 无 | 样本数: 0 个")
        self.lbl_analysis_details.setStyleSheet("color: #E0E0E0; font-size: 12px;")
        ai_layout.addWidget(self.lbl_analysis_details)

        main_layout.addWidget(ai_group)

        # -------------------------------------------------------------
        # 5. 实时测试验证测试区
        # -------------------------------------------------------------
        test_group = QGroupBox("🧪 验证当前推导规则")
        test_group.setStyleSheet("""
            QGroupBox {
                background-color: #16213E;
                border: 1px solid #2D2D4A;
                border-radius: 6px;
                margin-top: 6px;
                padding: 10px;
                font-weight: bold;
                color: #17A2B8;
            }
        """)
        test_layout = QHBoxLayout(test_group)
        test_layout.setSpacing(10)

        test_lbl = QLabel("测试条码:")
        test_lbl.setStyleSheet("color: #E0E0E0; font-size: 13px;")

        self.verify_input = QLineEdit()
        self.verify_input.setPlaceholderText("扫码或输入任意测试条码进行实时判定...")
        self.verify_input.textChanged.connect(self._on_verify_text_changed)

        self.lbl_verify_status = QLabel("等待验证...")
        self.lbl_verify_status.setStyleSheet("color: #888888; font-size: 13px; font-weight: bold;")

        test_layout.addWidget(test_lbl)
        test_layout.addWidget(self.verify_input, 2)
        test_layout.addWidget(self.lbl_verify_status, 1)

        main_layout.addWidget(test_group)

        # -------------------------------------------------------------
        # 6. 底部确认 / 取消操作栏
        # -------------------------------------------------------------
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(15)

        btn_apply = QPushButton("✅ 确认应用规则到配方")
        btn_apply.setMinimumHeight(42)
        btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #28A745;
                color: white;
                font-size: 15px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #34CE57; }
        """)
        btn_apply.clicked.connect(self._on_apply_and_accept)

        btn_cancel = QPushButton("❌ 取消")
        btn_cancel.setMinimumHeight(42)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #6C757D;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #7E878F; }
        """)
        btn_cancel.clicked.connect(self.reject)

        btn_bar.addWidget(btn_apply, 2)
        btn_bar.addWidget(btn_cancel, 1)

        main_layout.addLayout(btn_bar)

        self.scan_input.setFocus()

    def _refresh_samples_list_ui(self):
        self.samples_list_widget.clear()
        for idx, sample in enumerate(self.samples, 1):
            self.samples_list_widget.addItem(f"[{idx:02d}]  {sample}")

    def _on_add_sample_from_input(self):
        text = self.scan_input.text().strip()
        if not text:
            return
        # 拆分连续扫码或多条录入
        items = [s.strip() for s in re.split(r"[\n,\s;]+", text) if s.strip()]
        added = False
        for item in items:
            if item not in self.samples:
                self.samples.append(item)
                added = True
        self.scan_input.clear()
        if added:
            self._refresh_samples_list_ui()
            self._recalculate_ai_rule()
        self.scan_input.setFocus()

    def _on_delete_selected_sample(self):
        curr_row = self.samples_list_widget.currentRow()
        if 0 <= curr_row < len(self.samples):
            self.samples.pop(curr_row)
            self._refresh_samples_list_ui()
            self._recalculate_ai_rule()

    def _on_clear_all_samples(self):
        if not self.samples:
            return
        self.samples.clear()
        self._refresh_samples_list_ui()
        self._recalculate_ai_rule()

    def _recalculate_ai_rule(self):
        if not self.samples:
            self.generated_regex = "^.+$"
            self.lbl_regex_result.setText(self.generated_regex)
            self.lbl_analysis_details.setText("前缀: 无 | 后缀: 无 | 变化号段: 无 | 样本数: 0 个")
            self._on_verify_text_changed(self.verify_input.text())
            return

        if len(self.samples) == 1:
            sample = self.samples[0]
            match_prefix = re.match(r"^([A-Za-z0-9_]{3,8})", sample)
            prefix = match_prefix.group(1) if match_prefix else ""
            remaining = sample[len(prefix):]
            if remaining.isdigit():
                mid_pattern = r"\d"
            elif remaining.isalnum():
                mid_pattern = r"[A-Z0-9]"
            else:
                mid_pattern = r"[\w-]"

            if prefix and len(remaining) > 0:
                self.generated_regex = f"^{prefix}{mid_pattern}{{{len(remaining)}}}$"
            elif len(sample) > 0:
                self.generated_regex = f"^{mid_pattern}{{{len(sample)}}}$"
            else:
                self.generated_regex = r"^.+$"

            self.lbl_regex_result.setText(self.generated_regex)
            self.lbl_analysis_details.setText(f"前缀: '{prefix}' ({len(prefix)}位) | 后缀: 无 | 变化段: {mid_pattern}{{{len(remaining)}}} | 样本数: 1 个 (建议至少提供 2 个样本)")
            self._on_verify_text_changed(self.verify_input.text())
            return

        # 多样本对比分析核心逻辑
        first = self.samples[0]
        min_len_sample = min(len(s) for s in self.samples)
        
        # 1. 最长公共前缀 LCP
        lcp_len = 0
        for i in range(min_len_sample):
            char = first[i]
            if all(s[i] == char for s in self.samples):
                lcp_len += 1
            else:
                break
        prefix = first[:lcp_len]

        # 2. 最长公共后缀 LCS
        lcs_len = 0
        max_possible_lcs = min_len_sample - lcp_len
        for i in range(1, max_possible_lcs + 1):
            char = first[-i]
            if all(s[-i] == char for s in self.samples):
                lcs_len += 1
            else:
                break
        suffix = first[len(first)-lcs_len:] if lcs_len > 0 else ""

        # 3. 截取中间号段
        mid_parts = []
        for s in self.samples:
            mid = s[lcp_len:len(s)-lcs_len] if lcs_len > 0 else s[lcp_len:]
            mid_parts.append(mid)

        mid_min_len = min(len(m) for m in mid_parts)
        mid_max_len = max(len(m) for m in mid_parts)

        all_mid_chars = "".join(mid_parts)
        if not all_mid_chars:
            mid_pattern = ""
        elif all_mid_chars.isdigit():
            mid_pattern = r"\d"
        elif all_mid_chars.isalnum() and all_mid_chars.isupper():
            mid_pattern = r"[A-Z0-9]"
        elif all_mid_chars.isalnum():
            mid_pattern = r"[a-zA-Z0-9]"
        else:
            mid_pattern = r"[\w-]"

        if mid_pattern:
            if mid_min_len == mid_max_len:
                mid_regex = f"{mid_pattern}{{{mid_min_len}}}"
            else:
                mid_regex = f"{mid_pattern}{{{mid_min_len},{mid_max_len}}}"
        else:
            mid_regex = ""

        safe_prefix = re.escape(prefix)
        safe_suffix = re.escape(suffix)

        self.generated_regex = f"^{safe_prefix}{mid_regex}{safe_suffix}$"
        self.lbl_regex_result.setText(self.generated_regex)
        
        prefix_desc = f"'{prefix}' ({len(prefix)}位)" if prefix else "无"
        suffix_desc = f"'{suffix}' ({len(suffix)}位)" if suffix else "无"
        mid_desc = f"{mid_pattern}[{mid_min_len}~{mid_max_len}]" if mid_pattern else "无"

        self.lbl_analysis_details.setText(f"前缀: {prefix_desc} | 后缀: {suffix_desc} | 变化段: {mid_desc} | 样本数: {len(self.samples)} 个")
        self._on_verify_text_changed(self.verify_input.text())

    def _on_verify_text_changed(self, text=""):
        verify_str = text.strip()
        if not verify_str:
            self.lbl_verify_status.setText("等待验证...")
            self.lbl_verify_status.setStyleSheet("color: #888888; font-size: 13px; font-weight: bold;")
            return

        try:
            if re.match(self.generated_regex, verify_str):
                self.lbl_verify_status.setText("🟢 匹配校验通过！")
                self.lbl_verify_status.setStyleSheet("color: #00FF66; font-size: 14px; font-weight: bold;")
            else:
                self.lbl_verify_status.setText("🔴 不符合规则")
                self.lbl_verify_status.setStyleSheet("color: #FF3366; font-size: 14px; font-weight: bold;")
        except Exception:
            self.lbl_verify_status.setText("❌ 规则错误")
            self.lbl_verify_status.setStyleSheet("color: #FF3366; font-size: 14px; font-weight: bold;")

    def _on_apply_and_accept(self):
        if not self.samples:
            QMessageBox.warning(self, "应用规则", "未添加任何条码样本，无法生成有效规则！")
            return
        self.accept()

    def get_generated_regex(self):
        return self.generated_regex
