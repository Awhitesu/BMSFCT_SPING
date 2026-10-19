"""Mixed relay targets. Missing points are deliberately untouched."""
import base64
import json

PREFIX = 'RELAY64:'
ACTION = '组合设置ON/OFF'


def validate(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('请至少启用一个继电器点位')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get('index')) is not int or type(row.get('on')) is not bool:
            raise ValueError('继电器组合参数无效')
        if not 0 <= row['index'] <= 0xffff - 0xfc08 or row['index'] in seen:
            raise ValueError('继电器点位重复或越界')
        seen.add(row['index'])
    return rows


def encode(rows):
    return PREFIX + base64.urlsafe_b64encode(json.dumps(validate(rows), separators=(',', ':')).encode()).decode()


def decode(text):
    text = str(text).strip()
    if not text.startswith(PREFIX):
        return None
    return validate(json.loads(base64.urlsafe_b64decode(text[len(PREFIX):]).decode()))


def execute(rows, driver, allowed, cancelled, log):
    validate(rows)
    if any(row['index'] not in allowed for row in rows):
        raise ValueError('组合工步包含当前导入表不存在的DO点位，请重新编辑工步')
    # OFF before ON; this is a single configured step, not an atomic PLC write.
    for row in sorted(rows, key=lambda r: (r['on'], r['index'])):
        if cancelled():
            raise RuntimeError('测试已停止')
        index, state = row['index'], row['on']
        ok = driver.write_relay(index, state)
        log(f'Easy320 DO{index:o} {allowed[index].get("name", "")} -> {"ON" if state else "OFF"}: {"OK" if ok else "FAIL"}')
        if not ok:
            return False
    return True
