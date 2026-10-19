"""Per-control offline layout audit. Synthetic data only; no control commands.

Run separately for QT_SCALE_FACTOR 1, 1.25, 1.5, 2. Inventory is JSONL so every
finding can be traced to its page/state/widget instead of a page-size summary.
"""
import argparse
import ast
from collections import Counter
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser()
parser.add_argument('--scale',default='1')
parser.add_argument('--quick',action='store_true')
parser.add_argument('--output',default='logs/controls_layout_20261018')
args=parser.parse_args()
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_SCALE_FACTOR']=args.scale

from PySide6.QtCore import Qt,QPoint,QRect,QTimer,QCoreApplication,QEvent
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtWidgets import (QApplication,QWidget,QDialog,QLabel,QAbstractButton,
    QLineEdit,QAbstractSpinBox,QComboBox,QAbstractItemView,QTabWidget,QStackedWidget,
    QScrollArea,QTableWidget,QTextEdit,QPlainTextEdit,QProgressBar,
    QInputDialog,QMessageBox,QFileDialog,QTabBar,QAbstractSlider,QHeaderView)


SEMANTIC=(QAbstractButton,QLineEdit,QAbstractSpinBox,QComboBox,QLabel,
          QAbstractItemView,QTextEdit,QPlainTextEdit,QProgressBar,QTabBar,QAbstractSlider)
INTERACTIVE=(QAbstractButton,QLineEdit,QAbstractSpinBox,QComboBox,QAbstractItemView,
             QTextEdit,QPlainTextEdit,QTabBar,QAbstractSlider)


def text_of(widget):
    if isinstance(widget,QComboBox):return widget.currentText()
    if isinstance(widget,QLabel):return widget.text()
    if isinstance(widget,QAbstractButton):return widget.text()
    if isinstance(widget,QLineEdit):return widget.text()
    return ''


def source_text(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.JoinedStr):
        return ''.join(value.value if isinstance(value,ast.Constant) else '[动态内容：设备未响应，请检查配置与通讯状态]'
                       for value in node.values)
    return '设备未响应，请检查配置与通讯状态。'*6


class Audit:
    def __init__(self,app):
        self.app=app
        self.output=ROOT/f'{args.output}_{args.scale}{"_quick" if args.quick else ""}'
        self.output.mkdir(parents=True,exist_ok=True)
        self.stream=(self.output/'controls.jsonl').open('w',encoding='utf-8')
        self.findings=[];self.states=[];self.classes=Counter();self.count=0
        self.seen=set();self.covered=set();self.visibility={};self.reached=set()

    def pump(self):
        for _ in range(2):self.app.processEvents()

    def names(self,root):
        names={}
        def register(value,prefix,depth=0):
            if isinstance(value,QWidget):names.setdefault(id(value),prefix)
            elif depth<3 and isinstance(value,(list,tuple,dict)):
                entries=value.items() if isinstance(value,dict) else enumerate(value)
                for key,item in entries:register(item,f'{prefix}[{key}]',depth+1)
        for position,owner in enumerate([root,*root.findChildren(QWidget)]):
            for name,value in vars(owner).items():
                prefix=names.get(id(owner),f'{type(owner).__name__}[{position}]')
                register(value,f'{prefix}.{name}')
        return names

    def reachable(self,widget):
        ancestor=widget.parentWidget()
        while ancestor:
            if isinstance(ancestor,QTableWidget):
                for row in range(ancestor.rowCount()):
                    found=False
                    for col in range(ancestor.columnCount()):
                        cell=ancestor.cellWidget(row,col)
                        if cell is widget or (cell and cell.isAncestorOf(widget)):
                            ancestor.scrollTo(ancestor.model().index(row,col));found=True;break
                    if found:break
            if isinstance(ancestor,QScrollArea):ancestor.ensureWidgetVisible(widget,4,4)
            ancestor=ancestor.parentWidget()
        self.pump()
        if not widget.isVisible():return 'hidden'
        top=widget.window()
        visible=QRect(widget.mapTo(top,QPoint(0,0)),widget.size())
        ancestor=widget
        while ancestor is not top and ancestor.parentWidget():
            parent=ancestor.parentWidget()
            visible=visible.intersected(QRect(parent.mapTo(top,QPoint(0,0)),parent.size()))
            ancestor=parent
        if visible.isEmpty():return 'clipped_by_parent'
        local=visible.center()
        hit=top.childAt(local)
        while hit:
            if hit is widget:return 'reachable'
            hit=hit.parentWidget()
        return 'covered'

    def inspect(self,root,path,reach=True):
        self.pump()
        names=self.names(root);state_count=0
        siblings={}
        for widget in root.findChildren(QWidget):
            if not isinstance(widget,(QLabel,QAbstractButton,QComboBox,QAbstractSpinBox,QLineEdit)):continue
            if not widget.isVisibleTo(root) or isinstance(widget,QLineEdit) and isinstance(widget.parentWidget(),(QComboBox,QAbstractSpinBox)):continue
            if widget.objectName().startswith('qt_'):continue
            siblings.setdefault(widget.parentWidget(),[]).append(widget)
        intersections={}
        for controls in siblings.values():
            for index,first in enumerate(controls):
                for second in controls[index+1:]:
                    overlap=first.geometry().adjusted(2,2,-2,-2).intersected(second.geometry().adjusted(2,2,-2,-2))
                    if not overlap.isEmpty():
                        intersections.setdefault(id(first),[]).append(names.get(id(second),type(second).__name__))
                        intersections.setdefault(id(second),[]).append(names.get(id(first),type(first).__name__))
        classes={type(w).__name__ for w in [root,*root.findChildren(QWidget)]
                 if type(w).__module__.startswith('ui.')}
        self.covered.update(classes)
        for index,widget in enumerate(root.findChildren(QWidget)):
            if not isinstance(widget,SEMANTIC):continue
            # Internal line editors/scrollbars/viewports are checked through
            # their public owner; otherwise the same control is counted twice.
            parent=widget.parentWidget()
            if isinstance(widget,QLineEdit) and isinstance(parent,(QComboBox,QAbstractSpinBox)):continue
            if widget.objectName().startswith('qt_scrollarea_'):continue
            if isinstance(widget,QHeaderView) or widget.objectName()=='qt_tableview_cornerbutton':continue
            control_name=names.get(id(widget),widget.objectName() or f'{type(widget).__name__}[{index}]')
            visibility_key=f'{type(root).__name__}/{control_name}'
            self.visibility[visibility_key]=self.visibility.get(visibility_key,False) or widget.isVisibleTo(root)
            if not widget.isVisibleTo(root):continue
            text=text_of(widget)
            record=dict(path=path,widget=control_name,
                        cls=type(widget).__name__,enabled=widget.isEnabled(),
                        rect=[widget.x(),widget.y(),QWidget.width(widget),QWidget.height(widget)],
                        text=text,issues=[])
            if id(widget) in intersections:
                record['overlap_with']=intersections[id(widget)]
                record['issues'].append('sibling_overlap')
            self.classes[record['cls']]+=1;state_count+=1;self.count+=1
            h,w=QWidget.height(widget),QWidget.width(widget)
            if h<=0 or w<=0:record['issues'].append('zero_size')
            if isinstance(widget,QAbstractButton) and text:
                plain=text.replace('&&','\x00').replace('&','').replace('\x00','&')
                needed=max((widget.fontMetrics().horizontalAdvance(line) for line in plain.splitlines()),default=0)
                if needed>w-4:record['issues'].append('button_text_width')
                lines=plain.splitlines() or ['']
                ink=max(widget.fontMetrics().tightBoundingRect(line).height() for line in lines)
                if ink+widget.fontMetrics().lineSpacing()*(len(lines)-1)>h:
                    record['issues'].append('button_text_height')
            if isinstance(widget,QLabel) and text and widget.textFormat()!=Qt.RichText and '<' not in text:
                content=widget.contentsRect();flags=Qt.TextWordWrap if widget.wordWrap() else Qt.TextSingleLine
                bounds=widget.fontMetrics().boundingRect(QRect(0,0,max(1,content.width()),100000),flags,text)
                if bounds.height()>content.height()+3:record['issues'].append('label_text_height')
                if not widget.wordWrap() and max((widget.fontMetrics().horizontalAdvance(t) for t in text.splitlines()),default=0)>content.width()+3:
                    if not widget.toolTip():record['issues'].append('label_text_width_no_tooltip')
            if isinstance(widget,QTabBar):
                record['tabs']=[dict(index=i,text=widget.tabText(i),tooltip=widget.tabToolTip(i),
                                    rect=[widget.tabRect(i).x(),widget.tabRect(i).y(),widget.tabRect(i).width(),widget.tabRect(i).height()]) for i in range(widget.count())]
            if isinstance(widget,QTableWidget):
                record['cells']=[dict(row=r,column=c,text=widget.item(r,c).text(),tooltip=widget.item(r,c).toolTip())
                                 for r in range(widget.rowCount()) for c in range(widget.columnCount()) if widget.item(r,c)]
            if isinstance(widget,INTERACTIVE) and reach and widget.isEnabled():
                record['hit_region']=self.reachable(widget)
                if record['hit_region'] not in ('reachable','hidden'):record['issues'].append(record['hit_region'])
            key=(path,record['widget'],record['cls'])
            self.seen.add(key)
            self.stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            if record['issues']:self.findings.append(record)
        self.states.append(dict(path=path,controls=state_count))
        if args.scale=='1' and not path.startswith('busy-disabled/'):
            digest=hashlib.sha256(path.encode()).hexdigest()[:12]
            root.grab().save(str(self.output/f'view_{digest}.png'))
            self.states[-1]['image']=f'view_{digest}.png'

    def tabs(self,widget,path):
        self.inspect(widget,path)
        containers=[widget] if isinstance(widget,QTabWidget) else widget.findChildren(QTabWidget)
        for tabs in containers:
            ancestor=None if tabs is widget else tabs.parentWidget();nested=False
            while ancestor and ancestor is not widget:
                if isinstance(ancestor,(QTabWidget,QStackedWidget)):nested=True;break
                ancestor=ancestor.parentWidget()
            if nested:continue
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index);self.pump()
                self.tabs(tabs.widget(index),path+'/'+tabs.tabText(index))

    def finish(self):
        self.stream.close()
        summary=dict(scale=args.scale,observations=self.count,states=len(self.states),
                     device_pixel_ratio=self.app.devicePixelRatio(),
                     classes=dict(self.classes),ui_classes=sorted(self.covered),
                     findings=len(self.findings),finding_types=dict(Counter(i for r in self.findings for i in r['issues'])),
                     state_inventory=self.states)
        (self.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.output/'findings.json').write_text(json.dumps(self.findings,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.output/'visibility.json').write_text(json.dumps(self.visibility,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in summary.items() if k not in ('state_inventory','classes')},ensure_ascii=True))


def main():
    from data.db_manager import DBManager
    from ui.main_window import MainWindow
    from ui.dialogs.startup_dialog import StartupCheckDialog
    from devices.rn_board import board_settings
    from ui.dialogs.step_dialog import StepDialog
    app=QApplication([]);app.setFont(QFont('Microsoft YaHei',10))
    for name in ('msyh.ttc','segoeui.ttf'):
        font=Path('C:/Windows/Fonts')/name
        if font.exists():QFontDatabase.addApplicationFont(str(font))
    app.setStyleSheet((ROOT/'resources/style.qss').read_text(encoding='utf-8'))
    audit=Audit(app)
    cfg={f'sim{i}_{field}':value for i in range(1,7) for field,value in (('enabled',True),('channels',24))}
    cfg['rn_boards']={s['key']:dict(enabled=True,ip=f'192.0.2.{i+1}') for i,s in enumerate(board_settings({}))}
    real_init=DBManager.__init__
    import socket
    def forbidden(*a,**k):raise AssertionError('Offline layout audit forbids external I/O')
    with tempfile.TemporaryDirectory() as temporary, \
         patch.object(DBManager,'__init__',lambda self,*a,**k:real_init(self,str(Path(temporary)/'audit.db'))), \
         patch.object(DBManager,'load_sys_config',lambda self:cfg), \
         patch.object(DBManager,'save_sys_config',lambda self,data:True), \
         patch.object(DBManager,'get_user_role',lambda *a:'admin'), \
         patch.object(StartupCheckDialog,'_start_check',lambda self:None), \
         patch.object(StartupCheckDialog,'exec',lambda self:QDialog.Accepted), \
         patch.object(QTimer,'start',lambda *a,**k:None), \
         patch.object(socket.socket,'connect',forbidden):
        window=MainWindow();window.current_user='ADMIN';window.apply_role_permissions()
        try:
            for size in ((1024,700),(1440,900)):
                window.resize(*size);window.show();audit.pump()
                audit.inspect(window,'window/'+str(size))
                for index in range(window.tabs.count()):
                    window.tabs.setCurrentIndex(index);audit.pump()
                    audit.tabs(window.tabs.widget(index),f'main/{size}/{window.tabs.tabText(index)}')
                window.tabs.setCurrentWidget(window.tab_debug_center)
                for index in range(window.tab_debug_center.pages.count()):
                    page=window.tab_debug_center.pages.widget(index)
                    window.tab_debug_center.pages.setCurrentIndex(index);audit.pump()
                    audit.tabs(page,f'device/{size}/{type(page).__name__}/{index}')
                # Reveal the network CAN table, including every cell widget.
                window.tab_debug_center.pages.setCurrentWidget(window.tab_pcan_fd)
                window.tab_pcan_fd.device_type.setCurrentIndex(1)
                audit.tabs(window.tab_pcan_fd,f'zlg/{size}')
            if not args.quick:
                window.resize(1024,700)
                window.tabs.setCurrentWidget(window.tab_debug_center)
                for index in range(window.tab_debug_center.pages.count()):
                    page=window.tab_debug_center.pages.widget(index)
                    window.tab_debug_center.pages.setCurrentIndex(index)
                    changed=[]
                    # Inject only runtime result fields, leaving captions alone.
                    # These are layout stress values, not fake protocol success.
                    for owner in [page,*page.findChildren(QWidget)]:
                        for name,label in vars(owner).items():
                            if not isinstance(label,QLabel) or not any(tag in name for tag in ('status','result','confirmation','reading','readback','audit_source','audit_sectors','lbl_idn','lbl_sn')):continue
                            if name=='lbl_status' and (hasattr(owner,'refresh_connection_state') or type(owner).__name__=='Easy320StandaloneTab'):continue
                            value=('请求电压=80.000V，电流=20.000A；回读电压=79.999V；输出状态未知；'
                                   '设备通信超时，请检查连接配置。')*3
                            if 'audit_source' in name:value='C:/firmware/'+'LongProjectName_'*16+'.hex'
                            changed.append((label,label.text()));label.setText(value)
                    audit.tabs(page,f'long-readback/{type(page).__name__}/{index}')
                    for label,text in changed:label.setText(text)
                # Mark synthetic driver flags only; never call connect/read/write.
                drivers=[getattr(window.device_manager,name,None) for name in
                         ('hv_source','dut_power','it6333b','ca550','easy320','opteeq',
                          'e2_rfp','pcan','dg2052','dmm34461','tbs2102b')]
                drivers+=window.device_manager.simulators+list(window.device_manager.rn_boards.values())
                original=[(d,d.is_connected) for d in drivers if d is not None]
                e2 = window.device_manager.e2_rfp
                e2_ready = e2.tool_ready
                for d,_ in original:
                    if d is e2:d.tool_ready=True
                    d.is_connected=True
                for index in range(window.tab_debug_center.pages.count()):
                    page=window.tab_debug_center.pages.widget(index)
                    window.tab_debug_center.pages.setCurrentIndex(index)
                    for view in [page,*page.findChildren(QWidget)]:
                        if hasattr(type(view),'refresh_connection_state'):view.refresh_connection_state()
                    if hasattr(page,'update_controls'):page.update_controls()
                    if hasattr(page,'show_relay_states') and page.relay_buttons:
                        page.show_relay_states([True]*len(page.relay_buttons))
                    audit.tabs(page,f'connected/{type(page).__name__}/{index}')
                    buttons=page.findChildren(QAbstractButton)
                    saved=[(b,b.isEnabled()) for b in buttons]
                    for b,_ in saved:b.setEnabled(False)
                    audit.inspect(page,f'busy-disabled/{type(page).__name__}/{index}',reach=False)
                    for b,enabled in saved:b.setEnabled(enabled)
                for d,value in original:d.is_connected=value
                e2.tool_ready = e2_ready
                # Device selector states that reveal conditional controls.
                window.tab_debug_center.pages.setCurrentWidget(window.tab_scope)
                for index in range(window.tab_scope.kind.count()):
                    window.tab_scope.kind.setCurrentIndex(index)
                    audit.tabs(window.tab_scope,'scope-trigger/'+window.tab_scope.kind.currentText())
                from ui.dialogs.login_dialog import LoginDialog
                from ui.dialogs.test_item_dialog import TestItemDialog
                from ui.dialogs.bulk_edit_dialog import BulkEditDialog
                from ui.dialogs.ai_barcode_dialog import AiBarcodeRuleDialog
                from ui.dialogs.ai_recipe_dialog import AiRecipeDialog
                from ui.dialogs.monitor_dialog import MonitorDialog
                from ui.dialogs.user_account_dialog import UserAccountDialog
                from ui.dialogs.flash_status_dialog import FlashStatusDialog
                from ui.tabs.debug_tab import SingleChannelDebugTab
                factories=[lambda:LoginDialog(window.db_manager),TestItemDialog,BulkEditDialog,AiBarcodeRuleDialog,
                    lambda:AiRecipeDialog(window.db_manager,'audit'),MonitorDialog,
                    lambda:UserAccountDialog(window.db_manager),FlashStatusDialog,
                    lambda:StartupCheckDialog(window.device_manager),SingleChannelDebugTab]
                for factory in factories:
                    dialog=factory();dialog.resize(800,600);dialog.show();audit.pump()
                    audit.tabs(dialog,'dialog/'+type(dialog).__name__)
                    if isinstance(dialog,TestItemDialog):
                        for index in range(dialog.type_combo.count()):
                            dialog.type_combo.setCurrentIndex(index)
                            audit.inspect(dialog,'test-item-mode/'+dialog.type_combo.currentText())
                    if isinstance(dialog,FlashStatusDialog):
                        for kind in ('APP','SG400','E2'):
                            for result in ('','PASS','FAIL','STOPPED'):
                                dialog.render_state(dict(kind=kind,active=not bool(result),result=result,
                                    started_at=time.monotonic(),project='C:/firmware/'+'long_project_'*20+'.opj',
                                    phase='执行中' if not result else '执行结束',percent=None if not result else 100))
                                audit.inspect(dialog,f'flash/{kind}/{result or "active"}')
                    dialog.hide();dialog.deleteLater();audit.pump()
                step=StepDialog(device_manager=window.device_manager);step.show()
                try:
                    for category in range(step.category_combo.count()):
                        step.category_combo.setCurrentIndex(category)
                        for group in range(step.sub_category_combo.count() if category==0 else 1):
                            if category==0:step.sub_category_combo.setCurrentIndex(group)
                            for device in range(step.device_combo.count()):
                                step.device_combo.setCurrentIndex(device)
                                for action in range(step.action_combo.count()):
                                    step.action_combo.setCurrentIndex(action);audit.pump()
                                    path='/'.join([step.category_combo.currentText(),step.sub_category_combo.currentText(),step.device_combo.currentText(),step.action_combo.currentText()])
                                    audit.tabs(step,'step/'+path)
                                    if step.eol_op.isVisibleTo(step):
                                        for op in range(step.eol_op.count()):
                                            step.eol_op.setCurrentIndex(op);audit.inspect(step,'eol/'+path+'/'+step.eol_op.currentText())
                                    if step.uds_extract_mode.isVisibleTo(step):
                                        step.uds_expected.setText(' '.join(f'{i:02X}' for i in range(32)))
                                        for mode in range(step.uds_extract_mode.count()):
                                            step.uds_extract_mode.setCurrentIndex(mode);audit.inspect(step,'uds-extract/'+path+'/'+step.uds_extract_mode.currentText())
                    step.hide()
                finally:step.deleteLater();audit.pump()
                for kind in (QInputDialog,QMessageBox,QFileDialog):
                    dialog=kind();dialog.setOption(QFileDialog.DontUseNativeDialog,True) if kind is QFileDialog else None
                    if kind is QMessageBox:
                        dialog.setText('设备未连接 / 操作未确认；检查配置和回读状态。'*8)
                        dialog.setStandardButtons(QMessageBox.Yes|QMessageBox.No)
                    dialog.show();audit.pump();audit.tabs(dialog,'standard/'+kind.__name__)
                    dialog.hide();dialog.deleteLater();audit.pump()
                def capture_inline(dialog):
                    dialog.show();audit.pump();audit.tabs(dialog,'inline/'+dialog.windowTitle())
                    dialog.hide();return QDialog.Rejected
                with patch.object(QDialog,'exec',capture_inline):
                    window.tab_overview.select_statistics_range()
                popup_sites=[]
                for source in (ROOT/'ui').rglob('*.py'):
                    tree=ast.parse(source.read_text(encoding='utf-8'))
                    for call in ast.walk(tree):
                        if not isinstance(call,ast.Call):continue
                        function=ast.unparse(call.func)
                        if function not in ('QMessageBox.warning','QMessageBox.critical','QMessageBox.information','QMessageBox.question',
                            'QInputDialog.getText','QFileDialog.getOpenFileName','QFileDialog.getSaveFileName','QFileDialog.getExistingDirectory'):continue
                        site=f'{source.relative_to(ROOT)}:{call.lineno}'
                        title=source_text(call.args[1]) if len(call.args)>1 else '提示'
                        message=source_text(call.args[2]) if len(call.args)>2 else '请检查参数'
                        if function.startswith('QMessageBox'):
                            dialog=QMessageBox();dialog.setWindowTitle(title);dialog.setText(message)
                            dialog.setStandardButtons(QMessageBox.Yes|QMessageBox.No if function.endswith('question') else QMessageBox.Ok)
                        elif function.startswith('QInputDialog'):
                            dialog=QInputDialog();dialog.setWindowTitle(title);dialog.setLabelText(message)
                            if '密码' in title or '密码' in message:dialog.setTextEchoMode(QLineEdit.Password)
                        else:
                            dialog=QFileDialog();dialog.setOption(QFileDialog.DontUseNativeDialog,True)
                            dialog.setWindowTitle(title);dialog.setDirectory(temporary)
                            if function.endswith('getExistingDirectory'):dialog.setFileMode(QFileDialog.Directory)
                            if function.endswith('getSaveFileName'):dialog.setAcceptMode(QFileDialog.AcceptSave)
                        dialog.show();audit.pump();audit.tabs(dialog,'popup-site/'+site+'/'+function)
                        popup_sites.append(dict(source=site,function=function,check='rendered-static-template-with-dynamic-placeholder'))
                        dialog.hide();dialog.deleteLater();audit.pump()
                (audit.output/'popup_sites.json').write_text(json.dumps(popup_sites,ensure_ascii=False,indent=2),encoding='utf-8')
                # Build the source-to-runtime class coverage, including classes
                # deliberately not reachable through the current navigation.
                sources=[]
                for source in (ROOT/'ui').rglob('*.py'):
                    tree=ast.parse(source.read_text(encoding='utf-8'))
                    for node in tree.body:
                        if isinstance(node,ast.ClassDef) and any(ast.unparse(b) in
                            ('QWidget','QDialog','QMainWindow','QFrame','QComboBox','BoardDebugTab','QTreeWidget','QLabel') for b in node.bases):
                            sources.append(dict(file=str(source.relative_to(ROOT)),line=node.lineno,name=node.name,
                                runtime_observed=node.name in audit.covered))
                (audit.output/'source_coverage.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            # Stop workers and close only the temporary database; synthetic
            # drivers are offline, and no hardware shutdown is requested.
            from ui.async_shutdown import request_ui_shutdown,threads_finished
            window.tab_overview.begin_shutdown();threads=request_ui_shutdown(window)
            end=time.monotonic()+10
            while not threads_finished(threads) and time.monotonic()<end:audit.pump();time.sleep(.005)
            assert threads_finished(threads)
            window.db_manager.close();window.hide();window.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    audit.finish()


if __name__=='__main__':main()
