from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QComboBox, QPushButton, QGroupBox, QTextEdit, 
                               QGridLayout, QFrame, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QStackedWidget, QSplitter, QLineEdit,
                               QStyledItemDelegate,QHeaderView)
from PySide6.QtCore import Qt,QSize,QEvent


class DeviceNavigationDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        self.initStyleOption(option,index)
        tree=self.parent();depth=1;parent=index.parent()
        while parent.isValid():depth+=1;parent=parent.parent()
        width=max(60,tree.viewport().width()-tree.indentation()*depth-20)
        bounds=option.fontMetrics.boundingRect(0,0,width,10000,Qt.TextWordWrap,option.text)
        return QSize(width,max(42,bounds.height()+24))

class SingleChannelDebugTab(QWidget):
    def __init__(self):
        super().__init__()
        self._init_ui()
        
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        
        # --- 左侧：控制与状态区 ---
        left_panel = QVBoxLayout()
        
        # 1. 通道选择
        select_group = QGroupBox("调试通道选择")
        select_layout = QHBoxLayout()
        select_layout.addWidget(QLabel("当前通道:"))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems([f"Channel {i+1}" for i in range(48)])
        select_layout.addWidget(self.channel_combo)
        self.btn_connect = QPushButton("连接/开启监听")
        self.btn_connect.setStyleSheet("background-color: #28A745; color: white;")
        select_layout.addWidget(self.btn_connect)
        select_group.setLayout(select_layout)
        left_panel.addWidget(select_group)
        
        # 2. 实时状态监控 (看板)
        monitor_group = QGroupBox("实时数据看板")
        monitor_layout = QGridLayout()
        
        # 模拟几个大字显示数据
        self.labels = {}
        items = [("总电压 (V)", "0.00", "cyan"), ("电流 (A)", "0.0", "lime"), 
                 ("最高温 (℃)", "25.0", "orange"), ("SOC (%)", "0.0", "yellow")]
        
        for i, (name, val, color) in enumerate(items):
            lbl_name = QLabel(name)
            lbl_val = QLabel(val)
            lbl_val.setStyleSheet(f"font-size: 30px; font-weight: bold; color: {color}; font-family: 'Consolas';")
            monitor_layout.addWidget(lbl_name, i // 2 * 2, i % 2)
            monitor_layout.addWidget(lbl_val, i // 2 * 2 + 1, i % 2)
            self.labels[name] = lbl_val
            
        monitor_group.setLayout(monitor_layout)
        left_panel.addWidget(monitor_group)
        
        # 3. 手动控制指令
        ctrl_group = QGroupBox("手动控制指令")
        ctrl_layout = QGridLayout()
        
        cmds = ["闭合主继电器", "断开主继电器", "强制充电开始", "强制放电开始", 
                "清除报警", "读取版本信息", "系统复位"]
        for i, cmd in enumerate(cmds):
            btn = QPushButton(cmd)
            btn.setMinimumHeight(40)
            ctrl_layout.addWidget(btn, i // 2, i % 2)
            
        ctrl_group.setLayout(ctrl_layout)
        left_panel.addWidget(ctrl_group)
        left_panel.addStretch()
        
        main_layout.addLayout(left_panel, 2)
        
        # --- 右侧：报文日志区 ---
        right_panel = QVBoxLayout()
        log_group = QGroupBox("底层通讯报文监控 (Raw Data)")
        log_layout = QVBoxLayout()
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("""
            background-color: #000000; 
            color: #00FF00; 
            font-family: 'Consolas'; 
            font-size: 11px;
        """)
        self.log_output.append("[System] 调试控制台就绪...")
        self.log_output.append("[Log] 2026-05-11 11:22:34 RX: 7E 01 02 03 04 05 FF 0D")
        
        log_layout.addWidget(self.log_output)
        
        btn_log_layout = QHBoxLayout()
        btn_log_layout.addWidget(QPushButton("清空日志"))
        btn_log_layout.addWidget(QPushButton("导出当前日志"))
        log_layout.addLayout(btn_log_layout)
        
        log_group.setLayout(log_layout)
        right_panel.addWidget(log_group)
        
        main_layout.addLayout(right_panel, 3)

class DebugTab(QWidget):
    """可搜索的设备分类导航，设备内部保留各自的分页。"""
    def __init__(self, device_manager=None):
        super().__init__()
        self.mgr = device_manager
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.splitter = QSplitter(Qt.Horizontal)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 12, 10, 12)
        sidebar.setMinimumWidth(260)
        sidebar_layout.addWidget(QLabel("设备调试"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索设备 / 型号…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(40)
        sidebar_layout.addWidget(self.search)
        self.navigation = QTreeWidget()
        self.navigation.setHeaderHidden(True)
        self.navigation.setIndentation(14)
        self.navigation.setWordWrap(True)
        self.navigation.setTextElideMode(Qt.ElideNone)
        self.navigation.setUniformRowHeights(False)
        self.navigation.setItemDelegate(DeviceNavigationDelegate(self.navigation))
        self.navigation.header().setSectionResizeMode(0,QHeaderView.Stretch)
        self.navigation.viewport().installEventFilter(self)
        self.navigation.setStyleSheet("QTreeWidget::item { padding: 9px 4px; } QTreeWidget::item:selected { background: #24466c; color: #ffffff; }")
        sidebar_layout.addWidget(self.navigation, 1)
        self.empty_label = QLabel("没有匹配的设备")
        self.empty_label.setVisible(False)
        sidebar_layout.addWidget(self.empty_label)
        self.groups = {}
        self.items = []
        self._search_expansion=None
        for name in ("电源与模拟器", "测量与信号", "通讯与控制", "下载与刷写"):
            item = QTreeWidgetItem(self.navigation, [name])
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            item.setExpanded(False)
            self.groups[name] = item
        self.splitter.addWidget(sidebar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 12, 8, 0)
        self.page_title = QLabel("请选择设备")
        self.page_title.setStyleSheet("font-size: 18px; font-weight: bold; padding: 0 4px 8px;")
        content_layout.addWidget(self.page_title)
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages, 1)
        self.splitter.addWidget(content)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([300, 1200])
        layout.addWidget(self.splitter)
        self.navigation.currentItemChanged.connect(self._select_device)
        self.search.textChanged.connect(self._filter_devices)

    def add_debug_tab(self, widget, label, category=None):
        """外部调用：向调试中心添加子页面"""
        if category is None:
            if any(word in label for word in ("电源", "高压", "电池模拟")):
                category = "电源与模拟器"
            elif any(word in label for word in ("示波器", "信号发生", "万用表", "CA550")):
                category = "测量与信号"
            elif any(word in label for word in ("下载", "刷写")):
                category = "下载与刷写"
            else:
                category = "通讯与控制"
        if category not in self.groups:
            group = QTreeWidgetItem(self.navigation, [category])
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            group.setExpanded(False)
            self.groups[category] = group
        index = self.pages.addWidget(widget)
        item = QTreeWidgetItem(self.groups[category], [label])
        item.setData(0, Qt.UserRole, index)
        item.setToolTip(0, label)
        aliases = {'电源调试': 'IT6722A IT6333B', '下载与刷写': 'SG400 E2 APP 瑞萨 力捷丰',
                   'CAN调试（PCAN / ZLG）': 'CANFD UDS'}
        item.setData(0, Qt.UserRole + 1, label + ' ' + aliases.get(label, ''))
        self.items.append(item)
        if len(self.items) == 1:
            self._select_device(item)
        self._filter_devices(self.search.text())

    def eventFilter(self,watched,event):
        if watched is self.navigation.viewport() and event.type()==QEvent.Resize:
            self.navigation.scheduleDelayedItemsLayout()
        return super().eventFilter(watched,event)

    def _select_device(self, item, previous=None):
        if item is None or item.data(0, Qt.UserRole) is None:
            return
        self.pages.setCurrentIndex(item.data(0, Qt.UserRole))
        self.page_title.setText(f"{item.parent().text(0)} / {item.text(0)}")

    def _filter_devices(self, text):
        query = text.strip().casefold()
        if query and self._search_expansion is None:
            self._search_expansion={name:group.isExpanded() for name,group in self.groups.items()}
        elif not query and self._search_expansion is not None:
            for name,group in self.groups.items():group.setExpanded(self._search_expansion.get(name,False))
            self._search_expansion=None
        visible = []
        for item in self.items:
            match = query in item.data(0, Qt.UserRole + 1).casefold() or query in item.parent().text(0).casefold()
            item.setHidden(not match)
            if match:
                visible.append(item)
        for group in self.groups.values():
            group.setHidden(not any(not group.child(i).isHidden() for i in range(group.childCount())))
            if query:
                group.setExpanded(True)
        self.empty_label.setVisible(not visible)
        current = self.navigation.currentItem()
        if query and visible and (current is None or current.isHidden()):
            self.navigation.setCurrentItem(visible[0])
