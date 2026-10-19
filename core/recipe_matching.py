"""Bounded recipe selection; runs outside the GUI thread."""
import hashlib
import json
from pathlib import Path
import time
import regex


def barcode_rule_matches(rule,barcode,timeout=.05):
    if len(rule)>4096 or len(barcode)>4096:raise ValueError('条码或规则过长')
    return bool(regex.match(rule,barcode,flags=regex.VERSION0,timeout=timeout,concurrent=True))


def match_recipe_files(root,entries,barcode,*,timeout=2.0,cancelled=lambda:False):
    root=Path(root).resolve();deadline=time.monotonic()+timeout
    if len(barcode)>4096:raise ValueError('条码过长，请检查扫码内容')
    matches=[];errors=[]
    for index,filename in entries:
        if cancelled():raise RuntimeError('配方匹配已取消')
        remaining=deadline-time.monotonic()
        if remaining<=0:errors.append('配方匹配总耗时超过限制，请减少或简化条码规则');break
        try:
            path=(root/filename).resolve()
            if path.parent!=root:raise ValueError('配方路径超出配置目录')
            if path.stat().st_size>10*1024*1024:raise ValueError('配方文件超过10MB')
            with path.open('rb') as stream:content=stream.read(10*1024*1024+1)
            if len(content)>10*1024*1024:raise ValueError('配方文件超过10MB')
            data=json.loads(content.decode('utf-8'))
            if not isinstance(data,dict):raise ValueError('配方内容必须为对象')
            rule=str(data.get('sn_regex','')).strip()
            if len(rule)>4096:raise ValueError('条码规则过长')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('匹配总耗时超时')
            if rule and barcode_rule_matches(rule,barcode,timeout=min(.05,remaining)):
                matches.append((index,str(path),data.get('name') or filename,rule,hashlib.sha256(content).hexdigest()))
        except (OSError,ValueError,TypeError,regex.error,TimeoutError) as exc:
            errors.append(f'{filename}：'+('条码规则匹配超时，请简化规则' if isinstance(exc,TimeoutError) else str(exc)))
    return matches,errors
