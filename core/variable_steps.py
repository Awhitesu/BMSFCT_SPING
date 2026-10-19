"""Execution-local keyed values and explicit, visual segment conversion."""
import base64
import json
import math
import re

PREFIX='VARSTEP64:'

def validate(c):
    if not isinstance(c,dict) or c.get('operation') not in ('write','read'):raise ValueError('变量操作无效')
    if not isinstance(c.get('key'),str) or not c['key'].strip():raise ValueError('请填写变量键名')
    if c['operation']=='write' and not isinstance(c.get('source'),str):raise ValueError('请选择来源工步')
    if c.get('input','text') not in ('text','hex'):raise ValueError('变量输入格式无效')
    for s in c.get('segments',[]):
        if type(s.get('start')) is not int or s['start']<1 or type(s.get('length')) is not int or s['length']<0:raise ValueError('起始位置从1计数，长度0表示到末尾')
        if s.get('format') not in ('原值','ASCII','HEX','BCD','UINT_LE','UINT_BE'):raise ValueError('分段输出格式无效')
        if c.get('input','text')=='text' and s['format'] not in ('原值','HEX'):raise ValueError('ASCII/BCD/整数解析需选择HEX字节输入')
    return c

def encode(c):
    validate(c);return PREFIX+base64.urlsafe_b64encode(json.dumps(c,ensure_ascii=False).encode()).decode()

def decode(text):
    m=re.search(r'(?:^|\s/\s)VARSTEP64:([^\s]+)',str(text))
    return validate(json.loads(base64.urlsafe_b64decode(m[1]).decode())) if m else None

def parse(value,c):
    validate(c)
    if not c.get('segments'):return value
    raw=(bytes(value) if isinstance(value,(bytes,bytearray)) else bytes.fromhex(str(value))) if c.get('input','text')=='hex' else str(value)
    parts=[]
    for index,s in enumerate(c['segments'],1):
        start=s['start']-1;end=start+s['length'] if s['length'] else len(raw)
        if start>=len(raw) or end>len(raw):
            unit='字节' if isinstance(raw,bytes) else '字符'
            raise ValueError(f'变量【{c["key"]}】第{index}段截取超出实际长度：实际{len(raw)}{unit}，起始位置{s["start"]}，截取长度{s["length"]}（0为到末尾）；位置从1计数')
        part=raw[start:end];kind=s['format']
        if isinstance(part,str):part=part.encode('ascii').hex(' ').upper() if kind=='HEX' else part
        elif kind=='ASCII':part=part.decode('ascii')
        elif kind=='BCD':
            if any(b>>4>9 or b&15>9 for b in part):raise ValueError('BCD数据包含无效半字节')
            part=''.join(f'{b:02X}' for b in part)
        elif kind.startswith('UINT_'):part=str(int.from_bytes(part,'little' if kind=='UINT_LE' else 'big'))
        else:part=part.hex().upper() if s.get('compact_hex',False) else part.hex(' ').upper()
        parts.append(part)
    return c.get('separator','').join(parts)

def execute(worker,c):
    validate(c);key=c['key']
    if c['operation']=='write':
        worker.variables.pop(key,None)
        source=c.get('source','last')
        record=worker.last_output if source=='last' else worker.step_outputs.get(source)
        if not record or not record['ok'] or record['value'] is None:raise ValueError('来源工步未执行成功或没有输出值，不能写入变量')
        value=parse(record['value'],c)
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('变量值不是有限数值')
        worker.variables[key]=value
    else:
        if key not in worker.variables:raise ValueError('本次测试尚未写入变量：'+key)
        value=parse(worker.variables[key],c)
    return value
