"""Guard legacy power/calibrator commands without changing driver protocols."""
import re

from core.numeric_validation import NUMBER, finite_reading
from core.instrument_control import (confirm_result as _confirm, ensure_active as _active,
    control_power, control_calibrator, connect_instrument, instrument_transaction,
    shutdown_calibrator)


ON = {'ON', '开启', '开启输出'}
OFF = {'OFF', '关闭', '关闭输出'}
KEEP = {'KEEP', '保持', '保持现状'}


def _parts(text):
    return [part.strip() for part in re.split(r'[/；;]', str(text)) if part.strip()]


def _states(parts):
    values = [part.split(':', 1)[-1].strip().upper() for part in parts]
    return any(value in ON for value in values), any(value in OFF for value in values)


def _run(driver, build, on, off, enable, disable, cancelled):
    with instrument_transaction(driver):
        return _run_locked(driver, build, on, off, enable, disable, cancelled)


def _run_locked(driver, build, on, off, enable, disable, cancelled):
    errors = []
    try:
        if on and off:
            raise ValueError('不能同时要求开启和关闭输出')
        actions = build()
        for action, configuration in actions:
            _active(cancelled)
            _confirm(action(), driver, configuration)
        if on:
            _active(cancelled)
            _confirm(enable(), driver)
        if not off:
            _active(cancelled)
    except Exception as exc:
        errors.append(str(exc))
    # OFF must not be short-circuited by invalid settings, failed setup or stop.
    if off:
        try:
            disable()
        except Exception as exc:
            errors.append('关闭输出失败: ' + str(exc))
    if errors:
        raise RuntimeError('; '.join(errors))
    return True, 'PASS'


def execute_power(driver, text, logger, cancelled):
    parts = _parts(text)
    on, off = _states(parts)

    def build():
        settings = {}
        if not parts:
            raise ValueError('电源控制参数不能为空')
        for part in parts:
            if part.upper() in ON | OFF | KEEP:
                continue
            match = re.fullmatch(r'\s*(' + NUMBER + r')\s*([VA]?)\s*', part, re.I)
            if not match:
                raise ValueError('无效电源参数: ' + part)
            key = (match[2] or 'V').upper()
            value = finite_reading(match[1])
            if value < 0 or key in settings:
                raise ValueError('电源参数为负数或重复: ' + part)
            settings[key] = value
        if not settings and not (on or off):
            raise ValueError('没有可执行的电源控制参数')
        actions = []
        for key, method in [('V', 'set_voltage'), ('A', 'set_current')]:
            if key in settings:
                value = settings[key]
                actions.append((lambda method=method, value=value:
                    control_power(driver, method, value, logger=logger, cancelled=cancelled), False))
        return actions

    def disable():
        control_power(driver, 'output_control', False, logger=logger, cancelled=cancelled)

    return _run(driver, build, on, off,
                lambda: control_power(driver, 'output_control', True, logger=logger, cancelled=cancelled), disable, cancelled)


def execute_calibrator(driver, text, logger, cancelled):
    parts = _parts(text)
    on, off = _states(parts)
    structured = any(':' in part for part in parts)
    # Historical bare values mean configure and enable; bare OFF only closes.
    if not structured and not off:
        on = True

    def build():
        fields = {}
        if structured:
            for part in parts:
                key, separator, value = part.partition(':')
                key, value = key.strip().upper(), value.strip()
                if not separator or key not in {'TYPE', 'RANGE', 'VAL', 'OUTPUT'} or key in fields:
                    raise ValueError('无效或重复的CA550参数: ' + part)
                fields[key] = value
            if 'OUTPUT' in fields and fields['OUTPUT'].upper() not in ON | OFF | KEEP:
                raise ValueError('无效的CA550输出状态')
            value = finite_reading(fields['VAL']) if 'VAL' in fields else None
            kind = fields.get('TYPE', '').upper()
        elif off:
            return []
        else:
            value = finite_reading(text)
            kind = 'V' if 'V' in str(text).upper() else 'MA'
        actions = []
        if not driver.is_connected:
            actions.append((lambda: connect_instrument(driver, cancelled), False))
        if kind:
            if kind not in {'V', 'MV', 'MA', 'OHM', 'RTD', 'TC',
                            'TC_K', 'TC_J', 'TC_T', 'TC_E', 'TC_N', 'TC_R', 'TC_S', 'TC_B'}:
                raise ValueError('无效的CA550输出类型')
            code = 1 if kind == 'MA' else 2 if kind == 'OHM' else 3 if kind == 'RTD' else 4 if kind.startswith('TC') else 0
            actions.append((lambda: control_calibrator(driver, 'set_source_func', code, cancelled=cancelled), True))
        if 'RANGE' in fields:
            span = fields['RANGE'].upper()
            if span not in {'AUTO', '100MV', '1V', '5V', '10V', '30V', '20MA', '20MA_SINK'}:
                raise ValueError('无效的CA550量程')
            range_code = 1 if span == '1V' else 2 if span in {'5V', '10V'} else 3 if span == '30V' else 0
            actions.append((lambda: control_calibrator(driver, 'set_source_range', range_code, cancelled=cancelled), True))
        if value is not None:
            actions.append((lambda: control_calibrator(driver, 'set_source_data', value, cancelled=cancelled), False))
        if not actions and not (on or off):
            raise ValueError('没有可执行的CA550参数')
        return actions

    def disable():
        shutdown_calibrator(driver)

    return _run(driver, build, on, off,
        lambda: control_calibrator(driver, 'set_source_output', 1, cancelled=cancelled), disable, cancelled)
