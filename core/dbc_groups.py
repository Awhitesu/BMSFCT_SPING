"""Explicit DBC sampling ranges, shared by recipe validation and execution."""
from copy import deepcopy


class DBCGroupError(ValueError):
    def __init__(self, message, position):
        super().__init__(message)
        self.position = position


def test_item_prefix(name):
    """The underscore separates the prefix from the title; # only marks a skipped item."""
    prefix, separator, _ = str(name).strip().lstrip('#').strip().partition('_')
    return prefix.strip() if separator else ''


def sync_group_name(text, item_name):
    """Sync prefixed markers; unprefixed items retain their manually entered names."""
    from core.dbc_signals import decode_step, encode_step
    config = decode_step(text, allow_empty_group=True)
    if not config.get('group_role'):
        return text
    name = test_item_prefix(item_name)
    if not name or config.get('group', '') == name:
        return text
    config['group'] = name
    _, separator, suffix = text.partition(' / ')
    return encode_step(config) + separator + suffix


def validate_group_names(items):
    """Save-time check includes skipped markers, so incomplete groups cannot be saved."""
    for (i, j), _, config, _ in _entries(items, allow_empty_group=True):
        if config and config.get('group_role') and not config.get('group', '').strip():
            raise DBCGroupError(
                f'测试项【{items[i].get("name", "")}】/ 工步{j+1}：采样组名称为空，请填写采样组名称；起点与终点应填写相同组名。',
                (i, j))


def _entries(items, *, allow_empty_group=False):
    from core.dbc_signals import decode_step
    for i, item in enumerate(items):
        parent_active = not item.get('skip_runtime') and not str(item.get('name', '')).strip().startswith('#')
        for j, step in enumerate(item.get('sub_steps', [])):
            active = parent_active and not step.get('skip_runtime') and not str(step.get('name', '')).strip().startswith('#')
            config = None
            if 'CANDBC' in str(step.get('type', '')) + str(step.get('device', '')):
                try:
                    config = decode_step(step.get('params', ''), allow_empty_group=allow_empty_group)
                except (ValueError, TypeError, KeyError):
                    if active:
                        raise
            yield (i, j), step, config, active


def normalize_legacy_groups(items):
    """Convert old consecutive named reads to explicit boundaries, without changing other fields."""
    from core.dbc_signals import encode_step
    pending = []
    explicit = False
    changed = 0

    def flush():
        nonlocal changed
        for index, (step, config) in enumerate(pending):
            config = dict(config)
            if len(pending) == 1:
                config['group_role'] = 'single'
            elif index == 0:
                config['group_role'] = 'start'
            elif index == len(pending) - 1:
                config['group_role'] = 'end'
            else:
                config.pop('group', None)
            _, separator, suffix = step['params'].partition(' / ')
            step['params'] = encode_step(config) + separator + suffix
            changed += 1
        pending.clear()

    # Loading editor drafts must retain explicit markers whose prefix is still missing.
    for _, step, config, _ in _entries(items, allow_empty_group=True):
        role = config.get('group_role', '') if config else ''
        if role:
            flush()
            if role == 'start':
                explicit = True
            elif role == 'end':
                explicit = False
            continue
        if explicit:
            continue
        if config and config.get('group'):
            if pending and pending[-1][1]['group'] != config['group']:
                flush()
            pending.append((step, config))
        else:
            flush()
    flush()
    return changed


def plan_groups(items, normalize=True):
    """Map executable DBC positions to their range; skipped markers still delimit debug subsets."""
    from core.read_until import settings
    if normalize:
        items = deepcopy(items)
        normalize_legacy_groups(items)
    plan = {}
    opened = None

    def finish(end):
        nonlocal opened
        opened['end'] = end
        opened['id'] = (opened['start'], end, opened['name'])
        for position, _, _ in opened['members']:
            plan[position] = opened
        opened = None

    for position, step, config, active in _entries(items):
        if config is None:
            continue
        role = config.get('group_role', '')
        location = f'测试项{position[0]+1} / 工步{position[1]+1}'
        if role in ('start', 'single'):
            if opened:
                raise DBCGroupError(f'{location}：采样组【{opened["name"]}】尚无终点，不能嵌套起点', position)
            opened = dict(name=config['group'], start=position, members=[])
        if role == 'end' and (not opened or opened['name'] != config['group']):
            raise DBCGroupError(f'{location}：采样组【{config["group"]}】终点没有匹配的同名起点', position)
        if opened:
            if config.get('group') and config['group'] != opened['name']:
                raise DBCGroupError(f'{location}：组内名称与采样组【{opened["name"]}】不一致', position)
            if active:
                if settings(step.get('params', '')):
                    raise DBCGroupError(f'{location}：采样组使用固定快照，请使用测试项上下限判定，不支持范围等待', position)
                opened['members'].append((position, config, step))
        elif config.get('group'):
            raise DBCGroupError(f'{location}：采样组【{config["group"]}】缺少起点/终点标记', position)
        if role in ('end', 'single'):
            finish(position)
    if opened:
        raise DBCGroupError(f'采样组【{opened["name"]}】起点缺少同名终点（测试项{opened["start"][0]+1}）', opened['start'])
    return plan
