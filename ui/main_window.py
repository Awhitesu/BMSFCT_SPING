import sys
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget, QStatusBar, QHBoxLayout, QPushButton, QSizePolicy
from PySide6.QtCore import Qt

from ui.tabs.overview_tab import OverviewTab
from ui.tabs.config_tab import ConfigTab
from ui.tabs.hardware_config_tab import HardwareConfigTab

from ui.tabs.debug_tab import DebugTab
from ui.tabs.mes_tab import MesTab
from ui.tabs.simulator_tab import SimulatorTab
from ui.tabs.multimeter_tab import MultimeterTab
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
        self.setWindowTitle("BMS软件注入&APP刷写系统")
        
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
        self.device_manager = DeviceManager(self.db_manager)
        
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
        self.current_user = "ADMIN"
        
        self._init_ui()

    def _init_ui(self):
        central_widget = QWidget()
        central_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 顶部标题栏 (纯净居中 APP刷写系统 标题)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(10, 6, 10, 6)

        header_label = QLabel("APP刷写系统")
        header_label.setStyleSheet("color: #FFFFFF; font-size: 26px; font-weight: bold; letter-spacing: 3px; padding: 4px;")
        header_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(header_label, 1, Qt.AlignCenter)

        layout.addLayout(header_layout)

        # 缩小版 图2 控件：用户账户按钮 & 老化执行计数 (保持与状态栏严格 56px 等高)
        self.btn_user_account = QPushButton()
        self.btn_user_account.setFixedSize(130, 56)
        self.btn_user_account.setCursor(Qt.PointingHandCursor)
        self.btn_user_account.setToolTip("点击切换登录用户或管理用户账户")
        self.btn_user_account.clicked.connect(self.on_user_account_clicked)
        self.update_user_display()

        initial_count = self.db_manager.get_aging_exec_count() if hasattr(self, 'db_manager') and self.db_manager else 0
        self.lbl_aging_counter = QLabel(f"⚡ 测试次数\n{initial_count} 次")
        self.lbl_aging_counter.setFixedSize(100, 56)
        self.lbl_aging_counter.setStyleSheet("""
            QLabel {
                background-color: #16213E;
                color: #FFD700;
                border: 1px solid #555533;
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 12px;
                font-weight: bold;
                line-height: 1.3;
            }
        """)
        self.lbl_aging_counter.setAlignment(Qt.AlignCenter)

        # 核心导航 Tab
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 1. 单通道测试与序列编辑页
        self.tab_overview = OverviewTab(self.engine, self.db_manager)
        # 将缩小版的图2 控件嵌入到 tab_overview 右下角
        if hasattr(self.tab_overview, 'embed_right_controls'):
            self.tab_overview.embed_right_controls(self.btn_user_account, self.lbl_aging_counter)
        self.tabs.addTab(self.tab_overview, "单通道测试与序列编辑")

        # 3. 测试用例/工步配置页
        self.tab_config = ConfigTab(self.db_manager, self.device_manager)
        self.tabs.addTab(self.tab_config, "工步与配方配置")

        # 4. 硬件设备配置页 (硬件管理中心)
        self.tab_hardware = HardwareConfigTab(self.db_manager)
        self.tab_hardware.set_device_manager(self.device_manager)
        self.tabs.addTab(self.tab_hardware, "硬件管理中心")

        # 5. 单通道/硬件独立调试中心 (主容器)
        self.tab_debug_center = DebugTab(self.device_manager)
        self.tabs.addTab(self.tab_debug_center, "单通道/硬件独立调试")

        # --- 将各硬件调试页加入到调试中心 (Sub-Tabs) ---
        
        # 模拟器控制
        self.tab_simulator = SimulatorTab(self.device_manager)
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
        self.tab_debug_center.add_debug_tab(self.tab_pcan_fd, "CAN调试（PCAN / ZLG）")

        # 力捷丰 SG400 下载器（复用启动自检建立的共享连接）
        self.tab_download_center = QTabWidget()
        self.tab_debug_center.add_debug_tab(self.tab_download_center, "下载与刷写")
        self.tab_opteeq = OpteeQTab(self.device_manager)
        self.tab_download_center.addTab(self.tab_opteeq, "力捷丰 SG400")

        # 瑞萨 E2 仿真器 (rfp-cli 命令行)
        self.tab_e2_rfp = RenesasE2Tab(self.device_manager)
        self.tab_download_center.addTab(self.tab_e2_rfp, "瑞萨 E2")

        # MES 过站接口配置与手动联调（用户名始终读取当前登录账户）
        self.tab_mes = MesTab(lambda: self.current_user, self)
        self.tab_debug_center.add_debug_tab(self.tab_mes, "MES交互测试")

        # APP 固件刷写独立调试 (LabVIEW 触发刷写与固件检索)
        self.tab_app_flash = AppFlashTab(self.device_manager)
        self.tab_download_center.addTab(self.tab_app_flash, "APP刷写")

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
        for name in ('tab_opteeq','tab_e2_rfp'):
            page=getattr(self,name,None)
            if page:page.shutdown()
        if hasattr(self,'tab_ca550_standalone'):self.tab_ca550_standalone.shutdown()
        if hasattr(self,'tab_dut_power'):self.tab_dut_power.shutdown()
        if hasattr(self,'tab_pcan_fd'):self.tab_pcan_fd.shutdown()
        if hasattr(self,'tab_easy320_standalone'):self.tab_easy320_standalone.shutdown()
        if hasattr(self,'tab_hv'):self.tab_hv.shutdown()
        if hasattr(self,'tab_hardware'):self.tab_hardware.shutdown()
        if hasattr(self,'tab_simulator'):self.tab_simulator.shutdown()
        """软件关闭时的清理逻辑"""
        print("正在关闭软件，清理资源...")
        if hasattr(self, 'tab_multimeter'):
            self.tab_multimeter.shutdown()
        if hasattr(self, 'tab_it6333b'):
            self.tab_it6333b.shutdown()
        if hasattr(self, 'tab_dg2052'):
            self.tab_dg2052.shutdown()
        if hasattr(self, 'tab_scope'):
            self.tab_scope.shutdown()
        for page in getattr(self, 'board_debug_pages', []):
            page.shutdown()
        if hasattr(self, 'engine'):
            print("正在停止所有测试引擎...")
            self.engine.stop_all()
            
        if hasattr(self, 'tab_overview'):
            print("正在停止测试监控线程...")
            self.tab_overview.stop_monitor()

        if hasattr(self, 'device_manager'):
            print("正在断开所有硬件设备连接...")
            self.device_manager.disconnect_all()

        if hasattr(self, 'db_manager'):
            print("正在关闭数据库连接...")
            self.db_manager.close()

        event.accept()

    def on_tab_changed(self, index):
        if index == 0:
            recipes = self.tab_config.get_all_recipes()
            self.tab_overview.update_recipes(recipes)

    def update_user_display(self):
        """刷新底部右下角用户卡片显示及其权限角色"""
        role = self.db_manager.get_user_role(self.current_user) if hasattr(self, 'db_manager') and self.db_manager else "admin"
        if self.current_user.upper() == "ADMIN":
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
        is_admin = (role == "admin" or self.current_user.upper() == "ADMIN")

        # 1. 顶部 Tab 控制：除“单通道测试与序列编辑”外，其余配置/调试页仅管理员可用
        if hasattr(self, 'tabs') and self.tabs:
            if not is_admin and self.tabs.currentIndex() >= 1:
                self.tabs.setCurrentIndex(0)
            for idx in range(1, self.tabs.count()):
                self.tabs.setTabEnabled(idx, is_admin)
                self.tabs.setTabToolTip(idx, "" if is_admin else "需要管理员权限访问此配置/调试页面")

        # 2. 多通道监控控制按钮：强制停止 (btn_stop), 重置大屏 (btn_reset_screen)
        if hasattr(self, 'tab_overview') and self.tab_overview:
            if hasattr(self.tab_overview, 'btn_stop'):
                self.tab_overview.btn_stop.setEnabled(is_admin)
                self.tab_overview.btn_stop.setToolTip("强制停止勾选通道的测试" if is_admin else "需要管理员权限执行强制停止")
            if hasattr(self.tab_overview, 'btn_reset_screen'):
                self.tab_overview.btn_reset_screen.setEnabled(is_admin)
                self.tab_overview.btn_reset_screen.setToolTip("重置大屏通道状态与扫码信息" if is_admin else "需要管理员权限执行重置大屏")

            # 3. 生产统计数据清零权限控制：仅管理员可用
            if hasattr(self.tab_overview, 'btn_reset_stats'):
                self.tab_overview.btn_reset_stats.setEnabled(is_admin)
                if is_admin:
                    self.tab_overview.btn_reset_stats.setToolTip("清空生产总数、合格数、不合格数及合格率统计")
                    self.tab_overview.btn_reset_stats.setText("🔄 数据清零\n(仅管理员)")
                else:
                    self.tab_overview.btn_reset_stats.setToolTip("需要管理员权限执行统计清零")
                    self.tab_overview.btn_reset_stats.setText("🔄 数据清零\n(无权操作)")

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

    def on_aging_sequence_started(self):
        """测试序列执行开始时调用：持久化计数加一并实时刷新界面"""
        if hasattr(self, 'db_manager') and self.db_manager:
            new_count = self.db_manager.increment_aging_exec_count()
            self.lbl_aging_counter.setText(f"⚡ 测试次数\n{new_count} 次")


