"""Actual python-can virtual buses + production receive loop + engine + XLS/MES."""
import sqlite3
import threading
import time
import uuid
from types import SimpleNamespace
import can
import pytest
import xlrd
from PySide6.QtWidgets import QApplication
from core.dbc_signals import DBCMonitor,database,encode_step
from core.engine import TestEngine as Engine
from core.read_until import attach
from data.db_manager import DBManager
from devices.pcan_fd_driver import PCANFDDriver
from test_dbc_signals import asset,config

app=QApplication.instance() or QApplication([])


def pump(predicate,timeout=8):
    deadline=time.perf_counter()+timeout
    while not predicate():
        assert time.perf_counter()<deadline,'engine did not finish within timeout'
        app.processEvents();time.sleep(.002)


@pytest.mark.parametrize('fault,expected',[(False,'PASS'),(True,'NG')])
def test_virtual_bms_to_engine_to_report_and_separate_mes(asset,tmp_path,monkeypatch,fault,expected):
    factory=can.Bus;bus_name='dbc-test-'+uuid.uuid4().hex
    producer=factory(interface='virtual',channel=bus_name,receive_own_messages=False)
    monkeypatch.setattr(can,'Bus',lambda **kwargs:factory(interface='virtual',channel=bus_name,receive_own_messages=False))
    driver=PCANFDDriver(fd_enabled=False);monitor=DBCMonitor();stop=threading.Event()
    db=DBManager(str(tmp_path/'test.db'))
    db.load_sys_config=lambda:dict(report_root_dir=str(tmp_path/'archive'),mes_report_dir=str(tmp_path/'mes'))
    manager=SimpleNamespace(pcan=driver,dbc_monitor=monitor,safe_shutdown_outputs=lambda *args,**kwargs:True)
    engine=Engine(manager,db);finished=[];events=[];thread=None
    engine.channel_test_finished.connect(lambda cid,ok:finished.append(ok))
    engine.log_message.connect(lambda cid,line:events.append(line))
    extra=[];driver.add_message_listener(extra.append)
    try:
        assert driver.connect();monitor.configure(asset,driver)
        message=database(asset).get_message_by_name('Status')
        def send():
            while not stop.is_set():
                data=message.encode(dict(Voltage=12.0 if not fault else 30.0,Temp=-12,State=1))
                producer.send(can.Message(arbitration_id=256,data=data,is_extended_id=False))
                stop.wait(.02)
        thread=threading.Thread(target=send,daemon=True);thread.start()
        items=[]
        for name,signal,lower,upper,output,standard in [
            ('电压','Voltage','11','13','value','数值'),
            ('温度','Temp','-15','0','value','数值'),
            ('运行状态','State','On','--','choice','字符串')]:
            c=config(asset,signal,output=output);c['new_frame']=True;c['timeout']=.5;c['variable']='BMS_'+signal
            params=encode_step(c)+' / PCH:1'
            if signal=='Voltage' and not fault:
                params=attach(params,dict(min=11,max=13,timeout=2,interval=.01,consecutive=2))
            items.append(dict(name=name,min=lower,max=upper,standard_type=standard,unit='V' if signal=='Voltage' else '',
                strategy='任何NG停止',sub_steps=[dict(type='CANDBC交互',device='CANDBC 交互',action='读取DBC信号',params=params,is_judgment=True)]))
        recipe=dict(name='DBC_VIRTUAL',dbc=asset,can_config=dict(channel='PCAN_USBBUS1'),items=items)
        test_id=db.start_traced_test(1,'CH1','DBC_SAMPLE',recipe,operator='simulation')
        assert engine.start_channel_test(1,items,test_id=test_id,sn_barcode='DBC_SAMPLE',recipe_name=recipe['name'])
        pump(lambda:bool(finished) and not engine.workers and not engine.threads)
        assert finished==[not fault]
        with sqlite3.connect(db.db_path) as conn:
            result=conn.execute('SELECT result FROM test_main WHERE test_id=?',(test_id,)).fetchone()[0]
            saved=conn.execute('SELECT measured_value_raw FROM test_items_results WHERE test_id=? ORDER BY id',(test_id,)).fetchall()
        assert result==expected
        assert saved[0][0] in ('30.0','12.0')
        assert len(saved)==(1 if fault else 3)
        if not fault:assert saved[-1][0]=='On'
        reports=list((tmp_path/'archive').rglob('*.xls'));assert len(reports)==1
        report=reports[0];assert (tmp_path/'mes'/report.name).read_bytes()==report.read_bytes()
        sheet=xlrd.open_workbook(str(report)).sheet_by_index(0)
        assert sheet.cell_value(18,0)=='电压'
        assert float(sheet.cell_value(18,1))==(30 if fault else 12)
        assert len(extra)>0 and not driver.msg_queue.empty() # DBC never consumes other readers' queues.
        assert any('DBC输出' in line for line in events)
    finally:
        engine.stop_all();pump(lambda:not engine.workers and not engine.threads)
        stop.set()
        if thread:thread.join(1)
        monitor.stop();driver.disconnect();producer.shutdown();db.close()
