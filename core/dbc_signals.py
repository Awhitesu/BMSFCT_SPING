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
    return _catalog(database(asset))


def _catalog(db):
    return [dict(message=m.name,signal=s.name,frame_id=m.frame_id,extended=m.is_extended_frame,
                 unit=s.unit or '',minimum=s.minimum,maximum=s.maximum,has_choices=bool(s.choices),
                 label=f'{m.name}.{s.name} [0x{m.frame_id:X}]')
            for m in db.messages for s in m.signals]


@lru_cache(maxsize=4)
def _binding_index(text):
    names={};signals={};messages=set()
    for row in _catalog(_load(text)):
        messages.add(row['message'])
        names.setdefault((row['message'],row['signal']),[]).append(row)
        signals.setdefault(row['signal'],[]).append(row)
    return names,signals,messages


def resolve_step(config,asset,index=None):
    """Bind an existing named signal to the current DBC, without changing read settings."""
    if not asset:raise ValueError('当前配方未导入DBC')
    if index is None:
        database(asset)  # Keep the embedded content integrity check.
        index=_binding_index(asset['text'])
    names,signals,messages=index
    candidates=names.get((config['message'],config['signal']),[])
    # A surviving message with a removed signal must not silently bind another message's common name.
    if not candidates and config['message'] not in messages:candidates=signals.get(config['signal'],[])
    if len(candidates)>1:
        addressed=[row for row in candidates if (row['frame_id'],row['extended'])==
                   (config['frame_id'],config['extended'])]
        if len(addressed)==1:candidates=addressed
    if not candidates:raise ValueError(f'DBC中找不到信号 {config["message"]}.{config["signal"]}')
    if len(candidates)!=1:raise ValueError(f'DBC中存在多个同名信号 {config["signal"]}，请重新选择报文')
    row=candidates[0]
    if config.get('output')=='choice' and not row['has_choices']:
        raise ValueError(f'信号 {row["message"]}.{row["signal"]} 已无枚举定义，请重新选择输出类型')
    resolved=dict(config,**row,sha256=asset['sha256'])
    # Older recipes briefly stored raw output; numeric steps now consistently use the decoded actual value.
    if resolved.get('output')=='raw':resolved['output']='value'
    return resolved


def encode_step(config):
    return 'DBC64:'+base64.urlsafe_b64encode(json.dumps(config,ensure_ascii=False,allow_nan=False).encode()).decode()


def decode_step(text, *, allow_empty_group=False):
    token=str(text).split(' / ',1)[0]
    if not token.startswith('DBC64:'):raise ValueError('DBC工步参数格式无效')
    c=json.loads(base64.urlsafe_b64decode(token[6:]))
    for key in ('sha256','signal','message','frame_id','extended'):
        if key not in c:raise ValueError(f'DBC工步缺少{key}')
    for key,default,maximum in [('timeout',5,3600),('interval',.1,60),('max_age',1,3600)]:
        c[key]=float(c.get(key,default))
        if not math.isfinite(c[key]) or not .01<=c[key]<=maximum:raise ValueError(f'DBC {key}超出范围')
    if c.get('output','value') not in ('value','raw','choice'):raise ValueError('DBC输出类型无效')
    group=c.get('group','')
    if not isinstance(group,str) or len(group)>128 or any(ord(char)<32 for char in group):
        raise ValueError('DBC采样组名称应为不超过128字符的单行文字')
    if 'group' in c:c['group']=group.strip()
    role=c.get('group_role','')
    if role not in ('','start','end','single'):raise ValueError('DBC采样组位置应为起点、终点或起点兼终点')
    if role and not group.strip() and not allow_empty_group:raise ValueError('DBC采样组起点/终点必须填写组名')
    if group.strip():
        if c.get('new_frame',True):raise ValueError('按组刷新不能同时启用逐工步等待新报文')
    variable=c.get('variable','')
    if variable and (not isinstance(variable,str) or not variable.isidentifier() or variable.startswith('__')):raise ValueError('DBC变量名无效')
    return c


def validate_recipe_dbc(recipe):
    _recipe_step_updates(recipe)


def rebind_recipe_dbc(recipe):
    """Update bindings atomically after every active DBC step has been checked."""
    updates=_recipe_step_updates(recipe)
    for step,params in updates:step['params']=params
    return len(updates)


def _recipe_step_updates(recipe):
    from copy import deepcopy
    from core.dbc_groups import normalize_legacy_groups,plan_groups
    asset=recipe.get('dbc')
    if asset:database(asset)
    index=_binding_index(asset['text']) if asset else None
    if asset and (not recipe.get('can_config') or recipe['can_config'].get('enabled') is False):
        raise ValueError('已导入DBC，请选择配方CAN卡；不使用DBC时可移除DBC')
    items=deepcopy(recipe.get('items',[]))
    normalize_legacy_groups(items)
    plan_groups(items,normalize=False)
    updates=[]
    for i,item in enumerate(items):
        for j,step in enumerate(item.get('sub_steps',[])):
            original=recipe['items'][i]['sub_steps'][j]
            disabled=(item.get('skip_runtime') or str(item.get('name','')).strip().startswith('#')
                      or step.get('skip_runtime') or str(step.get('name','')).strip().startswith('#'))
            if disabled:
                if step.get('params')!=original.get('params'):updates.append((original,step['params']))
                continue
            if 'CANDBC' not in str(step.get('type',''))+str(step.get('device','')):continue
            c=decode_step(step.get('params',''))
            try:resolved=resolve_step(c,asset,index)
            except ValueError as exc:
                raise ValueError(f'测试项【{item.get("name","")}】的DBC信号已失效，请重新选择：{exc}') from exc
            from core.read_until import settings
            if settings(step.get('params','')) and c.get('output')=='choice':raise ValueError('枚举文字输出不能配置数值范围等待')
            if resolved!=c or step.get('params')!=original.get('params'):
                _,separator,suffix=step['params'].partition(' / ')
                updates.append((original,encode_step(resolved)+separator+suffix))
    return updates


def _multiplexer_plan(message):
    """预先记录索引位置；接收回调只读索引，不解码所有信号。"""
    def build(tree):
        plan=[]
        for node in tree:
            if not isinstance(node,dict):continue
            for name,branches in node.items():
                signal=message.get_signal_by_name(name)
                big=signal.byte_order=='big_endian'
                shift=(message.length*8-(signal.start//8*8+7-signal.start%8)-signal.length
                       if big else signal.start)
                plan.append((name,big,shift,(1<<signal.length)-1,
                    (1<<(signal.length-1)) if signal.is_signed else 0,signal,
                    {value:build(children) for value,children in branches.items()}))
        return tuple(plan)
    return build(message.signal_tree)


def _multiplexer_key(data,length,plan):
    if not plan:return ()
    if len(data)<length:return None
    packed={};key=[]
    def read(nodes):
        for name,big,shift,mask,sign,signal,branches in nodes:
            if big not in packed:packed[big]=int.from_bytes(data[:length],'big' if big else 'little')
            raw=(packed[big]>>shift)&mask
            if sign and raw&sign:raw-=mask+1
            value=int(signal.raw_to_scaled(raw,decode_choices=False))
            if value not in branches:return False
            key.append((name,value))
            if not read(branches[value]):return False
        return True
    # 未定义索引及损坏帧共用一个槽位，避免随任意索引值增长。
    return tuple(key) if read(plan) else None


class DBCMonitor:
    def __init__(self):
        self._condition=threading.Condition();self._lifecycle_lock=threading.RLock();self._thread=None;self._driver=None
        self._callback=None;self._generation=0;self._pending={};self._values={}
        self.asset=None;self._messages={};self._multiplexers={};self._bus=None;self.last_error=''
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
        multiplexers={key:_multiplexer_plan(message) for key,message in messages.items()}
        with self._condition:
            self.asset=dict(asset);self._driver=driver;self._messages=messages;self._multiplexers=multiplexers
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
            self._driver=None;self._callback=None;self._thread=None;self.asset=None;self._messages={};self._multiplexers={}
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
            data=bytes(frame['data'])
            branch=_multiplexer_key(data,self._messages[key].length,self._multiplexers[key])
            pending_key=(interface,*key,branch)
            # 相同索引只留最新帧；不同索引分别保留，不能用后一个温度/电压页覆盖前一页。
            # 刷新插入顺序，确保公共信号也按接收时间更新。
            self._pending.pop(pending_key,None)
            self._pending[pending_key]=(data,time.perf_counter(),bus)
            self._condition.notify()

    def _decode_loop(self,generation):
        while True:
            with self._condition:
                self._condition.wait_for(lambda:generation!=self._generation or self._pending)
                if generation!=self._generation:return
                pending=self._pending;self._pending={};messages=self._messages
            for (interface,ident,extended,_branch),(data,received,bus) in pending.items():
                message=messages[(ident,extended)]
                try:
                    numeric=message.decode(data,decode_choices=False,allow_truncated=False)
                    choices=message.decode(data,decode_choices=True,allow_truncated=False)
                    raw=message.decode(data,scaling=False,decode_choices=False)
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
                        raw_value=raw[signal.name]
                        if not isinstance(raw_value,(int,float)) or not math.isfinite(raw_value):
                            self._values.pop(key,None);continue
                        self._values[key]=dict(value=value,raw=raw_value,choice=str(choices[signal.name]),received=received,
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

    def context_token(self):
        with self._condition:
            return (self._generation,(self.asset or {}).get('sha256'),id(self._driver),
                    id(getattr(self._driver,'bus',None)),self.context_key)

    def read(self,c,interface,since=0,context=None):
        with self._condition:
            if context is not None and self.context_token()!=context:
                raise RuntimeError('DBC采样期间连接或配方已变化，请重新采样')
            if not self.asset or self.asset['sha256']!=c['sha256']:raise ValueError('工步DBC与当前配方不一致，请重新选择信号')
            value=self._values.get((interface,int(c['frame_id']),bool(c['extended']),c['signal']))
            if not self._driver or not self._driver.is_connected or (value and value['bus'] is not getattr(self._driver,'bus',None)):value=None
            if value is None:raise ValueError(f'{interface} 尚未收到有效信号 {c["message"]}.{c["signal"]}')
            value=dict(value,age=max(0,time.perf_counter()-value['received']))
            if value['message']!=c['message']:raise ValueError('DBC报文名称不一致')
            if value['age']>c['max_age'] or value['received']<since:raise ValueError('DBC信号过期或尚未收到本工步的新数据')
            return value


def signal_key(c,interface):
    return interface,int(c['frame_id']),bool(c['extended']),c['signal']


def refresh_group(monitor,requests,name,cancelled,wait,logger):
    """Capture one complete fresh snapshot. Age limits apply only at capture time."""
    asset=monitor.asset
    if not asset:raise ValueError('当前配方未启用DBC监控')
    database(asset)
    index=_binding_index(asset['text'])
    targets={}
    for config,interface in requests:
        c=resolve_step(decode_step(encode_step(config)),asset,index)
        key=signal_key(c,interface)
        if key in targets:
            previous=targets[key][0]
            c=dict(c,max_age=min(c['max_age'],previous['max_age']),
                   timeout=min(c['timeout'],previous['timeout']),interval=min(c['interval'],previous['interval']))
        targets[key]=(c,interface)
    if not targets:raise ValueError('DBC采样组没有待刷新信号')
    context=monitor.context_token();start=time.perf_counter()
    if context[1]!=asset['sha256']:raise RuntimeError('DBC采样期间连接或配方已变化，请重新采样')
    deadline=start+min(c['timeout'] for c,_ in targets.values())
    logger(f'DBC采样组【{name}】开始刷新，共{len(targets)}个信号；等待起点后的全组有效数据，超时{deadline-start:g}s')
    while not cancelled():
        if monitor.context_token()!=context:raise RuntimeError('DBC采样期间连接或配方已变化，请重新采样')
        missing=[];values={}
        # The decoder cannot replace part of this view while the group is being checked.
        with monitor._condition:
            for key,(c,interface) in targets.items():
                try:
                    value=monitor.read(c,interface,start,context=context)
                    values[key]=dict(value)
                except ValueError as exc:
                    missing.append(f'{interface} {c["message"]}.{c["signal"]}：{exc}')
            captured=time.perf_counter()
            for key,value in values.items():
                c,interface=targets[key]
                value['age']=max(0,captured-value['received'])
                if value['age']>c['max_age']:
                    missing.append(f'{interface} {c["message"]}.{c["signal"]}：数据过期')
        if captured>=deadline:
            detail='；'.join(missing) or '未在等待超时内形成全组有效数据'
            raise RuntimeError(f'DBC采样组【{name}】刷新超时：{detail}')
        if not missing:
            if cancelled():break
            logger(f'DBC采样组【{name}】快照已保存，共{len(targets)}个信号，耗时{captured-start:.3f}s；后续固定使用本次数据')
            return dict(name=name,since=start,captured=captured,context=context,keys=frozenset(targets),values=values)
        if wait(max(0,min(min(c['interval'] for c,_ in targets.values()),deadline-captured))):break
    raise RuntimeError('DBC采样组刷新已取消')


def execute_snapshot(snapshot,c,key,cancelled,logger):
    if cancelled():raise RuntimeError('DBC快照读取已取消')
    value=snapshot['values'][key]
    result=value['choice'] if c.get('output')=='choice' else value['value']
    logger(f'DBC快照输出【{snapshot["name"]}】{key[0]} {c["message"]}.{c["signal"]}={result} {value["unit"]} 采集时数据年龄={value["age"]:.3f}s')
    return True,result


def execute_read(monitor,c,interface,cancelled,wait,logger,range_config=None,since=0,context=None):
    c=decode_step(encode_step(c))
    asset=monitor.asset
    if asset:c=resolve_step(c,asset)
    if range_config:
        from core.read_until import validate
        validate(range_config)
        if c.get('output','value')!='value':raise ValueError('范围等待需要DBC数值输出')
    start=time.perf_counter();deadline=start+(range_config['timeout'] if range_config else c['timeout'])
    since=max(since,start if c.get('new_frame',True) else 0)
    interval=range_config['interval'] if range_config else c['interval']
    last='未收到数据';last_stamp=None;streak=0
    while not cancelled():
        if time.perf_counter()>=deadline:raise RuntimeError(f'DBC信号等待超时：{last}')
        try:
            value=(monitor.read(c,interface,since,context=context) if context is not None
                   else monitor.read(c,interface,since))
            last=value['value']
            unit=value['unit']
            if value['received']!=last_stamp:
                last_stamp=value['received']
                if range_config:
                    streak=streak+1 if range_config['min']<=last<=range_config['max'] else 0
                    logger(f'DBC {interface} {c["message"]}.{c["signal"]}={last} {unit} 连续合格={streak}/{range_config["consecutive"]}')
                if not range_config or streak>=range_config['consecutive']:
                    result=value['choice'] if c.get('output')=='choice' else last
                    logger(f'DBC输出 {interface} {c["message"]}.{c["signal"]}={result} {unit} 数据年龄={value["age"]:.3f}s')
                    return True,result
        except ValueError as exc:
            last=str(exc);streak=0
            if not monitor.asset or monitor.asset['sha256']!=c['sha256']:raise
        remaining=deadline-time.perf_counter()
        if remaining<=0:raise RuntimeError(f'DBC信号等待超时：{last}')
        if wait(min(interval,remaining)):break
    raise RuntimeError('DBC信号读取已取消')
