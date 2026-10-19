"""Shared, deterministic CAN payload extraction for preview and execution."""
import base64
import json
import math
import struct


def bit_positions(c):
    """DBC sawtooth Motorola numbering; absent layout preserves legacy Intel."""
    position = c['start'] * 8 + c['bit']
    positions = []
    for _ in range(c['count']):
        positions.append(position)
        position = (position + 15 if position % 8 == 0 else position - 1) if c.get('bit_order', 'intel') == 'motorola' else position + 1
    return positions


def encode_parser(config):
    validate_parser(config)
    return base64.urlsafe_b64encode(json.dumps(config).encode()).decode()


def decode_parser(value):
    config = json.loads(base64.urlsafe_b64decode(value).decode())
    validate_parser(config)
    return config


def validate_parser(c):
    for key in ('length', 'start', 'count', 'bit'):
        if type(c.get(key)) is not int:
            raise ValueError('长度和位置必须是整数')
    if not 1 <= c['length'] <= 64 or not 0 <= c['start'] < c['length']:
        raise ValueError('返回长度为 1～64 字节；起始 Byte 必须在返回范围内')
    if c.get('mode') not in ('bytes', 'bits') or c.get('endian') not in ('big', 'little'):
        raise ValueError('解析方式或字节序无效')
    if c.get('type') not in ('unsigned', 'signed', 'float', 'hex', 'ascii'):
        raise ValueError('数据类型无效')
    if not 0 <= c['bit'] <= 7 or c['count'] < 1:
        raise ValueError('起始 Bit 为 0～7，提取长度至少为 1')
    if c.get('bit_order', 'intel') not in ('intel', 'motorola'):
        raise ValueError('位序无效')
    if c['count'] > 512:
        raise ValueError('提取长度不能超过512位或64字节')
    end = max(bit_positions(c)) + 1 if c['mode'] == 'bits' else (c['start'] + c['count']) * 8
    if end > c['length'] * 8:
        raise ValueError('选取范围超过返回长度，请减少提取长度或调整起始位置')
    if c['mode'] == 'bits' and c['type'] not in ('unsigned', 'signed'):
        raise ValueError('按位提取仅支持有符号或无符号整数')
    if c['type'] == 'float' and c['count'] not in (4, 8):
        raise ValueError('浮点数需要取 4 字节或 8 字节')
    if not all(math.isfinite(float(c[k])) for k in ('scale', 'offset')):
        raise ValueError('系数和偏移必须是有限数值')
    if c['type'] in ('hex', 'ascii') and (float(c['scale']) != 1 or float(c['offset']) != 0):
        raise ValueError('HEX 和文字不使用数值换算，请设置系数 1、偏移 0')


def parse_payload(data, c):
    validate_parser(c)
    data = bytes(data)
    if len(data) != c['length']:
        raise ValueError(f"返回长度不符：期望 {c['length']} 字节，实际 {len(data)} 字节")
    start, count = c['start'], c['count']
    if c['mode'] == 'bits':
        # Increasing bit addresses: B0.bit0 ... B0.bit7, B1.bit0 ...
        raw = 0
        for index, position in enumerate(bit_positions(c)):
            shift = count - 1 - index if c.get('bit_order', 'intel') == 'motorola' else index
            raw |= ((data[position // 8] >> (position % 8)) & 1) << shift
        if c['type'] == 'signed' and raw & (1 << (count - 1)):
            raw -= 1 << count
    else:
        selected = data[start:start + count]
        if c['type'] == 'hex':
            return selected.hex(' ').upper(), selected.hex(' ').upper()
        if c['type'] == 'ascii':
            return selected.decode('ascii'), selected.decode('ascii')
        if c['type'] == 'float':
            raw = struct.unpack(('>' if c['endian'] == 'big' else '<') + ('f' if count == 4 else 'd'), selected)[0]
        else:
            raw = int.from_bytes(selected, c['endian'], signed=c['type'] == 'signed')
    value = raw * float(c['scale']) + float(c['offset'])
    if not math.isfinite(value):
        raise ValueError('解析结果不是有限数值')
    return raw, value
