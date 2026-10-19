import json
from datetime import datetime

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from core.mes_client import DEFAULT_MES_URL, MesClient
from core.mes_client import MesCheckResult
from core.numeric_validation import finite_reading
from core import system_config


class _MesRequestWorker(QObject):
    finished = Signal(object, dict)

    def __init__(self, settings, payload):
        super().__init__()
        self.settings = settings
        self.payload = payload

    def run(self):
        try:
            client = MesClient(self.settings["url"], self.settings["timeout"])
            result = client.move_in_check(**self.payload)
        except Exception as exc:
            result = MesCheckResult(False, f"MES 请求异常: {exc}")
        self.finished.emit(result, self.payload)


class MesTab(QWidget):
    """MES endpoint configuration and manual move-in check page."""

    def __init__(self, user_name_provider=None, parent=None, db_manager=None):
        super().__init__(parent)
        self.user_name_provider = user_name_provider or (lambda: "")
        self.db_manager = db_manager
        self.config_path = system_config.default_path()
        self._thread = None
        self._worker = None
        self._build_ui()
        self.load_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        config_box = QGroupBox("MES 接口配置")
        config_form = QFormLayout(config_box)
        self.url_edit = QLineEdit(DEFAULT_MES_URL)
        self.equipment_edit = QLineEdit("GS-APP-08")
        self.timeout_edit = QLineEdit("10")
        config_form.addRow("URL:", self.url_edit)
        config_form.addRow("设备编号 equipmentNo:", self.equipment_edit)
        config_form.addRow("超时（秒）:", self.timeout_edit)
        save_button = QPushButton("保存配置")
        save_button.clicked.connect(self.save_config)
        config_form.addRow("", save_button)
        layout.addWidget(config_box)

        test_box = QGroupBox("MES 过站校验测试")
        test_form = QFormLayout(test_box)
        self.order_edit = QLineEdit()
        self.sn_edit = QLineEdit()
        self.sn_edit.setPlaceholderText("扫描或输入测试条码")
        self.user_label = QLabel()
        test_form.addRow("工单号 orderNo:", self.order_edit)
        test_form.addRow("条码 sn:", self.sn_edit)
        test_form.addRow("登录用户 userName:", self.user_label)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("发送 moveInCheck")
        self.test_button.clicked.connect(self.send_test)
        refresh_button = QPushButton("刷新登录用户")
        refresh_button.clicked.connect(self.refresh_user)
        buttons.addWidget(self.test_button)
        buttons.addWidget(refresh_button)
        buttons.addStretch()
        test_form.addRow("", buttons)
        layout.addWidget(test_box)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(QLabel("请求 / 响应日志"))
        layout.addWidget(self.log, 1)
        self.refresh_user()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)

    def refresh_user(self):
        self.user_label.setText(str(self.user_name_provider() or ""))

    def _read_all_config(self):
        return self.db_manager.load_sys_config() if self.db_manager is not None else system_config.load(self.config_path)

    def load_config(self):
        try:
            config = self._read_all_config()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "配置读取失败", str(exc))
            return
        self.url_edit.setText(config.get("mes_url", DEFAULT_MES_URL))
        self.equipment_edit.setText(config.get("mes_equipment_no", "GS-APP-08"))
        self.timeout_edit.setText(str(config.get("mes_timeout", 10)))

    def save_config(self, show_message=True):
        url = self.url_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "配置错误", "MES URL 必须以 http:// 或 https:// 开头。")
            return False
        try:
            timeout = finite_reading(self.timeout_edit.text().strip())
            if timeout <= 0:
                raise ValueError('Timeout must be positive')
        except ValueError:
            QMessageBox.warning(self, "配置错误", "超时必须是数字。")
            return False
        config = {
            "mes_url": url,
            "mes_equipment_no": self.equipment_edit.text().strip(),
            "mes_timeout": timeout,
        }
        try:
            if self.db_manager is not None:
                if not self.db_manager.save_sys_config(config):
                    raise OSError('系统配置未保存，请检查文件权限及配置内容')
            else:
                system_config.update(config, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "配置保存失败", str(exc))
            return False
        if show_message:
            QMessageBox.information(self, "保存成功", "MES 配置已保存，后续测试项立即生效。")
        return True

    def send_test(self):
        if self._thread is not None or getattr(self, '_closing', False):
            return
        self.refresh_user()
        if not self.save_config(show_message=False):
            return
        payload = {
            "equipment_no": self.equipment_edit.text().strip(),
            "order_no": self.order_edit.text().strip(),
            "sn": self.sn_edit.text().strip(),
            "user_name": self.user_label.text().strip(),
        }
        if not payload["sn"]:
            QMessageBox.warning(self, "参数缺失", "请输入或扫描 SN 条码。")
            return
        settings = {"url": self.url_edit.text().strip(), "timeout": finite_reading(self.timeout_edit.text())}
        display_payload = {"equipmentNo": payload["equipment_no"], "orderNo": payload["order_no"],
                           "sn": payload["sn"], "userName": payload["user_name"]}
        self.log.append(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] POST {settings['url']}\n"
                        f"REQUEST {json.dumps(display_payload, ensure_ascii=False)}")
        self.test_button.setEnabled(False)
        self._thread = QThread(self)
        self._worker = _MesRequestWorker(settings, payload)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._retire_thread)
        self._thread.start()

    def _on_finished(self, result, payload):
        status = "PASS" if result.passed else "FAIL"
        response = json.dumps(result.response, ensure_ascii=False) if isinstance(result.response, (dict, list)) else str(result.response)
        self.log.append(f"RESPONSE HTTP={result.status_code} {status}: {result.message}\n{response}\n")

    def _retire_thread(self):
        from PySide6.QtCore import QTimer
        thread = self._thread
        if thread is None:
            return
        if not thread.wait(0):
            QTimer.singleShot(10, self._retire_thread)
            return
        self._thread = None
        self._worker = None
        thread.deleteLater()
        if not getattr(self, '_closing', False):
            self.test_button.setEnabled(True)
