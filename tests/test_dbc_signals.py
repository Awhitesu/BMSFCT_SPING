import copy
import threading
import time
from types import SimpleNamespace
import pytest
from core.dbc_signals import (import_dbc,database,catalog,DBCMonitor,execute_read,
    encode_step,decode_step,validate_recipe_dbc)

DBC='''VERSION ""
NS_ :
BS_:
BU_: BMS
BO_ 256 Status: 8 BMS
 SG_ Voltage : 0|16@1+ (0.1,0) [0|6553.5] "V" BMS
 SG_ Temp : 16|8@1- (1,0) [-128|127] "degC" BMS
 SG_ State : 24|8@1+ (1,0) [0|255] "" BMS
BO_ 257 BigEndian: 8 BMS
 SG_ Current : 7|16@0- (0.01,-1) [-328|327] "A" BMS
BO_ 258 Multiplex: 8 BMS
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" BMS
 SG_ A m0 : 8|8@1+ (1,0) [0|255] "" BMS
 SG_ B m1 : 8|8@1+ (1,0) [0|255] "" BMS
BO_ 259 LongFrame: 12 BMS
 SG_ LongValue : 64|32@1+ (1,0) [0|4294967295] "" BMS
BO_ 2147483904 ExtStatus: 8 BMS
 SG_ Voltage : 0|16@1+ (1,0) [0|65535] "V" BMS
VAL_ 256 State 0 "Off" 1 "On";
'''


class Driver:
    channel='PCAN_USBBUS1'
    is_connected=True
    def __init__(self):self.bus=object();self.listeners=set()
    def add_message_listener(self,callback):self.listeners.add(callback)
    def remove_message_listener(self,callback):self.listeners.discard(callback)
    def emit(self,ident,data,interface=None,extended=False,**kwargs):
        frame=dict(can_id=ident,data=data,interface=interface or self.channel,is_extended_id=extended,direction='RX')
        frame.update(kwargs)
        for callback in tuple(self.listeners):callback(frame)


@pytest.fixture
def asset(tmp_path):
    path=tmp_path/'BMS.dbc';path.write_text(DBC,encoding='utf-8')
    return import_dbc(path)


@pytest.fixture
def monitored(asset):
    driver=Driver();monitor=DBCMonitor();monitor.configure(asset,driver)
    yield monitor,driver
    monitor.stop()
    assert not driver.listeners


def until(predicate):
    deadline=time.monotonic()+1
    while not predicate():
        assert time.monotonic()<deadline
        time.sleep(.005)


def config(asset,signal='Voltage',message='Status',**kwargs):
    row=next(r for r in catalog(asset) if r['signal']==signal and r['message']==message)
    return decode_step(encode_step(dict(row,sha256=asset['sha256'],timeout=.2,interval=.01,max_age=1,new_frame=False,**kwargs)))


def test_endianness_signed_scaling_enum_multiplex_fd_and_id_format(asset,monitored):
    monitor,driver=monitored;db=database(asset)
    for name,data in [('Status',dict(Voltage=321.5,Temp=-12,State=1)),('BigEndian',dict(Current=-2.5)),
                      ('Multiplex',dict(Mux=1,B=42)),('LongFrame',dict(LongValue=123456)),('ExtStatus',dict(Voltage=77))]:
        message=db.get_message_by_name(name)
        driver.emit(message.frame_id,message.encode(data),extended=message.is_extended_frame)
    until(lambda:len(monitor.snapshot())==8)
    assert monitor.read(config(asset),'PCAN_USBBUS1')['value']==321.5
    assert monitor.read(config(asset,'Temp'),'PCAN_USBBUS1')['value']==-12
    assert monitor.read(config(asset,'State'),'PCAN_USBBUS1')['choice']=='On'
    assert monitor.read(config(asset,'Current','BigEndian'),'PCAN_USBBUS1')['value']==-2.5
    assert monitor.read(config(asset,'B','Multiplex'),'PCAN_USBBUS1')['value']==42
    assert monitor.read(config(asset,'LongValue','LongFrame'),'PCAN_USBBUS1')['value']==123456
    assert monitor.read(config(asset,message='ExtStatus'),'PCAN_USBBUS1')['value']==77
    with pytest.raises(ValueError):monitor.read(config(asset,'A','Multiplex'),'PCAN_USBBUS1')


def test_tx_remote_bad_frames_do_not_become_valid_values(asset,monitored):
    monitor,driver=monitored
    driver.emit(256,b'\x01'*8,direction='TX');driver.emit(256,b'\x01'*8,is_remote_frame=True)
    assert not monitor.snapshot()
    driver.emit(256,b'\x01'*8);until(lambda:bool(monitor.snapshot()))
    driver.emit(256,b'\x01');until(lambda:bool(monitor.last_error))
    assert not monitor.snapshot()


def test_channels_and_reconnect_are_isolated(asset,monitored):
    monitor,driver=monitored;driver.channel='ZLG_CAN1'
    driver.emit(256,b'\x0a\x00'+b'\x00'*6,'ZLG_CAN1')
    driver.emit(256,b'\x14\x00'+b'\x00'*6,'ZLG_CAN2')
    until(lambda:len(monitor.snapshot())==6)
    assert monitor.read(config(asset),'ZLG_CAN1')['value']==1
    assert monitor.read(config(asset),'ZLG_CAN2')['value']==2
    driver.bus=object();assert not monitor.snapshot()
    driver.emit(256,b'\x1e\x00'+b'\x00'*6,'ZLG_CAN1');until(lambda:len(monitor.snapshot())==3)
    with pytest.raises(ValueError):monitor.read(config(asset),'ZLG_CAN2')


def test_freshness_new_frames_cancel_and_early_range(asset,monitored):
    monitor,driver=monitored;c=config(asset)
    driver.emit(256,b'\x64\x00'+b'\x00'*6);until(lambda:bool(monitor.snapshot()))
    event=threading.Event()
    assert execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None)==(True,10)
    c['new_frame']=True;c['timeout']=.05
    with pytest.raises(RuntimeError,match='超时'):execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None)
    event.set()
    with pytest.raises(RuntimeError,match='取消'):execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None)
    event.clear();c['timeout']=1
    threading.Timer(.04,lambda:driver.emit(256,b'\x78\x00'+b'\x00'*6)).start()
    start=time.monotonic()
    assert execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None,dict(min=11,max=13,timeout=1,interval=.01,consecutive=1))==(True,12)
    assert time.monotonic()-start<.5
    c['new_frame']=False;c['max_age']=.01;time.sleep(.02)
    with pytest.raises(ValueError,match='过期'):monitor.read(c,driver.channel)


def test_recipe_validation_and_disabled_service(asset,monitored):
    monitor,driver=monitored;c=config(asset)
    recipe=dict(can_config=dict(channel='PCAN_USBBUS1'),dbc=asset,items=[dict(name='V',sub_steps=[dict(type='CANDBC交互',params=encode_step(c))])])
    validate_recipe_dbc(recipe)
    invalid=copy.deepcopy(recipe);invalid['dbc']=None
    with pytest.raises(ValueError):validate_recipe_dbc(invalid)
    invalid=copy.deepcopy(recipe);invalid['can_config']={}
    with pytest.raises(ValueError):validate_recipe_dbc(invalid)
    validate_recipe_dbc(dict(can_config={},items=[]))
    monitor.configure(None,driver);assert monitor.asset is None and not driver.listeners
    invalid=copy.deepcopy(asset);invalid['text']+='changed'
    with pytest.raises(ValueError,match='校验'):database(invalid)


def test_identical_dbc_in_another_project_does_not_reuse_cache(asset,monitored):
    monitor,driver=monitored
    monitor.configure(asset,driver,context_key='Project A')
    driver.emit(256,b'\x01'*8);until(lambda:bool(monitor.snapshot()))
    old_callback=next(iter(driver.listeners))
    monitor.configure(asset,driver,context_key='Project B')
    assert not monitor.snapshot() and monitor.context_key=='Project B'
    old_callback(dict(can_id=256,data=b'\x02'*8,direction='RX',interface=driver.channel))
    assert not monitor.snapshot() and len(driver.listeners)==1


def test_slow_decoder_does_not_block_can_callback_or_grow_queue(asset,monitored,monkeypatch):
    monitor,driver=monitored
    message=database(asset).get_message_by_name('Status');original=message.decode
    entered=threading.Event()
    def slow(*args,**kwargs):
        entered.set();time.sleep(.05);return original(*args,**kwargs)
    monkeypatch.setattr(message,'decode',slow)
    driver.emit(256,b'\x00'*8);assert entered.wait(1)
    begin=time.perf_counter()
    for value in range(2000):driver.emit(256,value.to_bytes(2,'little')+b'\x00'*6)
    assert time.perf_counter()-begin<.1
    with monitor._condition:assert len(monitor._pending)<=1
    until(lambda:monitor.snapshot().get((driver.channel,256,False,'Voltage'),{}).get('value')==199.9)


def test_nan_float_invalidates_previous_value(tmp_path):
    import struct
    text='''VERSION ""
NS_ :
BS_:
BU_: BMS
BO_ 264 FloatData: 4 BMS
 SG_ FloatValue : 0|32@1+ (1,0) [0|100] "V" BMS
SIG_VALTYPE_ 264 FloatValue : 1;
'''
    path=tmp_path/'float.dbc';path.write_text(text,encoding='utf-8');asset=import_dbc(path)
    driver=Driver();monitor=DBCMonitor();monitor.configure(asset,driver)
    try:
        driver.emit(264,struct.pack('<f',1.5));until(lambda:bool(monitor.snapshot()))
        driver.emit(264,struct.pack('<f',float('nan')))
        until(lambda:not monitor.snapshot())
    finally:monitor.stop()


def test_concurrent_configure_and_stop_do_not_leak_callbacks_or_threads(asset):
    driver=Driver();monitor=DBCMonitor();errors=[]
    def configure():
        try:
            for i in range(40):monitor.configure(asset,driver,context_key=str(i))
        except Exception as exc:errors.append(exc)
    def stop():
        try:
            for _ in range(40):monitor.stop()
        except Exception as exc:errors.append(exc)
    one=threading.Thread(target=configure);two=threading.Thread(target=stop)
    one.start();two.start();one.join(5);two.join(5);monitor.stop()
    assert not one.is_alive() and not two.is_alive() and not errors
    assert not driver.listeners and monitor._thread is None
