from PySide6.QtWidgets import (QGroupBox,QFormLayout,QHBoxLayout,QLineEdit,QComboBox,QSpinBox,
    QDoubleSpinBox,QLabel,QPushButton,QFileDialog,QWidget,QMessageBox)
from core.seedkey import validate_config, architecture


class SecurityAccessForm(QGroupBox):
    def __init__(self,parent=None):
        super().__init__('安全访问 DLL（ZCANPRO兼容）',parent)
        self.setCheckable(True);self.setChecked(False)
        form=QFormLayout(self)
        row=QWidget();layout=QHBoxLayout(row);layout.setContentsMargins(0,0,0,0)
        self.path=QLineEdit();self.path.setPlaceholderText('选择项目授权的 Seed/Key DLL（支持32位和64位）')
        browse=QPushButton('选择 DLL…');browse.clicked.connect(self.browse)
        layout.addWidget(self.path,1);layout.addWidget(browse)
        form.addRow('算法DLL：',row)
        self.api=QComboBox();self.api.addItems(['auto','GenerateKeyEx','ZLGKey'])
        self.calling=QComboBox();self.calling.addItems(['cdecl','stdcall'])
        self.level=QSpinBox();self.level.setRange(1,0x7D);self.level.setSingleStep(2);self.level.setDisplayIntegerBase(16);self.level.setPrefix('0x')
        self.dll_level=QSpinBox();self.dll_level.setRange(0,0x7FFFFFFF);self.dll_level.setValue(1)
        self.variant=QLineEdit();self.variant.setPlaceholderText('可留空；按算法供应商要求填写')
        self.seed_data=QLineEdit();self.seed_data.setPlaceholderText('可留空；请求Seed时的附加HEX数据')
        self.capacity=QSpinBox();self.capacity.setRange(1,4096);self.capacity.setValue(256)
        self.timeout=QDoubleSpinBox();self.timeout.setRange(.1,60);self.timeout.setValue(5);self.timeout.setSuffix(' s')
        for label,widget in [('导出接口：',self.api),('调用约定：',self.calling),('Seed请求子功能（奇数）：',self.level),
            ('传给DLL的安全等级（十进制）：',self.dll_level),('Variant：',self.variant),('Seed附加参数：',self.seed_data),
            ('Key最大长度（字节）：',self.capacity),('DLL计算超时：',self.timeout)]:form.addRow(label,widget)
        tip=QLabel('先按ECU协议进入所需会话，再执行DLL解锁，最后发送读写请求。Seed子功能必须为奇数，Key自动加1；传给DLL的等级按供应商要求设置，不自动换算。DLL采用独立32/64位进程计算。选择文件只保存路径，请保留原目录和依赖DLL；换电脑需重新选择或使用项目根目录下的相对路径。')
        tip.setWordWrap(True);form.addRow(tip)

    def browse(self):
        filename,_=QFileDialog.getOpenFileName(self,'选择Seed/Key DLL',self.path.text(),'Windows DLL (*.dll)')
        if filename:
            try:
                bits=architecture(filename)
            except (ValueError,OSError) as exc:
                QMessageBox.warning(self,'DLL文件无效',str(exc));return
            self.path.setText(filename);self.path.setToolTip(f'{bits}，调用时使用独立辅助程序')

    def config(self):
        if not self.isChecked():return None
        return validate_config(dict(path=self.path.text().strip(),api=self.api.currentText(),calling=self.calling.currentText(),
            level=self.level.value(),dll_level=self.dll_level.value(),variant=self.variant.text(),seed_data=self.seed_data.text().strip(),
            capacity=self.capacity.value(),timeout=self.timeout.value()))

    def load(self,c):
        self.setChecked(c is not None)
        if not c:return
        self.path.setText(c['path']);self.api.setCurrentText(c['api']);self.calling.setCurrentText(c['calling'])
        self.level.setValue(c['level']);self.dll_level.setValue(c['dll_level']);self.variant.setText(c.get('variant',''))
        self.seed_data.setText(c.get('seed_data',''));self.capacity.setValue(c['capacity']);self.timeout.setValue(c['timeout'])
