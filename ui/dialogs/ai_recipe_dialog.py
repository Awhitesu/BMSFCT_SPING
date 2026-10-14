# -*- coding: utf-8 -*-
"""AI 配方助手对话框：输入自然语言指令，由大模型编辑当前配方，预览确认后应用。"""

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QGroupBox, QMessageBox, QFormLayout
)

from core.ai_recipe_assistant import (
    AIRecipeError, DEFAULT_BASE_URL, DEFAULT_MODEL,
    backup_recipe, generate_recipe_edit
)


class _AIWorker(QThread):
    """后台调用 AI API，避免阻塞 UI"""
    succeeded = Signal(dict, list)
    failed = Signal(str)

    def __init__(self, instruction, recipe, api_key, base_url, model, parent=None):
        super().__init__(parent)
        self._instruction = instruction
        self._recipe = recipe
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    def run(self):
        try:
            new_recipe, changes = generate_recipe_edit(
                self._instruction, self._recipe,
                api_key=self._api_key, base_url=self._base_url, model=self._model,
            )
            self.succeeded.emit(new_recipe, changes)
        except AIRecipeError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"未预期的错误: {e}")


class AiRecipeDialog(QDialog):
    """
    AI 配方编辑对话框。
    1. 顶部为 AI 连接设置（API Key / 接口地址 / 模型，持久化到系统配置）；
    2. 中部输入自然语言指令；
    3. 点击“生成预览”后显示变更摘要；
    4. 确认应用时自动备份原配方，再写入新配方。
    """

    def __init__(self, db_manager, recipe_name: str, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.recipe_name = recipe_name
        self.recipe = db_manager.load_recipe_json(recipe_name)
        self._new_recipe = None
        self._worker = None

        self.setWindowTitle(f"🤖 AI 配方助手 - {recipe_name}")
        self.setMinimumSize(680, 600)
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # AI 连接设置
        settings_group = QGroupBox("AI 连接设置 (OpenAI 兼容接口，默认 Kimi)")
        form = QFormLayout(settings_group)
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.Password)
        self.edit_api_key.setPlaceholderText("在 platform.kimi.com 创建的 API Key")
        self.edit_base_url = QLineEdit(DEFAULT_BASE_URL)
        self.edit_model = QLineEdit(DEFAULT_MODEL)
        form.addRow("API Key:", self.edit_api_key)
        form.addRow("接口地址:", self.edit_base_url)
        form.addRow("模型:", self.edit_model)
        layout.addWidget(settings_group)

        # 指令输入
        layout.addWidget(QLabel("修改指令（用自然语言描述要对当前配方做什么）："))
        self.edit_instruction = QPlainTextEdit()
        self.edit_instruction.setPlaceholderText(
            "例如：\n"
            "· 在“刷写”测试项后面新增一个“版本读取校验”测试项，用 UDS 读取软件版本\n"
            "· 把 BMS上电 的电流判定范围改成 200~300 mA\n"
            "· 删除 设备初始化 测试项"
        )
        self.edit_instruction.setMinimumHeight(90)
        layout.addWidget(self.edit_instruction)

        self.btn_generate = QPushButton("🚀 生成预览")
        self.btn_generate.setStyleSheet("background-color: #0D6EFD; color: white; font-weight: bold; min-height: 34px;")
        self.btn_generate.clicked.connect(self._on_generate)
        layout.addWidget(self.btn_generate)

        # 变更预览
        layout.addWidget(QLabel("变更预览（确认无误后再应用）："))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        self.btn_apply = QPushButton("✅ 确认应用（自动备份原配方）")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet("background-color: #28A745; color: white; font-weight: bold; min-height: 34px; padding: 0 16px;")
        self.btn_apply.clicked.connect(self._on_apply)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _load_settings(self):
        cfg = self.db_manager.load_sys_config()
        self.edit_api_key.setText(cfg.get("ai_api_key", ""))
        self.edit_base_url.setText(cfg.get("ai_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL)
        self.edit_model.setText(cfg.get("ai_model", DEFAULT_MODEL) or DEFAULT_MODEL)

    def _save_settings(self):
        self.db_manager.save_sys_config({
            "ai_api_key": self.edit_api_key.text().strip(),
            "ai_base_url": self.edit_base_url.text().strip(),
            "ai_model": self.edit_model.text().strip(),
        })

    def _on_generate(self):
        instruction = self.edit_instruction.toPlainText().strip()
        if not instruction:
            QMessageBox.warning(self, "提示", "请先输入修改指令")
            return
        if not self.edit_api_key.text().strip():
            QMessageBox.warning(self, "提示", "请先填写 API Key")
            return
        if self.recipe is None:
            QMessageBox.warning(self, "提示", f"配方 {self.recipe_name} 加载失败")
            return

        self._save_settings()
        self.btn_generate.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.preview.setPlainText("⏳ 正在调用 AI 模型，请稍候…")

        self._worker = _AIWorker(
            instruction, self.recipe,
            self.edit_api_key.text().strip(),
            self.edit_base_url.text().strip() or DEFAULT_BASE_URL,
            self.edit_model.text().strip() or DEFAULT_MODEL,
            parent=self,
        )
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_success(self, new_recipe: dict, changes: list):
        self.btn_generate.setEnabled(True)
        self._new_recipe = new_recipe
        self.preview.setPlainText("AI 生成的修改内容：\n\n" + "\n".join(changes))
        self.btn_apply.setEnabled(True)

    def _on_failure(self, message: str):
        self.btn_generate.setEnabled(True)
        self.preview.setPlainText(f"❌ 生成失败：\n{message}")

    def _on_apply(self):
        if self._new_recipe is None:
            return
        try:
            backup_name = backup_recipe(self.db_manager, self.recipe_name, self.recipe)
        except Exception as e:
            QMessageBox.warning(self, "备份失败", f"原配方备份失败，已中止应用：\n{e}")
            return
        if not self.db_manager.save_recipe_json(self.recipe_name, self._new_recipe):
            QMessageBox.warning(self, "保存失败", "新配方写入失败")
            return
        QMessageBox.information(
            self, "应用成功",
            f"配方已更新！\n原配方已备份为: backup/{backup_name}.json"
        )
        self.accept()
