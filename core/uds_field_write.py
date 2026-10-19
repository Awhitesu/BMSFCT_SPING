"""Standalone 2E write: literal ASCII + execution date + current barcode slice."""
import base64
import json
from datetime import datetime

DEVICE = 'UDS 2E 三字段写入'


def encode(config):
    validate(config)
    return base64.urlsafe_b64encode(json.dumps(config,ensure_ascii=False).encode()).decode()


def decode(value):
    config=json.loads(base64.urlsafe_b64decode(value).decode('utf-8'))
    validate(config)
    return config


def validate(c):
    if not isinstance(c,dict):raise ValueError('三字段写入配置无效')
    for key,maximum in (('did',65535),('tx_id',0x1FFFFFFF),('rx_id',0x1FFFFFFF)):
        if type(c.get(key)) is not int or not 0<=c[key]<=maximum:raise ValueError(key+' 必须为有效 HEX 地址')
    if c['tx_id']==c['rx_id']:raise ValueError('请求与响应 ID 不能相同')
    if 'uds_settings' in c:
        from core.uds_sequence import settings_from, encode_settings
        if not isinstance(c['uds_settings'], dict):raise ValueError('UDS通信设置应为对象')
        settings = settings_from(encode_settings(c['uds_settings']))
        if settings['address'] != 'physical':raise ValueError('三字段2E写入使用物理地址')
        if settings['frame_format'] == 'standard' and max(c['tx_id'], c['rx_id']) > 0x7FF:
            raise ValueError('标准帧CAN ID不能超过0x7FF')
    variable=c.get('variable','')
    if not isinstance(variable,str) or (variable and not variable.isidentifier()):raise ValueError('结果变量名应为有效标识符，不能包含空格')
    if not isinstance(c.get('literal'),str):raise ValueError('字段1必须为 ASCII 文本')
    try:c['literal'].encode('ascii')
    except UnicodeEncodeError:raise ValueError('字段1仅支持 ASCII 字符，不能包含中文')
    for key,low,high in (('start',1,4095),('length',0,4095),('timeout',1,600000)):
        if type(c.get(key)) is not int or not low<=c[key]<=high:raise ValueError(key+' 超出允许范围')
    if len(c['literal'])+6+max(1,c['length'])+3>4095:raise ValueError('写入长度超过 ISO-TP 支持的 4095 字节')
    return c


def build_details(c,barcode,now=None):
    validate(c)
    if not isinstance(barcode,str) or not barcode:raise ValueError('本次没有扫码条码，不能执行三字段写入')
    start=c['start']-1
    if start>=len(barcode):raise ValueError('条码截取起始位置超过本次条码长度')
    end=start+c['length'] if c['length'] else len(barcode)
    if end>len(barcode):raise ValueError('条码长度不足，不能完整截取指定长度')
    date=(now or datetime.now()).strftime('%y%m%d')
    text=c['literal']+date+barcode[start:end]
    try:data=text.encode('ascii')
    except UnicodeEncodeError:raise ValueError('条码选中部分包含非 ASCII 字符')
    request=b'\x2e'+c['did'].to_bytes(2,'big')+data
    if len(request)>4095:raise ValueError('写入长度超过 ISO-TP 支持的 4095 字节')
    return dict(request=request, text=text, literal=c['literal'], date=date, barcode_slice=barcode[start:end])


def build(c,barcode,now=None):
    details = build_details(c,barcode,now)
    return details['request'],details['text']


def execution_values(c,barcode,now=None):
    request,text=build(c,barcode,now)
    script=request.hex(' ').upper()+' => '+(b'\x6e'+request[1:3]).hex(' ').upper()
    values = dict(TX_ID=hex(c['tx_id']),RX_ID=hex(c['rx_id']),TIMEOUT=str(c['timeout']),
                  CYCLES='1',INTERVAL='0',LIST64=base64.urlsafe_b64encode(script.encode()).decode())
    if 'uds_settings' in c:
        from core.uds_sequence import encode_settings
        values['UDSCFG64'] = encode_settings(c['uds_settings'])
    return values,text
