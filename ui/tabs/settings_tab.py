# -*- coding: utf-8 -*-
"""软件设置页：条码去重统计、数据清零等生产统计相关配置。"""

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsTab(QWidget):
    """软件设置 TAB 页。

    控件本身不直接改统计状态，通过信号交给 MainWindow 转发到 OverviewTab 处理：
    - dedup_changed(bool)：条码去重开关变化
    - reset_requested()：请求数据清零
    - range_requested()：请求查看当前项目累计统计详情
    """

    dedup_changed = Signal(bool)
    reset_requested = Signal()
    range_requested = Signal()
    titles_save_requested = Signal(str, str)
    mes_save_requested = Signal(dict)
    mes_switch_save_requested = Signal(dict)
    generic_mes_config_requested = Signal()
    generic_mes_retry_requested = Signal()
    ng_alarm_save_requested = Signal(bool, int)
    report_save_requested = Signal(dict)
    start_mode_save_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea,QFormLayout,QFileDialog
        outer=QVBoxLayout(self)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        content=QWidget();scroll.setWidget(content);outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ---- 界面设置：软件名称（适配不同使用现场） ----
        ui_group = QGroupBox("界面设置")
        ui_layout = QVBoxLayout(ui_group)
        ui_layout.setContentsMargins(16, 14, 16, 14)
        ui_layout.setSpacing(8)

        edit_style = (
            "QLineEdit { background-color: #1A1A2E; color: #E0E0E0; border: 1px solid #0F3460;"
            " border-radius: 4px; padding: 6px; font-size: 13px; }"
        )
        window_row = QHBoxLayout()
        window_row.setSpacing(12)
        window_label = QLabel("窗口标题：")
        window_label.setFixedWidth(80)
        window_label.setStyleSheet("color: #E0E0E0; font-size: 13px; font-weight: bold;")
        self.edit_window_title = QLineEdit()
        self.edit_window_title.setPlaceholderText("显示在任务栏与窗口标题栏，如：BMS软件注入&APP刷写系统")
        self.edit_window_title.setStyleSheet(edit_style)
        window_row.addWidget(window_label)
        window_row.addWidget(self.edit_window_title, 1)
        ui_layout.addLayout(window_row)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_label = QLabel("主标题：")
        header_label.setFixedWidth(80)
        header_label.setStyleSheet("color: #E0E0E0; font-size: 13px; font-weight: bold;")
        self.edit_header_title = QLineEdit()
        self.edit_header_title.setPlaceholderText("显示在界面顶部与登录窗口，如：APP刷写系统")
        self.edit_header_title.setStyleSheet(edit_style)
        header_row.addWidget(header_label)
        header_row.addWidget(self.edit_header_title, 1)
        ui_layout.addLayout(header_row)

        save_row = QHBoxLayout()
        save_hint = QLabel("名称保存后立即生效；留空保存则恢复默认名称。")
        save_hint.setStyleSheet("color: #8899AA; font-size: 12px;")
        save_row.addWidget(save_hint, 1)
        self.btn_save_titles = QPushButton("保存名称")
        self.btn_save_titles.setFixedSize(110, 36)
        self.btn_save_titles.setCursor(Qt.PointingHandCursor)
        self.btn_save_titles.clicked.connect(self._emit_titles_save)
        save_row.addWidget(self.btn_save_titles)
        ui_layout.addLayout(save_row)

        layout.addWidget(ui_group)
        from PySide6.QtWidgets import QComboBox
        mode_group=QGroupBox('测试启动顺序');mode_layout=QVBoxLayout(mode_group)
        self.start_mode=QComboBox()
        self.start_mode.addItem('板子到位 → 扫码 → MES检查 → 测试','board_first')
        self.start_mode.addItem('人工点击开始 → 扫码 → MES检查 → 等待板子到位 → 测试','scan_first')
        mode_layout.addWidget(self.start_mode)
        mode_tip=QLabel('先扫码模式使用现有PLC保持寄存器1：值1为到位启动，值2为停止。MES未启用时跳过MES；等待到位期间可点击停止取消，取消后必须重新扫码。')
        mode_tip.setWordWrap(True);mode_layout.addWidget(mode_tip)
        self.save_start_mode=QPushButton('保存启动模式')
        self.save_start_mode.clicked.connect(lambda:self.start_mode_save_requested.emit(self.start_mode.currentData()))
        mode_layout.addWidget(self.save_start_mode);layout.addWidget(mode_group)

        report_group=QGroupBox('测试报告与MES独立保存目录')
        report_layout=QFormLayout(report_group)
        self.edit_report_root=QLineEdit()
        root_row=QWidget();root_layout=QHBoxLayout(root_row);root_layout.setContentsMargins(0,0,0,0)
        root_layout.addWidget(self.edit_report_root,1)
        browse=QPushButton('选择目录…')
        def select_report_root():
            folder=QFileDialog.getExistingDirectory(self,'选择所有项目共用的报告根目录',self.edit_report_root.text())
            if folder:self.edit_report_root.setText(folder)
        browse.clicked.connect(select_report_root);root_layout.addWidget(browse)
        report_layout.addRow('测试报告根目录',root_row)
        self.edit_mes_report_dir=QLineEdit()
        self.edit_mes_report_dir.setPlaceholderText('第三方MES抓取目录；留空不额外输出')
        mes_root_row=QWidget();mes_root_layout=QHBoxLayout(mes_root_row);mes_root_layout.setContentsMargins(0,0,0,0)
        mes_root_layout.addWidget(self.edit_mes_report_dir,1)
        mes_browse=QPushButton('选择目录…')
        def select_mes_report_dir():
            folder=QFileDialog.getExistingDirectory(self,'选择第三方MES文件抓取目录',self.edit_mes_report_dir.text())
            if folder:self.edit_mes_report_dir.setText(folder)
        mes_browse.clicked.connect(select_mes_report_dir);mes_root_layout.addWidget(mes_browse)
        report_layout.addRow('MES保存目录（选填）',mes_root_row)
        self.edit_report_equipment=QLineEdit();self.edit_report_station=QLineEdit()
        self.edit_report_position1=QLineEdit();self.edit_report_position2=QLineEdit()
        for label,widget in [('设备编号',self.edit_report_equipment),('站别名称',self.edit_report_station),('位号1（选填）',self.edit_report_position1),('位号2（选填）',self.edit_report_position2)]:report_layout.addRow(label,widget)
        hint=QLabel('测试报告：根目录／项目编号／YYYY-MM-DD／PASS或FAIL／报告.xls。MES：另存一份同名、同内容的XLS到独立MES目录，供第三方软件抓取；留空不输出。两个路径分别保存，修改后下次测试生效。项目编号及机种名称在配方中填写。')
        hint.setWordWrap(True);report_layout.addRow(hint)
        self.btn_save_reports=QPushButton('保存报告设置')
        self.btn_save_reports.clicked.connect(lambda:self.report_save_requested.emit(dict(
            report_root_dir=self.edit_report_root.text().strip(),mes_report_dir=self.edit_mes_report_dir.text().strip(),report_equipment_no=self.edit_report_equipment.text().strip(),
            report_station_name=self.edit_report_station.text().strip(),report_position1=self.edit_report_position1.text().strip(),report_position2=self.edit_report_position2.text().strip())))
        report_layout.addRow(self.btn_save_reports);layout.addWidget(report_group)
        self.set_report_settings({})

        from PySide6.QtWidgets import QFormLayout, QDoubleSpinBox
        mes_group = QGroupBox('精华MES — 测试前过站检查')
        mes_layout = QFormLayout(mes_group)
        self.chk_mes_precheck = QCheckBox('启用：扫码后先检查MES，通过后才允许测试')
        self.edit_mes_url = QLineEdit()
        self.edit_mes_equipment = QLineEdit()
        self.edit_mes_order = QLineEdit()
        self.spin_mes_timeout = QDoubleSpinBox()
        self.spin_mes_timeout.setRange(0.1, 120); self.spin_mes_timeout.setSuffix(' s')
        mes_layout.addRow(self.chk_mes_precheck)
        mes_layout.addRow('MES接口URL', self.edit_mes_url)
        mes_layout.addRow('设备/工站编号 equipmentNo', self.edit_mes_equipment)
        mes_layout.addRow('工单号 orderNo（可选）', self.edit_mes_order)
        mes_layout.addRow('请求超时', self.spin_mes_timeout)
        hint = QLabel('使能开关切换后自动保存；接口参数单独保存。失败、超时或返回异常均禁止启动测试，并显示原因；用户名使用当前登录账号。')
        hint.setWordWrap(True); mes_layout.addRow(hint)
        self.btn_save_mes = QPushButton('保存精华MES参数')
        self.btn_save_mes.clicked.connect(lambda: self.mes_save_requested.emit(dict(
            mes_url=self.edit_mes_url.text().strip(),
            mes_equipment_no=self.edit_mes_equipment.text().strip(), mes_order_no=self.edit_mes_order.text().strip(),
            mes_timeout=self.spin_mes_timeout.value())))
        mes_layout.addRow(self.btn_save_mes)
        layout.addWidget(mes_group)
        ruineng_group = QGroupBox('锐能MES')
        ruineng_layout = QVBoxLayout(ruineng_group)
        self.chk_generic_mes = QCheckBox('启用锐能MES：扫码查询、检测次数限制及测试完成上传')
        ruineng_layout.addWidget(self.chk_generic_mes)
        self.chk_generic_mes.toggled.connect(lambda checked: self.mes_switch_save_requested.emit({'generic_mes_enabled': checked}))
        self.chk_mes_precheck.toggled.connect(lambda checked: self.mes_switch_save_requested.emit({'mes_precheck_enabled': checked}))
        buttons = QHBoxLayout()
        self.btn_config_generic_mes = QPushButton('配置锐能MES…')
        self.btn_retry_generic_mes = QPushButton('补传待上传数据')
        self.btn_config_generic_mes.clicked.connect(self.generic_mes_config_requested)
        self.btn_retry_generic_mes.clicked.connect(self.generic_mes_retry_requested)
        for button in (self.btn_config_generic_mes, self.btn_retry_generic_mes):
            buttons.addWidget(button)
        ruineng_layout.addLayout(buttons)
        hint = QLabel('使能开关切换后自动保存，精华与锐能互斥启用；参数单独配置保存。上传与检测判定独立，上传失败不改变PASS/FAIL。'
                      '失败数据保存在程序目录 data/mes_uploads/ruineng/pending；网络恢复后点击补传，成功后移入sent归档。')
        hint.setWordWrap(True)
        ruineng_layout.addWidget(hint)
        layout.addWidget(ruineng_group)
        self.set_mes_settings({})

        from PySide6.QtWidgets import QSpinBox
        alarm_group = QGroupBox('连续不良报警')
        alarm_layout = QFormLayout(alarm_group)
        self.chk_ng_alarm = QCheckBox('启用连续不良报警')
        self.spin_ng_alarm = QSpinBox()
        self.spin_ng_alarm.setRange(1, 10000); self.spin_ng_alarm.setValue(3)
        self.spin_ng_alarm.setSuffix(' 片')
        alarm_layout.addRow(self.chk_ng_alarm)
        alarm_layout.addRow('连续不良达到', self.spin_ng_alarm)
        hint = QLabel('达到N片NG弹窗；同一条码重复NG只算一片，OK后清零。停止、取消和MES拦截不计入；每段连续不良报警一次。保存设置或重启软件后重新计数。')
        hint.setWordWrap(True); alarm_layout.addRow(hint)
        self.btn_save_ng_alarm = QPushButton('保存连续不良报警设置')
        self.btn_save_ng_alarm.clicked.connect(lambda: self.ng_alarm_save_requested.emit(
            self.chk_ng_alarm.isChecked(), self.spin_ng_alarm.value()))
        alarm_layout.addRow(self.btn_save_ng_alarm)
        layout.addWidget(alarm_group)

        # ---- 生产统计设置 ----
        group = QGroupBox("生产统计设置")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(16, 14, 16, 14)
        group_layout.setSpacing(8)

        self.chk_stats_deduplicate = QCheckBox("按条码去重统计")
        self.chk_stats_deduplicate.setToolTip(
            "同一条码只计一个产品，重复NG不累加；以最后一次完成结果为准。设置会保存，切换后重新计算当前统计。"
        )
        self.chk_stats_deduplicate.setStyleSheet(
            "QCheckBox { color: #E0E0E0; font-size: 15px; font-weight: bold; }"
        )
        self.chk_stats_deduplicate.toggled.connect(self.dedup_changed)
        group_layout.addWidget(self.chk_stats_deduplicate)

        dedup_hint = QLabel("同一条码只计一个产品，重复NG不累加；以最后一次完成结果为准。设置会保存，切换后重新计算当前统计。")
        dedup_hint.setStyleSheet("color: #8899AA; font-size: 12px;")
        group_layout.addWidget(dedup_hint)

        range_row = QHBoxLayout()
        range_row.setSpacing(12)
        self.btn_stats_range = QPushButton("项目统计详情")
        self.btn_stats_range.setFixedSize(130, 36)
        self.btn_stats_range.setCursor(Qt.PointingHandCursor)
        self.btn_stats_range.setToolTip("查看当前项目自上次清零以来的累计统计，不按时间重置。")
        self.btn_stats_range.clicked.connect(self.range_requested)
        range_row.addWidget(self.btn_stats_range)

        range_hint = QLabel("按项目累计，跨天和重启持续保留；清零只影响当前项目。")
        range_hint.setStyleSheet("color: #8899AA; font-size: 12px;")
        range_row.addWidget(range_hint, 1)
        group_layout.addLayout(range_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #2D2D4A;")
        group_layout.addWidget(separator)

        reset_row = QHBoxLayout()
        reset_row.setSpacing(12)
        self.btn_reset_stats = QPushButton("🔄 数据清零\n(仅管理员)")
        self.btn_reset_stats.setFixedSize(130, 56)
        self.btn_reset_stats.setCursor(Qt.PointingHandCursor)
        self.btn_reset_stats.setToolTip("需要管理员权限执行统计清零")
        self.btn_reset_stats.setStyleSheet("""
            QPushButton {
                background-color: #241D35;
                color: #FFAA33;
                border: 1px solid #FF9900;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
                line-height: 1.3;
            }
            QPushButton:hover {
                background-color: #33264A;
                border: 1px solid #FFB84D;
                color: #FFCC66;
            }
            QPushButton:pressed {
                background-color: #1A1426;
            }
            QPushButton:disabled {
                background-color: #1A1A28;
                color: #555566;
                border: 1px dashed #3D3D4E;
                font-size: 12px;
                font-weight: normal;
            }
        """)
        self.btn_reset_stats.clicked.connect(self.reset_requested)
        reset_row.addWidget(self.btn_reset_stats)

        reset_hint = QLabel("清空生产总数、合格数、不合格数及合格率统计；历史测试记录保留，仅统计起点重置。")
        reset_hint.setStyleSheet("color: #8899AA; font-size: 12px;")
        reset_row.addWidget(reset_hint, 1)
        group_layout.addLayout(reset_row)

        layout.addWidget(group)
        layout.addStretch()
        from ui.layout_safety import protect_form_layout
        protect_form_layout(self)

    def set_mes_settings(self, config):
        from core.mes_client import DEFAULT_MES_URL
        self.edit_mes_url.setText(config.get('mes_url', DEFAULT_MES_URL))
        self.edit_mes_equipment.setText(config.get('mes_equipment_no', ''))
        self.edit_mes_order.setText(config.get('mes_order_no', ''))
        self.spin_mes_timeout.setValue(float(config.get('mes_timeout', 10)))
        self.set_mes_enabled_states(config)

    def set_mes_enabled_states(self, config):
        """Refresh persisted switches without saving recursively or discarding parameter edits."""
        self._saved_mes_switches = {
            'mes_precheck_enabled': config.get('mes_precheck_enabled', False) is True,
            'generic_mes_enabled': config.get('generic_mes_enabled', False) is True,
        }
        with QSignalBlocker(self.chk_mes_precheck), QSignalBlocker(self.chk_generic_mes):
            self.chk_mes_precheck.setChecked(self._saved_mes_switches['mes_precheck_enabled'])
            self.chk_generic_mes.setChecked(self._saved_mes_switches['generic_mes_enabled'])

    def set_report_settings(self,config):
        from core.report_settings import settings_from
        config=settings_from(config)
        for key,widget in [('report_root_dir',self.edit_report_root),('mes_report_dir',self.edit_mes_report_dir),('report_equipment_no',self.edit_report_equipment),('report_station_name',self.edit_report_station),('report_position1',self.edit_report_position1),('report_position2',self.edit_report_position2)]:widget.setText(config[key])

    def set_ng_alarm_settings(self, enabled, threshold):
        self.chk_ng_alarm.setChecked(bool(enabled))
        self.spin_ng_alarm.setValue(threshold)

    def set_dedup_checked(self, checked):
        """无信号地刷新去重勾选状态（初始化与保存失败回滚时使用）。"""
        self.chk_stats_deduplicate.blockSignals(True)
        self.chk_stats_deduplicate.setChecked(bool(checked))
        self.chk_stats_deduplicate.blockSignals(False)

    def set_titles(self, window_title, header_title):
        """初始化界面名称输入框。"""
        self.edit_window_title.setText(str(window_title or ""))
        self.edit_header_title.setText(str(header_title or ""))

    def _emit_titles_save(self):
        self.titles_save_requested.emit(
            self.edit_window_title.text(), self.edit_header_title.text())

    def set_reset_enabled(self, is_admin):
        """按角色刷新数据清零按钮的可用态与文案。"""
        self.btn_reset_stats.setEnabled(bool(is_admin))
        if is_admin:
            self.btn_reset_stats.setToolTip("清空生产总数、合格数、不合格数及合格率统计")
            self.btn_reset_stats.setText("🔄 数据清零\n(仅管理员)")
        else:
            self.btn_reset_stats.setToolTip("需要管理员权限执行统计清零")
            self.btn_reset_stats.setText("🔄 数据清零\n(无权操作)")
