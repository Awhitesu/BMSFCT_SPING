"""Exercise the complete DBC example on an isolated virtual CAN bus only."""
import argparse
import copy
import json
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import can
import xlrd
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from core.dbc_signals import DBCMonitor,database,decode_step,encode_step,validate_recipe_dbc
from core.engine import TestEngine as Engine
from core.read_until import settings,attach
from data.db_manager import DBManager
from devices.pcan_fd_driver import PCANFDDriver
from tools.create_dbc_example import create


def run(cycles,output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    recipe,_,_=create(output/'example')
    factory=can.Bus;bus_name='dbc-sandbox-'+uuid.uuid4().hex
    sender=factory(interface='virtual',channel=bus_name,receive_own_messages=False)
    driver=PCANFDDriver(fd_enabled=False);monitor=DBCMonitor()
    db=DBManager(str(output/'sandbox.db'))
    db.load_sys_config=lambda:dict(report_root_dir=str(output/'reports'),mes_report_dir=str(output/'mes'))
    manager=SimpleNamespace(pcan=driver,dbc_monitor=monitor,safe_shutdown_outputs=lambda *args,**kwargs:True)
    engine=Engine(manager,db);finished=[];events=[];receive_count=[0]
    engine.channel_test_finished.connect(lambda cid,ok:finished.append(ok))
    engine.log_message.connect(lambda cid,line:events.append(line))
    driver.on_message_received=lambda frame:receive_count.__setitem__(0,receive_count[0]+1)
    heartbeat=QTimer();heartbeat.setInterval(10);heartbeats=[]
    heartbeat.timeout.connect(lambda:heartbeats.append(time.perf_counter()));heartbeat.start()
    def pump(predicate,timeout=10):
        deadline=time.perf_counter()+timeout
        while not predicate():
            if time.perf_counter()>=deadline:raise TimeoutError('Sandbox engine did not complete')
            app.processEvents();time.sleep(.001)
    results=[];started=time.perf_counter();producer_stop=threading.Event();producer=None;batch=time.time_ns()
    modes=['PASS','VOLTAGE_NG','FAULT_NG','NO_DATA','SHORT_FRAME','RECONNECT_PASS','STOP']
    try:
        with patch('can.Bus',side_effect=lambda **kwargs:factory(interface='virtual',channel=bus_name,receive_own_messages=False)):
            if not driver.connect():raise RuntimeError(driver.last_error)
            for index in range(cycles):
                mode=modes[index%len(modes)];case=copy.deepcopy(recipe)
                case['name']=f'DBC_SANDBOX_{index:04d}_{mode}'
                for item in case['items']:
                    for step in item['sub_steps']:
                        if step['type']!='CANDBC交互':continue
                        c=decode_step(step['params']);c.update(timeout=.3,interval=.01,max_age=.2)
                        rw=settings(step['params'])
                        if rw:rw.update(timeout=.3,interval=.01)
                        step['params']=attach(encode_step(c)+' / PCH:1',rw)
                validate_recipe_dbc(case)
                if mode=='RECONNECT_PASS':
                    if not driver.disconnect() or not driver.connect():raise RuntimeError('Virtual CAN reconnect failed')
                monitor.configure(case['dbc'],driver,context_key=case['name'])
                messages=database(case['dbc'])
                producer_stop=threading.Event()
                def transmit():
                    counter=0
                    while not producer_stop.is_set():
                        counter=(counter+1)%256
                        for name,values in [
                            ('BMS_Status',dict(PackVoltage=400 if mode=='VOLTAGE_NG' else 360,PackCurrent=.2,SOC=68.8,
                                BmsState=1,FaultCount=1 if mode=='FAULT_NG' else 0,AliveCounter=counter)),
                            ('BMS_CellStats',dict(MinCellVoltage=3.60,MaxCellVoltage=3.62,MaxTemperature=35,MinTemperature=20))]:
                            message=messages.get_message_by_name(name)
                            data=b'\x00' if mode=='SHORT_FRAME' else message.encode(values)
                            sender.send(can.Message(arbitration_id=message.frame_id,is_extended_id=False,data=data))
                        producer_stop.wait(.01)
                producer=None
                if mode not in ('NO_DATA','STOP'):
                    producer=threading.Thread(target=transmit,daemon=True);producer.start()
                events.clear();finished.clear();sn=f'DBC_DEMO_{batch}{index:06d}'
                tid=db.start_traced_test(1,'CH1',sn,case,operator='sandbox')
                begin=time.perf_counter()
                if not engine.start_channel_test(1,case['items'],test_id=tid,sn_barcode=sn,recipe_name=case['name']):raise RuntimeError('Engine refused simulation')
                if mode=='STOP':QTimer.singleShot(40,lambda:engine.stop_test(1))
                pump(lambda:not engine.workers and not engine.threads)
                producer_stop.set()
                if producer:producer.join(1)
                with sqlite3.connect(db.db_path) as conn:
                    actual=conn.execute('SELECT result FROM test_main WHERE test_id=?',(tid,)).fetchone()[0]
                    values=conn.execute('SELECT item_name,measured_value_raw,result FROM test_items_results WHERE test_id=? ORDER BY id',(tid,)).fetchall()
                expected='STOPPED' if mode=='STOP' else ('PASS' if mode in ('PASS','RECONNECT_PASS') else 'NG')
                reports=list((output/'reports').rglob(sn+'_*.xls'))
                if actual!=expected or len(reports)!=1:raise AssertionError(f'{mode}: expected {expected}, got {actual}; reports={reports}')
                report=reports[0];mes=output/'mes'/report.name
                if mes.read_bytes()!=report.read_bytes():raise AssertionError('MES copy differs')
                book=xlrd.open_workbook(str(report));sheet=book.sheet_by_index(0)
                if expected=='PASS':
                    if len(values)!=10 or sheet.nrows<28:raise AssertionError('Missing example test result')
                    if abs(float(values[-1][1])-.02)>.00001:raise AssertionError('DBC variables did not feed the formula')
                    if values[-2][1]!='Ready':raise AssertionError('DBC enum text is incorrect')
                elapsed=time.perf_counter()-begin
                result=dict(case=index+1,mode=mode,expected=expected,actual=actual,passed=True,seconds=round(elapsed,4),
                    result_count=len(values),report=str(report),mes=str(mes))
                results.append(result)
                (output/f'case_{index+1:04d}_{mode}.log').write_text('\n'.join(events),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        producer_stop.set()
        if producer:producer.join(1)
        engine.stop_all()
        try:pump(lambda:not engine.workers and not engine.threads)
        finally:
            heartbeat.stop();monitor.stop();driver.disconnect();sender.shutdown();db.close()
    gaps=[b-a for a,b in zip(heartbeats,heartbeats[1:])]
    summary=dict(cases=len(results),all_passed=all(r['passed'] for r in results),
        elapsed_seconds=round(time.perf_counter()-started,3),received_frames=receive_count[0],
        maximum_gui_heartbeat_gap_ms=round(max(gaps,default=0)*1000,3),results=results,
        scope='Isolated python-can virtual bus; no physical CAN device or product outputs used.')
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SUMMARY '+json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False),flush=True)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cycles',type=int,default=28)
    parser.add_argument('--output',default='logs/dbc_sandbox')
    args=parser.parse_args()
    if not 1<=args.cycles<=10000:parser.error('cycles must be 1–10000')
    run(args.cycles,args.output)
