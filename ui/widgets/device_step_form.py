from PySide6.QtWidgets import QWidget,QLabel,QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit
from core.device_steps import validate
from ui.widgets.compact_step_layout import ResponsiveFieldsLayout


class DeviceStepForm(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.form=ResponsiveFieldsLayout(self)
        self.controls={}
        self.device=None
        self.spec=None

    def configure(self,device,spec,values=None):
        if self.device==device and self.spec==spec and values is None:return
        while self.form.count():
            item=self.form.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.controls={};self.device=device;self.spec=spec
        help_label=QLabel(spec.help or ('回读返回数值，单位随结果记录；可参与测试项上下限判定。' if spec.read else '操作成功返回 PASS；失败执行所选失败策略。'))
        self.help_label = help_label
        help_label.setWordWrap(True)
        fields=[]
        for field in spec.fields:
            value=(values or {}).get(field.key,field.default)
            if field.choices:
                control=QComboBox()
                for label,data in field.choices:control.addItem(label,data)
                control.setCurrentIndex(control.findData(value))
            elif isinstance(field.default,int):
                control=QSpinBox();control.setRange(int(field.minimum),int(field.maximum));control.setValue(value)
            elif isinstance(field.default,float):
                control=QDoubleSpinBox();control.setDecimals(12)
                control.setRange(field.minimum,field.maximum);control.setValue(value)
            else:
                control=QLineEdit(str(value))
            control.setMinimumHeight(40)
            self.controls[field.key]=control
            fields.append((QLabel(field.label),control))
        self.form.set_fields(help_label,fields)
        if device=='tbs2102b' and spec.key=='trigger':
            for key in ('kind','when','source'):
                self.controls[key].currentIndexChanged.connect(self.update_trigger_fields)
            self.update_trigger_fields()

    def update_trigger_fields(self):
        kind=self.controls['kind'].currentData()
        source=self.controls['source'].currentData()
        enabled={'level':kind!='RUNT' and source!='LINE',
                 'slope':kind=='EDGE','coupling':kind=='EDGE',
                 'polarity':kind!='EDGE','when':kind!='EDGE',
                 'width':kind!='EDGE' and self.controls['when'].currentData()!='OCCURS',
                 'lower':kind=='RUNT','upper':kind=='RUNT'}
        for key,state in enabled.items():self.controls[key].setEnabled(state)

    def values(self):
        result={}
        for key,control in self.controls.items():
            if isinstance(control,QComboBox):value=control.currentData()
            elif isinstance(control,(QSpinBox,QDoubleSpinBox)):value=control.value()
            else:value=control.text().strip()
            result[key]=value
        return validate(self.device,self.spec.key,result)[1]
