"""ZCANPRO-style service browser and sequence sending workspace."""
import base64
import json
import threading
import re
from pathlib import Path
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QSplitter,QTreeWidget,QTreeWidgetItem,
    QLabel,QPushButton,QLineEdit,QComboBox,QCheckBox,QSpinBox,QPlainTextEdit,QFileDialog,
    QMessageBox,QInputDialog,QDialog,QTabWidget,QAbstractItemView,QDoubleSpinBox,QTableWidget,QGroupBox,QTableWidgetItem)
from ui.device_task import DeviceTask
from core.uds_sequence import encode_settings,settings_from,validate_sequence
from core.uds_result_config import encode as encode_rows,decode as decode_rows


CASES = {
 '10': [('默认会话','10 01'),('编程会话','10 02'),('扩展会话','10 03')],
 '11': [('硬复位','11 01'),('软复位','11 03')],
 '14': [('清除全部DTC','14 FF FF FF')],
 '19': [('按状态读取DTC','19 02 FF'),('读取DTC数量','19 01 FF')],
 '22': [('读取VIN','22 F1 90'),('读取软件版本（示例DID）','22 F1 89')],
 '27': [('请求Seed（等级1）','27 01'),('DLL自动Seed/Key解锁','@SECURITY')],
 '28': [('启用收发','28 00 03'),('禁止收发','28 03 03')],
 '3E': [('会话保持','3E 00')],
 '85': [('启用DTC设置','85 01'),('禁用DTC设置','85 02')],
}


class UDSSequenceWorkbench(QObject):
    log_received = Signal(str)
    response_received = Signal(str)

    def __init__(self, dialog):
        super().__init__(dialog)
        self.page=dialog
        self.task=DeviceTask(self)
        self.cancel=threading.Event()
        self.log_received.connect(self._log)
        self.response_received.connect(self._response)
        self.count=self.passed=self.failed=0
        self._settings_popup=None
        self._live_log=None
        self.last_response=''
        self._run_immediate=True
        self._run_items=[]
        self._opening_parser=False
        self.dtc_dictionary={}
        self.custom_cases=[]

    def install(self):
        p=self.page
        p.page_uds.setStyleSheet(p.page_uds.styleSheet()+'''
            QWidget { font-size: 12px; }
            QHeaderView::section, QTabBar::tab { font-size: 12px; }
            QPushButton { color: white; background: #243246; border: 1px solid #475569; border-radius: 4px; padding: 5px 9px; }
            QPushButton:hover { background: #334155; }
            QPushButton:disabled { color: #94A3B8; background: #1E293B; }
            QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox { padding: 4px; }
            QTreeWidget,QTableWidget,QPlainTextEdit { color: #F1F5F9; background: #0F172A; border: 1px solid #334155; }
            QTreeWidget::item:selected { background: #1D4ED8; color: white; }
            QHeaderView::section { background: #243246; color: white; border: 1px solid #475569; padding: 5px; }
        ''')
        tabs=p.uds_tabs
        compact=QWidget();compact_row=QHBoxLayout(compact);compact_row.setContentsMargins(0,0,0,0)
        summary=QLabel();summary.setWordWrap(True);compact_row.addWidget(summary,1)
        change=QPushButton('工步设备／CAN通道');compact_row.addWidget(change)
        change.clicked.connect(lambda:p.device_selection_frame.setVisible(p.device_selection_frame.isHidden()))
        p.layout().insertWidget(0,compact)
        def update_summary(*_):
            field_write=p.param_stack.currentWidget() is getattr(p,'uds_field_write_form',None)
            title='UDS 2E 三字段写入' if field_write else 'UDS交互列表'
            summary.setText(title+'　|　'+p.can_binding_label.text()+'　|　'+p.can_physical_channel.currentText())
        def update_header(*_):
            uds=p.param_stack.currentIndex()==13 or p.param_stack.currentWidget() is getattr(p,'uds_field_write_form',None)
            compact.setVisible(uds);p.device_selection_frame.setVisible(not uds)
            update_summary()
        p.param_stack.currentChanged.connect(update_header)
        p.can_physical_channel.currentIndexChanged.connect(update_summary)
        update_header()
        tabs.setTabText(0,'通讯参数')
        tabs.setTabText(1,'安全 DLL')
        tabs.setTabText(2,'数据解析')
        tabs.setTabText(3,'服务与发送序列')
        # Reuse the existing request form, edit buttons and result-aware list.
        edit_page=tabs.widget(2).widget().layout()
        request_form=edit_page.takeAt(1).layout()
        response_form=edit_page.takeAt(1).layout()
        request_form.setRowVisible(p.uds_service,False)
        # Keep result expectations beside their parser, leaving only PDU fields on the send page.
        for widget in (p.uds_result_expected,p.uds_sync_expected):
            taken=request_form.takeRow(widget)
            if taken.labelItem:
                row=QHBoxLayout();row.addWidget(taken.labelItem.widget());row.addWidget(taken.fieldItem.widget(),1)
                edit_page.insertLayout(1,row)
            else:edit_page.insertWidget(1,taken.fieldItem.widget())
        self.response_sample=QLineEdit()
        self.response_sample.setPlaceholderText('在线抓取自动填入，也可手输完整UDS响应，例如 62 F1 90 41 42 43')
        self.response_sample.setToolTip('这是用于选区的响应样本，不是报文前缀校验。使用重组后的UDS PDU，不包含CAN长度或ISO-TP分帧头。')
        sample_row=QHBoxLayout();sample_row.addWidget(QLabel('期望响应／实际响应样本（HEX）：'));sample_row.addWidget(self.response_sample,1)
        self.response_length=QLabel('完整UDS长度：0 字节')
        self.response_length.setToolTip('ISO-TP重组后的PDU长度，包含肯定响应服务号、DID及DATA；不包含分帧头、流控和填充。长度单独展示，不改变已有解析偏移。')
        sample_row.addWidget(self.response_length)
        edit_page.insertLayout(1,sample_row)
        hint=QLabel('先发送列表或在线抓取 → 选择最终输出行 → 拖选 DATA 字节 → 选择 ASCII／数值／公式 → 保存。偏移从UDS响应服务字节开始计数，不含ISO-TP头。')
        hint.setWordWrap(True);edit_page.insertWidget(2,hint)
        self.response_sample.textChanged.connect(self._sample_changed)
        edit_page.removeWidget(p.uds_edit_actions)
        send_page=tabs.widget(3).widget().layout()
        while send_page.count():
            item=send_page.takeAt(0)
            if item.widget():item.widget().setParent(None)
            elif item.layout():
                if item.layout().indexOf(p.uds_add_btn)>=0:
                    item.layout().removeWidget(p.uds_add_btn)
                    self.old_header=item.layout()
                    while self.old_header.count():
                        old=self.old_header.takeAt(0)
                        if old.widget():old.widget().hide();old.widget().deleteLater()
                else:self.controls=item.layout()
        top=QHBoxLayout()
        guide=QLabel('UDS服务 → 编辑请求 → 添加到列表 → 列表发送；解析规则在“数据解析”页配置。')
        guide.setWordWrap(True);top.addWidget(guide,1)
        settings=QPushButton('更多设置／算法 DLL');settings.clicked.connect(self.open_settings)
        top.addWidget(settings);send_page.addLayout(top)
        splitter=QSplitter(Qt.Horizontal)
        self.services=QTreeWidget();self.services.setHeaderLabel('功能服务与案例')
        self.services.setMinimumWidth(240)
        raw=QTreeWidgetItem(self.services,['原始报文（自定义）']);raw.setData(0,Qt.UserRole,'')
        for i in range(p.uds_service.count()-1):
            title=p.uds_service.itemText(i);sid=title.split()[0]
            parent=QTreeWidgetItem(self.services,[title]);parent.setData(0,Qt.UserRole,sid)
            for name,request in CASES.get(sid,[]):
                child=QTreeWidgetItem(parent,[name]);child.setData(0,Qt.UserRole,request)
        sequences=QTreeWidgetItem(self.services,['功能案例（组合序列，不自动发送）'])
        for title,requests in [('扩展会话 → 读取VIN',['10 03','22 F1 90']),
                               ('扩展会话 → DLL解锁 → 读取VIN',['10 03','@SECURITY','22 F1 90'])]:
            item=QTreeWidgetItem(sequences,[title]);item.setData(0,Qt.UserRole,dict(sequence=requests))
        self.services.itemClicked.connect(self.select_case)
        left_tabs=QTabWidget();left_tabs.addTab(self.services,'服务')
        dtc_page=QWidget();dtc_layout=QVBoxLayout(dtc_page)
        dtc_tip=QLabel('读取 19 02 后显示DTC和状态字节；其他子功能保留原始响应。');dtc_tip.setWordWrap(True);dtc_layout.addWidget(dtc_tip)
        self.dtc_table=QTableWidget(0,3);self.dtc_table.setHorizontalHeaderLabels(['DTC（HEX）','状态','说明'])
        self.dtc_table.setEditTriggers(QAbstractItemView.NoEditTriggers);dtc_layout.addWidget(self.dtc_table)
        dtc_dict=QPushButton('故障码字典编辑器');dtc_dict.clicked.connect(self.edit_dtc_dictionary);dtc_layout.addWidget(dtc_dict)
        left_tabs.addTab(dtc_page,'故障码');splitter.addWidget(left_tabs)
        right=QWidget();layout=QVBoxLayout(right)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(5)
        layout.addLayout(request_form)
        options=QHBoxLayout()
        self.address=QComboBox();self.address.addItem('跟随通讯参数','default');self.address.addItem('物理地址','physical');self.address.addItem('功能地址','functional')
        self.suppress=QCheckBox('抑制肯定响应')
        self.suppress.setToolTip('仅支持带子功能的服务，自动置子功能bit7；不接收响应，不能用于解析或最终输出。')
        self.check=QCheckBox('校验响应内容');self.check.setChecked(True)
        def suppression_changed(enabled):
            p.uds_auto_response.setEnabled(not enabled)
            if enabled:
                p.uds_expected.clear();p.uds_result_expected.clear();p.uds_extract_mode.setCurrentIndex(0)
                self.check.setChecked(False)
        self.suppress.toggled.connect(suppression_changed)
        options.addWidget(QLabel('请求地址：'));options.addWidget(self.address)
        options.addWidget(self.suppress);options.addWidget(self.check);options.addStretch()
        layout.addLayout(options);layout.addLayout(response_form)
        layout.addWidget(p.uds_edit_actions)
        row=QHBoxLayout()
        parse=QPushButton('配置数据解析');parse.clicked.connect(lambda:tabs.setCurrentIndex(3))
        row.addWidget(parse)
        self.now=QPushButton('立即发送');self.now.clicked.connect(lambda:self.run(True))
        row.addWidget(self.now);row.addWidget(p.uds_add_btn)
        # Keep list editing operations together; sending stays on the right.
        delay=QPushButton('添加延时');delay.clicked.connect(self.add_delay)
        self.controls.insertWidget(0,delay)
        while self.controls.count():
            item=self.controls.takeAt(0)
            if item.widget():row.addWidget(item.widget())
        self.run_button=QPushButton('列表发送');self.run_button.clicked.connect(lambda:self.run(False))
        self.stop_button=QPushButton('停止');self.stop_button.clicked.connect(self.cancel.set);self.stop_button.setEnabled(False)
        row.addWidget(self.run_button);row.addWidget(self.stop_button)
        row.setSpacing(4)
        # This toolbar belongs only to the UDS sending page.
        for i in range(row.count()):
            widget=row.itemAt(i).widget()
            if isinstance(widget,QPushButton):
                widget.setStyleSheet(widget.styleSheet()+'QPushButton { font-size:12px; padding:2px 5px; min-width:0px; min-height:0px; }')
                widget.setFixedHeight(28)
                widget.setFixedWidth(widget.fontMetrics().horizontalAdvance(widget.text())+16)
        p.uds_cycles.setFixedWidth(68);p.uds_cycles.setFixedHeight(28)
        p.uds_interval.setFixedWidth(94);p.uds_interval.setFixedHeight(28)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(p.uds_draft_summary)
        # Address/suppression columns stay with their row through reorder and import.
        p.uds_table.setColumnCount(10)
        from ui.widgets.uds_output_delegate import UDSOutputDelegate
        p.uds_table.setItemDelegateForColumn(6,UDSOutputDelegate(p.uds_table))
        p.uds_table.setMinimumHeight(150)
        p.uds_table.setHorizontalHeaderLabels(['名称','请求PDU','响应前缀校验（选填）','校验状态','数据提取/计算','期望结果','最终输出','请求地址','抑制响应','校验响应'])
        from ui.widgets.compact_step_layout import adaptive_columns
        adaptive_columns(p.uds_table, [1.3, 1.8, 1.5, .8, 2, 1.4, .8, 1, .8, .8])
        layout.addWidget(p.uds_table,1)
        logbar=QHBoxLayout()
        self.format=QComboBox();self.format.addItems(['十六进制','ASCII'])
        self.format.currentIndexChanged.connect(self.show_last_response)
        logbar.addWidget(self.format)
        export=QPushButton('导出日志');export.clicked.connect(self.export_log);logbar.addWidget(export)
        self.realtime=QPushButton('实时保存日志');self.realtime.setCheckable(True);self.realtime.toggled.connect(self.toggle_log);logbar.addWidget(self.realtime)
        clear=QPushButton('清空日志');logbar.addWidget(clear)
        self.stats=QLabel();logbar.addWidget(self.stats,1)
        reset=QPushButton('重置统计');reset.clicked.connect(self.reset_stats);logbar.addWidget(reset)
        layout.addLayout(logbar)
        self.last=QLineEdit();self.last.setReadOnly(True);self.last.setPlaceholderText('最近一次响应PDU')
        layout.addWidget(self.last)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(2000);self.log.setMinimumHeight(80);self.log.setMaximumHeight(140)
        clear.clicked.connect(self.log.clear)
        layout.addWidget(self.log)
        splitter.addWidget(right);splitter.setStretchFactor(1,1);splitter.setSizes([240,900])
        send_page.addWidget(splitter,1)
        p.uds_previous.hide();p.uds_next.hide()
        self.functional_id=QLineEdit('0x7DF')
        from ui.widgets.uds_transport_settings import UDSTransportSettings
        self.transport_settings = UDSTransportSettings()
        self.frame_format = self.transport_settings.frame_format
        self.padding = self.transport_settings.padding
        self.block_size = self.transport_settings.block_size
        self.st_min = self.transport_settings.st_min
        self.p2_star = self.transport_settings.p2_star
        self.default_address=QComboBox();self.default_address.addItem('物理地址','physical');self.default_address.addItem('功能地址','functional')
        self.keepalive=QCheckBox('启用会话保持（仅本次序列执行期间）')
        self.keepalive_ms=QSpinBox();self.keepalive_ms.setRange(100,600000);self.keepalive_ms.setValue(2000);self.keepalive_ms.setSuffix(' ms')
        self.keepalive_address=QComboBox();self.keepalive_address.addItem('物理地址','physical');self.keepalive_address.addItem('功能地址','functional')
        self.keepalive_suppress=QCheckBox('会话保持抑制响应');self.keepalive_suppress.setChecked(True)
        settings_layout=tabs.widget(0).widget().layout()
        for label,widget in [('功能地址（HEX）',self.functional_id),('帧格式',self.frame_format),('默认请求地址',self.default_address),
                             ('会话保持',self.keepalive),('发送周期',self.keepalive_ms),
                             ('会话保持地址',self.keepalive_address),('',self.keepalive_suppress),
                             ('ISO-TP填充字节',self.padding),('接收流控块大小（0不限）',self.block_size),
                             ('接收流控STmin（ISO-TP编码）',self.st_min),('P2*等待时间',self.p2_star)]:
            r=QHBoxLayout();r.addWidget(QLabel(label));r.addWidget(widget,1);settings_layout.insertLayout(settings_layout.count()-1,r)
        p.uds_security.setTitle('安全算法 DLL（ZCANPRO兼容，支持32/64位）')
        parser_page=tabs.widget(2)
        tabs.removeTab(2);tabs.insertTab(3,parser_page,'数据解析')
        tabs.currentChanged.connect(self._tab_changed)
        tabs.setCurrentIndex(2)
        p.btn_live_sniff.clicked.disconnect()
        p.btn_live_sniff.clicked.connect(lambda:self.run(True,capture=True))
        for button in p.page_uds.findChildren(QPushButton):button.setAutoDefault(False)
        self.reset_stats()

    def select_case(self,item,_):
        request=item.data(0,Qt.UserRole)
        if self.page._uds_edit_item is not None and not self.page.save_uds_edited_row(show_list=False):return
        if isinstance(request,dict):
            for row in request['sequence']:
                rule='ASCII(offset=3,len=17,var=VIN)' if row=='22 F1 90' else ''
                self.page._append_uds_table_row('读取VIN' if rule else ('DLL安全解锁' if row=='@SECURITY' else '扩展会话'),row,'',rule)
                if rule:self.page.uds_table.item(self.page.uds_table.rowCount()-1,6).setCheckState(Qt.Checked)
            self.page.uds_tabs.setCurrentIndex(2)
            self._log('已追加组合案例。请检查ECU支持的会话、DID与安全等级；配置DLL后才能发送含解锁的序列。')
            return
        if request=='@SECURITY':self.page.uds_tabs.setCurrentIndex(1);return
        self.page.uds_service.setCurrentIndex(self.page.uds_service.count()-1)
        self.page.uds_request.setText(request or '')
        self.page._set_uds_extract_from_rule_string('ASCII(offset=3,len=17,var=VIN)' if request=='22 F1 90' else '')
        self.page._uds_case_name=(request or '',item.text(0))
        self._log('案例只预填请求，不会自动发送。DID、等级和附加参数请按ECU协议确认。')

    def config(self):
        return settings_from(encode_settings(dict(functional_id=int(self.functional_id.text(),16),
            address=self.default_address.currentData(),keepalive=self.keepalive.isChecked(),
            keepalive_ms=self.keepalive_ms.value(),keepalive_address=self.keepalive_address.currentData(),
            keepalive_suppress=self.keepalive_suppress.isChecked(),padding=self.padding.value(),
            block_size=self.block_size.value(),st_min=self.st_min.value(),p2_star_ms=self.p2_star.value(),frame_format=self.frame_format.currentData(),
            cases=self.custom_cases,dtc_dictionary=self.dtc_dictionary)))

    def load_config(self,cfg):
        self.functional_id.setText(hex(cfg['functional_id']))
        self.frame_format.setCurrentIndex(self.frame_format.findData(cfg['frame_format']))
        self.default_address.setCurrentIndex(self.default_address.findData(cfg['address']))
        self.keepalive.setChecked(cfg['keepalive']);self.keepalive_ms.setValue(cfg['keepalive_ms'])
        self.keepalive_address.setCurrentIndex(self.keepalive_address.findData(cfg['keepalive_address']))
        self.keepalive_suppress.setChecked(cfg['keepalive_suppress'])
        self.padding.setValue(cfg['padding']);self.block_size.setValue(cfg['block_size'])
        self.st_min.setValue(cfg['st_min']);self.p2_star.setValue(cfg['p2_star_ms'])
        for i in reversed(range(self.services.topLevelItemCount())):
            if self.services.topLevelItem(i).data(0,Qt.UserRole+1)=='custom':self.services.takeTopLevelItem(i)
        self.custom_cases=[]
        if cfg.get('cases'):self.add_cases(cfg['cases'],'配方案例')
        self.dtc_dictionary=dict(cfg.get('dtc_dictionary',{}))

    def row_options(self):return dict(address=self.address.currentData(),suppress=self.suppress.isChecked(),check=self.check.isChecked(),response_sample=self.response_sample.text().strip())

    def load_options(self,options):
        self.address.setCurrentIndex(self.address.findData(options.get('address','default')))
        self.suppress.blockSignals(True)
        self.suppress.setChecked(options.get('suppress',False))
        self.suppress.blockSignals(False)
        self.page.uds_auto_response.setEnabled(not self.suppress.isChecked())
        self.check.setChecked(options.get('check',True))
        expected=self.page.uds_result_expected.text()
        self.response_sample.setText(options.get('response_sample',''))
        self.page.uds_result_expected.setText(expected)

    def _sample_changed(self,text):
        try:
            raw=bytes.fromhex(text)
            if len(raw)>4095:raise ValueError('响应样本最多4095字节')
        except ValueError:
            self.page._uds_response_preview=''
            self.response_length.setText('完整UDS长度：—')
            self.response_sample.setToolTip('请填写完整HEX字节；输入未完成时暂不生成方块，最多4095字节。')
            self.page._sync_byte_grid_from_pdu()
            return
        self.page._uds_response_preview=raw.hex(' ').upper()
        self.response_length.setText(f'完整UDS长度：{len(raw)} 字节（0x{len(raw):X}）')
        self.page._sync_byte_grid_from_pdu()

    def _tab_changed(self,index):
        if index!=3 or self._opening_parser or self.page.is_loading:return
        self._opening_parser=True
        try:
            p=self.page
            output=next((r for r in range(p.uds_table.rowCount()) if p.uds_table.item(r,6).checkState()==Qt.Checked),None)
            if output is not None and p._uds_edit_row()!=output:
                p.load_uds_row(output)
            p.uds_tabs.setCurrentIndex(3)
        finally:self._opening_parser=False

    def open_settings(self):
        # Move the actual forms into a modal; restore on every exit so no duplicate draft exists.
        p=self.page;dialog=QDialog(p);dialog.setWindowTitle('UDS参数设置');dialog.resize(850,700)
        dialog.setStyleSheet(p.styleSheet()+p.page_uds.styleSheet())
        dialog.setWindowFlags(dialog.windowFlags()|Qt.WindowMinMaxButtonsHint)
        layout=QVBoxLayout(dialog);tabs=QTabWidget();layout.addWidget(tabs)
        pages=[p.uds_tabs.widget(0),p.uds_tabs.widget(1)]
        for page in reversed(pages):p.uds_tabs.removeTab(p.uds_tabs.indexOf(page))
        tabs.addTab(pages[0],'常规设置／地址／会话保持');tabs.addTab(pages[1],'安全算法 DLL')
        snapshot=[]
        for page in pages:
            for widget in page.findChildren(QWidget):
                if isinstance(widget,QComboBox):snapshot.append((widget,widget.setCurrentIndex,widget.currentIndex()))
                elif isinstance(widget,(QSpinBox,QDoubleSpinBox)):snapshot.append((widget,widget.setValue,widget.value()))
                elif isinstance(widget,QLineEdit):snapshot.append((widget,widget.setText,widget.text()))
                elif isinstance(widget,(QCheckBox,QGroupBox)) and (not isinstance(widget,QGroupBox) or widget.isCheckable()):
                    snapshot.append((widget,widget.setChecked,widget.isChecked()))
        buttons=QHBoxLayout()
        close=QPushButton('确定')
        def confirm():
            try:self.config();p.uds_security.config()
            except Exception as exc:QMessageBox.warning(dialog,'参数错误',str(exc));return
            dialog.accept()
        close.clicked.connect(confirm);buttons.addStretch();buttons.addWidget(close)
        cancel=QPushButton('取消');cancel.clicked.connect(dialog.reject);buttons.addWidget(cancel);layout.addLayout(buttons)
        self._settings_popup=dialog
        try:
            if dialog.exec()!=QDialog.Accepted:
                for widget,setter,value in snapshot:setter(value)
        finally:
            for i,page in enumerate(pages):
                tabs.removeTab(tabs.indexOf(page));p.uds_tabs.insertTab(i,page,['通讯参数','安全 DLL'][i])
            self._settings_popup=None;p.uds_tabs.setCurrentIndex(2)

    def add_delay(self):
        ms,ok=QInputDialog.getInt(self.page,'添加延时','延时（ms）',100,0,3600000)
        if ok:self.page._append_uds_table_row(f'延时 {ms}ms',f'@DELAY {ms}','')

    def _log(self,text):
        self.log.appendPlainText(text)
        match=re.search(r'RX=([0-9A-F]{2}(?: [0-9A-F]{2})*)',text)
        if match:
            text_response=match.group(1)
            if bytes.fromhex(text_response)[0]!=0x7F:
                if self._run_immediate:self._response(text_response)
                else:
                    row_match=re.search(r'轮次\d+/\d+ 序号(\d+)',text)
                    if row_match:
                        index=int(row_match.group(1))-1
                        if 0<=index<len(self._run_items):
                            item=self._run_items[index]
                            from shiboken6 import isValid
                            if isValid(item) and item.tableWidget() is self.page.uds_table:
                                options=dict(item.data(Qt.UserRole) or {})
                                options['response_sample']=text_response;item.setData(Qt.UserRole,options)
                                if self.page._uds_edit_item is item:self._response(text_response)
        if self._live_log:
            try:
                with self._live_log.open('a',encoding='utf-8') as stream:stream.write(text+'\n')
            except OSError as exc:
                self._live_log=None;self.realtime.setChecked(False)
                self.log.appendPlainText('日志保存失败：'+str(exc))

    def _response(self,text):
        self.last_response=text;self.show_last_response()
        raw=bytes.fromhex(text)
        if raw[:2]==b'\x59\x02':
            self.dtc_table.setRowCount(0)
            if (len(raw)-3)%4==0:
                for offset in range(3,len(raw),4):
                    row=self.dtc_table.rowCount();self.dtc_table.insertRow(row)
                    code=raw[offset:offset+3].hex().upper()
                    for col,value in enumerate([code,f'{raw[offset+3]:02X}',self.dtc_dictionary.get(code,'')]):
                        self.dtc_table.setItem(row,col,QTableWidgetItem(value))
        self.response_sample.setText(text)
        self._sample_changed(text)

    def show_last_response(self,*_):
        self.last.setText(self.last_response if self.format.currentIndex()==0 else bytes.fromhex(self.last_response).decode('ascii',errors='replace'))

    def reset_stats(self):
        self.count=self.passed=self.failed=0;self.update_stats()

    def update_stats(self):self.stats.setText(f'执行次数 {self.count}　成功 {self.passed}　失败 {self.failed}')

    def run(self,immediate,*,capture=False):
        p=self.page
        if self.task.isRunning():return
        try:
            from ui.widgets.uds_online import connected_recipe_driver
            driver=connected_recipe_driver(p)
            if not immediate and p._uds_edit_item is not None and not p.save_uds_edited_row(show_list=False):return
            if immediate:
                request=p.uds_request.text().strip()
                if not request:raise ValueError('请填写请求PDU')
                rule='' if capture else p._get_uds_extract_rule_string();prefix='' if capture else p.uds_expected.text().strip()
                script=request+(' => '+prefix if prefix else '')+(' | '+rule if rule else '')
                options=[dict(expected='' if capture else p.uds_result_expected.text(),output=False,**self.row_options())]
                if capture:options[0]['suppress']=False
                cycles=1
            else:
                script=p._uds_script();options=p._uds_row_metadata();cycles=p.uds_cycles.value()
            if not script:raise ValueError('列表为空')
            decode_rows(encode_rows(options),script.splitlines())
            cfg=self.config()
            validate_sequence(script.splitlines(),options,cfg)
            from core.seedkey import encode as encode_security
            security=p.uds_security.config()
            if any(row=='@SECURITY' and config.get('enabled',True) for row,config in zip(script.splitlines(),options)) and not security:
                raise ValueError('请先导入并启用安全DLL')
            params=f'TX_ID:{p.uds_tx_id.text()} / RX_ID:{p.uds_rx_id.text()} / TYPE:{p.uds_can_type.currentIndex()} / TIMEOUT:{p.uds_timeout.value()} / CYCLES:{cycles} / INTERVAL:{p.uds_interval.value()} / PCH:{p.can_physical_channel.currentData() or 0} / LIST64:{base64.urlsafe_b64encode(script.encode()).decode()} / UDSMETA64:{encode_rows(options)} / UDSCFG64:{encode_settings(cfg)}'
            if security:params+=' / SEC64:'+encode_security(security)
        except Exception as exc:QMessageBox.warning(p,'无法发送',str(exc));return
        self.cancel=threading.Event()
        self._run_immediate=immediate
        self._run_items=[p.uds_table.item(r,0) for r in range(p.uds_table.rowCount())]
        p.destroyed.connect(self.cancel.set)
        cancel=self.cancel;manager=p.device_manager
        def perform():
            from core.engine import ChannelWorker,SubStep,SubStepType
            worker=ChannelWorker(1,[],device_manager=manager);worker.cancel_event=cancel
            worker.log_message.connect(lambda _,text:self.log_received.emit(text),Qt.DirectConnection)
            ok,result=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device='UDS',params=params,is_judgment=True)))
            return ok,result
        def done(ok,value):
            success=ok and value[0]
            self.count+=1;self.passed+=bool(success);self.failed+=not success;self.update_stats()
            self._log(('PASS: ' if success else 'NG: ')+str(value[1] if ok else value))
            from ui.widgets.uds_online import restore_controls
            restore_controls(self.frozen,self.stop_button)
            if success and not immediate:
                p.uds_tabs.setCurrentIndex(3)
                self._tab_changed(3)
        from ui.widgets.uds_online import freeze_controls,restore_controls
        self.frozen=freeze_controls(p,p.page_uds,self.stop_button)
        if self.task.run(perform,done) is False:
            restore_controls(self.frozen,self.stop_button)
            self._log('UDS task did not start; no request was sent.')

    def stop(self):
        self.cancel.set();self.task.begin_shutdown()

    def export_sequence(self):
        try:
            p=self.page
            if p._uds_edit_item is not None and not p.save_uds_edited_row(show_list=False):return
            data=p.get_data()
            path,_=QFileDialog.getSaveFileName(p,'导出本软件UDS序列（含解析与DLL路径）','','JSON (*.json)')
            if path:Path(path).write_text(json.dumps(dict(format='BMSFCT-UDS',version=1,step=data),ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc:QMessageBox.warning(self.page,'导出失败',str(exc))

    def import_sequence(self):
        path,_=QFileDialog.getOpenFileName(self.page,'导入本软件UDS序列','','JSON (*.json)')
        if not path:return
        try:
            data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if data.get('format')!='BMSFCT-UDS' or data.get('version')!=1:raise ValueError('请选择本软件导出的UDS序列JSON；不支持直接导入ZCANPRO专有文件')
            step=data['step']
            if 'UDS' not in step.get('device','').upper():raise ValueError('不是UDS序列')
            # Validate in an isolated editor before replacing current work.
            from ui.dialogs.step_dialog import StepDialog
            probe=StepDialog(step_data=step)
            try:probe.get_data()
            finally:probe.close()
            self.page.cancel_uds_edit();self.page._load_data(step);self.page.uds_tabs.setCurrentIndex(2)
        except Exception as exc:QMessageBox.warning(self.page,'导入失败',str(exc))

    def import_cases(self):
        path,_=QFileDialog.getOpenFileName(self.page,'功能案例JSON：[{"name":"读取VIN","request":"22 F1 90"}]','','JSON (*.json)')
        if not path:return
        try:
            self.add_cases(json.loads(Path(path).read_text(encoding='utf-8-sig')),Path(path).stem)
        except Exception as exc:QMessageBox.warning(self.page,'案例导入失败',str(exc))

    def add_cases(self,rows,name):
        if not isinstance(rows,list):raise ValueError('案例必须为JSON列表')
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('name'),str):raise ValueError('案例缺少name')
            if not bytes.fromhex(row['request']):raise ValueError('案例请求为空')
        root=QTreeWidgetItem(self.services,['自定义案例：'+name])
        root.setData(0,Qt.UserRole+1,'custom')
        self.custom_cases.extend(dict(row) for row in rows)
        for row in rows:
            item=QTreeWidgetItem(root,[row['name']]);item.setData(0,Qt.UserRole,row['request'])
        root.setExpanded(True)

    def edit_json(self,title,initial,apply):
        dialog=QDialog(self.page);dialog.setWindowTitle(title);dialog.resize(700,500)
        layout=QVBoxLayout(dialog);editor=QPlainTextEdit(json.dumps(initial,ensure_ascii=False,indent=2));layout.addWidget(editor)
        row=QHBoxLayout()
        export=QPushButton('导出JSON文件');row.addWidget(export)
        def export_data():
            path,_=QFileDialog.getSaveFileName(dialog,'导出JSON','','JSON (*.json)')
            if path:
                try:Path(path).write_text(editor.toPlainText(),encoding='utf-8')
                except OSError as exc:QMessageBox.warning(dialog,'导出失败',str(exc))
        export.clicked.connect(export_data)
        save=QPushButton('应用');row.addWidget(save)
        def commit():
            try:apply(json.loads(editor.toPlainText()));dialog.accept()
            except Exception as exc:QMessageBox.warning(dialog,'格式错误',str(exc))
        save.clicked.connect(commit)
        cancel=QPushButton('取消');cancel.clicked.connect(dialog.reject);row.addWidget(cancel);layout.addLayout(row)
        dialog.exec()

    def edit_cases(self):
        self.edit_json('服务编辑器：填写案例名称和请求PDU',
                       [dict(name='读取VIN',request='22 F1 90')],lambda rows:self.add_cases(rows,'编辑器'))

    def edit_dtc_dictionary(self):
        def apply(data):
            if not isinstance(data,dict):raise ValueError('字典格式为 {"123456":"故障说明"}')
            normalized={}
            for code,description in data.items():
                if not re.fullmatch('[0-9A-Fa-f]{6}',code) or not isinstance(description,str):raise ValueError('DTC为6位HEX，说明为字符串')
                normalized[code.upper()]=description
            self.dtc_dictionary=normalized
            if self.last_response:self._response(self.last_response)
        self.edit_json('故障码字典编辑器：24位DTC与说明',self.dtc_dictionary,apply)

    def export_log(self):
        path,_=QFileDialog.getSaveFileName(self.page,'导出日志','','文本 (*.txt)')
        if path:
            try:Path(path).write_text(self.log.toPlainText(),encoding='utf-8')
            except OSError as exc:QMessageBox.warning(self.page,'保存失败',str(exc))

    def toggle_log(self,enabled):
        if not enabled:self._live_log=None;return
        path,_=QFileDialog.getSaveFileName(self.page,'实时保存日志','','文本 (*.txt)')
        if path:self._live_log=Path(path)
        else:self.realtime.setChecked(False)
