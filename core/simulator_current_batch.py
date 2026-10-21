"""Collect independent per-channel current samples and publish a complete result."""
import math
from core.measurement_units import MeasurementValue


def selected_channels(manager, selection):
    if selection not in ('ALL', 'ODD', 'EVEN'):
        raise ValueError('读取通道选项无效')
    channels = manager.simulator_channels()
    return [route for route in channels if selection == 'ALL' or route[0] % 2 == (1 if selection == 'ODD' else 0)]


def execute_current_batch(manager, config, variables, logger, cancelled=lambda:False):
    # Snapshot routes once so every sample and variable uses the same mapping.
    routes = selected_channels(manager, config['selection'])
    if not routes:
        raise ValueError('没有符合选择的已启用模拟器通道')
    count, mode = config['count'], config['mode']
    if type(count) is not int or not 1 <= count <= 100000 or mode not in ('max', 'mean'):
        raise ValueError('电流采集次数或取值方式无效')
    for channel, _, _ in routes:
        variables.pop(f'电流-{channel}', None)
    virtual = getattr(manager, 'is_virtual', False) is True
    def log(message):
        logger(('[仿真，无硬件通信] ' if virtual else '') + message)
    def active():
        if cancelled():raise RuntimeError('电流采集已停止')
    active()
    # Compensated sums keep high-count reads bounded in memory without losing small currents.
    totals = {channel: 0.0 for channel, _, _ in routes}
    corrections = dict(totals)
    maxima = {channel: -math.inf for channel, _, _ in routes}
    log(f'电流采集开始：{len(routes)}通道，每通道{count}次，取值={"最大值" if mode == "max" else "平均值"}')
    for sample_index in range(count):
        for channel, driver, local in routes:
            active()
            try:
                value = 0.0 if virtual else driver.read_current_sample(local, cancelled=cancelled)
                if isinstance(value, bool) or value is None:
                    raise ValueError('电流数据缺失或类型无效')
                value = float(value)
                if not math.isfinite(value) or abs(value) >= 9e37:
                    raise ValueError('电流数据无效或超量程')
                active()
                if mode == 'max':
                    maxima[channel] = max(maxima[channel], value)
                else:
                    total = totals[channel]
                    combined = total + value
                    corrections[channel] += ((total - combined) + value if abs(total) >= abs(value)
                                             else (value - combined) + total)
                    totals[channel] = combined
            except Exception as exc:
                raise RuntimeError(f'电流采集失败：全局通道{channel}，设备{driver.sequence_number}/CH{local}，'
                                   f'第{sample_index + 1}/{count}次：{exc}') from exc
        if (sample_index + 1) % max(1, count // 10) == 0 or sample_index + 1 == count:
            log(f'电流采集进度：每通道已读取{sample_index + 1}/{count}次')
    results = {}
    for channel, _, _ in routes:
        value = maxima[channel] if mode == 'max' else (totals[channel] + corrections[channel]) / count
        if not math.isfinite(value):raise ValueError(f'通道{channel}计算结果无效')
        results[f'电流-{channel}'] = MeasurementValue(value, 'mA')
    active()
    variables.update(results)
    for key, value in results.items():
        log(f'{key}={float(value):g} mA（{count}次）')
    return f'PASS:已读取{len(routes)}通道，每通道{count}次，已写入{len(results)}个电流变量'
