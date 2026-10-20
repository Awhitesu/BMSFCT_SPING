"""配方 APP 刷写参数校验及同级 config.ini 的定点更新。"""
import configparser
import os
from pathlib import Path
import re
import tempfile


def validate_app_flash(config):
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError('APP刷写参数格式无效')
    labels = {'host_path': '上位机路径', 'model': 'App刷写型号', 'flash_path': 'App刷写路径'}
    values = {}
    for key, label in labels.items():
        value = config.get(key, '')
        if not isinstance(value, str) or any(char in value for char in '\r\n\0'):
            raise ValueError(f'{label}必须是单行文本')
        values[key] = value.strip()
        if key == 'host_path':
            values[key] = values[key].strip('"').strip()
    if not any(values.values()):
        return None  # 旧配方及未启用此功能的配方继续使用原工步配置。
    missing = [label for key, label in labels.items() if not values[key]]
    if missing:
        raise ValueError('APP刷写参数未填写完整：' + '、'.join(missing))
    return values


def prepare_app_flash(config, logger=None):
    """选择测试型号时同步两项键值；刷写工步不重复写入。"""
    values = validate_app_flash(config)
    if values is None:
        return None
    host = Path(os.path.abspath(os.path.expandvars(values['host_path'])))
    directory = host if host.is_dir() else host.parent
    ini = directory / 'config.ini'
    if not ini.is_file():
        raise ValueError(f'未找到上位机同级配置文件：{ini}')
    raw = ini.read_bytes()
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'gbk'
        text = raw.decode(encoding)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    sections = [name for name in parser.sections() if name.strip().casefold() == 'app']
    if len(sections) > 1:
        raise ValueError('config.ini 存在多个 App 段，无法确定写入位置')
    if sections:
        for key in ('type', 'addr'):
            if '\n' in parser.get(sections[0], key, fallback=''):
                raise ValueError(f'config.ini 的 {key} 包含多行内容，无法安全替换')

    # 保留原文件的编码、换行、注释和其他段，仅替换目标键所在的行。
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines(keepends=True)
    start, end = None, len(lines)
    for index, line in enumerate(lines):
        section = re.match(r'^\s*\[([^\]\r\n]+)\]', line)
        if section:
            if start is not None:
                end = index
                break
            if section.group(1).strip().casefold() == 'app':
                start = index + 1
    desired = {'type': ('Type', values['model']), 'addr': ('Addr', values['flash_path'])}
    seen = set()
    if start is None:
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines[-1] += newline
        lines.append('[App]' + newline)
        end = len(lines)
    else:
        for index in range(start, end):
            body = lines[index].rstrip('\r\n')
            ending = lines[index][len(body):]
            match = re.match(r'^([ \t]*)([^#;\s][^=:]*?)([ \t]*[=:][ \t]*).*$', body)
            if match and match.group(2).strip().casefold() in desired:
                key = match.group(2).strip().casefold()
                canonical, value = desired[key]
                lines[index] = match.group(1) + canonical + match.group(3) + value + ending
                seen.add(key)
    missing = [f'{key}={value}{newline}' for name, (key, value) in desired.items() if name not in seen]
    if missing:
        if end and not lines[end - 1].endswith(('\n', '\r')):
            lines[end - 1] += newline
        lines[end:end] = missing
    updated = ''.join(lines).encode(encoding)
    if updated != raw:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.app_config_', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, ini)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    if logger:
        logger(f'[APP刷写参数] {ini} → [App] Type={values["model"]}；Addr={values["flash_path"]}')
    return values
