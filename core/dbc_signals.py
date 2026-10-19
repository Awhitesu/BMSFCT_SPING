"""Recipe-owned DBC assets, bounded background decoding and fresh signal reads."""
import base64
import hashlib
import json
import math
import threading
import time
from functools import lru_cache
from pathlib import Path

MAX_BYTES=10*1024*1024


def import_dbc(path):
    path=Path(path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('DBC文件超过10MB')
    data=path.read_bytes()
    for encoding in ('utf-8-sig','gb18030','cp1252'):
        try:text=data.decode(encoding);break
        except UnicodeDecodeError:continue
    else:raise ValueError('无法识别DBC文本编码')
    asset=dict(name=path.name,text=text,sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    database(asset)
    return asset


def database(asset):
    if not isinstance(asset,dict) or not isinstance(asset.get('text'),str):raise ValueError('配方DBC内容缺失')
    text=asset['text']
    if len(text.encode('utf-8'))>MAX_BYTES:raise ValueError('DBC内容超过10MB')
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    if digest!=asset.get('sha256'):raise ValueError('DBC内容校验不一致，请重新导入')
    return _load(text)


@lru_cache(maxsize=4)
def _load(text):
    import cantools
    db=cantools.database.load_string(text,database_format='dbc',strict=True)
    if not db.messages:raise ValueError('DBC没有报文定义')
    if any(not 0<=m.length<=64 for m in db.messages):raise ValueError('DBC报文长度必须为0–64字节')
    ids=[(m.frame_id,m.is_extended_frame) for m in db.messages]
    if len(ids)!=len(set(ids)):raise ValueError('DBC存在重复CAN ID及帧格式，请整理后导入')
    if any(m.is_container for m in db.messages):raise ValueError('当前不支持DBC容器报文')
    return db


def catalog(asset):
    if not asset:return []
    return [dict(message=m.name,signal=s.name,frame_id=m.frame_id,extended=m.is_extended_frame,
                 unit=s.unit or '',minimum=s.minimum,maximum=s.maximum,has_choices=bool(s.choices),
                 label=f'{m.name}.{s.name} [0x{m.frame_id:X}]')
            for m in database(asset).messages for s in m.signals]


def encode_step(config):
    return 'DBC64:'+base64.urlsafe_b64encode(json.dumps(config,ensure_ascii=False,allow_nan=False).encode()).decode()


def decode_step(text):
    token=str(text).split(' / ',1)[0]
    if not token.startswith('DBC64:'):raise ValueError('DBC工步参数格式无效')
    c=json.loads(base64.urlsafe_b64decode(token[6:]))
    for key in ('sha256','signal','message','frame_id','extended'):
        if key not in c:raise ValueError(f'DBC工步缺少{key}')
    for key,default,maximum in [('timeout',5,3600),('interval',.1,60),('max_age',1,3600)]:
        c[key]=float(c.get(key,default))
        if not math.isfinite(c[key]) or not .01<=c[key]<=maximum:raise ValueError(f'DBC {key}超出范围')
    if c.get('output','value') not in ('value','choice'):raise ValueError('DBC输出类型无效')
    variable=c.get('variable','')
    if variable and (not isinstance(variable,str) or not variable.isidentifier() or variable.startswith('__')):raise ValueError('DBC变量名无效')
    return c


def validate_recipe_dbc(recipe):
    asset=recipe.get('dbc');rows=catalog(asset) if asset else []
    if asset and (not recipe.get('can_config') or recipe['can_config'].get('enabled') is False):
        raise ValueError('已导入DBC，请选择配方CAN卡；不使用DBC时可移除DBC')
    for item in recipe.get('items',[]):
        if item.get('skip_runtime') or str(item.get('name','')).strip().startswith('#'):continue
        for step in item.get('sub_steps',[]):
            if step.get('skip_runtime') or str(step.get('name','')).strip().startswith('#') or 'CANDBC' not in str(step.get('type',''))+str(step.get('device','')):continue
            c=decode_step(step.get('params',''))
            if not asset or c['sha256']!=asset['sha256'] or not any(all(row[k]==c[k] for k in ('message','signal','frame_id','extended')) for row in rows):
                raise ValueError(f'测试项【{item.get("name","")}】的DBC信号已失效，请重新选择')
            from core.read_until import settings
            if settings(step.get('params','')) and c.get('output')=='choice':raise ValueError('枚举文字输出不能配置数值范围等待')


class DBCMonitor:
    def __init__(self):
        self._condition=threading.Condition();self._lifecycle_lock=threading.RLock();self._thread=None;self._driver=None
        self._callback=None;self._generation=0;self._pending={};self._values={}
        self.asset=None;self._messages={};self._bus=None;self.last_error=''
        self.context_key=None

    def configure(self,asset,driver,context_key=None):
        with self._lifecycle_lock:self._configure(asset,driver,context_key)

    def _configure(self,asset,driver,context_key):
        digest=(asset or {}).get('sha256')
        with self._condition:
            if self._thread and self._driver is driver and self.asset and digest==self.asset['sha256'] and self._bus is getattr(driver,'bus',None) and self.context_key==context_key:return
        self.stop()
        if not asset:return
        db=database(asset)
        if driver is None or not getattr(driver,'is_connected',False):raise ValueError('DBC监控需要已连接的配方CAN卡')
        messages={(m.frame_id,m.is_extended_frame):m for m in db.messages}
        with self._condition:
            self.asset=dict(asset);self._driver=driver;self._messages=messages
            self._bus=getattr(driver,'bus',None);generation=self._generation
            self.context_key=context_key
            self.last_error=''
        self._callback=lambda frame:self._receive(frame,generation,driver)
        driver.add_message_listener(self._callback)
        try:
            self._thread=threading.Thread(target=self._decode_loop,args=(generation,),name='dbc-decoder',daemon=True)
            self._thread.start()
        except Exception:
            self._thread=None;self.stop();raise

    def stop(self):
        with self._lifecycle_lock:self._stop()

    def _stop(self):
        with self._condition:
            self._generation+=1;self._pending.clear();self._values.clear()
            driver,callback,thread=self._driver,self._callback,self._thread
            self._driver=None;self._callback=None;self._thread=None;self.asset=None;self._messages={}
            self.context_key=None;self._bus=None;self.last_error=''
            self._condition.notify_all()
        if driver is not None and callback is not None:driver.remove_message_listener(callback)
        if thread and thread is not threading.current_thread():thread.join(1)
        if thread and thread.is_alive():raise RuntimeError('DBC解码线程尚未退出')

    def _receive(self,frame,generation,driver):
        if frame.get('direction')!='RX' or frame.get('is_error_frame') or frame.get('is_remote_frame'):return
        key=(frame.get('can_id'),bool(frame.get('is_extended_id')))
        interface=str(frame.get('interface') or driver.channel)
        with self._condition:
            if generation!=self._generation or key not in self._messages:return
            bus=getattr(driver,'bus',None)
            if bus is not self._bus:
                self._values.clear();self._pending.clear();self._bus=bus
            self._pending[(interface,*key)]=(bytes(frame['data']),time.perf_counter(),bus)
            self._condition.notify()

    def _decode_loop(self,generation):
        while True:
            with self._condition:
                self._condition.wait_for(lambda:generation!=self._generation or self._pending)
                if generation!=self._generation:return
                pending=self._pending;self._pending={};messages=self._messages
            for (interface,ident,extended),(data,received,bus) in pending.items():
                message=messages[(ident,extended)]
                try:
                    numeric=message.decode(data,decode_choices=False,allow_truncated=False)
                    choices=message.decode(data,decode_choices=True,allow_truncated=False)
                    raw=message.decode(data,scaling=False,decode_choices=False) if any(s.raw_invalid is not None for s in message.signals) else {}
                    error=''
                except Exception as exc:numeric={};choices={};raw={};error=str(exc)
                with self._condition:
                    if generation!=self._generation:return
                    if bus is not self._bus:continue
                    for signal in message.signals:
                        key=(interface,ident,extended,signal.name)
                        if error:
                            self._values.pop(key,None);continue
                        if signal.name not in numeric:continue # Inactive multiplexed signal.
                        value=numeric[signal.name]
                        invalid=(signal.raw_invalid is not None and raw.get(signal.name)==signal.raw_invalid)
                        if not isinstance(value,(int,float)) or not math.isfinite(value) or invalid:
                            self._values.pop(key,None);continue
                        self._values[key]=dict(value=value,choice=str(choices[signal.name]),received=received,
                            message=message.name,signal=signal.name,unit=signal.unit or '',bus=bus)
                    self.last_error=f'{message.name}: {error}' if error else ''
                    self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            connected=bool(self._driver and self._driver.is_connected)
            bus=getattr(self._driver,'bus',None)
            now=time.perf_counter()
            return {key:dict(value,age=max(0,now-value['received'])) for key,value in self._values.items()
                    if connected and value['bus'] is bus}

    def read(self,c,interface,since=0):
        with self._condition:
            if not self.asset or self.asset['sha256']!=c['sha256']:raise ValueError('工步DBC与当前配方不一致，请重新选择信号')
            value=self._values.get((interface,int(c['frame_id']),bool(c['extended']),c['signal']))
            if not self._driver or not self._driver.is_connected or (value and value['bus'] is not getattr(self._driver,'bus',None)):value=None
            if value is None:raise ValueError(f'{interface} 尚未收到有效信号 {c["message"]}.{c["signal"]}')
            value=dict(value,age=max(0,time.perf_counter()-value['received']))
            if value['message']!=c['message']:raise ValueError('DBC报文名称不一致')
            if value['age']>c['max_age'] or value['received']<since:raise ValueError('DBC信号过期或尚未收到本工步的新数据')
            return value


def execute_read(monitor,c,interface,cancelled,wait,logger,range_config=None):
    c=decode_step(encode_step(c))
    if range_config:
        from core.read_until import validate
        validate(range_config)
        if c.get('output','value')!='value':raise ValueError('范围等待需要DBC数值输出')
    start=time.perf_counter();deadline=start+(range_config['timeout'] if range_config else c['timeout'])
    since=start if c.get('new_frame',True) else 0
    interval=range_config['interval'] if range_config else c['interval']
    last='未收到数据';last_stamp=None;streak=0
    while not cancelled():
        if time.perf_counter()>=deadline:raise RuntimeError(f'DBC信号等待超时：{last}')
        try:
            value=monitor.read(c,interface,since)
            last=value['value']
            if value['received']!=last_stamp:
                last_stamp=value['received']
                if range_config:
                    streak=streak+1 if range_config['min']<=last<=range_config['max'] else 0
                    logger(f'DBC {interface} {c["message"]}.{c["signal"]}={last} {value["unit"]} 连续合格={streak}/{range_config["consecutive"]}')
                if not range_config or streak>=range_config['consecutive']:
                    result=value['choice'] if c.get('output')=='choice' else last
                    logger(f'DBC输出 {interface} {c["message"]}.{c["signal"]}={result} {value["unit"]} 数据年龄={value["age"]:.3f}s')
                    return True,result
        except ValueError as exc:
            last=str(exc);streak=0
            if not monitor.asset or monitor.asset['sha256']!=c['sha256']:raise
        remaining=deadline-time.perf_counter()
        if remaining<=0:raise RuntimeError(f'DBC信号等待超时：{last}')
        if wait(min(interval,remaining)):break
    raise RuntimeError('DBC信号读取已取消')
