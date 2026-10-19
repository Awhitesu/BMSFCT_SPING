"""Synthetic DBC load/UI probe. No hardware, sockets or physical CAN access."""
import argparse
import faulthandler
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication
from core.dbc_signals import DBCMonitor,database
from ui.widgets.dbc_forms import DBCMonitorTab,DBCStepForm


class MemoryDriver:
    channel='PCAN_USBBUS1';is_connected=True
    def __init__(self):self.bus=object();self.listeners=set()
    def add_message_listener(self,callback):self.listeners.add(callback)
    def remove_message_listener(self,callback):self.listeners.discard(callback)


def run(output,messages=1000,seconds=8):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    diagnostic=(output/'diagnostic.log').open('w',encoding='utf-8')
    faulthandler.dump_traceback_later(15,repeat=True,file=diagnostic)
    def stage(message):print(f'{time.perf_counter():.3f} {message}',flush=True)
    parts=['VERSION "Synthetic performance probe"','NS_ :','BS_:','BU_: BMS']
    for i in range(messages):
        parts.append(f'BO_ {256+i} Msg{i}: 8 BMS')
        for j in range(8):parts.append(f' SG_ Sig{j} : {j*8}|8@1+ (1,0) [0|255] "" BMS')
    text='\n'.join(parts)+'\n'
    asset=dict(name='synthetic.dbc',text=text,sha256=hashlib.sha256(text.encode()).hexdigest())
    stage('parse start')
    parse_start=time.perf_counter();database(asset);parse_seconds=time.perf_counter()-parse_start
    stage('parse done')
    app=QApplication.instance() or QApplication([])
    driver=MemoryDriver();monitor=DBCMonitor();monitor.configure(asset,driver,context_key='Synthetic load only')
    manager=SimpleNamespace(dbc_monitor=monitor,pcan=driver)
    page=DBCMonitorTab(manager);page.setAttribute(Qt.WA_DontShowOnScreen);page.resize(1400,800)
    form=DBCStepForm(asset);form.setAttribute(Qt.WA_DontShowOnScreen)
    stage('monitor show start')
    create_start=time.perf_counter();page.show();app.processEvents();create_ms=(time.perf_counter()-create_start)*1000
    stage('monitor show done')
    form.ensure_loaded()
    deadline=time.perf_counter()+5
    while not form._loaded:
        if time.perf_counter()>deadline:raise TimeoutError('Signal selector did not load')
        app.processEvents();time.sleep(.001)
    stage('selector loaded')
    if form.signal.count()!=messages*8:raise AssertionError('Missing selector signals')
    page.page.setValue(page.page.maximum());app.processEvents()
    stage('last page done')
    if page._rows[-1]['message']!=f'Msg{messages-1}':raise AssertionError('Pagination lost final signals')
    page.search.setText(f'Msg{messages-1}.Sig7');app.processEvents()
    stage('filter done')
    if len(page._rows)!=1:raise AssertionError('Qualified signal search failed')
    page.search.clear();page.page.setValue(1);app.processEvents()
    stage('warmup done')
    gaps=[];last=[time.perf_counter()];count=[0];callback_max=[0.0];callback_total=[0.0]
    timer=QTimer();timer.setInterval(10)
    def tick():
        now=time.perf_counter();gaps.append(now-last[0]);last[0]=now
    timer.timeout.connect(tick);timer.start()
    stopped=threading.Event()
    def send():
        number=0
        while not stopped.is_set():
            for _ in range(40):
                number+=1
                frame=dict(direction='RX',can_id=256+number%messages,is_extended_id=False,data=bytes([number%256])*8,interface=driver.channel)
                begin=time.perf_counter()
                for callback in tuple(driver.listeners):callback(frame)
                elapsed=time.perf_counter()-begin;callback_max[0]=max(callback_max[0],elapsed);callback_total[0]+=elapsed;count[0]+=1
            stopped.wait(.005)
    thread=threading.Thread(target=send,daemon=True);thread.start();begin=time.perf_counter()
    try:
        while time.perf_counter()-begin<seconds:
            app.processEvents();time.sleep(.001)
        stage('load done')
        # Allow the bounded latest-frame queue to settle, without generating new input.
        stopped.set();thread.join(1)
        settle=time.perf_counter()+1
        while time.perf_counter()<settle:
            app.processEvents();time.sleep(.001)
        snapshot=monitor.snapshot()
        if len(snapshot)!=messages*8:raise AssertionError(f'Expected {messages*8} cached signals, found {len(snapshot)}')
        # Capture/render work is measured separately from the streaming heartbeat.
        timer.stop()
        page.grab().save(str(output/'monitor.png'))
        stage('monitor captured')
        form.resize(1100,600);selector_start=time.perf_counter();form.show();app.processEvents()
        selector_render_ms=(time.perf_counter()-selector_start)*1000
        form.grab().save(str(output/'selector.png'))
        result=dict(messages=messages,signals=messages*8,parse_seconds=round(parse_seconds,4),
            monitor_initial_render_ms=round(create_ms,3),duration_seconds=seconds,received_frames=count[0],
            selector_initial_render_ms=round(selector_render_ms,3),
            approximate_input_fps=round(count[0]/seconds,1),mean_subscription_callback_us=round(callback_total[0]/max(1,count[0])*1e6,2),
            max_subscription_callback_ms=round(callback_max[0]*1000,3),
            maximum_gui_heartbeat_gap_ms=round(max(gaps,default=0)*1000,3),
            p99_gui_heartbeat_gap_ms=round(sorted(gaps)[int(len(gaps)*.99)]*1000,3) if gaps else 0,
            cached_signals=len(snapshot),page_count=page.page.maximum(),
            scope='Synthetic in-memory CAN subscription input; platform-specific load measurements, not a hardware throughput guarantee.')
        (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    finally:
        stopped.set();thread.join(1);timer.stop();page.close();form.close();monitor.stop()
        faulthandler.cancel_dump_traceback_later();diagnostic.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='logs/dbc_performance')
    parser.add_argument('--messages',type=int,default=1000);parser.add_argument('--seconds',type=float,default=8)
    args=parser.parse_args()
    if not 1<=args.messages<=1700 or not .1<=args.seconds<=3600:parser.error('Invalid probe size or duration')
    run(args.output,args.messages,args.seconds)
