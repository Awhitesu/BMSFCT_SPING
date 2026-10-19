"""Shared ISO-TP controls for standard UDS and generated 2E requests."""
from PySide6.QtWidgets import QComboBox, QSpinBox
from core.uds_sequence import settings_from, encode_settings


class UDSTransportSettings:
    KEYS = ('frame_format', 'padding', 'block_size', 'st_min', 'p2_star_ms')

    def __init__(self):
        self.frame_format = QComboBox()
        for label, value in [('按CAN ID自动选择', 'auto'), ('标准帧（11位）', 'standard'), ('扩展帧（29位）', 'extended')]:
            self.frame_format.addItem(label, value)
        self.padding = QSpinBox(); self.padding.setRange(0, 255)
        self.padding.setDisplayIntegerBase(16); self.padding.setPrefix('0x')
        self.block_size = QSpinBox(); self.block_size.setRange(0, 255)
        self.st_min = QSpinBox(); self.st_min.setRange(0, 255)
        self.st_min.setDisplayIntegerBase(16); self.st_min.setPrefix('0x')
        self.p2_star = QSpinBox(); self.p2_star.setRange(1, 600000); self.p2_star.setSuffix(' ms')
        self.load({})
        self.st_min.setToolTip('本机接收多帧响应时发给ECU的流控参数；写入请求的发送节奏由ECU返回的FC决定。')

    def rows(self):
        return [('帧格式', self.frame_format), ('ISO-TP填充字节', self.padding),
                ('接收流控块大小（0不限）', self.block_size), ('接收流控STmin（ISO-TP编码）', self.st_min),
                ('P2*等待时间', self.p2_star)]

    def config(self):
        config = dict(frame_format=self.frame_format.currentData(), padding=self.padding.value(),
                      block_size=self.block_size.value(), st_min=self.st_min.value(), p2_star_ms=self.p2_star.value())
        checked = settings_from(encode_settings(config))
        return {key: checked[key] for key in self.KEYS}

    def load(self, config):
        cfg = settings_from(encode_settings(config))
        self.frame_format.setCurrentIndex(self.frame_format.findData(cfg['frame_format']))
        self.padding.setValue(cfg['padding']); self.block_size.setValue(cfg['block_size'])
        self.st_min.setValue(cfg['st_min']); self.p2_star.setValue(cfg['p2_star_ms'])

