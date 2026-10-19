"""Adapt legacy multi-channel recipes to a sequential single-channel run."""
import copy

LEGACY_CONTROL_TYPES = ('同步屏障', '申请顺序锁', '释放顺序锁')
LEGACY_OPTIONS = ('sync_exec', 'seq_exec', 'exec_mode', 'is_block_start', 'is_block_end', 'target_board')


def single_channel_items(items):
    result = copy.deepcopy(items)
    for item in result:
        for key in LEGACY_OPTIONS:
            item.pop(key, None)
        steps = []
        for sub in item.get('sub_steps', []):
            if any(sub.get(key) in LEGACY_CONTROL_TYPES for key in ('type', 'stype', 'device', 'action')):
                continue
            for key in LEGACY_OPTIONS:
                sub.pop(key, None)
            if isinstance(sub.get('params'), dict):
                for key in LEGACY_OPTIONS:
                    sub['params'].pop(key, None)
            steps.append(sub)
        item['sub_steps'] = steps
    return result
