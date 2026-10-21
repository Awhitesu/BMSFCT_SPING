"""Explicit physical channels for recipe steps, including legacy-default migration."""
import re

_PCH = re.compile(r'(?:^|\s*/\s*)PCH\s*:\s*([^/]*)', re.I)


def selection(text):
    matches = _PCH.findall(str(text))
    if len(matches) > 1:raise ValueError('工步物理通道参数重复')
    return matches[0].strip() if matches else '0'


def resolve_channels(text, default=1, maximum=4, enabled=None, multiple=False):
    token = selection(text)
    enabled = set(range(1, maximum + 1)) if enabled is None else set(enabled)
    if token.upper() == 'ALL':
        if not multiple:raise ValueError('此工步必须指定单一物理通道')
        channels = sorted(enabled)
    elif token == '0':
        channels = [int(default)]
    else:
        parts = token.replace('，', ',').split(',')
        if any(not part.strip().isdigit() for part in parts):raise ValueError('物理通道格式无效')
        channels = [int(part.strip()) for part in parts]
    if not channels:raise ValueError('没有可用的已启用CAN通道')
    if not multiple and len(channels) != 1:raise ValueError('此工步必须指定单一物理通道')
    if len(channels) != len(set(channels)):raise ValueError('物理通道重复')
    for channel in channels:
        if not 1 <= channel <= maximum:raise ValueError(f'物理通道必须为CH1–CH{maximum}')
        if channel not in enabled:raise ValueError(f'所选物理通道CH{channel}未启用')
    return channels


def is_can_step(step):
    text = ' '.join(str(step.get(key, '')) for key in ('device', 'type', 'stype')).upper()
    return any(word in text for word in ('CAN', 'UDS', 'EOL', '报文交互'))


def is_plain_send(step):
    return 'CAN发送' in str(step.get('type', step.get('stype', ''))) or (
        step.get('action') == '发送指令' and 'UDS' not in str(step.get('device', '')).upper())


def explicit_step(step, default):
    """Return a copy whose former default selection still means the same physical port."""
    result = dict(step)
    if not is_can_step(step):return result
    text = str(step.get('params', ''))
    if selection(text) == '0':
        if _PCH.search(text):
            result['params'] = _PCH.sub(lambda match:match[0].replace('0', str(default), 1), text)
        else:
            result['params'] = text + (' / ' if text.strip() else '') + f'PCH:{default}'
    return result


def migrate_recipe_channels(recipe):
    config = recipe.get('can_config')
    if not config or config.get('enabled') is False:return 0
    channel = str(config.get('channel', ''))
    if not channel.startswith(('ZLG_CAN', 'PCAN_')):return 0
    default = int(channel.removeprefix('ZLG_CAN')) if channel.startswith('ZLG_CAN') else 1
    zlg = None
    if channel.startswith('ZLG_CAN') and config.get('zlg') is not None:
        from core.zlg_recipe_config import validate_zlg_config
        zlg = validate_zlg_config(config['zlg'])
    changed = 0
    for item in recipe.get('items', []):
        for step in item.get('sub_steps', []):
            updated = explicit_step(step, default)
            if updated != step:step.update(updated);changed += 1
    if zlg is not None:
        selected = zlg['channels'][zlg['send_channel'] - 1]
        config.update(zlg=zlg, channel=f'ZLG_CAN{zlg["send_channel"]}',
                      nominal_bitrate=selected['nominal'], data_bitrate=selected['data'])
    return changed


def validate_step_channels(recipe):
    config = recipe.get('can_config') or {}
    channel = str(config.get('channel', ''))
    if config.get('enabled') is False or not channel.startswith(('ZLG_CAN','PCAN_')):return
    maximum = 4 if channel.startswith('ZLG_CAN') else 1
    default = int(channel.removeprefix('ZLG_CAN')) if maximum == 4 else 1
    enabled = None
    if maximum == 4 and config.get('zlg') is not None:
        from core.zlg_recipe_config import validate_zlg_config
        zlg = validate_zlg_config(config['zlg'])
        enabled = {i+1 for i, row in enumerate(zlg['channels']) if row['enabled']}
    for item in recipe.get('items', []):
        if item.get('skip_runtime') or str(item.get('name','')).strip().startswith('#'):continue
        for step in item.get('sub_steps', []):
            if not is_can_step(step) or step.get('skip_runtime') or str(step.get('name','')).strip().startswith('#'):continue
            try:resolve_channels(step.get('params',''), default, maximum, enabled, is_plain_send(step))
            except ValueError as exc:
                raise ValueError(f'测试项【{item.get("name", "")}】的工步【{step.get("name", "")}】：{exc}') from exc
