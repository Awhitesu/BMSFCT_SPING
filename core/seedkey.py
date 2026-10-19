"""ZCANPRO GenerateKeyEx / ZLGKey adapter and fail-closed UDS unlock."""
import base64
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time


def validate_config(c, require_file=True):
    if c.get('api') not in ('auto','GenerateKeyEx','ZLGKey') or c.get('calling') not in ('cdecl','stdcall'):
        raise ValueError('SeedKey DLL接口或调用约定无效')
    for key in ('level','dll_level','capacity'):
        if type(c.get(key)) is not int:
            raise ValueError('安全等级和缓冲区长度必须为整数')
    if not 1 <= c['level'] <= 0x7D or c['level'] % 2 != 1:
        raise ValueError('Seed请求子功能必须为01–7D的奇数，Key子功能自动加1')
    if not 0 <= c['dll_level'] <= 0xFFFFFFFF or not 1 <= c['capacity'] <= 4096:
        raise ValueError('DLL等级或Key缓冲区长度无效')
    if not math.isfinite(float(c['timeout'])) or not .1 <= float(c['timeout']) <= 60:
        raise ValueError('DLL计算超时应为0.1–60秒')
    if not isinstance(c.get('path'),str) or not c['path'].strip():
        raise ValueError('请选择 SeedKey DLL')
    if '\x00' in c.get('variant',''):
        raise ValueError('Variant不能包含空字符')
    bytes.fromhex(c.get('seed_data',''))
    if require_file and not dll_path(c['path']).is_file():
        raise ValueError('SeedKey DLL文件不存在：'+c['path'])
    return c


def dll_path(value):
    path=Path(os.path.expandvars(value))
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    return path if path.is_absolute() else root/path


def architecture(path):
    with Path(path).open('rb') as stream:
        header=stream.read(64)
        if len(header)!=64 or header[:2]!=b'MZ': raise ValueError('不是有效的Windows DLL')
        stream.seek(struct.unpack_from('<I',header,60)[0]); pe=stream.read(6)
    if len(pe)!=6 or pe[:4]!=b'PE\0\0': raise ValueError('DLL PE头无效')
    machine=struct.unpack_from('<H',pe,4)[0]
    if machine not in (0x14C,0x8664): raise ValueError('仅支持x86或x64 Windows DLL')
    return 'x86' if machine==0x14C else 'x64'


def encode(c):
    validate_config(c)
    return base64.urlsafe_b64encode(json.dumps(c,ensure_ascii=False).encode()).decode()


def decode(value):
    return validate_config(json.loads(base64.urlsafe_b64decode(value)),require_file=False)


def helper_path(arch):
    if arch not in ('x86','x64'):raise ValueError('SeedKey辅助程序位数无效')
    if getattr(sys,'frozen',False):
        executable_root=Path(sys.executable).resolve().parent
        roots=([Path(sys._MEIPASS)] if getattr(sys,'_MEIPASS',None) else [])
        roots.extend([executable_root/'_internal',executable_root])
    else:roots=[Path(__file__).resolve().parents[1]]
    candidates=list(dict.fromkeys(root/f'resources/seedkey/SeedKeyHost-{arch}.exe' for root in roots))
    for path in candidates:
        if path.is_file():return path
    detail='\n'.join(str(p) for p in candidates)
    advice=('请从最新发布包复制整个 BMSFCT 文件夹（包含 _internal），不能只复制主 EXE。'
            if getattr(sys,'frozen',False) else '请运行 tools/build_seedkey_hosts.py。')
    raise RuntimeError(f'缺少SeedKey辅助程序（{arch}）。{advice}\n已检查路径：\n{detail}')


def calculate_key(seed, config, cancelled=lambda:False):
    validate_config(config)
    if not 1 <= len(seed) <= 4096: raise ValueError('Seed长度应为1–4096字节')
    if cancelled(): raise RuntimeError('安全访问已取消')
    path=dll_path(config['path']).resolve()
    arch=architecture(path)
    helper=helper_path(arch)
    enc=lambda text:base64.b64encode(text.encode('utf-8')).decode('ascii')
    payload='\n'.join([enc(str(path)),config['api'],config['calling'],bytes(seed).hex(),str(config['dll_level']),
                        enc(config.get('variant','')),str(config['capacity'])])+'\n'
    process=subprocess.Popen([str(helper)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    start=time.monotonic(); first=True
    try:
        while True:
            if cancelled(): raise RuntimeError('安全访问已取消')
            if time.monotonic()-start >= float(config['timeout']): raise RuntimeError('SeedKey DLL计算超时')
            try:
                output,_=process.communicate(payload.encode() if first else None,timeout=.05)
                break
            except subprocess.TimeoutExpired:
                first=False
        lines=output.decode('utf-8',errors='replace').splitlines()
        for line in reversed(lines):
            if line.startswith('BMS_SEEDKEY_ERROR:'):
                raise RuntimeError('SeedKey DLL调用失败：'+base64.b64decode(line.split(':',1)[1]).decode('utf-8',errors='replace'))
            if line.startswith('BMS_SEEDKEY_OK:') and process.returncode==0:
                key=bytes.fromhex(line.split(':',1)[1])
                if not 1 <= len(key) <= config['capacity']: raise RuntimeError('DLL返回Key长度无效')
                return key
        raise RuntimeError(f'SeedKey辅助程序异常退出（{process.returncode}），请检查DLL接口、依赖和调用约定')
    finally:
        if process.poll() is None:
            process.kill();process.communicate()


def unlock(client, config, logger=lambda _:None, cancelled=lambda:False, algorithm=None):
    """One fresh challenge per operation; never retry a rejected key automatically."""
    validate_config(config,require_file=algorithm is None)
    if algorithm is None:
        helper_path(architecture(dll_path(config['path'])))
    def active():
        if cancelled(): raise RuntimeError('安全访问已取消，未发送后续请求')
    with client._lock:
        active()
        level=config['level']
        response=client.security_access_request_seed(level,bytes.fromhex(config.get('seed_data','')))
        if not response.positive: raise RuntimeError(f'Seed请求失败：{response.error or response.nrc_name}')
        if not response.data or response.data[0]!=level or len(response.data)<2:
            raise RuntimeError('Seed响应为空或安全等级不匹配')
        seed=bytes(response.data[1:]);active()
        logger(f'UDS安全访问：Seed等级=0x{level:02X}，Seed长度={len(seed)}，DLL接口={config["api"]}')
        if not any(seed):
            logger('UDS安全访问：ECU返回全零Seed，当前等级已解锁')
            return response
        key=algorithm(seed,config) if algorithm else calculate_key(seed,config,cancelled)
        active()
        if not isinstance(key,(bytes,bytearray)) or not 1 <= len(key) <= config['capacity']:
            raise RuntimeError('DLL返回Key无效')
        response=client.security_access_send_key(level+1,bytes(key))
        if not response.positive: raise RuntimeError(f'Key验证失败：{response.error or response.nrc_name}；不会自动重试解锁')
        if response.data != bytes([level+1]): raise RuntimeError('Key验证响应不匹配')
        logger(f'UDS安全访问成功：等级=0x{level:02X}，Key长度={len(key)}，已收到ECU确认')
        return response
