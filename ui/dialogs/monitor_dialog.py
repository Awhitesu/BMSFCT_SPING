from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTreeWidget, QTreeWidgetItem, QTextEdit, QPushButton
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QColor
from core.result_display import format_result

class MonitorDialog(QDialog):
    """
    单个通道的详细测试监控对话框 (支持子工步与判定详情)
    """
    def __init__(self, parent=None, channel_id=1, engine=None):
        super().__init__(parent)
        self.channel_id = channel_id
        self.engine = engine
        self.setWindowTitle(f"通道 CH-{channel_id:02d} 详细监控")
        self.resize(900, 700)
        self.setStyleSheet("""
            QDialog { background-color: #1A1A2E; color: #E0E0E0; }
            QLabel { font-size: 14px; }
            QTreeWidget { 
                background-color: #16213E; 
                border: 1px solid #0F3460; 
                color: #E0E0E0; 
                font-size: 13px; 
            }
            QTreeWidget::item { height: 25px; border-bottom: 1px solid #1F1F35; }
            QTreeWidget::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #4ECCA3;
                border-radius: 3px;
                background-color: #1A1A2E;
            }
            QTreeWidget::indicator:checked {
                background-color: #4ECCA3;
                border: 2px solid #4ECCA3;
            }
            QTreeWidget::indicator:unchecked:hover {
                border: 2px solid #00E5FF;
            }
            QProgressBar {
                border: 2px solid #0F3460;
                border-radius: 5px;
                text-align: center;
                background-color: #16213E;
                color: white;
            }
            QProgressBar::chunk { background-color: #4ECCA3; }
            QTextEdit {
                background-color: #16213E;
                border: 1px solid #0F3460;
                color: #00E5FF;
                font-family: 'Consolas', monospace;
            }
        """)
        
        self.step_items = {} # 用于快速索引树节点 (step_idx -> item)
        self.sub_step_items = {} # (step_idx, sub_idx) -> item
        self.originally_disabled_names = set() # 记录配方配置中原本就禁用的项
        self._pending_log_messages = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(200)
        self._log_flush_timer.timeout.connect(self._flush_log_messages)
        self._log_flush_timer.start()
        
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 1. 顶部：通道基本信息
        header = QHBoxLayout()
        self.lbl_title = QLabel(f"<b>通道: CH-{self.channel_id:02d}</b>")
        self.lbl_title.setStyleSheet("font-size: 18px; color: #4ECCA3;")
        header.addWidget(self.lbl_title)
        
        self.lbl_status = QLabel("状态: 等待中")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addStretch()
        header.addWidget(self.lbl_status)
        layout.addLayout(header)
        
        # 2. 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # 3. 中部：工步详情树
        layout.addWidget(QLabel("测试序列与实时判定详情:"))
        self.step_tree = QTreeWidget()
        self.step_tree.setHeaderLabels(["测试项 / 子动作", "下限", "上限", "测量值", "状态 / 判定"])
        self.step_tree.setColumnWidth(0, 300)
        self.step_tree.setColumnWidth(1, 100)
        self.step_tree.setColumnWidth(2, 100)
        self.step_tree.setColumnWidth(3, 120)
        layout.addWidget(self.step_tree)
        
        # 4. 底部：日志
        layout.addWidget(QLabel("实时执行日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(500)
        layout.addWidget(self.log_text)
        
        # 5. 按钮
        btn_layout = QHBoxLayout()
        
        self.btn_run = QPushButton("启动勾选项")
        self.btn_run.clicked.connect(self.run_selected_test)
        self.btn_run.setStyleSheet("background-color: #17A2B8; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        btn_layout.addWidget(self.btn_run)

        self.btn_unselect_all = QPushButton("取消全部勾选")
        self.btn_unselect_all.clicked.connect(self.unselect_all_steps)
        self.btn_unselect_all.setStyleSheet("background-color: #3E3E5C; color: white; padding: 10px; border-radius: 5px;")
        btn_layout.addWidget(self.btn_unselect_all)
        
        self.btn_start_all = QPushButton("开启全部测试")
        self.btn_start_all.clicked.connect(self.run_all_test)
        self.btn_start_all.setStyleSheet("background-color: #28A745; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        btn_layout.addWidget(self.btn_start_all)

        self.btn_stop = QPushButton("结束/停止测试")
        self.btn_stop.clicked.connect(self.stop_test)
        self.btn_stop.setStyleSheet("background-color: #E94560; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        btn_layout.addWidget(self.btn_stop)
        
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.clear_logs)
        self.btn_clear_log.setStyleSheet("background-color: #3E3E5C; color: #00E5FF; padding: 10px; border-radius: 5px; font-weight: bold;")
        btn_layout.addWidget(self.btn_clear_log)
        
        btn_layout.addStretch()
        
        btn_close = QPushButton("关闭窗口")
        btn_close.clicked.connect(self.close)
        btn_close.setStyleSheet("background-color: #533483; color: white; padding: 10px; border-radius: 5px;")
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def run_selected_test(self):
        """执行勾选的测试项 (保持列表完整，将未选中的标记为屏蔽)"""
        if not self.engine: return
        
        full_data = []
        has_selection = False
        # 遍历树中所有测试项
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            step_data = item.data(0, Qt.UserRole)
            if not step_data: continue
            
            # 克隆一份数据以防修改到缓存中的原始配方
            new_step_data = step_data.copy()
            
            if item.checkState(0) == Qt.Checked:
                has_selection = True
                new_step_data['skip_runtime'] = False
            else:
                new_step_data['skip_runtime'] = True
            
            full_data.append(new_step_data)
        
        if not has_selection:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提示", "请先勾选要执行的测试项！")
            return
            
        self.log_text.append(f"--- 启动单通道手动测试: CH-{self.channel_id} (保留全部项目列表) ---")
        # 获取同步组信息
        sync_group = None
        if self.parent() and hasattr(self.parent(), "get_sync_group_for_channel"):
            sync_group = self.parent().get_sync_group_for_channel(self.channel_id)
            
        # 下发全量数据，引擎会自动根据 skip_runtime 跳过项目
        self.engine.start_channel_test(self.channel_id, full_data, sync_group=sync_group)
        self._connect_signals() # 重新绑定信号以获取最新 worker

    def run_all_test(self):
        """执行全部测试项 (确保清除屏蔽标记)"""
        if not self.engine: return
        
        all_data = []
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            step_data = item.data(0, Qt.UserRole)
            if step_data:
                # 克隆数据并清除屏蔽标记
                new_step_data = step_data.copy()
                new_step_data['skip_runtime'] = False
                all_data.append(new_step_data)
        
        if not all_data:
            return
            
        self.log_text.append(f"--- 启动单通道完整测试: CH-{self.channel_id} ---")
        # 获取同步组信息
        sync_group = None
        if self.parent() and hasattr(self.parent(), "get_sync_group_for_channel"):
            sync_group = self.parent().get_sync_group_for_channel(self.channel_id)
            
        self.engine.start_channel_test(self.channel_id, all_data, sync_group=sync_group)
        self._connect_signals()

    def stop_test(self):
        """强制结束测试"""
        if not self.engine: return
        self.engine.stop_channel_test(self.channel_id)
        self.log_text.append(f"<font color='red'>--- 用户手动停止测试: CH-{self.channel_id} ---</font>")
        self.lbl_status.setText("状态: 已停止")
        self.lbl_status.setStyleSheet("color: #FF4C29; font-weight: bold;")
        self.progress_bar.setValue(0)

    def unselect_all_steps(self):
        """清空树中所有测试项的勾选状态"""
        for i in range(self.step_tree.topLevelItemCount()):
            self.step_tree.topLevelItem(i).setCheckState(0, Qt.Unchecked)

    def clear_logs(self):
        """清空日志框并同时清空 Worker 历史日志缓存，实现彻底清空"""
        self.log_text.clear()
        if hasattr(self, '_current_worker') and self._current_worker:
            try:
                self._current_worker.log_history.clear()
            except:
                pass
        self.log_text.append("<font color='#00E5FF'>--- 实时执行日志已被操作员清空 ---</font>")

    def _get_checked_step_names(self):
        """获取当前已勾选的工步名称列表"""
        checked_names = []
        for i in range(self.step_tree.topLevelItemCount()):
            item = self.step_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                checked_names.append(item.text(0))
        return checked_names

    def _connect_signals(self):
        if not self.engine: return
        worker = self.engine.workers.get(self.channel_id)
        
        # 如果已经连接过同一个 worker，则不再重复连接
        if hasattr(self, '_current_worker') and self._current_worker == worker:
            return
            
        if worker:
            # 如果之前连接过别的 worker，尝试断开（虽然旧 worker 可能已经失效）
            if hasattr(self, '_current_worker') and self._current_worker:
                try:
                    self._current_worker.step_started.disconnect(self.on_step_started)
                    self._current_worker.step_finished.disconnect(self.on_step_finished)
                    self._current_worker.sub_step_finished.disconnect(self.on_sub_step_finished)
                    self._current_worker.log_message.disconnect(self.on_log_message)
                    self._current_worker.progress_updated.disconnect(self.on_progress_updated)
                except:
                    pass

            self._current_worker = worker
            worker.step_started.connect(self.on_step_started)
            worker.step_finished.connect(self.on_step_finished)
            worker.sub_step_finished.connect(self.on_sub_step_finished)
            worker.log_message.connect(self.on_log_message)
            worker.progress_updated.connect(self.on_progress_updated)
            
            # 还原历史日志，防止重复追加
            self.log_text.clear()
            for msg in worker.log_history[-500:]:
                self.log_text.append(msg)
                
            # 还原进度条
            self.progress_bar.setValue(int(worker.current_progress))
            
            # 加载并还原测试工步树
            self.load_steps(worker.steps)
            
            # 还原各工步与子工步的判定及测试详情
            for step_idx in range(self.step_tree.topLevelItemCount()):
                item = self.step_tree.topLevelItem(step_idx)
                orig_idx = item.data(0, Qt.UserRole + 10)
                if orig_idx is None:
                    orig_idx = step_idx
                    
                step_name = item.text(0)
                
                # 检查工步状态
                if orig_idx == worker.current_step_index:
                    item.setText(4, "执行中...")
                    item.setForeground(4, QColor("#00E5FF"))
                elif (orig_idx, step_name) in worker.step_statuses:
                    is_pass = worker.step_statuses[(orig_idx, step_name)]
                    item.setText(4, "PASS" if is_pass else "NG")
                    item.setForeground(4, QColor("#4ECCA3") if is_pass else QColor("#FF4C29"))
                    
                    # 还原历史测量值到界面上！
                    measured_val = getattr(worker, "step_measured_values", {}).get((orig_idx, step_name))
                    if measured_val is not None and measured_val != "PASS":
                        item.setText(3, format_result(measured_val, item.data(0, Qt.UserRole)))
                        item.setForeground(3, QColor("#00E5FF"))
                    else:
                        item.setText(3, "--")
                        item.setForeground(3, QColor("#888888"))
                    
                # 检查子工步状态
                for sub_idx in range(item.childCount()):
                    sub_item = item.child(sub_idx)
                    status_tuple = worker.sub_step_statuses.get((orig_idx, sub_idx))
                    if status_tuple:
                        status, result = status_tuple
                        sub_item.setText(4, status)
                        sub_item.setForeground(4, QColor("#4ECCA3") if status == "PASS" else QColor("#FF4C29"))
                        if result is not None and result != "PASS":
                            sub_item.setText(3, format_result(result, item.data(0, Qt.UserRole)))
                            sub_item.setForeground(3, QColor("#00E5FF"))
                        else:
                            sub_item.setText(3, "--")
                            sub_item.setForeground(3, QColor("#888888"))
                    elif orig_idx == worker.current_step_index and sub_idx == worker.current_sub_step_index:
                        sub_item.setText(4, "执行中...")
                        sub_item.setForeground(4, QColor("#00E5FF"))
                        
            self.lbl_status.setText("状态: 运行中")
            self.lbl_status.setStyleSheet("color: #4ECCA3; font-weight: bold;")
        else:
            self._current_worker = None
            if self.parent() and hasattr(self.parent(), "get_recipe_for_channel"):
                cached_steps = self.parent().get_recipe_for_channel(self.channel_id)
                if cached_steps:
                    self.load_steps_from_data(cached_steps)
                    self.lbl_status.setText("状态: 已下发(待启动)")
                    self.lbl_status.setStyleSheet("color: #AAAAAA;")

    def load_steps(self, steps):
        """加载 TestStep 对象列表 (转换为 dict 存储以便重跑)"""
        checked_names = self._get_checked_step_names()
        self.step_tree.clear()
        self.step_items = {}
        self.sub_step_items = {}
        for i, step in enumerate(steps):
            if step.name.strip().startswith("#"):
                continue  # 彻底不加载配方配置中原本就禁用的项
                
            # 将对象还原为 dict 格式以便后续 run_selected_test 使用
            sub_data_list = []
            for sub in step.sub_steps:
                sub_info = sub.params.copy()
                sub_info['type'] = sub.type.value
                sub_info['fail_strategy'] = sub.fail_strategy.value
                sub_data_list.append(sub_info)
            
            step_data = {
                "name": step.name,
                "min": step.min_limit if step.min_limit else "--",
                "max": step.max_limit if step.max_limit else "--",
                "strategy": step.ng_strategy.value,
                "standard_type": getattr(step, 'standard_type', '数值'),
                "decimal_places": getattr(step, 'decimal_places', None),
                "sub_steps": sub_data_list,
                "skip_runtime": getattr(step, 'skip_runtime', False)
            }
            
            is_shielded_runtime = step.name.strip().startswith("#") or getattr(step, 'skip_runtime', False)
            
            parent = QTreeWidgetItem([
                step.name, 
                str(step.min_limit) if step.min_limit else "--", 
                str(step.max_limit) if step.max_limit else "--", 
                "--", 
                "等待执行" if not is_shielded_runtime else "跳过"
            ])
            
            if is_shielded_runtime:
                state = Qt.Unchecked
                parent.setForeground(0, QColor("#888888"))
                parent.setBackground(0, QColor("#161625"))
            else:
                state = Qt.Checked if step.name in checked_names else Qt.Unchecked
                parent.setBackground(0, QColor("#1F1F35"))
                
            parent.setCheckState(0, state)
            parent.setData(0, Qt.UserRole, step_data)
            parent.setData(0, Qt.UserRole + 10, i)  # 存入原始索引 i
            self.step_tree.addTopLevelItem(parent)
            self.step_items[i] = parent
            
            for j, sub in enumerate(step.sub_steps):
                is_judg = sub.params.get("is_judgment", False)
                prefix = "  └─ [判定] " if is_judg else "  └─ "
                child = QTreeWidgetItem([
                    f"{prefix}{sub.params.get('name', sub.type.value)}", 
                    "--", "--", "--", "等待" if not is_shielded_runtime else "跳过"
                ])
                if is_shielded_runtime:
                    child.setForeground(0, QColor("#888888"))
                elif is_judg:
                    child.setForeground(0, QColor("#FFD700"))
                parent.addChild(child)
                self.sub_step_items[(i, j)] = child
        self.step_tree.collapseAll()

    def load_steps_from_data(self, steps_data):
        """加载原始 JSON 数据列表"""
        checked_names = self._get_checked_step_names()
        self.step_tree.clear()
        self.step_items = {}
        self.sub_step_items = {}
        for i, step in enumerate(steps_data):
            name = step.get('name', '未命名')
            if name.strip().startswith("#"):
                continue  # 彻底不加载配方配置中原本就禁用的项
                
            is_shielded_runtime = name.strip().startswith("#") or step.get('skip_runtime', False)
            
            parent = QTreeWidgetItem([
                name, 
                step.get('min', '--'), 
                step.get('max', '--'), 
                "--", 
                "待命" if not is_shielded_runtime else "跳过"
            ])
            
            if is_shielded_runtime:
                state = Qt.Unchecked
                parent.setForeground(0, QColor("#888888"))
                parent.setBackground(0, QColor("#161625"))
            else:
                state = Qt.Checked if name in checked_names else Qt.Unchecked
                parent.setBackground(0, QColor("#1F1F35"))
            
            parent.setCheckState(0, state)
            parent.setData(0, Qt.UserRole, step) # 直接存原始 dict
            parent.setData(0, Qt.UserRole + 10, i)  # 存入原始索引 i
            self.step_tree.addTopLevelItem(parent)
            self.step_items[i] = parent
            
            for j, sub in enumerate(step.get('sub_steps', [])):
                is_judg = sub.get("is_judgment", False)
                prefix = "  └─ [判定] " if is_judg else "  └─ "
                child = QTreeWidgetItem([
                    f"{prefix}{sub.get('name', sub.get('action', '动作'))}", 
                    "--", "--", "--", "待命" if not is_shielded_runtime else "跳过"
                ])
                if is_shielded_runtime:
                    child.setForeground(0, QColor("#888888"))
                elif is_judg:
                    child.setForeground(0, QColor("#FFD700"))
                parent.addChild(child)
                self.sub_step_items[(i, j)] = child
        self.step_tree.collapseAll()

    @Slot(int, int, str)
    def on_step_started(self, ch_id, step_idx, step_name):
        if ch_id != self.channel_id: return
        self.lbl_status.setText("状态: 运行中")
        item = self.step_items.get(step_idx)
        if item is None:
            for i in range(self.step_tree.topLevelItemCount()):
                cand = self.step_tree.topLevelItem(i)
                if cand.text(0).lstrip("#").strip() == step_name.lstrip("#").strip():
                    item = cand
                    break
        if item:
            item.setText(4, "执行中...")
            item.setForeground(4, QColor("#00E5FF"))
            self.step_tree.scrollToItem(item)

    @Slot(int, int, str, bool, object)
    def on_step_finished(self, ch_id, step_idx, step_name, is_pass, measured_val):
        if ch_id != self.channel_id: return
        item = self.step_items.get(step_idx)
        if item is None:
            for i in range(self.step_tree.topLevelItemCount()):
                cand = self.step_tree.topLevelItem(i)
                if cand.text(0).lstrip("#").strip() == step_name.lstrip("#").strip():
                    item = cand
                    break
        if item:
            if measured_val == "跳过":
                item.setText(4, "跳过")
                item.setForeground(4, QColor("#888888"))
                item.setText(3, "--")
                item.setForeground(3, QColor("#888888"))
            else:
                item.setText(4, "PASS" if is_pass else "NG")
                item.setForeground(4, QColor("#4ECCA3") if is_pass else QColor("#FF4C29"))
                if measured_val is not None and measured_val != "PASS":
                    item.setText(3, format_result(measured_val, item.data(0, Qt.UserRole)))
                    item.setForeground(3, QColor("#00E5FF"))
                else:
                    item.setText(3, "--")
                    item.setForeground(3, QColor("#888888"))

    @Slot(int, int, int, str, object)
    def on_sub_step_finished(self, ch_id, step_idx, sub_idx, status, result):
        if ch_id != self.channel_id: return
        item = self.sub_step_items.get((step_idx, sub_idx))
        if item:
            item.setText(4, status)
            if status == "跳过":
                item.setForeground(4, QColor("#888888"))
                item.setText(3, "--")
                item.setForeground(3, QColor("#888888"))
            else:
                if result is not None and result != "PASS":
                    item.setText(3, format_result(result, item.parent().data(0, Qt.UserRole)))
                    item.setForeground(3, QColor("#00E5FF"))
                else:
                    item.setText(3, "--")
                    item.setForeground(3, QColor("#888888"))
                item.setForeground(4, QColor("#4ECCA3") if status == "PASS" else QColor("#FF4C29"))

    @Slot(int, str)
    def on_log_message(self, ch_id, message):
        if ch_id != self.channel_id: return
        self._pending_log_messages.append(message)
        if len(self._pending_log_messages) > 200:
            self._pending_log_messages = self._pending_log_messages[-200:]

    def _flush_log_messages(self):
        if not self._pending_log_messages:
            return
        messages = self._pending_log_messages
        self._pending_log_messages = []
        self.log_text.append("\n".join(messages))

    @Slot(int, float, dict)
    def on_progress_updated(self, ch_id, progress, data):
        if ch_id != self.channel_id: return
        self.progress_bar.setValue(int(progress))
