"""Execute legacy simulator recipe text without silent defaults or false success."""
import re

from core.numeric_validation import NUMBER


def execute_simulator_command(manager, text, channel, logger, cancelled=lambda: False):
    try:
        return _execute_simulator_command(manager, text, channel, logger, cancelled)
    except ValueError as original:
        fields = [field.strip() for field in re.split(r'[/；]', str(text)) if field.strip()]
        off = any(field.upper() == 'OFF' or field == '关闭输出' for field in fields)
        on = any(field.upper() == 'ON' or field == '开启输出' for field in fields)
        if off and not on:
            cleanup = ['OFF'] + [field for field in fields if field.upper().startswith('CH:')]
            try:
                ok, _ = _execute_simulator_command(manager, ' / '.join(cleanup), channel, logger)
                if not ok:
                    raise RuntimeError('关闭输出未确认成功')
            except Exception as stop_error:
                raise RuntimeError(f'{original}；关闭输出也失败：{stop_error}') from original
        raise


def _execute_simulator_command(manager, text, channel, logger, cancelled=lambda: False):
    values = {}
    for field in re.split(r'[/；]', str(text)):
        field = field.strip()
        if not field:
            continue
        match = re.fullmatch(r'(' + NUMBER + r')\s*(V|mA)', field, re.I)
        if match:
            key = 'voltage' if match[2].upper() == 'V' else 'current'
            if key in values:
                raise ValueError('模拟器参数重复')
            value = float(match[1])
            from core.numeric_validation import finite_reading
            value = finite_reading(value)
            if value < 0:
                raise ValueError('模拟器电压/限流不能小于0')
            values[key] = value if key == 'voltage' else value / 1000
        elif field.upper() in ('ON', 'OFF') or field in ('开启输出', '关闭输出'):
            if 'output' in values:
                raise ValueError('模拟器输出参数重复')
            values['output'] = field.upper() == 'ON' or field == '开启输出'
        elif re.fullmatch(r'RANGE\s*:\s*(HIGH|LOW|AUTO)', field, re.I):
            if 'range' in values:
                raise ValueError('模拟器量程参数重复')
            values['range'] = field.split(':', 1)[1].strip().upper()
        elif field.upper().startswith('CH:'):
            if 'channels' in values:
                raise ValueError('模拟器通道参数重复')
            selection = field.split(':', 1)[1].strip()
            if selection.upper() == 'ALL':
                values['channels'] = None
            else:
                pieces = selection.replace('，', ',').split(',')
                if not pieces or any(not part.strip().isdigit() for part in pieces):
                    raise ValueError('模拟器通道格式无效')
                selected = [int(part) for part in pieces]
                if len(set(selected)) != len(selected) or any(ch <= 0 for ch in selected):
                    raise ValueError('模拟器通道重复或无效')
                values['channels'] = selected
        else:
            raise ValueError(f'模拟器参数无法识别：{field}')
    if not any(key in values for key in ('voltage', 'current', 'range', 'output')):
        raise ValueError('模拟器工步没有有效操作')
    channels = values.get('channels', [channel])
    routes = []
    if channels is not None:
        for target in channels:
            sim, local = manager._get_sim_and_ch(target)
            if sim is None:
                raise ValueError(f'模拟器通道未配置：CH{target}')
            routes.append((target, sim, local))
    errors = []
    # Real manager methods accept the same cancellation token as the sequence.
    options={'cancelled':cancelled} if callable(getattr(type(manager),'simulator_channels',None)) else {}
    def confirmed(result, name):
        if result is not True:
            raise RuntimeError(name + '未确认成功')
    def active():
        if cancelled():
            raise RuntimeError('测试已停止')
    try:
        active()
        combined = getattr(type(manager), 'configure_simulators', None)
        if (callable(combined) and 'output' in values and
                any(key in values for key in ('range', 'current', 'voltage'))):
            configuration = {key: value for key, value in values.items() if key != 'channels'}
            confirmed(manager.configure_simulators(configuration, channels, logger=logger,
                                                   cancelled=cancelled), '快捷配置')
            return True, None
        order=('current','range','voltage') if values.get('range')=='LOW' else ('range','current','voltage')
        for key in order:
            if key not in values:
                continue
            active()
            if channels is None:
                confirmed(getattr(manager, 'broadcast_' + key)(values[key], logger=logger,**options), key)
            else:
                for target, sim, local in routes:
                    active()
                    if key == 'range':
                        from core.simulator_control import control_simulator
                        result = control_simulator(sim,'range',[local],values[key],logger=logger,cancelled=cancelled)
                    else:
                        result = getattr(manager, 'set_' + key)(target, values[key], logger=logger,**options)
                    confirmed(result, f'CH{target} {key}')
        active()
        if values.get('output') is True:
            if channels is None:
                confirmed(manager.broadcast_output(True, logger=logger,**options), '开启输出')
            else:
                for target, _, _ in routes:
                    active()
                    confirmed(manager.output_control(target, True, logger=logger,**options), f'CH{target}开启输出')
    except Exception as exc:
        errors.append(str(exc))
    # Explicit OFF must reach every selected channel even after a setup failure.
    if values.get('output') is False:
        try:
            if channels is None:
                confirmed(manager.broadcast_output(False, logger=logger), '关闭输出')
            else:
                for target, _, _ in routes:
                    try:
                        confirmed(manager.output_control(target, False, logger=logger), f'CH{target}关闭输出')
                    except Exception as exc:
                        errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
    for error in errors:
        logger('[模拟器工步失败] ' + error)
    return not errors, None
