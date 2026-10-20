"""Independent editor and online capture for the three-field write step."""
import threading
from datetime import datetime
from PySide6.QtCore import Signal,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QGroupBox,QLineEdit,QSpinBox,QLabel,QPushButton,QPlainTextEdit,QMessageBox,QCheckBox
from core.uds_field_write import encode,build_details,DEVICE
from ui.device_task import DeviceTask


class UDSFieldWriteForm(QWidget):
    log_received=Signal(str)

    def __init__(self,page):
        super().__init__(page);self.page=page;self.task=DeviceTask(self);self.cancel=threading.Event()
        layout=QVBoxLayout(self)
        communication=QGroupBox('1. 通信与写入地址');form=QFormLayout(communication)
        self.tx=QLineEdit('0x7E0');self.rx=QLineEdit('0x7E8');self.did=QLineEdit()
        self.did.setPlaceholderText('填写项目 DID，例如 1E5E')
        self.timeout=QSpinBox();self.timeout.setRange(1,600000);self.timeout.setValue(1000);self.timeout.setSuffix(' ms')
        for label,w in [('请求 ID（HEX）',self.tx),('响应 ID（HEX）',self.rx),('写入 DID（HEX）',self.did),('超时',self.timeout)]:form.addRow(label,w)
        layout.addWidget(communication)
        from ui.widgets.uds_transport_settings import UDSTransportSettings
        self.transport_settings=UDSTransportSettings()
        self.advanced_toggle=QPushButton('UDS通信高级参数（与标准UDS一致）')
        self.advanced_toggle.setCheckable(True);layout.addWidget(self.advanced_toggle)
        self.advanced=QGroupBox('ISO-TP / UDS通信设置');advanced_form=QFormLayout(self.advanced)
        self.use_transport=QCheckBox('启用本步UDS通信高级参数')
        advanced_form.addRow(self.use_transport)
        legacy_hint=QLabel('不启用时兼容旧配方：自动帧格式、00填充、BS/STmin为0，P2*取5秒与超时×5中的较大值。')
        legacy_hint.setWordWrap(True);advanced_form.addRow(legacy_hint)
        for label,widget in self.transport_settings.rows():advanced_form.addRow(label,widget)
        def enable_transport(enabled):
            for _,widget in self.transport_settings.rows():widget.setEnabled(enabled)
        self.use_transport.toggled.connect(enable_transport);enable_transport(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced);self.advanced.hide()
        fields=QGroupBox('2. 写入内容：字段1 + 字段2 + 字段3（ASCII，无分隔符）');form=QFormLayout(fields)
        self.literal=QLineEdit();self.start=QSpinBox();self.start.setRange(1,4095)
        self.variable=QLineEdit();self.variable.setPlaceholderText('保存实际写入的完整ASCII字符串')
        self.length=QSpinBox();self.length.setRange(0,4095);self.length.setSpecialValueText('到条码末尾')
        for label,w in [('字段1：手工输入',self.literal),('字段2：执行当天日期',QLabel('YYMMDD，例如 2026-10-19 → 261019；每次执行自动更新')),
                        ('条码起始位置（从1计数）',self.start),('截取长度（0为到末尾）',self.length)]:form.addRow(label,w)
        form.addRow('写入结果变量名',self.variable)
        self.variable.hide();form.labelForField(self.variable).hide()
        layout.addWidget(fields)
        output_tip=QLabel('三字段拼接成功后，先保存到变量“主板序列号”，再发送2E；通信失败仍保留本次字符串。收到匹配DID的肯定响应后，工步输出6E。')
        output_tip.setWordWrap(True);layout.addWidget(output_tip)
        capture=QGroupBox('3. 预览与在线抓取');form=QFormLayout(capture)
        self.sample=QLineEdit();self.sample.setPlaceholderText('仅调试：人工输入、粘贴或键盘扫码；生产执行使用本次扫码条码')
        self.scan=QPushButton('扫描调试条码');self.scan.clicked.connect(self.scan_barcode)
        self.scanner=None
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setMaximumHeight(130)
        self.capture=QPushButton('在线抓取（发送2E实际写入）');self.capture.clicked.connect(self.run_capture)
        self.stop=QPushButton('停止抓取');self.stop.setEnabled(False);self.stop.clicked.connect(self.cancel.set)
        form.addRow('调试条码（不随配方保存）',self.sample);form.addRow(self.scan);form.addRow('本次 ASCII / 请求 PDU',self.preview);form.addRow(self.capture,self.stop)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(300);self.log.setMaximumHeight(150);form.addRow('在线响应与结果',self.log)
        self.log_received.connect(self.log.appendPlainText);layout.addWidget(capture)
        tip=QLabel('只执行2E并检查6E＋相同DID，不自动进入会话或解锁。若需解锁，请在前面添加标准UDS工步。条码缺失或截取越界会失败，不发送写入。');tip.setWordWrap(True);layout.addWidget(tip)
        for w in (self.tx,self.rx,self.did,self.literal,self.sample):w.textChanged.connect(self.update_preview)
        self.start.valueChanged.connect(self.update_preview);self.length.valueChanged.connect(self.update_preview)
        for widget in (self.transport_settings.padding,self.transport_settings.block_size,
                       self.transport_settings.st_min,self.transport_settings.p2_star):
            widget.valueChanged.connect(self.update_preview)
        self.transport_settings.frame_format.currentIndexChanged.connect(self.update_preview)
        self.use_transport.toggled.connect(self.update_preview)
        page.finished.connect(self.close_capture)

    def close_capture(self,*_):
        self.cancel.set();self.stop_scan();self.task.begin_shutdown()

    def scan_barcode(self):
        if self.task.isRunning():return
        if self.scanner is not None:self.stop_scan();return
        node=self.page
        while node is not None:
            scanner=getattr(node,'serial_scanner',None)
            if scanner is not None:break
            node=node.parent()
        if node is None or not scanner.port.isOpen():
            QMessageBox.warning(self,'扫码器未连接','请先初始化扫码器串口，也可手工输入调试条码。');return
        self.scanner=scanner;scanner.barcode.connect(self.receive_scan)
        if not scanner.begin_scan(self,continuous=True):
            self.stop_scan();QMessageBox.warning(self,'扫码器忙','扫码器正在被其他功能使用。');return
        self.scan.setText('等待扫码，点击取消')

    def receive_scan(self,text):
        if self.scanner is None:return
        self.sample.setText(text);self.stop_scan()

    def stop_scan(self):
        scanner=self.scanner;self.scanner=None
        if scanner is not None:
            scanner.end_scan(self)
            try:scanner.barcode.disconnect(self.receive_scan)
            except (RuntimeError,TypeError):pass
        self.scan.setText('扫描调试条码')

    def config(self):
        def hex_value(widget,label):
            try:return int(widget.text().strip(),16)
            except ValueError:raise ValueError('请填写有效的'+label+'（HEX）')
        c=dict(tx_id=hex_value(self.tx,'请求 ID'),rx_id=hex_value(self.rx,'响应 ID'),did=hex_value(self.did,'写入 DID'),
               timeout=self.timeout.value(),literal=self.literal.text(),start=self.start.value(),length=self.length.value())
        if self.variable.text().strip():c['variable']=self.variable.text().strip()
        if self.use_transport.isChecked():
            c['uds_settings']=self.transport_settings.config()
        encode(c);return c

    def params(self):return 'WRITE64:'+encode(self.config())

    def load(self,c):
        self.transport_settings.load(c.get('uds_settings',{}))
        self.use_transport.setChecked('uds_settings' in c)
        self.tx.setText(hex(c['tx_id']));self.rx.setText(hex(c['rx_id']));self.did.setText(f"{c['did']:04X}")
        self.literal.setText(c['literal']);self.start.setValue(c['start']);self.length.setValue(c['length']);self.timeout.setValue(c['timeout'])
        self.variable.setText(c.get('variable',''))

    def update_preview(self,*_):
        try:
            self.show_preview(build_details(self.config(),self.sample.text()))
        except (ValueError,TypeError) as exc:self.preview.setPlainText('待填写：'+str(exc))

    def show_preview(self,details):
        lines=[f"字段1（{len(details['literal'])}字符）：{details['literal']}",
               f"日期（6字符）：{details['date']}",f"条码截取（{len(details['barcode_slice'])}字符）：{details['barcode_slice']}",
               f"最终ASCII（{len(details['text'])}字符）：{details['text']}",
               f"请求PDU（{len(details['request'])}字节）：{details['request'].hex(' ').upper()}"]
        self.preview.setPlainText('\n'.join(lines))

    def run_capture(self):
        if self.task.isRunning():return
        self.stop_scan()
        try:
            p=self.page;c=self.config();barcode=self.sample.text();when=datetime.now()
            details=build_details(c,barcode,when)
            from ui.widgets.uds_online import connected_recipe_driver
            connected_recipe_driver(p)
            params='WRITE64:'+encode(c)+f' / PCH:{p.can_physical_channel.currentData() or 0}'
        except (ValueError,TypeError,ConnectionError) as exc:QMessageBox.warning(self,'不能在线抓取',str(exc));return
        self.cancel=threading.Event();cancel=self.cancel;manager=p.device_manager
        from ui.widgets.uds_online import freeze_controls,restore_controls
        frozen=freeze_controls(p,self,self.stop);self.log.clear();self.show_preview(details)
        self.log.appendPlainText('本次发送快照：\n'+self.preview.toPlainText())
        def perform():
            from core.engine import ChannelWorker,SubStep,SubStepType
            worker=ChannelWorker(1,[],device_manager=manager);worker.sn_barcode=barcode;worker.cancel_event=cancel
            worker._uds_field_write_now=when
            worker.log_message.connect(lambda _,text:self.log_received.emit(text),Qt.DirectConnection)
            return worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params=params,is_judgment=True)))
        def done(ok,value):
            restore_controls(frozen,self.stop)
            self.log.appendPlainText(('PASS: ' if ok and value[0] else 'NG: ')+str(value[1] if ok else value))
        if self.task.run(perform,done) is False:
            restore_controls(frozen,self.stop)
            self.log.appendPlainText('未发送：在线任务未能启动，请重新打开工步编辑窗口。')
