"""离线生成单台/整体模拟器紧凑页面预览。"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
import sys
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import QPoint
from devices.manager import DeviceManager
from ui.tabs.simulator_tab import SimulatorTab

app = QApplication([])
if not QFontDatabase.families():
    for name in ('msyh.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
db=Mock()
db.load_sys_config.return_value=dict(sim1_enabled=True,sim1_channels=24,sim2_enabled=True,
                                    sim2_channels=24,sim3_enabled=True,sim3_channels=24)
manager=DeviceManager(db)
for sim in manager.simulators:
    sim.is_connected=True
widget=SimulatorTab(manager)
widget.status_timer.stop()
output=Path(os.environ.get('SIMULATOR_PREVIEW_DIR','scratch'))
output.mkdir(parents=True,exist_ok=True)
try:
    assert len(widget.channel_checks)==24 and not widget.findChildren(QTabWidget)
    assert widget.device_combo.count()==4 and not widget.btn_set_volt.isEnabled()
    for width,height,scope,name in [(1280,800,0,'simulator_layout_preview.png'),
                                   (1280,800,3,'simulator_whole_set_preview.png'),
                                   (800,650,0,'simulator_compact_800.png')]:
        widget.resize(width,height)
        widget.show()
        widget.device_combo.setCurrentIndex(scope)
        widget.chk_all.setChecked(True)
        for ch in widget._rows:
            widget.on_read_all_progress(ch,3.2)
            widget.on_current_read_progress(ch,.25)
        for _ in range(5):app.processEvents()
        assert widget.width()==width
        assert widget.table.y()==8 and widget.table.height()>=height-20
        assert widget.control_scroll.x()>widget.table.geometry().right()
        assert max(button.width() for button in widget.control_buttons)<=150
        assert all(button.mapTo(widget,button.rect().topRight()).x()<width for button in widget.control_buttons)
        parameters=(widget.spin_volt,widget.range_combo,widget.current_limit)
        settings=(widget.btn_set_volt,widget.btn_set_range,widget.btn_set_current)
        assert all(control.mapTo(widget,QPoint()).y()==button.mapTo(widget,QPoint()).y()
                   for control,button in zip(parameters,settings))
        if height==800 and scope==0:
            assert widget.table.visualItemRect(widget.table.item(23,0)).bottom()<widget.table.viewport().height()
        widget.grab().save(str(output/name))
        print(json.dumps(dict(size=[width,height],scope=scope,selected=len(widget.selected_channels()),
                             table_top=widget.table.y(),table_height=widget.table.height(),
                             configured_rows=sum(not widget.table.isRowHidden(row) for row in range(widget.table.rowCount())),
                             fully_visible_rows=sum(not widget.table.isRowHidden(row) and widget.table.viewport().rect().contains(
                                 widget.table.visualItemRect(widget.table.item(row,0))) for row in range(widget.table.rowCount())),
                             max_button_width=max(button.width() for button in widget.control_buttons))))
finally:
    widget.shutdown()
    widget.close()
