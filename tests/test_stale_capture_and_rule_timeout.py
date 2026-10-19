import json
import threading
import time
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from ui.dialogs.step_dialog import StepDialog
from core.recipe_matching import match_recipe_files

app=QApplication.instance() or QApplication([])


@pytest.mark.parametrize('uds,field,value',[(False,'c_wait_id','0x674'),(False,'c_data','01'),
                                         (True,'uds_rx_id','0x7E9'),(True,'uds_tx_id','0x7E1')])
def test_capture_with_changed_parameters_never_overwrites_sample(monkeypatch,uds,field,value):
    entered=threading.Event();release=threading.Event();notes=[]
    def response(*args,**kwargs):
        entered.set();release.wait(2)
        return SimpleNamespace(positive=True,raw=b'\x62\x01') if uds else {'data':b'\x01\x02'}
    driver=SimpleNamespace(is_connected=True,channel='PCAN_USBBUS1',bus=object(),
        send_and_wait_response=response,create_uds_client=lambda **kwargs:SimpleNamespace(request=response))
    page=StepDialog(device_manager=SimpleNamespace(pcan=driver))
    monkeypatch.setattr(QMessageBox,'information',lambda *args:notes.append(args))
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:pytest.fail(str(args)))
    try:
        page.uds_request.setText('22 F1 90')
        page.can_parser.sample.setText('AA BB')
        page._uds_response_preview='AA BB'
        if uds:page._on_live_sniff_clicked()
        else:page._capture_can_response()
        assert entered.wait(1)
        getattr(page,field).setText(value);release.set()
        task=page._uds_capture_task if uds else page._can_capture_task
        deadline=time.monotonic()+2
        while task.isRunning() and time.monotonic()<deadline:app.processEvents();time.sleep(.002)
        assert not task.isRunning() and notes
        assert page.can_parser.sample.text()=='AA BB' and page._uds_response_preview=='AA BB'
    finally:
        release.set()
        for name in ('_can_capture_task','_uds_capture_task'):
            if hasattr(page,name):getattr(page,name).shutdown()
        page.close()


def test_pathological_regex_is_bounded_and_never_counts_as_a_match(tmp_path):
    (tmp_path/'bad.json').write_text(json.dumps(dict(name='bad',sn_regex='^(a|aa)+$')),encoding='utf-8')
    start=time.perf_counter()
    matches,errors=match_recipe_files(tmp_path,[(0,'bad.json')],'a'*200+'!')
    assert time.perf_counter()-start<1
    assert not matches and any('超时' in error for error in errors)


def test_matching_keeps_all_overlapping_rules_for_explicit_rejection(tmp_path):
    for name,rule in [('A','^ABC[0-9]+$'),('B','^.+$')]:
        (tmp_path/f'{name}.json').write_text(json.dumps(dict(name=name,sn_regex=rule)),encoding='utf-8')
    matches,errors=match_recipe_files(tmp_path,[(0,'A.json'),(1,'B.json')],'ABC123')
    assert not errors and len(matches)==2
