"""Compare E2 physical readback against addressed firmware data."""
from pathlib import Path
import tempfile

from core.burn_recorder import compare_and_diff_data


def firmware_segments(path, binary_address=None):
    path = Path(path)
    memory = {}

    def put(address, data):
        if address < 0 or address + len(data) > 0x100000000:
            raise ValueError('源程序地址超出范围')
        for offset, byte in enumerate(data):
            position = address + offset
            if position in memory and memory[position] != byte:
                raise ValueError('源程序包含冲突的重叠地址')
            memory[position] = byte
        if len(memory) > 16 * 1024 * 1024:
            raise ValueError('源程序超过逐字节校验容量上限')

    suffix = path.suffix.lower()
    if suffix == '.bin':
        if binary_address is None:
            raise ValueError('BIN源程序需要明确配置校验起始地址 VERIFY_ADDRESS')
        put(binary_address, path.read_bytes())
    elif suffix in ('.hex', '.mot', '.srec', '.s19', '.s28', '.s37'):
        upper = 0
        ended = False
        for line in path.read_text(encoding='ascii').splitlines():
            line = line.strip()
            if not line:
                continue
            if ended:
                raise ValueError('源程序结束记录后存在额外数据')
            if suffix == '.hex':
                if not line.startswith(':'):
                    raise ValueError('HEX记录格式无效')
                data = bytes.fromhex(line[1:])
                if len(data) < 5 or len(data) != data[0] + 5 or sum(data) & 255:
                    raise ValueError('HEX记录长度或校验和错误')
                address, kind, body = int.from_bytes(data[1:3], 'big'), data[3], data[4:-1]
                if kind == 0:
                    put(upper + address, body)
                elif kind in (2, 4) and len(body) == 2:
                    upper = int.from_bytes(body, 'big') << (4 if kind == 2 else 16)
                elif kind == 1 and not body:
                    ended = True
                elif kind not in (3, 5):
                    raise ValueError('HEX记录类型无效')
            else:
                if len(line) < 4 or line[:1] != 'S' or line[1] not in '01235789':
                    raise ValueError('S-record记录格式无效')
                kind = int(line[1])
                data = bytes.fromhex(line[2:])
                if not data or len(data) != data[0] + 1 or sum(data) & 255 != 255:
                    raise ValueError('S-record长度或校验和错误')
                size = {1: 2, 2: 3, 3: 4, 7: 4, 8: 3, 9: 2}.get(kind, 2)
                if len(data) < size + 2:
                    raise ValueError('S-record地址截断')
                if kind in (1, 2, 3):
                    put(int.from_bytes(data[1:1+size], 'big'), data[1+size:-1])
                if kind in (7, 8, 9):
                    ended = True
        if not ended:
            raise ValueError('源程序缺少结束记录')
    else:
        raise ValueError('逐字节校验需要HEX/MOT/S-record源文件；RPJ工程无法作为字节基准')
    if not memory:
        raise ValueError('源程序没有有效数据')
    segments = []
    for address in sorted(memory):
        if not segments or address != segments[-1][0] + len(segments[-1][1]):
            segments.append((address, bytearray()))
        segments[-1][1].append(memory[address])
    return [(a, bytes(b)) for a, b in segments]


def verify_readback(programmer, path, binary_address=None, cancelled=lambda: False):
    segments = firmware_segments(path, binary_address)
    sources, reads, offsets, samples = [], [], [], []
    mismatch = total = 0
    with tempfile.TemporaryDirectory(prefix='e2_verify_') as directory:
        for address, data in segments:
            for offset in range(0, len(data), 65536):
                if cancelled():
                    raise RuntimeError('回读校验已停止')
                expected = data[offset:offset + 65536]
                output = Path(directory) / 'readback.bin'
                output.unlink(missing_ok=True)
                start = address + offset
                if not programmer.read_chip(start, len(expected), str(output)) or not output.is_file():
                    raise ValueError(f'回读失败 0x{start:X}')
                actual = output.read_bytes()
                if len(actual) != len(expected):
                    raise ValueError(f'回读长度错误 0x{start:X}: 期望{len(expected)}，实际{len(actual)}')
                diff = compare_and_diff_data(expected, actual, start)
                mismatch += diff['mismatch_count']
                samples.extend(diff['samples'][:max(0, 50-len(samples))])
                offsets.extend(total + n for n in diff['mismatch_offsets'])
                sources.append(expected)
                reads.append(actual)
                total += len(expected)
    return b''.join(sources), b''.join(reads), segments[0][0], {
        'is_match': mismatch == 0, 'mismatch_count': mismatch,
        'source_length': total, 'readback_length': total,
        'match_rate': (total-mismatch)/total*100,
        'samples': samples, 'mismatch_offsets': offsets,
        'segments': [{'address': a, 'length': len(b)} for a, b in segments],
    }
