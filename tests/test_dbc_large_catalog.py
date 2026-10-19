import hashlib
import time
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from core.dbc_signals import DBCMonitor
from ui.widgets.dbc_forms import DBCMonitorTab,DBCStepForm
from test_dbc_signals import Driver

app=QApplication.instance() or QApplication([])


def test_all_8000_signals_are_selectable_searchable_and_page_without_freezing():
    parts=['VERSION ""','NS_ :','BS_:','BU_: BMS']
    for i in range(1000):
        parts.append(f'BO_ {256+i} Msg{i}: 8 BMS')
        for j in range(8):parts.append(f' SG_ Sig{j} : {j*8}|8@1+ (1,0) [0|255] "" BMS')
    text='\n'.join(parts)
    asset=dict(name='large.dbc',text=text,sha256=hashlib.sha256(text.encode()).hexdigest())
    driver=Driver();monitor=DBCMonitor();monitor.configure(asset,driver)
    page=DBCMonitorTab(SimpleNamespace(dbc_monitor=monitor,pcan=driver))
    page.setAttribute(Qt.WA_DontShowOnScreen)
    form=DBCStepForm(asset)
    try:
        page.show();app.processEvents();assert page.page.maximum()==16
        begin=time.perf_counter();page.page.setValue(16);app.processEvents()
        assert time.perf_counter()-begin<2 # Prior ResizeToContents replacement took minutes.
        assert page._rows[-1]['label'].startswith('Msg999.Sig7')
        page.search.setText('Msg999.Sig7');assert len(page._rows)==1
        form.ensure_loaded();deadline=time.perf_counter()+5
        while not form._loaded:
            assert time.perf_counter()<deadline
            app.processEvents();time.sleep(.001)
        assert form.signal.count()==8000
        form.signal.setCurrentIndex(7999)
        assert form.config()['signal']=='Sig7' and form.config()['message']=='Msg999'
    finally:page.close();form.close();monitor.stop()
