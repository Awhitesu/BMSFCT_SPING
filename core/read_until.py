"""Bounded numeric polling shared by instrument read steps."""
import base64
import json
import math
import re
import time


def validate(c):
    for key in ('min', 'max', 'timeout', 'interval'):
        if not math.isfinite(float(c[key])):
            raise ValueError('范围和时间必须为有限数值')
    if float(c['min']) > float(c['max']):
        raise ValueError('回读下限不能大于上限')
    if not 0 < float(c['timeout']) <= 86400 or not .01 <= float(c['interval']) <= 3600:
        raise ValueError('最长等待应为0–86400秒，读取周期应为0.01–3600秒')
    if type(c['consecutive']) is not int or not 1 <= c['consecutive'] <= 10000:
        raise ValueError('连续合格次数应为1–10000')
    return c


def settings(text):
    text = str(text)
    if text.startswith(('设备工步:', 'DEVICE_STEP:')):
        c = json.loads(text.split(':', 1)[1]).get('read_wait')
    else:
        match = re.search(r'(?:^|\s/\s)RW:([^\s]+)', text)
        c = json.loads(base64.urlsafe_b64decode(match[1])) if match else None
    return validate(c) if c is not None else None


def attach(text, c):
    if c is None:
        return text
    validate(c)
    if text.startswith(('设备工步:', 'DEVICE_STEP:')):
        prefix, raw = text.split(':', 1)
        data = json.loads(raw); data['read_wait'] = c
        return prefix + ':' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return text + ' / RW:' + base64.urlsafe_b64encode(json.dumps(c).encode()).decode()


def poll(read, c, cancelled, wait, logger, clock=time.monotonic):
    validate(c)
    start = clock(); deadline = start + float(c['timeout']); streak = 0; attempt = 0; last = None
    while True:
        if cancelled():
            raise RuntimeError('回读等待已取消')
        if attempt and clock() >= deadline:
            raise RuntimeError(f'回读等待超时，最后读数={last}，要求范围[{c["min"]}, {c["max"]}]，耗时{clock()-start:.3f}s')
        ok, value = read(); attempt += 1
        if cancelled():
            raise RuntimeError('回读等待已取消')
        if not ok:
            raise RuntimeError(f'第{attempt}次回读失败：{value}')
        if isinstance(value, bool):
            raise ValueError('范围等待需要数值回读，不能使用布尔状态')
        last = float(value)
        if not math.isfinite(last):
            raise ValueError('回读值不是有限数值')
        now = clock()
        streak = streak + 1 if float(c['min']) <= last <= float(c['max']) else 0
        logger(f'范围等待 第{attempt}次 读数={last} 范围=[{c["min"]}, {c["max"]}] 连续合格={streak}/{c["consecutive"]} 耗时={now-start:.3f}s')
        if now <= deadline and streak >= c['consecutive']:
            return True, value
        if now >= deadline:
            raise RuntimeError(f'回读等待超时，最后读数={last}，耗时{now-start:.3f}s')
        if wait(min(float(c['interval']), deadline-now)):
            raise RuntimeError('回读等待已取消')
