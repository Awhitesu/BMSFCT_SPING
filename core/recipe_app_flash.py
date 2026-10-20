"""配方上位机使能、重启及同级 config.ini 的定点更新。"""
import configparser
import os
from pathlib import Path
import re
import tempfile

from core.host_process import check_cancelled, start_host, stop_host, wait_seconds


def validate_app_flash(config):
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError('APP刷写参数格式无效')
    if config.get('enabled') is not True:
        return None  # 旧配方默认关闭；隐藏参数不参与校验、同步及工步参数覆盖。
    labels = {'host_path': '上位机路径', 'model': 'App刷写型号', 'flash_path': 'App刷写路径',
              'restart_name': '重启上位机名称'}
    values = {'enabled': True}
    for key, label in labels.items():
        value = config.get(key, '')
        if not isinstance(value, str) or any(char in value for char in '\r\n\0'):
            raise ValueError(f'{label}必须是单行文本')
        values[key] = value.strip()
        if key == 'host_path':
            values[key] = values[key].strip('"').strip()
    missing = [label for key, label in labels.items() if not values[key]]
    if missing:
        raise ValueError('APP刷写参数未填写完整：' + '、'.join(missing))
    name = values['restart_name']
    if (not name.casefold().endswith('.exe') or any(char in name for char in '<>:"/\\|?*')
            or any(ord(char) < 32 for char in name)):
        raise ValueError('重启上位机名称必须是单独的 exe 文件名，例如 BMS_General.exe')
    return values


def prepare_app_flash(config, logger=None, cancel=None):
    """选择型号时停止上位机、校验写入并重启；任何失败均不进入下一阶段。"""
    values = validate_app_flash(config)
    if values is None:
        return None
    check_cancelled(cancel)
    try:
        ini, encoding, raw, updated, program = _prepare_ini_update(values)
    except Exception as exc:
        raise ValueError(f'上位机参数检查失败：{exc}') from exc
    stages = (
        ('停止上位机', lambda: stop_host(values['restart_name'], cancel)),
        ('停止确认后等待 500 ms', lambda: wait_seconds(.5, cancel)),
        ('写入并校验 config.ini', lambda: _write_and_verify_ini(ini, encoding, raw, updated, values)),
        ('写入确认后等待 500 ms', lambda: wait_seconds(.5, cancel)),
        ('启动并确认上位机', lambda: start_host(program, cancel)),
    )
    for label, action in stages:
        check_cancelled(cancel)
        if logger:
            logger(f'[上位机准备] {label}：{program.name if "上位机" in label else ini}')
        try:
            action()
        except Exception as exc:
            raise RuntimeError(f'{label}失败：{exc}') from exc
    if logger:
        logger(f'[APP刷写参数] {ini} → [App] Type={values["model"]}；Addr={values["flash_path"]}；上位机已启动')
    return values


def _prepare_ini_update(values):
    host = Path(os.path.abspath(os.path.expandvars(values['host_path'])))
    directory = host if host.is_dir() else host.parent
    if not host.is_file() and not host.is_dir():
        raise ValueError(f'未找到上位机程序文件或目录：{host}')
    # 文件路径决定实际启动的 EXE；目录形式仅兼容之前保存的配方。
    program = directory / values['restart_name'] if host.is_dir() else host
    if program.suffix.casefold() != '.exe':
        raise ValueError(f'启用上位机重启时，请将上位机路径填写为 exe 文件：{host}')
    if program.name.casefold() != values['restart_name'].casefold():
        raise ValueError('重启上位机名称与上位机路径中的 exe 文件名不一致，请检查配方参数')
    if not program.is_file():
        raise ValueError(f'未找到重启上位机程序：{program}')
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
    _verify_ini_values(updated.decode(encoding), values)
    return ini, encoding, raw, updated, program


def _verify_ini_values(text, values):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    sections = [name for name in parser.sections() if name.strip().casefold() == 'app']
    if len(sections) != 1 or any(parser.get(sections[0], key, fallback=None) != values[value]
                                 for key, value in (('Type', 'model'), ('Addr', 'flash_path'))):
        raise ValueError('config.ini 回读校验失败：Type 或 Addr 与当前配方不一致')


def _write_and_verify_ini(ini, encoding, raw, updated, values):
    # 停止程序可能触发其退出保存；重新基于最新文件定点更新，保留其他配置。
    if ini.read_bytes() != raw:
        ini, encoding, raw, updated, _ = _prepare_ini_update(values)
    if updated != raw:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=ini.parent, prefix='.app_config_', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, ini)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    _verify_ini_values(ini.read_bytes().decode(encoding), values)
