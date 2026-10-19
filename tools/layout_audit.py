"""Offline layout inventory; never initialize or poll physical devices."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtWidgets import QApplication,QDialog,QTabWidget,QScrollArea,QStackedWidget,QWidget
from data.db_manager import DBManager
from ui.main_window import MainWindow
from ui.dialogs.startup_dialog import StartupCheckDialog


def main():
    app=QApplication.instance() or QApplication([])
    for name in ('msyh.ttc','segoeui.ttf'):
        font_path=Path('C:/Windows/Fonts')/name
        if font_path.exists():QFontDatabase.addApplicationFont(str(font_path))
    app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
    maximum='--max-channels' in sys.argv
    output=Path('logs/layout_audit_20261017'+('_maximum' if maximum else ''));output.mkdir(parents=True,exist_ok=True)
    configuration={}
    if maximum:
        for index in range(1,7):
            configuration.update({f'sim{index}_enabled':True,f'sim{index}_channels':24})
        from devices.rn_board import board_settings
        configuration['rn_boards']={s['key']:dict(enabled=True,ip=f'192.0.2.{index+1}')
                                    for index,s in enumerate(board_settings({}))}
    rows=[]
    real_init=DBManager.__init__
    with tempfile.TemporaryDirectory() as temporary:
        with patch.object(DBManager,'__init__',lambda self,*a,**k:real_init(self,str(Path(temporary)/'audit.db'))), \
             patch.object(DBManager,'load_sys_config',lambda self:configuration), \
             patch.object(StartupCheckDialog,'__init__',lambda self,*a,**k:QDialog.__init__(self)), \
             patch.object(StartupCheckDialog,'exec',lambda self:QDialog.Accepted):
            window=MainWindow()
            for timer in window.findChildren(QTimer):timer.stop()
            # Timer starts from showEvent are suppressed too: no device polling.
            with patch.object(QTimer,'start',lambda *a,**k:None):
                def inspect(widget,path):
                    app.processEvents()
                    minimum=widget.minimumSizeHint()
                    row=dict(path=path,width=QWidget.width(widget),height=QWidget.height(widget),
                             minimum_width=minimum.width(),minimum_height=minimum.height(),
                             scroll_areas=len(widget.findChildren(QScrollArea)))
                    row['width_pressure']=minimum.width()>QWidget.width(widget)
                    row['height_pressure']=minimum.height()>QWidget.height(widget)
                    rows.append(row)
                    # Follow direct nested navigation containers without repeating
                    # those that belong to another page.
                    containers=[widget] if isinstance(widget,QTabWidget) else widget.findChildren(QTabWidget)
                    for tabs in containers:
                        ancestor=None if tabs is widget else tabs.parentWidget();nested=False
                        while ancestor and ancestor is not widget:
                            if isinstance(ancestor,(QTabWidget,QStackedWidget)):
                                nested=True;break
                            ancestor=ancestor.parentWidget()
                        if nested:continue
                        for index in range(tabs.count()):
                            tabs.setCurrentIndex(index);app.processEvents()
                            inspect(tabs.widget(index),path+'/'+tabs.tabText(index))
                for font in (10,12):
                    app.setFont(QFont('Microsoft YaHei',font))
                    for width,height in ((1024,700),(1440,900),(1920,1080)):
                        window.resize(width,height);window.show();app.processEvents()
                        for index in range(window.tabs.count()):
                            window.tabs.setCurrentIndex(index);app.processEvents()
                            inspect(window.tabs.widget(index),f'{font}pt/{width}x{height}/'+window.tabs.tabText(index))
                        for index in range(window.tab_debug_center.pages.count()):
                            page=window.tab_debug_center.pages.widget(index)
                            window.tabs.setCurrentWidget(window.tab_debug_center)
                            window.tab_debug_center.pages.setCurrentIndex(index)
                            inspect(page,f'{font}pt/{width}x{height}/device/{type(page).__name__}/{index}')
                        window.grab().save(str(output/f'main_{font}_{width}.png'))
                from ui.dialogs.login_dialog import LoginDialog
                from ui.dialogs.step_dialog import StepDialog
                from ui.dialogs.test_item_dialog import TestItemDialog
                from ui.dialogs.bulk_edit_dialog import BulkEditDialog
                from ui.dialogs.ai_barcode_dialog import AiBarcodeRuleDialog
                from ui.dialogs.ai_recipe_dialog import AiRecipeDialog
                from ui.dialogs.monitor_dialog import MonitorDialog
                from ui.dialogs.user_account_dialog import UserAccountDialog
                from ui.dialogs.flash_status_dialog import FlashStatusDialog
                factories=[lambda:LoginDialog(window.db_manager),lambda:StepDialog(device_manager=window.device_manager),
                           TestItemDialog,BulkEditDialog,AiBarcodeRuleDialog,
                           lambda:AiRecipeDialog(window.db_manager,'audit'),MonitorDialog,
                           lambda:UserAccountDialog(window.db_manager),FlashStatusDialog]
                for factory in factories:
                    dialog=factory()
                    for width,height in ((640,480),(1024,700)):
                        dialog.resize(width,height);dialog.show();app.processEvents()
                        inspect(dialog,'dialog/'+type(dialog).__name__+f'/{width}x{height}')
                        dialog.grab().save(str(output/f'{type(dialog).__name__}_{width}.png'))
                    dialog.close();dialog.deleteLater();app.processEvents()
            window.close()
            deadline=time.monotonic()+10
            while not getattr(window,'_shutdown_done',False) and time.monotonic()<deadline:
                app.processEvents();time.sleep(.005)
            assert window._shutdown_done
    (output/'inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    pressures=[r for r in rows if r['width_pressure'] or r['height_pressure']]
    print('SCENARIOS',len(rows),'PRESSURES',len(pressures))
    print(json.dumps(pressures[:30],ensure_ascii=True))


if __name__=='__main__':main()
