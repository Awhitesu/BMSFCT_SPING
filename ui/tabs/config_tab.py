from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, 
                               QComboBox, QMessageBox, QAbstractItemView, QMenu,
                               QTextBrowser, QGroupBox, QSpinBox, QGridLayout, QLineEdit)
from PySide6.QtGui import QColor, QFont, QAction, QKeySequence, QShortcut, QDrag, QPainter, QPen
from PySide6.QtCore import Qt, Signal, QMimeData
from typing import List


class ProportionalTreeWidget(QTreeWidget):
    """按可视宽度自动分配列宽，避免单列吞掉剩余空间。"""
    COLUMN_RATIOS = (0.34, 0.22, 0.22, 0.22)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._moving_nodes = []
        self._drop_target = None
        self.setDropIndicatorShown(False)
        self.setToolTip("测试项只能在顶层排序；测试步可移到其他测试项。蓝线表示插入位置。")

    def startDrag(self, supported_actions):
        selected = self.selectedItems()
        # 父项已包含子工步，不能再次单独移动它们。
        nodes = [n for n in selected if n.parent() not in selected]
        if not nodes or len({n.parent() is None for n in nodes}) != 1:
            return
        def order(n):
            p = n.parent()
            return (self.indexOfTopLevelItem(p if p is not None else n),
                    p.indexOfChild(n) if p is not None else -1)
        self._moving_nodes = sorted(nodes, key=order)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-test-sequence-move", b"move")
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._moving_nodes = []
        self._drop_target = None
        self.viewport().update()

    def _destination(self, pos):
        if not self._moving_nodes:
            return None
        hit = self.itemAt(pos)
        top_level = self._moving_nodes[0].parent() is None
        if hit is None:
            if top_level:
                return (None, self.topLevelItemCount())
            return None
        rect = self.visualItemRect(hit)
        after = pos.y() >= rect.center().y()
        if top_level:
            # 子工步行不是测试项排序的合法落点。
            if hit.parent() is not None:
                return None
            return (None, self.indexOfTopLevelItem(hit) + int(after))
        parent = hit.parent()
        if parent is None:
            # 放在测试项标题上表示插入该测试项开头。
            return (hit, 0)
        return (parent, parent.indexOfChild(hit) + int(after))

    def dragEnterEvent(self, event):
        if event.source() is self and self._moving_nodes:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self._drop_target = self._destination(event.position().toPoint()) if event.source() is self else None
        if self._drop_target is None:
            event.ignore()
        else:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._drop_target = None
        self.viewport().update()
        event.accept()

    def _move_nodes(self, parent, index):
        nodes = self._moving_nodes
        # 先扣除插入点前将被移走的节点，避免同组排序偏移。
        for node in nodes:
            old_parent = node.parent()
            old_index = old_parent.indexOfChild(node) if old_parent is not None else self.indexOfTopLevelItem(node)
            if old_parent is parent and old_index < index:
                index -= 1
                # 后续索引必须基于已删除后的结构。
            if old_parent is None:
                self.takeTopLevelItem(old_index)
            else:
                old_parent.takeChild(old_index)
        for offset, node in enumerate(nodes):
            if parent is None:
                self.insertTopLevelItem(index + offset, node)
            else:
                parent.insertChild(index + offset, node)
        if parent is not None:
            parent.setExpanded(True)
        self.clearSelection()
        self.setCurrentItem(nodes[0])
        for node in nodes:
            node.setSelected(True)
        self.scrollToItem(nodes[0])

    def dropEvent(self, event):
        target = self._destination(event.position().toPoint()) if event.source() is self else None
        if target is None:
            event.ignore()
        else:
            self._move_nodes(*target)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        self._drop_target = None
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_target is None:
            return
        parent, index = self._drop_target
        count = parent.childCount() if parent is not None else self.topLevelItemCount()
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#00BFFF"), 3))
        left = 8
        if parent is not None:
            rect = self.visualItemRect(parent)
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
            left = rect.left() + self.indentation()
        if index < count:
            node = parent.child(index) if parent is not None else self.topLevelItem(index)
            y = self.visualItemRect(node).top()
        elif count:
            node = parent.child(count - 1) if parent is not None else self.topLevelItem(count - 1)
            # 顶层项的下方应包括它的可见子工步。
            if parent is None and node.isExpanded() and node.childCount():
                node = node.child(node.childCount() - 1)
            y = self.visualItemRect(node).bottom() + 1
        else:
            y = self.visualItemRect(parent).bottom() + 1 if parent is not None else 2
        if parent is not None and not parent.isExpanded():
            y = self.visualItemRect(parent).bottom() + 1
        painter.drawLine(left, y, self.viewport().width() - 8, y)
        painter.end()

    def _resize_columns(self):
        width = max(0, self.viewport().width() - 2)
        if width <= 0:
            return
        used = 0
        for column, ratio in enumerate(self.COLUMN_RATIOS[:-1]):
            column_width = int(width * ratio)
            self.setColumnWidth(column, column_width)
            used += column_width
        self.setColumnWidth(len(self.COLUMN_RATIOS) - 1, max(0, width - used))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_columns()

    def showEvent(self, event):
        super().showEvent(event)
        self._resize_columns()


class ConfigTab(QWidget):
    def __init__(self, db_manager, device_manager=None):
        super().__init__()
        self.db_manager = db_manager
        self.device_manager = device_manager
        self.clipboard_data = None  # 用于存储复制的节点数据
        self.current_recipe_name = None # 记录当前加载的配方名
        self._init_ui()
        self.refresh_recipe_list()
        self.set_editor_enabled(False) # 初始禁用编辑
        
    def _init_ui(self):
        layout = QHBoxLayout(self)
        
        # 左侧：用例配方列表
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("测试用例列表 (配方)"))
        self.recipe_tree = QTreeWidget()
        self.recipe_tree.setHeaderLabel("配方名称")
        self.recipe_tree.itemClicked.connect(self.on_recipe_selected)
        left_panel.addWidget(self.recipe_tree)
        
        btn_recipe_layout = QHBoxLayout()
        self.btn_add_recipe = QPushButton("➕ 新建配方")
        self.btn_add_recipe.clicked.connect(self.add_new_recipe)
        self.btn_del_recipe = QPushButton("🗑️ 删除配方")
        self.btn_del_recipe.clicked.connect(self.delete_selected_recipe)
        self.btn_del_recipe.setStyleSheet("background-color: #5A5A5A;")
        btn_recipe_layout.addWidget(self.btn_add_recipe)
        btn_recipe_layout.addWidget(self.btn_del_recipe)
        left_panel.addLayout(btn_recipe_layout)
        
        # 增加配方列表右键菜单支持
        self.recipe_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recipe_tree.customContextMenuRequested.connect(self.on_recipe_context_menu)
        
        layout.addLayout(left_panel, 1)
        
        # 右侧：配方基础属性及工步编辑
        right_panel = QVBoxLayout()
        
        # 使用容器包装右侧编辑区域以便整体控制启用状态
        self.edit_container = QWidget()
        self.edit_layout = QVBoxLayout(self.edit_container)
        self.edit_layout.setContentsMargins(0, 0, 0, 0)

        recipe_hw_group = QGroupBox("配方硬件关联")
        recipe_hw_layout = QGridLayout(recipe_hw_group)
        recipe_hw_layout.setHorizontalSpacing(15)
        recipe_hw_layout.setVerticalSpacing(8)

        self.recipe_pcan_channel = QComboBox()
        self.recipe_pcan_channel.addItems([f"PCAN_USBBUS{i}" for i in range(1, 17)])
        self.recipe_pcan_channel.addItems([f"ZLG_CAN{i}" for i in range(1, 5)])
        self.recipe_pcan_channel.setToolTip('ZLG_CAN1–4：CANFDNET-400U物理通道1–4；网络参数在硬件管理中配置')
        self.recipe_can_mode = QComboBox()
        self.recipe_can_mode.addItems(["经典 CAN", "CAN-FD", "CAN-FD + BRS"])
        self.recipe_nominal_bitrate = QComboBox()
        self.recipe_nominal_bitrate.addItems(["125000", "250000", "500000", "1000000"])
        self.recipe_data_bitrate = QComboBox()
        self.recipe_data_bitrate.addItems(["500000", "1000000", "2000000", "4000000", "5000000", "8000000"])
        self.recipe_number = QSpinBox()
        self.recipe_number.setRange(0, 65535)
        self.recipe_number.setValue(0)

        # 单行展示 CAN 参数与 PLC 配方号
        row1_items = [
            ("CAN卡:", self.recipe_pcan_channel),
            ("模式:", self.recipe_can_mode),
            ("仲裁波特率:", self.recipe_nominal_bitrate),
            ("数据波特率:", self.recipe_data_bitrate),
            ("PLC配方号:", self.recipe_number),
        ]
        for col_idx, (lbl_text, widget) in enumerate(row1_items):
            recipe_hw_layout.addWidget(QLabel(lbl_text), 0, col_idx * 2)
            recipe_hw_layout.addWidget(widget, 0, col_idx * 2 + 1)
            recipe_hw_layout.setColumnStretch(col_idx * 2 + 1, 1)

        self.recipe_can_mode.currentIndexChanged.connect(
            lambda index: self.recipe_data_bitrate.setEnabled(index > 0)
        )
        self.recipe_data_bitrate.setEnabled(False)
        self.edit_layout.addWidget(recipe_hw_group)

        # 配方 SN 条码关联与 AI 规则推导区
        recipe_sn_group = QGroupBox("配方 SN 条码关联与 AI 规则")
        recipe_sn_layout = QHBoxLayout(recipe_sn_group)
        recipe_sn_layout.setContentsMargins(10, 8, 10, 8)
        recipe_sn_layout.setSpacing(10)

        self.edit_sn_regex = QLineEdit("^SCUx[A-Z0-9]+$")
        self.edit_sn_regex.setPlaceholderText("产品 SN 条码正则表达式 (例: ^SCUx03XP[A-Z0-9]{13}$)")

        btn_ai_rule = QPushButton("🤖 AI 智能样本推导")
        btn_ai_rule.setToolTip("点击打开 AI 样本条码推导与测试引擎弹窗，扫码自动生成规则并测试")
        btn_ai_rule.setStyleSheet("background-color: #6F42C1; color: white; font-weight: bold; padding: 4px 14px;")
        btn_ai_rule.clicked.connect(self.open_ai_barcode_dialog)

        recipe_sn_layout.addWidget(QLabel("条码规则:"))
        recipe_sn_layout.addWidget(self.edit_sn_regex, 1)
        recipe_sn_layout.addWidget(btn_ai_rule)

        self.edit_layout.addWidget(recipe_sn_group)
        
        # 树图标题与批量操作上移融合顶栏
        tree_header_layout = QHBoxLayout()
        tree_header_label = QLabel("测试项目与工步流 (树状结构):")
        tree_header_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFD700;")
        tree_header_layout.addWidget(tree_header_label)
        tree_header_layout.addStretch()

        self.btn_batch_skip = QPushButton("⚡ 批量屏蔽/取消 (Space)")
        self.btn_batch_skip.clicked.connect(self.batch_toggle_skip)
        self.btn_batch_skip.setStyleSheet("background-color: #4A5568; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px;")

        self.btn_batch_copy = QPushButton("📋 批量复制 (Ctrl+C)")
        self.btn_batch_copy.clicked.connect(self.copy_node)
        self.btn_batch_copy.setStyleSheet("background-color: #2D3748; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px;")

        self.btn_batch_paste = QPushButton("📥 批量粘贴 (Ctrl+V)")
        self.btn_batch_paste.clicked.connect(self.paste_node)
        self.btn_batch_paste.setStyleSheet("background-color: #1A202C; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px;")

        tree_header_layout.addWidget(self.btn_batch_skip)
        tree_header_layout.addWidget(self.btn_batch_copy)
        tree_header_layout.addWidget(self.btn_batch_paste)
        self.edit_layout.addLayout(tree_header_layout)

        self.step_tree = ProportionalTreeWidget()
        self.step_tree.setHeaderLabels(["名称/工步", "模式/范围", "目标值/下限", "截止时间/上限"])
        self.step_tree.header().setMinimumSectionSize(90)

        # 启用拖拽排序
        self.step_tree.setDragEnabled(True)
        self.step_tree.setAcceptDrops(True)
        self.step_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.step_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # 启用右键菜单
        self.step_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.step_tree.customContextMenuRequested.connect(self.on_context_menu)
        # 绑定双击编辑
        self.step_tree.itemDoubleClicked.connect(lambda: self.edit_node())

        self.edit_layout.addWidget(self.step_tree)

        # --- 子工步与测试项参数实时预览面板 ---
        self.preview_panel = QGroupBox("参数实时预览 (点击选中项即可预览)")
        self.preview_panel.setStyleSheet("""
            QGroupBox {
                border: 2px solid #2D3748;
                border-radius: 8px;
                margin-top: 5px;
                font-weight: bold;
                color: #FFD700;
                background-color: #1A202C;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(10, 20, 10, 10)

        header_layout = QHBoxLayout()
        self.lbl_preview_action = QLabel("参数预览: --")
        self.lbl_preview_action.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 12px;")

        btn_close_preview = QPushButton("✕ 关闭预览 (Esc)")
        btn_close_preview.setFixedWidth(110)
        btn_close_preview.setStyleSheet("""
            QPushButton {
                background-color: #4A5568;
                color: white;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: #E53E3E;
            }
        """)
        btn_close_preview.clicked.connect(self.close_preview_panel)

        header_layout.addWidget(self.lbl_preview_action)
        header_layout.addStretch()
        header_layout.addWidget(btn_close_preview)
        preview_layout.addLayout(header_layout)

        self.preview_browser = QTextBrowser()
        self.preview_browser.setStyleSheet("background-color: #10141D; border: 1px solid #2D3748; border-radius: 4px;")
        self.preview_browser.setMinimumHeight(100)
        self.preview_browser.setMaximumHeight(180)
        preview_layout.addWidget(self.preview_browser)

        self.preview_panel.setVisible(False)
        self.edit_layout.addWidget(self.preview_panel)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 6, 0, 0)

        # 节点编辑组
        btn_add_item = QPushButton("➕ 添加测试项")
        btn_add_item.clicked.connect(self.add_test_item)
        btn_add_item.setStyleSheet("background-color: #17A2B8; font-weight: bold; min-height: 36px; padding: 0 12px;")

        btn_add_step = QPushButton("➕ 添加子工步")
        btn_add_step.clicked.connect(self.add_step)
        btn_add_step.setStyleSheet("background-color: #0284C7; font-weight: bold; min-height: 36px; padding: 0 12px;")

        btn_edit = QPushButton("✏️ 编辑选中")
        btn_edit.clicked.connect(self.edit_node)
        btn_edit.setStyleSheet("background-color: #6F42C1; color: white; font-weight: bold; min-height: 36px; padding: 0 12px;")

        btn_del = QPushButton("🗑️ 删除选中")
        btn_del.clicked.connect(self.delete_node)
        btn_del.setStyleSheet("background-color: #DC3545; color: white; font-weight: bold; min-height: 36px; padding: 0 12px;")

        btn_layout.addWidget(btn_add_item)
        btn_layout.addWidget(btn_add_step)
        btn_layout.addWidget(btn_edit)
        btn_layout.addWidget(btn_del)

        btn_layout.addStretch()

        btn_ai_recipe = QPushButton("🤖 AI 助手")
        btn_ai_recipe.clicked.connect(self.open_ai_recipe_dialog)
        btn_ai_recipe.setStyleSheet("background-color: #6F42C1; color: white; font-weight: bold; min-height: 36px; padding: 0 16px;")
        btn_layout.addWidget(btn_ai_recipe)

        # 配方保存组
        btn_save_recipe = QPushButton("💾 保存配方")
        btn_save_recipe.clicked.connect(self.save_recipe)
        btn_save_recipe.setStyleSheet("background-color: #28A745; color: white; font-weight: bold; min-height: 36px; padding: 0 16px;")

        btn_save_as_recipe = QPushButton("📑 配方另存为")
        btn_save_as_recipe.clicked.connect(self.save_recipe_as)
        btn_save_as_recipe.setStyleSheet("background-color: #0D6EFD; color: white; font-weight: bold; min-height: 36px; padding: 0 16px;")

        btn_layout.addWidget(btn_save_recipe)
        btn_layout.addWidget(btn_save_as_recipe)

        self.edit_layout.addLayout(btn_layout)
        right_panel.addWidget(self.edit_container)

        layout.addLayout(right_panel, 3)

        # 绑定快捷键
        QShortcut(QKeySequence("Ctrl+C"), self.step_tree, self.copy_node)
        QShortcut(QKeySequence("Ctrl+V"), self.step_tree, self.paste_node)
        QShortcut(QKeySequence("Delete"), self.step_tree, self.delete_node)
        QShortcut(QKeySequence("Space"), self.step_tree, self.batch_toggle_skip)

    def close_preview_panel(self):
        """快速关闭预览面板并清空选中"""
        self.preview_panel.setVisible(False)
        self.step_tree.clearSelection()

    def toggle_preview_panel(self):
        """切换预览面板的显示与隐藏"""
        self.preview_panel.setVisible(not self.preview_panel.isVisible())

    def update_param_preview(self):
        """当选择发生变化时，自动更新子工步/测试项参数预览"""
        selected = self.step_tree.selectedItems()
        if not selected:
            return
            
        item = selected[0]
        # 判断是否为子工步 (有父节点代表是子工步)
        if item.parent() is not None:
            action = item.text(1)
            params = item.text(2)
            device = item.data(0, Qt.UserRole)
            
            # 显示预览面板
            self.preview_panel.setVisible(True)
            self.lbl_preview_action.setText(f"动作: {action} ({device or '未知设备'})")
            
            # 渲染 HTML (增加对 device 的传递以实现精确设备翻译)
            html = self.parse_params_to_html(action, device, params)
            self.preview_browser.setHtml(html)
        else:
            # 如果是主工步，显示主工步基本配置参数限制 (多列并排，完美填满宽度)
            name = item.text(0)
            mode = item.text(1)
            min_val = item.text(2)
            max_val = item.text(3)
            standard_type = item.data(0, Qt.UserRole) or "数值"
            retry_count = item.data(1, Qt.UserRole) or "不复测"
            unit = item.data(2, Qt.UserRole) or "NULL"
            
            self.preview_panel.setVisible(True)
            self.lbl_preview_action.setText(f"测试项: {name}")
            
            html = f"""
            <style>
                table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
                td {{ border: 1px solid #2D3748; padding: 6px; font-size: 12px; color: #E2E8F0; }}
                .label {{ font-weight: bold; color: #FFD700; background-color: #2D3748; width: 15%; text-align: right; padding-right: 10px; }}
                .value {{ background-color: #10141D; width: 18%; text-align: left; padding-left: 10px; }}
            </style>
            <table>
                <tr>
                    <td class='label'>标准类型</td><td class='value'>{standard_type}</td>
                    <td class='label'>判定模式</td><td class='value'>{mode}</td>
                    <td class='label'>下限/目标</td><td class='value'>{min_val}</td>
                </tr>
                <tr>
                    <td class='label'>上限/截止</td><td class='value'>{max_val}</td>
                    <td class='label'>物理单位</td><td class='value'>{unit}</td>
                    <td class='label'>复测次数</td><td class='value'>{retry_count}</td>
                </tr>
            </table>
            """
            self.preview_browser.setHtml(html)

    def translate_and_format_params(self, action, device, params_str):
        """将指令底层的 KEY:VALUE 翻译为同设置页面完全一致的友好中文配置项"""
        kv = {}
        for part in str(params_str).replace("；", "/").replace("，", "/").split("/"):
            part = part.strip()
            if not part: continue
            if ":" in part:
                k, v = part.split(":", 1)
                kv[k.strip().upper()] = v.strip()
            else:
                kv[part.strip().upper()] = part.strip()
                
        friendly_kv = {}
        
        # 1. 3.5H EOL 协议的精细翻译
        if "EOL" in str(device) or "EOL" in str(action):
            op = kv.get("EOL", action or "")
            
            # 公共协议底层参数
            if "TIMEOUT" in kv: friendly_kv["超时(ms)"] = kv["TIMEOUT"]
            if "CH" in kv: friendly_kv["通讯通道"] = f"通道 {kv['CH']}"
            if "TX_ID" in kv: friendly_kv["发送ID"] = kv["TX_ID"]
            if "RX_ID" in kv: friendly_kv["接收ID"] = kv["RX_ID"]
            if "ARGS" in kv: friendly_kv["额外参数"] = kv["ARGS"]
            
            p1 = kv.get("PARAM1", kv.get("ADC", kv.get("STATE", kv.get("INDEX", kv.get("PWM", kv.get("NTC", kv.get("HALL", kv.get("OP", kv.get("GPIO", "")))))))))
            p2 = kv.get("PARAM2", kv.get("MODE", kv.get("LEVEL", kv.get("VAL", kv.get("STATE_VAL", kv.get("INDEX", ""))))))
            
            p1_label = "参数1"
            p2_label = "参数2"
            
            if "0x04" in op:
                p1_label = "GPIO通道"
                p2_label = "动作类型"
                if p2 == "READ": p2 = "读取电平"
                elif p2 == "WRITE_HIGH": p2 = "写入高电平"
                elif p2 == "WRITE_LOW": p2 = "写入低电平"
            elif "0x06" in op:
                p1_label = "ADC选择"
                p2_label = "读取模式"
                if p2 == "VALUE": p2 = "转换值"
                elif p2 == "RAW": p2 = "原始值"
            elif "0x05" in op:
                p1_label = "PWM通道"
                p2_label = "读取内容"
                if p2 == "DUTY": p2 = "占空比"
                elif p2 == "FREQ": p2 = "频率"
            elif "0x03" in op:
                p1_label = "操作内容"
                p2_label = "控制值"
                if p1 == "READ": p1 = "读取绝缘阻抗"
                elif p1 == "WRITE": p1 = "设置控制状态"
                if p2 == "0": p2 = "0 P/N均断开"
                elif p2 == "1": p2 = "1 P闭合N断开"
                elif p2 == "2": p2 = "2 P断开N闭合"
            elif "0x07" in op:
                p1_label = "操作类别"
                p2_label = "高压索引"
                if p1 in ("0x01", "1", "NODE_COUNT", "设置节点数"): p1 = "设置节点数目"
                elif p1 in ("0x02", "2", "HV_VOLT", "总压/采样"): p1 = "高压读取"
                elif p1 in ("0x0E", "0x0e", "14", "CELL_VOLT", "单体电压"): p1 = "单体电压读取"
                elif p1 in ("0x0F", "0x0f", "15"): p1 = "Stack电压读取"
                elif p1 in ("0x10", "16"): p1 = "快充阻抗读取"
                
                if p2 == "0x00": p2 = "0x00 代表默认"
                elif p2 == "0x02": p2 = "0x02代表HV1"
                elif p2 == "0x03": p2 = "0x03代表HV2"
                elif p2 == "0x04": p2 = "0x04代表HV3"
                elif p2 in ("0x0B", "0x0b"): p2 = "0x0B代表Link1"
                elif p2 in ("0x0C", "0x0c"): p2 = "0x0C代表Link2"
                
                if "PARAM3" in kv: friendly_kv["节点数目"] = kv["PARAM3"]
                if "PARAM4" in kv: friendly_kv["索引"] = kv["PARAM4"]
            elif "0x10" in op:
                p1_label = "NTC索引"
                p2_label = "温感类型"
                if p2 == "CELL_NTC": p2 = "1 CELL_NTC"
                elif p2 == "PCB_NTC": p2 = "2 PCB_NTC"
                elif p2 == "SHUNT": p2 = "3 SHUNT"
                elif p2 == "NTCF": p2 = "4 NTCF"
                elif p2 == "FPCB_NTC": p2 = "5 FPCB_NTC"
            elif "0x08" in op:
                p1_label = "模式参数"
                p2_label = "索引"
                if p1 == "0x01": p1 = "0x01 占空比"
                elif p1 == "0x02": p1 = "0x02 频率"
                elif p1 == "0x03": p1 = "0x03 阻抗"
                elif p1 == "0x04": p1 = "0x04 脉宽"
                if p2 == "0": p2 = "sig1"
                elif p2 == "1": p2 = "sig3"
            elif "0x0A" in op:
                p1_label = "操作"
                if p1 == "READ": p1 = "读取数据"
                elif p1 == "WRITE": p1 = "写入数据"
                elif p1 == "SET_ADDR": p1 = "设置地址"
            elif "0x09" in op:
                p1_label = "RTC功能"
                if p1 == "READ": p1 = "读取时间"
                elif p1 == "SET_TIME": p1 = "设置时间"
                elif p1 == "SET_WAKEUP": p1 = "设置唤醒"
            elif "0xFF" in op:
                p1_label = "读取项"
                p2_label = "通道选择"
                if p1 == "0x06": p1 = "读取第一唤醒源"
                elif p1 == "0x0E": p1 = "读取压力传感器"
                elif p1 == "0x11": p1 = "读取高边负载回采电压"
                if p2: p2 = f"通道 {p2}"
            elif "0x0B" in op:
                p1_label = "霍尔通道"
                if p1 == "0x01": p1 = "通道1"
                elif p1 == "0x03": p1 = "通道2"
                
            if p1: friendly_kv[p1_label] = p1
            if p2: friendly_kv[p2_label] = p2
            
        # 2. CAN 交互的精细翻译
        elif "CAN" in str(device) or "报文" in str(action):
            
            self.preview_panel.setVisible(True)
            self.lbl_preview_action.setText(f"测试项: {name}")
            
            html = f"""
            <style>
                table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
                td {{ border: 1px solid #2D3748; padding: 6px; font-size: 12px; color: #E2E8F0; }}
                .label {{ font-weight: bold; color: #FFD700; background-color: #2D3748; width: 15%; text-align: right; padding-right: 10px; }}
                .value {{ background-color: #10141D; width: 18%; text-align: left; padding-left: 10px; }}
            </style>
            <table>
                <tr>
                    <td class='label'>标准类型</td><td class='value'>{standard_type}</td>
                    <td class='label'>判定模式</td><td class='value'>{mode}</td>
                    <td class='label'>下限/目标</td><td class='value'>{min_val}</td>
                </tr>
                <tr>
                    <td class='label'>上限/截止</td><td class='value'>{max_val}</td>
                    <td class='label'>物理单位</td><td class='value'>{unit}</td>
                    <td class='label'>复测次数</td><td class='value'>{retry_count}</td>
                </tr>
            </table>
            """
            self.preview_browser.setHtml(html)

    def translate_and_format_params(self, action, device, params_str):
        """将指令底层的 KEY:VALUE 翻译为同设置页面完全一致的友好中文配置项"""
        kv = {}
        for part in str(params_str).replace("；", "/").replace("，", "/").split("/"):
            part = part.strip()
            if not part: continue
            if ":" in part:
                k, v = part.split(":", 1)
                kv[k.strip().upper()] = v.strip()
            else:
                kv[part.strip().upper()] = part.strip()
                
        friendly_kv = {}
        
        # 1. 3.5H EOL 协议的精细翻译
        if "EOL" in str(device) or "EOL" in str(action):
            op = kv.get("EOL", action or "")
            
            # 公共协议底层参数
            if "TIMEOUT" in kv: friendly_kv["超时(ms)"] = kv["TIMEOUT"]
            if "CH" in kv: friendly_kv["通讯通道"] = f"通道 {kv['CH']}"
            if "TX_ID" in kv: friendly_kv["发送ID"] = kv["TX_ID"]
            if "RX_ID" in kv: friendly_kv["接收ID"] = kv["RX_ID"]
            if "ARGS" in kv: friendly_kv["额外参数"] = kv["ARGS"]
            
            p1 = kv.get("PARAM1", kv.get("ADC", kv.get("STATE", kv.get("INDEX", kv.get("PWM", kv.get("NTC", kv.get("HALL", kv.get("OP", kv.get("GPIO", "")))))))))
            p2 = kv.get("PARAM2", kv.get("MODE", kv.get("LEVEL", kv.get("VAL", kv.get("STATE_VAL", kv.get("INDEX", ""))))))
            
            p1_label = "参数1"
            p2_label = "参数2"
            
            if "0x04" in op:
                p1_label = "GPIO通道"
                p2_label = "动作类型"
                if p2 == "READ": p2 = "读取电平"
                elif p2 == "WRITE_HIGH": p2 = "写入高电平"
                elif p2 == "WRITE_LOW": p2 = "写入低电平"
            elif "0x06" in op:
                p1_label = "ADC选择"
                p2_label = "读取模式"
                if p2 == "VALUE": p2 = "转换值"
                elif p2 == "RAW": p2 = "原始值"
            elif "0x05" in op:
                p1_label = "PWM通道"
                p2_label = "读取内容"
                if p2 == "DUTY": p2 = "占空比"
                elif p2 == "FREQ": p2 = "频率"
            elif "0x03" in op:
                p1_label = "操作内容"
                p2_label = "控制值"
                if p1 == "READ": p1 = "读取绝缘阻抗"
                elif p1 == "WRITE": p1 = "设置控制状态"
                if p2 == "0": p2 = "0 P/N均断开"
                elif p2 == "1": p2 = "1 P闭合N断开"
                elif p2 == "2": p2 = "2 P断开N闭合"
            elif "0x07" in op:
                p1_label = "操作类别"
                p2_label = "高压索引"
                if p1 in ("0x01", "1", "NODE_COUNT", "设置节点数"): p1 = "设置节点数目"
                elif p1 in ("0x02", "2", "HV_VOLT", "总压/采样"): p1 = "高压读取"
                elif p1 in ("0x0E", "0x0e", "14", "CELL_VOLT", "单体电压"): p1 = "单体电压读取"
                elif p1 in ("0x0F", "0x0f", "15"): p1 = "Stack电压读取"
                elif p1 in ("0x10", "16"): p1 = "快充阻抗读取"
                
                if p2 == "0x00": p2 = "0x00 代表默认"
                elif p2 == "0x02": p2 = "0x02代表HV1"
                elif p2 == "0x03": p2 = "0x03代表HV2"
                elif p2 == "0x04": p2 = "0x04代表HV3"
                elif p2 in ("0x0B", "0x0b"): p2 = "0x0B代表Link1"
                elif p2 in ("0x0C", "0x0c"): p2 = "0x0C代表Link2"
                
                if "PARAM3" in kv: friendly_kv["节点数目"] = kv["PARAM3"]
                if "PARAM4" in kv: friendly_kv["索引"] = kv["PARAM4"]
            elif "0x10" in op:
                p1_label = "NTC索引"
                p2_label = "温感类型"
                if p2 == "CELL_NTC": p2 = "1 CELL_NTC"
                elif p2 == "PCB_NTC": p2 = "2 PCB_NTC"
                elif p2 == "SHUNT": p2 = "3 SHUNT"
                elif p2 == "NTCF": p2 = "4 NTCF"
                elif p2 == "FPCB_NTC": p2 = "5 FPCB_NTC"
            elif "0x08" in op:
                p1_label = "模式参数"
                p2_label = "索引"
                if p1 == "0x01": p1 = "0x01 占空比"
                elif p1 == "0x02": p1 = "0x02 频率"
                elif p1 == "0x03": p1 = "0x03 阻抗"
                elif p1 == "0x04": p1 = "0x04 脉宽"
                if p2 == "0": p2 = "sig1"
                elif p2 == "1": p2 = "sig3"
            elif "0x0A" in op:
                p1_label = "操作"
                if p1 == "READ": p1 = "读取数据"
                elif p1 == "WRITE": p1 = "写入数据"
                elif p1 == "SET_ADDR": p1 = "设置地址"
            elif "0x09" in op:
                p1_label = "RTC功能"
                if p1 == "READ": p1 = "读取时间"
                elif p1 == "SET_TIME": p1 = "设置时间"
                elif p1 == "SET_WAKEUP": p1 = "设置唤醒"
            elif "0xFF" in op:
                p1_label = "读取项"
                p2_label = "通道选择"
                if p1 == "0x06": p1 = "读取第一唤醒源"
                elif p1 == "0x0E": p1 = "读取压力传感器"
                elif p1 == "0x11": p1 = "读取高边负载回采电压"
                if p2: p2 = f"通道 {p2}"
            elif "0x0B" in op:
                p1_label = "霍尔通道"
                if p1 == "0x01": p1 = "通道1"
                elif p1 == "0x03": p1 = "通道2"
                
            if p1: friendly_kv[p1_label] = p1
            if p2: friendly_kv[p2_label] = p2
            
        # 2. CAN 交互的精细翻译
        elif "CAN" in str(device) or "报文" in str(action):
            if "ID" in kv: friendly_kv["帧ID"] = kv["ID"]
            if "DATA" in kv: friendly_kv["发送数据"] = kv["DATA"]
            if "WAIT_ID" in kv: friendly_kv["等待接收ID"] = kv["WAIT_ID"]
            if "TIMEOUT" in kv: friendly_kv["超时(ms)"] = kv["TIMEOUT"]
            if "DLC" in kv: friendly_kv["数据长度(DLC)"] = kv["DLC"]
            if "CH" in kv: friendly_kv["物理通道"] = f"通道 {kv['CH']}"
            if "TYPE" in kv:
                t = kv["TYPE"]
                if t == "0": friendly_kv["帧类型"] = "标准帧 (CAN 2.0A)"
                elif t == "1": friendly_kv["帧类型"] = "扩展帧 (CAN 2.0B)"
                elif t == "2": friendly_kv["帧类型"] = "CANFD 标准帧"
                elif t == "3": friendly_kv["帧类型"] = "CANFD 扩展帧"
                else: friendly_kv["帧类型"] = t

        # 3. CA550 的翻译
        elif "CA550" in str(device):
            if "VAL" in kv: friendly_kv["输出设定值"] = kv["VAL"]
            if "RANGE" in kv: friendly_kv["量程选择"] = kv["RANGE"]
            if "OUTPUT" in kv: friendly_kv["输出状态"] = kv["OUTPUT"]
            
        # 4. 通用等待
        elif "等待" in str(device) or "延时" in str(action):
            friendly_kv["延时时长"] = params_str
            
        # 5. 继电器控制
        elif "继电器" in str(action) or "Easy320" in str(device) or "Aging Board" in str(device):
            friendly_kv["选择的继电器通道"] = params_str
            
        # 6. 直流源、高压源、AFE、模拟电池等通用设备
        else:
            import re
            v_match = re.search(r'([\d.]+)V', params_str)
            if v_match: friendly_kv["设定电压"] = f"{v_match.group(1)} V"
            
            a_match = re.search(r'([\d.]+)A', params_str)
            if a_match: friendly_kv["设定电流"] = f"{a_match.group(1)} A"
            
            ma_match = re.search(r'([\d.]+)mA', params_str)
            if ma_match: friendly_kv["设定电流"] = f"{ma_match.group(1)} mA"
            
            if "开启" in params_str or "ON" in params_str: friendly_kv["通道输出"] = "开启 (ON)"
            elif "关闭" in params_str or "OFF" in params_str: friendly_kv["通道输出"] = "关闭 (OFF)"
            
            if not friendly_kv:
                for k, v in kv.items():
                    friendly_kv[k] = v
                    
        return friendly_kv

    def parse_params_to_html(self, action_type, device, params_str):
        # 1. 翻译获取对用户极其友好的 Chinese 属性映射表
        friendly_kv = self.translate_and_format_params(action_type, device, params_str)
        
        # 2. 产生极富视觉品质的玻璃拟态表格 (多列合并网格，极大程度榨干横向宽度，消除右侧空白)
        html = """
        <style>
            table { width: 100%; border-collapse: collapse; margin-top: 5px; }
            td { border: 1px solid #2D3748; padding: 6px; font-size: 12px; color: #E2E8F0; }
            .label { font-weight: bold; color: #00E5FF; background-color: #2D3748; width: 15%; text-align: right; padding-right: 10px; }
            .value { background-color: #10141D; width: 18%; text-align: left; padding-left: 10px; }
        </style>
        <table>
        """
        
        # 将参数列表转换为 3 组一行的网格显示
        items = list(friendly_kv.items())
        rows = [items[i:i + 3] for i in range(0, len(items), 3)]
        
        for row in rows:
            html += "<tr>"
            for k, v in row:
                html += f"<td class='label'>{k}</td><td class='value'>{v}</td>"
            # 如果最后一行不够 3 个，用空单元格补齐以维持表格边框对称
            if len(row) < 3:
                for _ in range(3 - len(row)):
                    html += "<td class='label' style='background: transparent; border: none;'></td><td class='value' style='background: transparent; border: none;'></td>"
            html += "</tr>"
            
        if not items:
            html += "<tr><td colspan='6' style='color: #A0AEC0; text-align: center;'>无详细配置参数</td></tr>"
            
        html += "</table>"
        return html

    def set_editor_enabled(self, enabled):
        """控制右侧编辑面板的启用状态"""
        self.edit_container.setEnabled(enabled)
        if not enabled:
            self.step_tree.clear()
            self.edit_container.setToolTip("请先在左侧选择或新建一个配方")
        else:
            self.edit_container.setToolTip("")

    def refresh_recipe_list(self):
        """从本地磁盘刷新配方列表"""
        self.recipe_tree.clear()
        recipes = self.db_manager.list_recipes()
        for name in recipes:
            self.recipe_tree.addTopLevelItem(QTreeWidgetItem([name]))

    def on_recipe_selected(self, item, column):
        """点击左侧配方时加载数据或切换折叠状态"""
        name = item.text(0)
        
        # 如果点击的是当前已选中的配方，则切换右侧树的展开/折叠状态
        if self.current_recipe_name == name:
            if self.step_tree.topLevelItemCount() > 0:
                # 以第一项的状态作为基准切换
                is_expanded = self.step_tree.topLevelItem(0).isExpanded()
                if is_expanded:
                    self.step_tree.collapseAll()
                else:
                    self.step_tree.expandAll()
            return

        data = self.db_manager.load_recipe_json(name)
        if data:
            self.current_recipe_name = name
            self.set_editor_enabled(True)
            self.load_recipe_to_tree(data)

    def load_recipe_to_tree(self, data):
        """将 JSON 数据还原到树状图中"""
        self.step_tree.clear()
        can_config = data.get("can_config", {})
        self.recipe_pcan_channel.setCurrentText(can_config.get("channel", "PCAN_USBBUS1"))
        self.recipe_can_mode.setCurrentText(can_config.get("mode", "CAN-FD + BRS"))
        self.recipe_nominal_bitrate.setCurrentText(str(can_config.get("nominal_bitrate", 500000)))
        self.recipe_data_bitrate.setCurrentText(str(can_config.get("data_bitrate", 2000000)))
        self.recipe_number.setValue(int(data.get("recipe_number", 0)))
        self.edit_sn_regex.setText(data.get("sn_regex", "^SCUx[A-Z0-9]+$"))

        for item_data in data.get("items", []):
            parent = QTreeWidgetItem([
                item_data['name'],
                item_data.get('mode', '范围判定') if item_data.get('standard_type', '数值') == '数值' else '字符串比对',
                item_data['min'],
                item_data['max']
            ])
            # 存储新字段元数据到 UserRole
            parent.setData(0, Qt.UserRole, item_data.get("standard_type", "数值"))
            parent.setData(1, Qt.UserRole, item_data.get("retry_count", "不复测"))
            parent.setData(2, Qt.UserRole, item_data.get("unit", "NULL"))
            
            if item_data['name'].strip().startswith("#"):
                parent.setForeground(0, QColor("#808080"))
            else:
                parent.setForeground(0, QColor("#00E5FF"))
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.step_tree.addTopLevelItem(parent)
            
            for sub_data in item_data.get("sub_steps", []):
                child = QTreeWidgetItem([
                    f"  └─ {sub_data['name']}",
                    sub_data['action'],
                    sub_data['params'],
                    "参与判定" if sub_data.get("is_judgment") else "--"
                ])
                # 恢复元数据
                child.setData(0, Qt.UserRole, sub_data.get("device"))
                child.setData(1, Qt.UserRole, sub_data.get("type"))
                child.setData(2, Qt.UserRole, sub_data.get("is_judgment"))
                child.setData(5, Qt.UserRole, sub_data.get("fail_strategy", "失败停止"))
                
                if sub_data['name'].strip().startswith("#"):
                    child.setForeground(0, QColor("#808080"))
                elif sub_data.get("is_judgment"):
                    child.setForeground(0, QColor("#FFD700")) # 亮金色
                else:
                    child.setForeground(0, QColor("#FFFFFF"))
                
                parent.addChild(child)
        self.step_tree.expandAll()

    def open_ai_recipe_dialog(self):
        """打开 AI 配方助手对话框，应用成功后重新加载当前配方"""
        if not self.current_recipe_name:
            QMessageBox.information(self, "提示", "请先在左侧选择一个配方")
            return
        from ui.dialogs.ai_recipe_dialog import AiRecipeDialog
        dialog = AiRecipeDialog(self.db_manager, self.current_recipe_name, self)
        if dialog.exec():
            data = self.db_manager.load_recipe_json(self.current_recipe_name)
            if data:
                self.load_recipe_to_tree(data)

    def open_ai_barcode_dialog(self):
        """打开 AI 样本条码推导与测试引擎弹窗"""
        from ui.dialogs.ai_barcode_dialog import AiBarcodeRuleDialog
        dialog = AiBarcodeRuleDialog(self, initial_regex=self.edit_sn_regex.text().strip())
        if dialog.exec():
            new_regex = dialog.get_generated_regex()
            if new_regex:
                self.edit_sn_regex.setText(new_regex)
        for item_data in data.get("items", []):
            parent = QTreeWidgetItem([
                item_data['name'],
                item_data.get('mode', '范围判定') if item_data.get('standard_type', '数值') == '数值' else '字符串比对',
                item_data['min'],
                item_data['max']
            ])
            # 存储新字段元数据到 UserRole
            parent.setData(0, Qt.UserRole, item_data.get("standard_type", "数值"))
            parent.setData(1, Qt.UserRole, item_data.get("retry_count", "不复测"))
            parent.setData(2, Qt.UserRole, item_data.get("unit", "NULL"))
            
            if item_data['name'].strip().startswith("#"):
                parent.setForeground(0, QColor("#808080"))
            else:
                parent.setForeground(0, QColor("#00E5FF"))
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.step_tree.addTopLevelItem(parent)
            
            for sub_data in item_data.get("sub_steps", []):
                child = QTreeWidgetItem([
                    f"  └─ {sub_data['name']}",
                    sub_data['action'],
                    sub_data['params'],
                    "参与判定" if sub_data.get("is_judgment") else "--"
                ])
                # 恢复元数据
                child.setData(0, Qt.UserRole, sub_data.get("device"))
                child.setData(1, Qt.UserRole, sub_data.get("type"))
                child.setData(2, Qt.UserRole, sub_data.get("is_judgment"))
                child.setData(5, Qt.UserRole, sub_data.get("fail_strategy", "失败停止"))
                
                if sub_data['name'].strip().startswith("#"):
                    child.setForeground(0, QColor("#808080"))
                elif sub_data.get("is_judgment"):
                    child.setForeground(0, QColor("#FFD700")) # 亮金色
                else:
                    child.setForeground(0, QColor("#FFFFFF"))
                
                parent.addChild(child)
        self.step_tree.expandAll()

    def on_recipe_context_menu(self, pos):
        item = self.recipe_tree.itemAt(pos)
        if not item: return
        
        self.recipe_tree.setCurrentItem(item)
        menu = QMenu()
        rename_act = QAction("重命名配方", self)
        rename_act.triggered.connect(self.rename_selected_recipe)
        menu.addAction(rename_act)
        del_act = QAction("删除该配方", self)
        del_act.triggered.connect(self.delete_selected_recipe)
        menu.addAction(del_act)
        menu.exec_(self.recipe_tree.viewport().mapToGlobal(pos))

    def on_context_menu(self, pos):
        item = self.step_tree.itemAt(pos)
        if not item: return
        
        selected = self.step_tree.selectedItems()
        sel_count = len(selected)
        
        menu = QMenu()
        
        if sel_count > 1:
            copy_act = QAction("批量复制 (Ctrl+C)", self)
            copy_act.triggered.connect(self.copy_node)
            
            paste_act = QAction("批量粘贴 (Ctrl+V)", self)
            paste_act.setEnabled(self.clipboard_data is not None)
            paste_act.triggered.connect(self.paste_node)
            
            dup_act = None # 批量克隆已不需要，删除
            
            del_act = QAction("批量删除 (Delete)", self)
            del_act.triggered.connect(self.delete_node)
            
            skip_action = QAction("批量屏蔽/取消 (Space)", self)
            skip_action.triggered.connect(self.batch_toggle_skip)
        else:
            copy_act = QAction("复制 (Ctrl+C)", self)
            copy_act.triggered.connect(self.copy_node)
            
            paste_act = QAction("粘贴 (Ctrl+V)", self)
            paste_act.setEnabled(self.clipboard_data is not None)
            paste_act.triggered.connect(self.paste_node)
            
            dup_act = QAction("克隆", self)
            dup_act.triggered.connect(self.duplicate_node)
            
            del_act = QAction("删除 (Delete)", self)
            del_act.triggered.connect(self.delete_node)
            
            is_skipped = item.text(0).strip().startswith("#") or "└─ #" in item.text(0)
            skip_action = QAction("取消屏蔽 (Space)" if is_skipped else "屏蔽该项 (添加#) (Space)", self)
            skip_action.triggered.connect(lambda: self.toggle_skip(item))
            
        bulk_act = QAction("批量修改参数...", self)
        bulk_act.triggered.connect(self.bulk_edit_nodes)
        
        menu.addAction(copy_act)
        menu.addAction(paste_act)
        menu.addSeparator()
        menu.addAction(skip_action)

        if sel_count <= 1:
            prefix_action = QAction("添加自定义前缀", self)
            prefix_action.triggered.connect(lambda: self.add_prefix(item))
            menu.addAction(prefix_action)

        menu.addSeparator()
        if dup_act:
            menu.addAction(dup_act)
        menu.addAction(bulk_act)
        menu.addSeparator()
        menu.addAction(del_act)
        menu.exec_(self.step_tree.viewport().mapToGlobal(pos))
        
    def toggle_skip(self, item):
        """切换屏蔽状态：在名称前添加或移除 #"""
        text = item.text(0)
        if "#" in text:
            # 移除所有 #
            item.setText(0, text.replace("#", ""))
            item.setForeground(0, QColor("#FFFFFF") if item.parent() else QColor("#00E5FF"))
        else:
            # 添加 #
            if "└─ " in text:
                parts = text.split("└─ ")
                item.setText(0, f"{parts[0]}└─ #{parts[1]}")
            else:
                item.setText(0, "#" + text)
            item.setForeground(0, QColor("#808080")) # 屏蔽后显示为灰色

    def batch_toggle_skip(self):
        """批量屏蔽/取消屏蔽选中的测试项或工步"""
        selected = self.step_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提醒", "请先选择一个或多个测试项/子工步。")
            return
            
        # 判断是否至少有一个未屏蔽的，如果有，则全部屏蔽；否则全部取消屏蔽
        has_unskipped = False
        for item in selected:
            text = item.text(0)
            is_skipped = text.strip().startswith("#") or "└─ #" in text
            if not is_skipped:
                has_unskipped = True
                break
                
        for item in selected:
            text = item.text(0)
            is_skipped = text.strip().startswith("#") or "└─ #" in text
            if has_unskipped:
                # 全部进行屏蔽
                if not is_skipped:
                    if "└─ " in text:
                        parts = text.split("└─ ")
                        item.setText(0, f"{parts[0]}└─ #{parts[1]}")
                    else:
                        item.setText(0, "#" + text)
                    item.setForeground(0, QColor("#808080"))
            else:
                # 全部取消屏蔽
                if is_skipped:
                    item.setText(0, text.replace("#", ""))
                    item.setForeground(0, QColor("#FFFFFF") if item.parent() else QColor("#00E5FF"))

    def add_prefix(self, item):
        """为工步名称添加自定义前缀"""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "添加前缀", "请输入前缀内容:")
        if ok and text:
            old_text = item.text(0)
            if "└─ " in old_text:
                parts = old_text.split("└─ ")
                item.setText(0, f"{parts[0]}└─ {text}_{parts[1]}")
            else:
                item.setText(0, f"{text}_{old_text}")

    def _get_node_data(self, item):
        """序列化单个节点及其子节点"""
        is_parent = item.parent() is None
        if is_parent:
            data = {
                "type": "item",
                "name": item.text(0),
                "mode": item.text(1),
                "min": item.text(2),
                "max": item.text(3),
                "standard_type": item.data(0, Qt.UserRole) or "数值",
                "retry_count": item.data(1, Qt.UserRole) or "不复测",
                "unit": item.data(2, Qt.UserRole) or "NULL",
                "sub_steps": []
            }
            for i in range(item.childCount()):
                data["sub_steps"].append(self._get_node_data(item.child(i)))
            return data
        else:
            return {
                "type": "step",
                "name": item.text(0).replace("  └─ ", ""),
                "action": item.text(1),
                "params": item.text(2),
                "device": item.data(0, Qt.UserRole),
                "stype": item.data(1, Qt.UserRole),
                "is_judgment": item.data(2, Qt.UserRole),
                "fail_strategy": item.data(5, Qt.UserRole) or "失败停止"
            }

    def copy_node(self):
        self.batch_copy_nodes()

    def paste_node(self):
        self.batch_paste_nodes()

    def duplicate_node(self):
        self.copy_node()
        self.paste_node()

    def batch_copy_nodes(self):
        """批量复制选中的节点"""
        selected = self.step_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提醒", "请先选择要复制的节点。")
            return
            
        # 序列化选中的所有节点
        copied_nodes = []
        for item in selected:
            node_data = self._get_node_data(item)
            copied_nodes.append(node_data)
            
        self.clipboard_data = {
            "type": "batch",
            "nodes": copied_nodes
        }

    def batch_paste_nodes(self):
        """批量粘贴节点"""
        if not self.clipboard_data:
            QMessageBox.warning(self, "提醒", "剪贴板为空。")
            return
            
        current = self.step_tree.currentItem()
        
        # 将 clipboard_data 统一规范为列表
        if isinstance(self.clipboard_data, dict) and self.clipboard_data.get("type") == "batch":
            nodes_to_paste = self.clipboard_data.get("nodes", [])
        else:
            nodes_to_paste = [self.clipboard_data]
            
        if not nodes_to_paste: return
        
        for data in nodes_to_paste:
            if data["type"] == "item":
                # 粘贴为顶层测试项
                new_item = QTreeWidgetItem([
                    data['name'], data['mode'], data['min'], data['max']
                ])
                new_item.setData(0, Qt.UserRole, data.get('standard_type', '数值'))
                new_item.setData(1, Qt.UserRole, data.get('retry_count', '不复测'))
                new_item.setData(2, Qt.UserRole, data.get('unit', 'NULL'))
                
                # 保留屏蔽或正常的高保真色彩
                if data['name'].strip().startswith("#"):
                    new_item.setForeground(0, QColor("#808080"))
                else:
                    new_item.setForeground(0, QColor("#00E5FF"))
                    
                font = new_item.font(0)
                font.setBold(True)
                new_item.setFont(0, font)
                
                for sub in data.get("sub_steps", []):
                    child = QTreeWidgetItem([
                        f"  └─ {sub['name']}", sub['action'], sub['params'],
                        "参与判定" if sub.get("is_judgment") else "--"
                    ])
                    child.setData(0, Qt.UserRole, sub.get("device"))
                    child.setData(1, Qt.UserRole, sub.get("stype"))
                    child.setData(2, Qt.UserRole, sub.get("is_judgment"))
                    child.setData(5, Qt.UserRole, sub.get("fail_strategy", "失败停止"))
                    
                    if sub['name'].strip().startswith("#"):
                        child.setForeground(0, QColor("#808080"))
                    elif sub.get("is_judgment"):
                        child.setForeground(0, QColor("#FFD700"))
                    else:
                        child.setForeground(0, QColor("#FFFFFF"))
                        
                    new_item.addChild(child)
                
                if current:
                    idx = self.step_tree.indexOfTopLevelItem(current if not current.parent() else current.parent())
                    self.step_tree.insertTopLevelItem(idx + 1, new_item)
                    current = new_item
                else:
                    self.step_tree.addTopLevelItem(new_item)
                    
            elif data["type"] == "step":
                # 粘贴为子工步
                parent = current if (current and not current.parent()) else (current.parent() if current else None)
                if not parent: continue
                
                child = QTreeWidgetItem([
                    f"  └─ {data['name']}", data['action'], data['params'],
                    "参与判定" if data.get("is_judgment") else "--"
                ])
                child.setData(0, Qt.UserRole, data.get("device"))
                child.setData(1, Qt.UserRole, data.get("stype"))
                child.setData(2, Qt.UserRole, data.get("is_judgment"))
                child.setData(5, Qt.UserRole, data.get("fail_strategy", "失败停止"))
                
                if data['name'].strip().startswith("#"):
                    child.setForeground(0, QColor("#808080"))
                elif data.get("is_judgment"):
                    child.setForeground(0, QColor("#FFD700"))
                else:
                    child.setForeground(0, QColor("#FFFFFF"))
                
                if current and current.parent():
                    idx = parent.indexOfChild(current)
                    parent.insertChild(idx + 1, child)
                    current = child
                else:
                    parent.addChild(child)
                parent.setExpanded(True)

    def bulk_edit_nodes(self):
        # 1. 查找选中的顶级测试项
        selected_nodes = []
        for item in self.step_tree.selectedItems():
            # 如果选中的是顶级节点 (没有 parent)
            if not item.parent():
                selected_nodes.append(item)
            # 如果选中了子节点，我们也可以找到它的顶级父节点
            elif item.parent() and item.parent() not in selected_nodes:
                selected_nodes.append(item.parent())
                
        # 如果选取的列表为空，则默认对所有顶级测试项进行批量修改
        if not selected_nodes:
            for i in range(self.step_tree.topLevelItemCount()):
                selected_nodes.append(self.step_tree.topLevelItem(i))
                
        if not selected_nodes:
            QMessageBox.warning(self, "提醒", "当前配方中没有可修改的测试项。")
            return
            
        from ui.dialogs.bulk_edit_dialog import BulkEditDialog
        dialog = BulkEditDialog(self)
        if dialog.exec():
            cfg = dialog.get_config()
            count = 0
            
            for idx, item in enumerate(selected_nodes):
                # 统一修改测试项名称
                if cfg["change_name"]:
                    prefix = cfg["name_prefix"]
                    if cfg["name_inc"]:
                        new_name = f"{prefix}_{idx+1:02d}"
                # 统一修改测试项名称
                if cfg["change_name"]:
                    prefix = cfg["name_prefix"]
                    if cfg["name_inc"]:
                        new_name = f"{prefix}_{idx+1:02d}"
                    else:
                        new_name = prefix
                    item.setText(0, new_name)
                    
                # 统一修改判定范围/期望值
                if cfg["change_range"]:
                    item.setText(1, "范围判定" if cfg['standard_type'] == "数值" else "字符串比对")
                    item.setText(2, cfg['min'] if cfg['min'] else "--")
                    item.setText(3, cfg['max'] if cfg['max'] else "--")
                    item.setData(0, Qt.UserRole, cfg['standard_type'])
                    
                # 统一修改单位
                if cfg["change_unit"]:
                    item.setData(2, Qt.UserRole, cfg['unit'] if cfg['unit'] else "NULL")
                    
                # 统一修改 NG 复测选择
                if cfg["change_retry"]:
                    item.setData(1, Qt.UserRole, cfg['retry_count'])

    def dry_run(self):
        """仿真运行：使用 Mock 设备管理器运行当前编辑的配方"""
        # 1. 序列化当前 UI 中的配方数据
        items = []
        for i in range(self.step_tree.topLevelItemCount()):
            node = self.step_tree.topLevelItem(i)
            item_data = {
                "name": node.text(0),
                "min": node.text(2),
                "max": node.text(3),
                "sub_steps": []
            }
            for j in range(node.childCount()):
                sub = node.child(j)
                item_data["sub_steps"].append({
                    "name": sub.text(0).replace("  └─ ", ""),
                    "type": sub.data(1, Qt.UserRole),
                    "device": sub.data(0, Qt.UserRole),
                    "action": sub.text(1),
                    "params": sub.text(2),
                    "is_judgment": sub.data(2, Qt.UserRole),
                    "fail_strategy": sub.data(5, Qt.UserRole) or "失败停止"
                })
            items.append(item_data)
            
        if not items:
            QMessageBox.warning(self, "提醒", "配方为空，无法运行仿真。")
            return

        # 2. 初始化 Mock 硬件和临时引擎
        from devices.mock_manager import MockDeviceManager
        from core.engine import TestEngine
        from ui.dialogs.monitor_dialog import MonitorDialog
        
        mock_mgr = MockDeviceManager()
        # 注意：这里我们不传真实 db_manager，防止仿真数据污染数据库
        temp_engine = TestEngine(device_manager=mock_mgr, db_manager=None)
        
        # 3. 弹出监控窗口（使用通道 1 进行演示）
        dialog = MonitorDialog(channel_id=1, engine=temp_engine, parent=self)
        dialog.setWindowTitle("🧪 配方仿真运行 - 通道 1 (虚拟硬件)")
        
        # 启动测试
        temp_engine.start_channel_test(1, items)
        dialog.exec()
        
        # 结束后清理
        temp_engine.stop_all()

    def validate_recipe(self) -> List[str]:
        """逻辑校验：返回错误列表"""
        errors = []
        if self.step_tree.topLevelItemCount() == 0:
            errors.append("配方中没有任何测试项。")
            
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            if item.childCount() == 0:
                errors.append(f"测试项【{item.text(0)}】下没有任何子工步。")
            
            for j in range(item.childCount()):
                sub = item.child(j)
                if "设置" in sub.text(1) and not sub.text(2):
                    errors.append(f"工步【{sub.text(0)}】参数为空。")
                    
        return errors

    def save_recipe(self):
        """将当前树状图序列化并保存为 JSON"""
        # 执行逻辑校验
        errors = self.validate_recipe()
        if errors:
            msg = "配方校验发现以下问题：\n\n" + "\n".join(errors[:5])
            if len(errors) > 5: msg += f"\n...等共 {len(errors)} 个问题"
            QMessageBox.warning(self, "校验未通过", msg)
            return

        recipe_item = self.recipe_tree.currentItem()
        if not recipe_item:
            QMessageBox.warning(self, "提醒", "请先在左侧选择或新建一个配方。")
            return
            
        recipe_name = recipe_item.text(0)
        data = {
            "name": recipe_name,
            "recipe_number": self.recipe_number.value(),
            "plc_recipe_register": 0,
            "sn_regex": self.edit_sn_regex.text().strip(),
            "can_config": {
                "channel": self.recipe_pcan_channel.currentText(),
                "mode": self.recipe_can_mode.currentText(),
                "nominal_bitrate": int(self.recipe_nominal_bitrate.currentText()),
                "data_bitrate": int(self.recipe_data_bitrate.currentText()),
            },
            "items": []
        }
        
        # 遍历树状图
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            
            item_data = {
                "name": item_node.text(0),
                "mode": item_node.text(1),
                "min": item_node.text(2),
                "max": item_node.text(3),
                "standard_type": item_node.data(0, Qt.UserRole) or "数值",
                "retry_count": item_node.data(1, Qt.UserRole) or "不复测",
                "unit": item_node.data(2, Qt.UserRole) or "NULL",
                "sub_steps": []
            }
            
            # 遍历子工步
            for j in range(item_node.childCount()):
                sub_node = item_node.child(j)
                sub_data = {
                    "name": sub_node.text(0).replace("  └─ ", ""),
                    "action": sub_node.text(1),
                    "params": sub_node.text(2),
                    "device": sub_node.data(0, Qt.UserRole), 
                    "type": sub_node.data(1, Qt.UserRole),
                    "is_judgment": sub_node.data(2, Qt.UserRole),
                    "fail_strategy": sub_node.data(5, Qt.UserRole) or "失败停止"
                }
                item_data["sub_steps"].append(sub_data)
                
            data["items"].append(item_data)
            
        if self.db_manager.save_recipe_json(recipe_name, data):
            QMessageBox.information(self, "成功", f"配方【{recipe_name}】已成功保存。")
            self.refresh_recipe_list()
            # 重新选中刚才保存的配方
            for i in range(self.recipe_tree.topLevelItemCount()):
                item = self.recipe_tree.topLevelItem(i)
                if item.text(0) == recipe_name:
                    self.recipe_tree.setCurrentItem(item)
                    break
        else:
            QMessageBox.critical(self, "错误", "配方保存失败，请检查日志。")

    def save_recipe_as(self):
        """Save the current recipe tree as a new recipe file."""
        errors = self.validate_recipe()
        if errors:
            msg = "配方校验发现以下问题：\n\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n...等共 {len(errors)} 个问题"
            QMessageBox.warning(self, "校验未通过", msg)
            return

        recipe_item = self.recipe_tree.currentItem()
        if not recipe_item:
            QMessageBox.warning(self, "提醒", "请先在左侧选择一个配方，再执行另存为。")
            return

        from PySide6.QtWidgets import QInputDialog
        current_name = recipe_item.text(0).strip()
        default_name = f"{current_name}_副本" if current_name else "新配方"
        recipe_name, ok = QInputDialog.getText(self, "配方另存为", "请输入新配方名称:", text=default_name)
        recipe_name = recipe_name.strip() if ok else ""
        if not recipe_name:
            return
        if recipe_name in self.db_manager.list_recipes():
            QMessageBox.warning(self, "名称重复", f"配方【{recipe_name}】已存在，请换一个名称。")
            return

        data = self._collect_recipe_data(recipe_name)
        if self.db_manager.save_recipe_json(recipe_name, data):
            self.current_recipe_name = recipe_name
            QMessageBox.information(self, "成功", f"配方【{recipe_name}】已另存。")
            self.refresh_recipe_list()
            for i in range(self.recipe_tree.topLevelItemCount()):
                item = self.recipe_tree.topLevelItem(i)
                if item.text(0) == recipe_name:
                    self.recipe_tree.setCurrentItem(item)
                    self.on_recipe_selected(item, 0)
                    break
        else:
            QMessageBox.critical(self, "错误", "配方另存失败，请检查文件权限。")

    def _collect_recipe_data(self, recipe_name):
        data = {
            "name": recipe_name,
            "recipe_number": self.recipe_number.value(),
            "plc_recipe_register": 0,
            "can_config": {
                "channel": self.recipe_pcan_channel.currentText(),
                "mode": self.recipe_can_mode.currentText(),
                "nominal_bitrate": int(self.recipe_nominal_bitrate.currentText()),
                "data_bitrate": int(self.recipe_data_bitrate.currentText()),
            },
            "items": []
        }
        for i in range(self.step_tree.topLevelItemCount()):
            item_node = self.step_tree.topLevelItem(i)
            item_data = {
                "name": item_node.text(0), "mode": item_node.text(1),
                "min": item_node.text(2), "max": item_node.text(3),
                "standard_type": item_node.data(0, Qt.UserRole) or "数值",
                "retry_count": item_node.data(1, Qt.UserRole) or "不复测",
                "unit": item_node.data(2, Qt.UserRole) or "NULL",
                "sub_steps": []
            }
            for j in range(item_node.childCount()):
                sub_node = item_node.child(j)
                sub_data = {
                    "name": sub_node.text(0).replace("  └─ ", ""),
                    "action": sub_node.text(1), "params": sub_node.text(2),
                    "device": sub_node.data(0, Qt.UserRole),
                    "type": sub_node.data(1, Qt.UserRole),
                    "is_judgment": sub_node.data(2, Qt.UserRole),
                    "fail_strategy": sub_node.data(5, Qt.UserRole) or "失败停止"
                }
                item_data["sub_steps"].append(sub_data)
            data["items"].append(item_data)
        return data

    def add_test_item(self):
        if not self.current_recipe_name:
            QMessageBox.warning(self, "提醒", "请先在左侧选择或新建一个配方。")
            return
            
        from ui.dialogs.test_item_dialog import TestItemDialog
        dialog = TestItemDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            mode_text = "范围判定" if data['standard_type'] == "数值" else "字符串比对"
            item = QTreeWidgetItem([
                data['name'], 
                mode_text, 
                str(data['min']),
                str(data['max'])
            ])
            # 存储新字段元数据
            item.setData(0, Qt.UserRole, data['standard_type'])
            item.setData(1, Qt.UserRole, data['retry_count'])
            item.setData(2, Qt.UserRole, data.get('unit', 'NULL'))
            
            item.setForeground(0, QColor("#00E5FF"))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            
            current = self.step_tree.currentItem()
            if current:
                top_level_item = current if not current.parent() else current.parent()
                idx = self.step_tree.indexOfTopLevelItem(top_level_item)
                self.step_tree.insertTopLevelItem(idx + 1, item)
            else:
                self.step_tree.addTopLevelItem(item)
                
            self.step_tree.setCurrentItem(item)

    def add_step(self):
        if not self.current_recipe_name:
            QMessageBox.warning(self, "提醒", "请先在左侧选择或新建一个配方。")
            return
            
        parent = self.step_tree.currentItem()
        if not parent:
            QMessageBox.warning(self, "提醒", "请先选择一个『测试项』作为父节点。")
            return
            
        if parent.parent():
            parent = parent.parent()

        from ui.dialogs.step_dialog import StepDialog
        dialog = StepDialog(self, device_manager=self.device_manager)
        if dialog.exec():
            data = dialog.get_data()
            item = QTreeWidgetItem([
                f"  └─ {data['name']}", 
                data['action'], 
                data['params'], 
                "参与判定" if data['is_judgment'] else "--"
            ])
            # 存储元数据
            item.setData(0, Qt.UserRole, data['device'])
            item.setData(1, Qt.UserRole, data['type'])
            item.setData(2, Qt.UserRole, data['is_judgment'])
            item.setData(5, Qt.UserRole, data['fail_strategy'])
            
            if data['is_judgment']:
                item.setForeground(0, QColor("#FFD700"))
            
            parent.addChild(item)
            parent.setExpanded(True)

    def edit_node(self):
        if not self.current_recipe_name:
            QMessageBox.warning(self, "提醒", "请先在左侧选择或新建一个配方。")
            return
            
        item = self.step_tree.currentItem()
        if not item:
            QMessageBox.warning(self, "提醒", "请先选中一个要编辑的测试项或工步。")
            return
            
        parent = item.parent()
        if not parent:
            # 这是一个『测试项』(Top Level)
            from ui.dialogs.test_item_dialog import TestItemDialog
            
            data = {
                'name': item.text(0),
                'min': item.text(2),
                'max': item.text(3),
                'standard_type': item.data(0, Qt.UserRole) or '数值',
                'retry_count': item.data(1, Qt.UserRole) or '不复测',
                'unit': item.data(2, Qt.UserRole) or 'NULL'
            }
            dialog = TestItemDialog(self, data=data)
            if dialog.exec():
                new_data = dialog.get_data()
                item.setText(0, new_data['name'])
                item.setText(1, "范围判定" if new_data['standard_type'] == "数值" else "字符串比对")
                item.setText(2, str(new_data['min']))
                item.setText(3, str(new_data['max']))
                
                # 保存新字段元数据
                item.setData(0, Qt.UserRole, new_data['standard_type'])
                item.setData(1, Qt.UserRole, new_data['retry_count'])
                item.setData(2, Qt.UserRole, new_data.get('unit', 'NULL'))
        else:
            # 这是一个『子工步』(Child)
            from ui.dialogs.step_dialog import StepDialog
            # 从 UserRole 还原数据
            data = {
                'name': item.text(0).replace("  └─ ", ""),
                'action': item.text(1),
                'params': item.text(2),
                'device': item.data(0, Qt.UserRole) or "",
                'type': item.data(1, Qt.UserRole) or "",
                'is_judgment': item.data(2, Qt.UserRole) or False,
                'fail_strategy': item.data(5, Qt.UserRole) or '失败停止'
            }
            dialog = StepDialog(self, step_data=data, device_manager=self.device_manager)
            if dialog.exec():
                new_data = dialog.get_data()
                item.setText(0, f"  └─ {new_data['name']}")
                item.setText(1, new_data['action'])
                item.setText(2, new_data['params'])
                item.setText(3, "参与判定" if new_data['is_judgment'] else "--")
                
                # 更新元数据
                item.setData(0, Qt.UserRole, new_data['device'])
                item.setData(1, Qt.UserRole, new_data['type'])
                item.setData(2, Qt.UserRole, new_data['is_judgment'])
                item.setData(5, Qt.UserRole, new_data['fail_strategy'])
                
                if new_data['is_judgment']:
                    item.setForeground(0, QColor("#FFD700"))
                else:
                    item.setForeground(0, QColor("#FFFFFF"))

    def delete_node(self):
        selected = self.step_tree.selectedItems()
        if not selected: return
        
        reply = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(selected)} 个节点吗？", 
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes: return
        
        children = [item for item in selected if item.parent() is not None]
        parents = [item for item in selected if item.parent() is None]
        
        for item in children:
            p = item.parent()
            if p:
                p.removeChild(item)
                
        for item in parents:
            index = self.step_tree.indexOfTopLevelItem(item)
            if index >= 0:
                self.step_tree.takeTopLevelItem(index)

    def add_new_recipe(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "新建测试配方", "请输入新配方名称:")
        if ok and text:
            self.step_tree.clear()
            self.recipe_pcan_channel.setCurrentText("PCAN_USBBUS1")
            self.recipe_can_mode.setCurrentText("CAN-FD + BRS")
            self.recipe_nominal_bitrate.setCurrentText("500000")
            self.recipe_data_bitrate.setCurrentText("2000000")
            self.recipe_number.setValue(0)
            item = QTreeWidgetItem([text])
            self.recipe_tree.addTopLevelItem(item)
            self.recipe_tree.setCurrentItem(item)
            self.current_recipe_name = text
            self.set_editor_enabled(True)
            # 顺便立即保存一个空的
            self.save_recipe()

    def rename_selected_recipe(self):
        from PySide6.QtWidgets import QInputDialog
        item = self.recipe_tree.currentItem()
        if not item:
            return
        old_name = item.text(0)
        name, ok = QInputDialog.getText(self, '重命名配方', '请输入新配方名称：', text=old_name)
        name = name.strip()
        if not ok or name == old_name:
            return
        if not self.db_manager.rename_recipe(old_name, name):
            QMessageBox.warning(self, '重命名失败', '名称为空、包含非法字符、名称已存在或文件无法修改。原配方保持不变。')
            return
        item.setText(0, name)
        if self.current_recipe_name == old_name:
            self.current_recipe_name = name
        # 不重新加载工步树，保留当前尚未保存的编辑。

    def delete_selected_recipe(self):
        item = self.recipe_tree.currentItem()
        if not item:
            QMessageBox.warning(self, "提醒", "请先在左侧选择要删除的配方。")
            return
            
        name = item.text(0)
        reply = QMessageBox.question(self, "确认删除", f"确定要彻底删除配方【{name}】吗？\n该操作不可撤销。", 
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            if self.db_manager.delete_recipe(name):
                QMessageBox.information(self, "成功", f"配方【{name}】已删除。")
                if self.current_recipe_name == name:
                    self.current_recipe_name = None
                    self.set_editor_enabled(False)
                self.refresh_recipe_list()
            else:
                QMessageBox.critical(self, "错误", "删除失败，请检查文件权限。")

    def get_all_recipes(self):
        recipes = []
        for i in range(self.recipe_tree.topLevelItemCount()):
            recipes.append(self.recipe_tree.topLevelItem(i).text(0))
        return recipes
