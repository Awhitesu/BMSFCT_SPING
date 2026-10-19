"""Per-request expected parsed values and the chosen final output row."""
import base64
import json
from decimal import Decimal, InvalidOperation


def encode(rows):
    return base64.urlsafe_b64encode(json.dumps(rows, ensure_ascii=False).encode('utf-8')).decode('ascii')


def decode(encoded, requests):
    if not encoded:
        return [{'expected': '', 'output': False} for _ in requests]
    rows = json.loads(base64.urlsafe_b64decode(encoded).decode('utf-8'))
    if not isinstance(rows, list) or len(rows) != len(requests):
        raise ValueError('UDS结果配置与交互列表行数不一致，请重新编辑保存')
    selected = 0
    for row, request in zip(rows, requests):
        if not isinstance(row, dict) or not isinstance(row.get('expected', ''), str) or not isinstance(row.get('output', False), bool):
            raise ValueError('UDS结果配置格式错误')
        sample=row.get('response_sample','')
        if not isinstance(sample,str):raise ValueError('UDS响应样本必须为HEX字符串')
        if sample:
            try:raw_sample=bytes.fromhex(sample)
            except ValueError:raise ValueError('UDS响应样本必须为完整HEX字节')
            if len(raw_sample)>4095:raise ValueError('UDS响应样本不能超过4095字节')
        if request == '@SECURITY' and (row.get('output') or row.get('expected')):
            raise ValueError('DLL解锁行不能作为数据输出或设置提取期望值')
        selected += bool(row.get('output'))
        for key in ('suppress','check','enabled'):
            if key in row and type(row[key]) is not bool:
                raise ValueError('UDS行选项必须为布尔值')
        if row.get('address','default') not in ('default','physical','functional'):
            raise ValueError('UDS行地址类型无效')
        if not row.get('enabled',True) and row.get('output'):
            raise ValueError('最终输出行不能取消启用')
        if request.startswith('@DELAY'):
            from core.uds_sequence import delay_seconds
            delay_seconds(request)
            if row.get('output') or row.get('expected') or row.get('suppress'):
                raise ValueError('延时行不能输出数据、比较结果或抑制响应')
        elif request != '@SECURITY':
            from core.uds_sequence import prepare_request
            pdu = bytes.fromhex(request.partition('|')[0].partition('=>')[0].replace(',',' '))
            if row.get('suppress') and ('|' in request or row.get('expected') or row.get('output') or '=>' in request):
                raise ValueError('抑制响应行不能配置响应校验、解析或最终输出')
            prepare_request(pdu,row.get('suppress',False),row.get('address')=='functional')
        if row.get('expected') and '|' not in request:
            raise ValueError('配置期望结果的UDS行必须配置数据提取规则')
    if selected > 1:
        raise ValueError('只能指定一行为最终输出')
    return rows


def check_expected(actual, expected, mode):
    if expected == '':
        return
    if mode == 'HEX':
        try:matched=bytes.fromhex(str(actual))==bytes.fromhex(expected)
        except ValueError:matched=False
    elif mode in ('ASCII','BCD','COMBINE'):
        matched = str(actual) == expected
    else:
        try:
            want, got = Decimal(expected), Decimal(str(actual))
            matched = want.is_finite() and got.is_finite() and want == got
        except InvalidOperation:
            matched = False
    if not matched:
        raise ValueError(f'提取结果不匹配：期望 {expected!r}，实际 {actual!r}')
