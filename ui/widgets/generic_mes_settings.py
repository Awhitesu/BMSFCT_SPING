"""Configuration for the independent checkBmsSnInfo/checkBmsProc MES."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QGroupBox,
                              QLabel, QLineEdit, QPushButton, QSpinBox)

from core.generic_mes import settings_from


class GenericMesSettings(QGroupBox):
    save_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__('锐能MES参数', parent)
        form = QFormLayout(self)
        self.query_url = QLineEdit()
        self.query_url.setPlaceholderText('按接口文档填写完整查询地址')
        form.addRow('查询接口URL', self.query_url)
        self.upload_url = QLineEdit()
        self.upload_url.setPlaceholderText('按接口文档填写完整上传地址')
        form.addRow('上传接口URL', self.upload_url)
        self.station_id = QLineEdit()
        self.station_id.setMaxLength(50)
        form.addRow('工位编号 stationId', self.station_id)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(.1, 120)
        self.timeout.setSuffix(' s')
        form.addRow('请求超时', self.timeout)
        self.max_check_times = QSpinBox()
        self.max_check_times.setRange(0, 1000000)
        self.max_check_times.setSuffix(' 次')
        form.addRow('最大允许检测次数', self.max_check_times)
        self.upload_attempts = QSpinBox()
        self.upload_attempts.setRange(1, 2)
        self.upload_attempts.setSuffix(' 次（含首次上传）')
        form.addRow('每轮最多上传次数', self.upload_attempts)
        hint = QLabel('精华与锐能MES互斥启用；锐能启用后同时执行扫码查询及结果上传。'
                      '检测次数大于上限才禁止测试，等于上限允许测试。每轮最多上传2次，全部失败后弹窗并保留文件供补传。'
                      'PASS/FAIL仅由检测决定，上传成功或失败不影响判定和统计。'
                      '上传使用本次测试的最终实测值、单位和判定，NG提前结束也上传，手动停止和调试运行不上传。'
                      '自动上传本次实际完成的全部测试项，无需选择或映射项目。'
                      'checkType为测试项序号，checkItem为测试项名称；标准值取上下限平均值，只有一个限值时取该值。'
                      '实测值、单位及单项判定直接取自检测数据。')
        hint.setWordWrap(True)
        form.addRow(hint)
        self.save_button = QPushButton('保存锐能MES设置')
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.values()))
        form.addRow(self.save_button)
        self.set_settings({})

    def set_settings(self, config):
        cfg = settings_from(config)
        self.query_url.setText(cfg['generic_mes_query_url'])
        self.upload_url.setText(cfg['generic_mes_upload_url'])
        self.station_id.setText(cfg['generic_mes_station_id'])
        self.timeout.setValue(float(cfg['generic_mes_timeout']))
        self.max_check_times.setValue(cfg['generic_mes_max_check_times'])
        self.upload_attempts.setValue(cfg['generic_mes_upload_attempts'])

    def values(self):
        return dict(generic_mes_query_enabled=True,
                    generic_mes_upload_enabled=True,
                    generic_mes_query_url=self.query_url.text().strip(),
                    generic_mes_upload_url=self.upload_url.text().strip(),
                    generic_mes_station_id=self.station_id.text().strip(),
                    generic_mes_timeout=self.timeout.value(),
                    generic_mes_max_check_times=self.max_check_times.value(),
                    generic_mes_upload_attempts=self.upload_attempts.value())
