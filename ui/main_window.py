
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget, QStatusBar, QHBoxLayout, QPushButton, QSizePolicy, QMessageBox
from PySide6.QtCore import Qt
from core.app_version import versioned_title

from ui.tabs.overview_tab import OverviewTab
from ui.tabs.config_tab import ConfigTab
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.settings_tab import SettingsTab

from ui.tabs.debug_tab import DebugTab
from ui.tabs.mes_tab import MesTab
from ui.tabs.simulator_tab import SimulatorTab
from ui.tabs.multimeter_tab import MultimeterTab
from ui.tabs.environment_tab import EnvironmentTab
from ui.tabs.it6333b_tab import IT6333BTab
from ui.tabs.dg2052_tab import DG2052Tab
from ui.tabs.tbs2102b_tab import TBS2102BTab
from ui.tabs.board_debug_tab import BoardDebugTab
from ui.tabs.resistor_bank_tab import ResistorBankTab
from ui.tabs.hv_tab import HVSourceTab
from ui.tabs.mainboard_power_tab import MainboardPowerTab
from ui.tabs.easy320_tab_standalone import Easy320StandaloneTab
from ui.tabs.ca550_tab_standalone import CA550StandaloneTab
from ui.tabs.pcan_fd_tab import PCANFDTab
from ui.tabs.opteeq_tab import OpteeQTab
from ui.tabs.renesas_e2_tab import RenesasE2Tab
from ui.tabs.app_flash_tab import AppFlashTab




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from ui.parameter_wheel_guard import install_parameter_wheel_guard
        install_parameter_wheel_guard()
        self.setWindowTitle(versioned_title("BMS软件注入&APP刷写系统"))

        # 动态加载窗体图标
        import os, sys
        from PySide6.QtGui import QIcon
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

        icon_path = os.path.join(base_dir, "resources", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.resize(1280, 800)
        self.setMinimumSize(1024, 700)

        # 初始化核心业务引擎
        from core.engine import TestEngine
        from data.db_manager import DBManager
        from devices.manager import DeviceManager

        self.db_manager = DBManager()
        try:
            self.device_manager = DeviceManager(self.db_manager)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "\u914d\u7f6e\u8bfb\u53d6\u5931\u8d25", "\u73b0\u573a\u914d\u7f6e\u65e0\u6cd5\u8bfb\u53d6\uff0c\u8bf7\u68c0\u67e5 config/sys_config.json \u6216\u4ece .bak \u6062\u590d\u3002\n" + str(exc))
            self.db_manager.close()
            raise

        # 启动自检弹窗阻塞主进程
        from ui.dialogs.startup_dialog import StartupCheckDialog
        from PySide6.QtWidgets import QDialog
        import sys

        dialog = StartupCheckDialog(self.device_manager, None)
        if dialog.exec() != QDialog.Accepted:
            # 用户选择退出程序或强制关闭弹窗
            self.device_manager.disconnect_all()
            if hasattr(self, 'db_manager'):
                self.db_manager.close()
            sys.exit(0)

        self.engine = TestEngine(self.device_manager, self.db_manager)
        from core.user_session import get_current_user
        self.current_user = get_current_user()

        self._init_ui()

    def _init_ui(self):
        from core.app_titles import load_titles
        window_title, header_title = load_titles(self.db_manager)
        self.setWindowTitle(versioned_title(window_title))

        central_widget = QWidget()
        central_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 缩小版 图2 控件：用户账户按钮 & 用户账户 (保持与状态栏严格 56px 等高)
        self.btn_user_account = QPushButton()
        self.btn_user_account.setFixedSize(130, 56)
        self.btn_user_account.setCursor(Qt.PointingHandCursor)
        self.btn_user_account.setToolTip("点击切换登录用户或管理用户账户")
        self.btn_user_account.clicked.connect(self.on_user_account_clicked)
        self.update_user_display()

        # 顶部标题栏 (左侧等宽占位保持标题居中，用户账户按钮固定在右上角)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(10, 6, 10, 6)

        header_spacer = QWidget()
        header_spacer.setFixedWidth(self.btn_user_account.width())
        header_layout.addWidget(header_spacer)

        from ui.widgets.eliding_label import ElidingLabel
        self.header_label = ElidingLabel(header_title)
        self.header_label.setTextFormat(Qt.PlainText)
        self.header_label.setMinimumWidth(0)
        self.header_label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.header_label.setStyleSheet("color: #FFFFFF; font-size: 26px; font-weight: bold; letter-spacing: 3px; padding: 4px;")
        self.header_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.header_label, 1)
        header_layout.addWidget(self.btn_user_account)

        layout.addLayout(header_layout)

        # 核心导航 Tab
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 1. 单通道测试与序列编辑页
        self.tab_overview = OverviewTab(self.engine, self.db_manager)
        self.tab_overview.run_activity_changed.connect(lambda _: self.apply_role_permissions())
        self.tabs.addTab(self.tab_overview, "单通道测试与序列编辑")

        # 3. 测试用例/工步配置页
        self.tab_config = ConfigTab(self.db_manager, self.device_manager)
        self.tab_config.test_engine=self.engine
        self.tabs.addTab(self.tab_config, "工步与配方配置")

        # 4. 硬件设备配置页 (硬件管理中心)
        self.tab_hardware = HardwareConfigTab(self.db_manager)
        self.tab_hardware.set_device_manager(self.device_manager)
        self.tabs.addTab(self.tab_hardware, "硬件管理中心")

        # 5. 单通道/硬件独立调试中心 (主容器)
        self.tab_debug_center = DebugTab(self.device_manager)
        self.tabs.addTab(self.tab_debug_center, "单通道/硬件独立调试")

        from ui.widgets.serial_scanner import SerialScanner, ScannerPanel, ScannerDebugTab
        reader=SerialScanner(self,driver_provider=lambda:self.device_manager.scanner)
        self.scanner_panel=ScannerPanel(self.db_manager,reader=reader)
        self.scanner_panel.can_edit=lambda:not self.tab_overview._run_busy() and not self.tab_overview._scan_pending and not getattr(self,'_closing',False) and not (self.tab_hardware.operation and self.tab_hardware.operation.isRunning())
        self.tab_hardware.scanner_reader=reader
        self.scanner_panel.settings_saved.connect(self._on_scanner_settings_saved)
        self.tab_overview.serial_scanner=self.scanner_panel.reader
        self.tab_config.serial_scanner=self.scanner_panel.reader
        self.tab_scanner=ScannerDebugTab(self.scanner_panel)
        self.tab_debug_center.add_debug_tab(self.tab_scanner,'infoscan FV31-2110 扫码器','通讯与控制')

        # --- 将各硬件调试页加入到调试中心 (Sub-Tabs) ---

        # 模拟器控制
        self.tab_simulator = SimulatorTab(self.device_manager)
        self.tab_hardware.configuration_changed.connect(self.tab_simulator.refresh_configuration)
        self.tab_debug_center.add_debug_tab(self.tab_simulator, "电池模拟器控制")

        # NGI 高压源
        self.tab_hv = HVSourceTab(self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_hv, "NGI 高压源控制")

        # 电源调试：两种电源集中显示，内部按型号分页。
        self.tab_power_center = QTabWidget()
        self.tab_debug_center.add_debug_tab(self.tab_power_center, "电源调试")
        self.tab_dut_power = MainboardPowerTab(self.device_manager)
        self.tab_power_center.addTab(self.tab_dut_power, "IT6722A 被测物供电")
        self.tab_it6333b = IT6333BTab(self.device_manager)
        self.tab_power_center.addTab(self.tab_it6333b, "IT6333B 三通道电源")

        # Easy320 调试
        self.tab_easy320_standalone = Easy320StandaloneTab(self.device_manager.easy320, self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_easy320_standalone, "Easy320调试")

        # CA550 调试
        self.tab_ca550_standalone = CA550StandaloneTab(self.device_manager.ca550, self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_ca550_standalone, "CA550调试")

        # USB PCAN-FD 调试
        self.tab_pcan_fd = PCANFDTab(self.device_manager)
        self.tab_hardware.configuration_changed.connect(self.tab_pcan_fd.refresh_configuration)
        self.tab_debug_center.add_debug_tab(self.tab_pcan_fd, "CAN调试（PCAN / ZLG）")
        self.tab_dbc_monitor = self.tab_pcan_fd.dbc_monitor

        # 力捷丰 SG400 下载器（复用启动自检建立的共享连接）
        self.tab_download_center = QTabWidget()
        self.tab_debug_center.add_debug_tab(self.tab_download_center, "下载与刷写")
        self.tab_opteeq = OpteeQTab(self.device_manager)
        self.tab_hardware.configuration_changed.connect(self.tab_opteeq.refresh_connection_state)
        self.tab_download_center.addTab(self.tab_opteeq, "力捷丰 SG400")

        # 瑞萨 E2 仿真器 (rfp-cli 命令行)
        self.tab_e2_rfp = RenesasE2Tab(self.device_manager)
        self.tab_download_center.addTab(self.tab_e2_rfp, "瑞萨 E2")

        # MES 过站接口配置与手动联调（用户名始终读取当前登录账户）
        self.tab_mes = MesTab(lambda: self.current_user, self, db_manager=self.db_manager)
        self.tab_debug_center.add_debug_tab(self.tab_mes, "精华MES交互测试")

        # APP 固件刷写独立调试 (LabVIEW 触发刷写与固件检索)
        self.tab_app_flash = AppFlashTab(self.device_manager)
        self.tab_download_center.addTab(self.tab_app_flash, "APP刷写")

        # 6. 软件设置页 (条码去重统计 / 数据清零 / 统计范围 / 界面名称)
        self.tab_settings = SettingsTab()
        self.tab_settings.set_dedup_checked(self.tab_overview.stats_deduplicate)
        self.tab_settings.set_titles(window_title, header_title)
        self.tab_settings.set_mes_settings(self.db_manager.load_sys_config() or {})
        self.tab_settings.set_report_settings(self.db_manager.load_sys_config() or {})
        self.tab_settings.report_save_requested.connect(self._on_settings_report_save)
        self.tab_settings.start_mode.setCurrentIndex(self.tab_settings.start_mode.findData((self.db_manager.load_sys_config() or {}).get('test_start_mode','board_first')))
        self.tab_settings.start_mode_save_requested.connect(self._on_settings_start_mode_save)
        self.tab_settings.mes_save_requested.connect(self._on_settings_mes_save)
        self.tab_settings.mes_switch_save_requested.connect(self._on_settings_mes_switch_save)
        self.tab_settings.generic_mes_config_requested.connect(self._on_settings_generic_mes_config)
        self.tab_settings.generic_mes_retry_requested.connect(self.tab_overview.retry_pending_mes_uploads)
        self.tab_settings.set_ng_alarm_settings(self.tab_overview.ng_alarm.enabled, self.tab_overview.ng_alarm.threshold)
        self.tab_settings.ng_alarm_save_requested.connect(self._on_settings_ng_alarm_save)
        self.tab_settings.dedup_changed.connect(self._on_settings_dedup_changed)
        self.tab_settings.reset_requested.connect(self.tab_overview.on_reset_stats_clicked)
        self.tab_settings.range_requested.connect(self.tab_overview.show_project_statistics)
        self.tab_settings.titles_save_requested.connect(self._on_settings_titles_save)
        # 统计读取中的按钮文案反馈由 OverviewTab 驱动
        self.tab_overview.btn_stats_range = self.tab_settings.btn_stats_range
        self.tabs.addTab(self.tab_settings, "软件设置")

        self.tab_multimeter = MultimeterTab(self.device_manager)
        self.board_debug_pages = []
        for kind, label in [('insulation','绝缘板（2块）'), ('resistor','电阻板（6块）'),
                            ('relay','Relay Card（1块）'), ('hv','HV Card（1块）'),
                            ('hsd','HSD Card（2块）'), ('dmm','DMM Card（2块）')]:
            page = ResistorBankTab(self.device_manager) if kind=='resistor' else BoardDebugTab(self.device_manager, kind)
            self.board_debug_pages.append(page)
            self.tab_debug_center.add_debug_tab(page, label, "板卡与继电器")
        self.tab_scope = TBS2102BTab(self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_scope, "TBS2102B 示波器")
        self.tab_dg2052 = DG2052Tab(self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_dg2052, "DG2052 信号发生器")
        self.tab_debug_center.add_debug_tab(self.tab_multimeter, "34461A万用表调试")
        self.tab_environment = EnvironmentTab(self.device_manager)
        self.tab_debug_center.add_debug_tab(self.tab_environment, "RS-WS-ETH-6 温湿度传感器", "测量与信号")
        layout.addWidget(self.tabs)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.show_status("系统就绪")

        # 绑定 Tab 切换事件以实现数据跨页面联动
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # 软件刚启动时，手动同步一次预设配方并应用权限
        self.on_tab_changed(0)
        self.apply_role_permissions()

    def show_status(self, message: str, timeout: int = 5000):
        """在状态栏显示信息"""
        self.status_bar.showMessage(message, timeout)

    def closeEvent(self, event):
        if getattr(self, '_shutdown_done', False):
            event.accept()
            return
        event.ignore()
        if getattr(self, '_closing', False):
            return
        self._closing = True
        if hasattr(self,'scanner_panel'):self.scanner_panel.reader.close()
        self.centralWidget().setEnabled(False)
        self.status_bar.showMessage("正在安全退出，等待当前设备通信及测试收尾…")
        self.engine.stop_all()
        self.tab_overview.begin_shutdown()
        from PySide6.QtCore import QTimer
        from ui.async_shutdown import request_ui_shutdown
        self._shutdown_timer = QTimer(self)
        self._shutdown_threads = request_ui_shutdown(self, exclude=tuple(self.engine.threads.values()))
        self._shutdown_timer.timeout.connect(self._poll_shutdown)
        self._shutdown_timer.start(50)

    def _poll_shutdown(self):
        from ui.async_shutdown import threads_finished
        if self.engine.workers or not threads_finished(self._shutdown_threads):
            return
        if not hasattr(self, '_close_io_thread'):
            import threading
            from ui.async_shutdown import cleanup_resources
            self._close_io_result = {}
            self._close_io_thread = threading.Thread(target=cleanup_resources,
                args=(self.device_manager, self.db_manager,
                      bool(getattr(self, '_ignore_output_error', False)), self._close_io_result), daemon=True)
            self._close_io_thread.start()
            return
        if self._close_io_thread.is_alive():
            return
        self._close_error = self._close_io_result.get('error', '')
        if getattr(self, '_close_error', ''):
            self._shutdown_timer.stop()
            from PySide6.QtWidgets import QMessageBox
            choice = QMessageBox.question(self, "退出清理未完成", self._close_error + "\n\u662f\uff1a\u786e\u8ba4\u540e\u4ecd\u7136\u9000\u51fa\uff1b\u5426\uff1a\u91cd\u8bd5", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            self._ignore_output_error = choice == QMessageBox.Yes
            self._close_error = ''
            del self._close_io_thread
            self._shutdown_timer.start(50)
            return
        self._shutdown_timer.stop()
        self._shutdown_done = True
        self.close()

    def on_tab_changed(self, index):
        if index == 0:
            recipes = self.tab_config.get_all_recipes()
            self.tab_overview.update_recipes(recipes, preserve_results=True)

    def update_user_display(self):
        """刷新底部右下角用户卡片显示及其权限角色"""
        role = self.db_manager.get_user_role(self.current_user) if hasattr(self, 'db_manager') and self.db_manager else "admin"
        if self.current_user == "ADMIN" and role == "admin":
            role_text = "超级管理员"
            color = "#00FFCC"
        elif role == "admin":
            role_text = "管理员"
            color = "#00FFCC"
        else:
            role_text = "操作员"
            color = "#FFC107"
        self.btn_user_account.setText(f"👤 {self.current_user}\n({role_text})")
        self.btn_user_account.setStyleSheet(f"""
            QPushButton {{
                background-color: #16213E;
                color: {color};
                border: 1px solid #2D3748;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: bold;
                line-height: 1.3;
            }}
            QPushButton:hover {{
                background-color: #1D2A4F;
                border: 1px solid {color};
            }}
            QPushButton:pressed {{
                background-color: #16213E;
            }}
        """)
        self.apply_role_permissions()

    def apply_role_permissions(self):
        """根据当前账户角色控制配置Tab与控制按钮的可用态与置灰态"""
        role = self.db_manager.get_user_role(self.current_user) if hasattr(self, 'db_manager') and self.db_manager else "admin"
        is_admin = (role == "admin")

        # 1. 顶部 Tab 控制：除“单通道测试与序列编辑”外，其余配置/调试页仅管理员可用
        if hasattr(self, 'tabs') and self.tabs:
            if (not is_admin or self.tab_overview._run_busy()) and self.tabs.currentIndex() >= 1:
                self.tabs.setCurrentIndex(0)
            for idx in range(1, self.tabs.count()):
                self.tabs.setTabEnabled(idx, is_admin and not self.tab_overview._run_busy())
                self.tabs.setTabToolTip(idx, "" if is_admin else "需要管理员权限访问此配置/调试页面")

        if hasattr(self, 'tab_overview') and self.tab_overview:
            self.tab_overview.set_recipe_selection_role(self.current_user,is_admin)
            if hasattr(self.tab_overview, 'btn_stop'):
                self.tab_overview.btn_stop.setEnabled(True)
                self.tab_overview.btn_stop.setToolTip("停止当前测试")
        # 3. 生产统计数据清零权限控制：仅管理员可用（按钮在“软件设置”页）
        if hasattr(self, 'tab_settings') and self.tab_settings:
            self.tab_settings.set_reset_enabled(is_admin)

    def _on_settings_dedup_changed(self, enabled):
        """软件设置页写入条码去重开关；保存失败时回滚设置页勾选状态。"""
        if not self.tab_overview.set_stats_deduplicate(enabled):
            self.tab_settings.set_dedup_checked(self.tab_overview.stats_deduplicate)

    def _on_settings_mes_save(self, settings):
        from PySide6.QtWidgets import QMessageBox
        from core.mes_precheck import validate_settings
        try:
            if self.tab_overview._run_busy() or self.tab_overview._scan_pending:
                raise ValueError('测试或准备中，不能修改MES设置')
            parameters = {key: settings[key] for key in ('mes_url', 'mes_equipment_no', 'mes_order_no', 'mes_timeout') if key in settings}
            validate_settings(dict(self.db_manager.load_sys_config() or {}, **parameters))
            if not self.db_manager.save_sys_config(parameters):
                raise ValueError('MES设置保存失败，请检查配置存储')
        except Exception as exc:
            QMessageBox.warning(self.tab_settings, 'MES设置未保存', str(exc))
            return
        if hasattr(self, 'tab_mes'):
            self.tab_mes.load_config()
        QMessageBox.information(self.tab_settings, '保存成功', '精华MES参数已保存，下次扫码启动时生效。')

    def _on_settings_generic_mes_config(self):
        from core.generic_mes import config_path
        from ui.dialogs.generic_mes_dialog import GenericMesDialog
        if self.tab_overview._run_busy() or self.tab_overview._scan_pending:
            QMessageBox.warning(self.tab_settings, '无法配置锐能MES', '请先结束扫码、测试或等待到位。')
            return
        GenericMesDialog(config_path(self.db_manager.db_path), self).exec()

    def _on_settings_mes_switch_save(self, settings):
        from core.mes_precheck import validate_settings
        from core.generic_mes import load_runtime_settings
        previous = dict(self.tab_settings._saved_mes_switches)
        try:
            if self.tab_overview._run_busy() or getattr(self.tab_overview, '_scan_pending', False):
                raise ValueError('扫码、测试或等待到位期间不能切换MES方案')
            current = self.db_manager.load_sys_config() or {}
            previous = {key: current.get(key, False) is True for key in previous}
            if len(settings) != 1 or next(iter(settings)) not in previous:
                raise ValueError('MES使能设置无效')
            key, enabled = next(iter(settings.items()))
            if type(enabled) is not bool:
                raise ValueError('MES使能状态必须是布尔值')
            updated = {key: enabled}
            if enabled:
                updated['mes_precheck_enabled' if key == 'generic_mes_enabled' else 'generic_mes_enabled'] = False
                candidate = dict(current, **updated)
                validate_settings(candidate)
                load_runtime_settings(candidate, self.db_manager.db_path)
            if not self.db_manager.save_sys_config(updated):
                raise ValueError('MES使能状态保存失败')
        except Exception as exc:
            self.tab_settings.set_mes_enabled_states(previous)
            QMessageBox.warning(self.tab_settings, 'MES使能状态未保存', str(exc))
            return
        persisted = dict(current, **updated)
        self.tab_settings.set_mes_enabled_states(persisted)
        self.tab_overview.set_generic_mes_enabled(persisted.get('generic_mes_enabled', False))

    def _on_settings_start_mode_save(self,mode):
        from PySide6.QtWidgets import QMessageBox
        if self.tab_overview._run_busy():
            QMessageBox.warning(self.tab_settings,'无法保存','测试、扫码准备或等待到位期间不能修改启动模式。');return
        if mode not in ('board_first','scan_first'):return
        if not self.db_manager.save_sys_config({'test_start_mode':mode}):
            QMessageBox.warning(self.tab_settings,'保存失败','启动模式未保存。');return
        QMessageBox.information(self.tab_settings,'保存成功','启动模式将在下次扫码时生效。')

    def _on_scanner_settings_saved(self,settings):
        self.device_manager.enabled_devices['scanner']=settings['scanner_enabled']
        # Refresh only the scanner row, preserving any unsaved instrument edits.
        from PySide6.QtCore import QSignalBlocker
        page=self.tab_hardware
        row=next(i for i,spec in enumerate(page.DEVICE_SPECS) if spec[0]=='scanner')
        with QSignalBlocker(page.table):
            page.table.item(row,0).setCheckState(Qt.Checked if settings['scanner_enabled'] else Qt.Unchecked)
            page.table.cellWidget(row,3).setEditText(settings['scanner_com'])
            page.table.item(row,5).setText(str(settings['scanner_baudrate']))

    def _on_settings_report_save(self,settings):
        from PySide6.QtWidgets import QMessageBox
        from core.report_settings import validate_settings
        try:
            if self.tab_overview._run_busy():raise ValueError('测试或准备中，不能修改报告设置。')
            settings=validate_settings(settings)
            if not self.db_manager.save_sys_config(settings):raise ValueError('报告设置保存失败，请检查配置存储。')
        except Exception as exc:
            QMessageBox.warning(self.tab_settings,'报告设置未保存',str(exc));return
        self.tab_settings.set_report_settings(settings)
        QMessageBox.information(self.tab_settings,'保存成功','测试报告目录和独立MES保存目录已保存，下次测试时生效。')

    def _on_settings_ng_alarm_save(self, enabled, threshold):
        from PySide6.QtWidgets import QMessageBox
        from core.consecutive_ng import ConsecutiveNgAlarm
        try:
            if self.tab_overview._run_busy():
                raise ValueError('测试或准备中，不能修改报警设置')
            ConsecutiveNgAlarm(enabled, threshold)
            if not self.db_manager.save_sys_config(dict(ng_alarm_enabled=enabled, ng_alarm_threshold=threshold)):
                raise ValueError('连续不良报警设置保存失败')
            self.tab_overview.ng_alarm.configure(enabled, threshold)
        except Exception as exc:
            QMessageBox.warning(self.tab_settings, '设置未保存', str(exc))
            return
        QMessageBox.information(self.tab_settings, '保存成功', '连续不良报警设置已保存，连续计数已清零。')

    def apply_titles(self, window_title, header_title):
        """应用界面名称到窗口标题与顶部主标题。"""
        self.setWindowTitle(versioned_title(window_title))
        if hasattr(self, 'header_label'):
            self.header_label.setText(header_title)

    def _on_settings_titles_save(self, window_title, header_title):
        """保存软件设置页的界面名称；留空恢复默认，保存后立即生效。"""
        from PySide6.QtWidgets import QMessageBox
        from core.app_titles import DEFAULT_WINDOW_TITLE, DEFAULT_HEADER_TITLE
        window_title = str(window_title).strip() or DEFAULT_WINDOW_TITLE
        header_title = str(header_title).strip() or DEFAULT_HEADER_TITLE
        cfg = self.db_manager.load_sys_config() or {}
        cfg['ui_window_title'] = window_title
        cfg['ui_header_title'] = header_title
        if not self.db_manager.save_sys_config(cfg):
            QMessageBox.warning(self.tab_settings, '保存失败', '界面名称未保存，请检查配置存储后重试。')
            return
        self.apply_titles(window_title, header_title)
        self.tab_settings.set_titles(window_title, header_title)
        self.show_status("界面名称已保存并生效")

    def on_user_account_clicked(self):
        """弹出用户登录切换与账号管理对话框"""
        from ui.dialogs.user_account_dialog import UserAccountDialog
        dlg = UserAccountDialog(self.db_manager, current_user=self.current_user, parent=self)
        if dlg.exec():
            self.current_user = dlg.logged_in_user
            from core.user_session import set_current_user
            set_current_user(self.current_user)
            self.update_user_display()
            if hasattr(self, "tab_mes"):
                self.tab_mes.refresh_user()

