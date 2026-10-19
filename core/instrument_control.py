"""Shared control boundary for NGI HV, IT6722A, IT6333B and CA550 sources.

Drivers own transport and physical readback; callers own their UI/sequence state.
"""
from contextlib import contextmanager, nullcontext
import threading

from core.numeric_validation import finite_reading


@contextmanager
def instrument_transaction(driver):
    lock = getattr(driver, '_lock', None)
    if not isinstance(lock, type(threading.RLock())):
        lock = getattr(driver, 'lock', None)
    # Only reentrant locks can surround drivers that also lock internally.
    with lock if isinstance(lock, type(threading.RLock())) else nullcontext():
        yield


def ensure_active(cancelled):
    if cancelled():
        raise RuntimeError('测试已停止')


def confirm_result(value, driver, configuration=False):
    if configuration:
        ok = value is not False and value is not None and 'ERROR' not in str(value).upper()
    else:
        ok = value is True
    if not ok:
        reason = getattr(driver, 'last_error', '')
        raise RuntimeError(reason if isinstance(reason, str) and reason else 'Control was not confirmed')
    return value


def connect_instrument(driver, cancelled=lambda: False):
    with instrument_transaction(driver):
        ensure_active(cancelled)
        result = confirm_result(driver.connect(), driver)
        ensure_active(cancelled)
        return result


def control_power(driver, action, value=None, *, logger=None,
                  cancelled=lambda: False, confirmation=None, channel=None):
    if channel is not None and (type(channel) is not int or channel not in (1, 2, 3)):
        raise ValueError('电源通道必须为1、2或3')
    if action not in {'set_voltage', 'set_current', 'output_control', 'clear_errors'}:
        raise ValueError('Unsupported power control action: ' + action)
    if action in {'set_voltage', 'set_current'}:
        value = finite_reading(value)
        if value < 0:
            raise ValueError('电源设定值不能为负数')
    elif action == 'output_control':
        if type(value) is not bool:
            raise ValueError('输出状态必须为布尔值')
    off = action == 'output_control' and value is False
    with instrument_transaction(driver):
        if not off:
            ensure_active(cancelled)
        saved = None
        configuration_error = None
        if confirmation is not None:
            try:
                timeout, tolerance = map(finite_reading, confirmation)
                if timeout <= 0 or tolerance < 0:
                    raise ValueError('控制确认参数无效')
                saved = (driver.control_timeout, driver.output_voltage_tolerance)
            except Exception as exc:
                if not off:
                    raise
                configuration_error = exc
        try:
            if saved is not None:
                try:
                    driver.configure_confirmation(timeout, tolerance)
                except Exception as exc:
                    if not off:
                        raise
                    driver.control_timeout, driver.output_voltage_tolerance = saved
                    configuration_error = exc
            if not off:
                ensure_active(cancelled)
            kwargs = {'logger': logger} if logger is not None else {}
            result = (getattr(driver, action)(**kwargs) if action == 'clear_errors'
                      else getattr(driver, action)(*(([channel] if channel is not None else []) + [value]), **kwargs))
            confirm_result(result, driver)
            if configuration_error is not None:
                raise RuntimeError('确认参数无效，已按原参数尝试关闭：' + str(configuration_error))
            return result
        finally:
            if saved is not None:
                driver.control_timeout, driver.output_voltage_tolerance = saved


def control_calibrator(driver, action, value, *, tolerance=None, cancelled=lambda: False):
    if action == 'set_source_data':
        value = finite_reading(value)
        if tolerance is not None:
            tolerance = finite_reading(tolerance)
            if tolerance < 0:
                raise ValueError('设定值容差不能为负数')
    elif action in {'set_source_func', 'set_source_range'}:
        maximum = 5 if action == 'set_source_func' else 3
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError('校准仪功能或量程编号无效')
    elif action == 'set_source_output':
        if type(value) not in (bool, int) or value not in (0, 1):
            raise ValueError('校准仪输出状态无效')
        value = int(value)
    else:
        raise ValueError('Unsupported calibrator action: ' + action)
    with instrument_transaction(driver):
        if not (action == 'set_source_output' and value == 0):
            ensure_active(cancelled)
        if action == 'set_source_data' and tolerance is not None:
            result = driver.set_source_data(value, tolerance)
        else:
            result = getattr(driver, action)(value)
        return confirm_result(result, driver, configuration=action in {'set_source_func', 'set_source_range'})


def shutdown_calibrator(driver):
    errors = []
    with instrument_transaction(driver):
        for action, value in [('set_source_data', 0.0), ('set_source_output', 0)]:
            try:
                control_calibrator(driver, action, value)
            except Exception as exc:
                errors.append(f'{action}: {exc}')
    if errors:
        raise RuntimeError('; '.join(errors))
    return True


def control_generator(driver, action, channel, *, state=None, parameters=None,
                      cancelled=lambda: False):
    """Preserve the generator's confirmed parameter snapshot for UI and sequence."""
    if type(channel) is not int or channel not in (1, 2):
        raise ValueError('信号发生器通道必须为1或2')
    if action not in ('configure', 'output'):
        raise ValueError('信号发生器操作无效')
    if action == 'output' and type(state) is not bool:
        raise ValueError('输出状态必须为布尔值')
    with instrument_transaction(driver):
        if not (action == 'output' and state is False):
            ensure_active(cancelled)
        if action == 'configure':
            if not isinstance(parameters, (tuple, list)) or len(parameters) != 5:
                raise ValueError('信号发生器配置参数不完整')
            result = driver.configure(channel, *parameters)
            ensure_active(cancelled)
        else:
            result = driver.output_control(channel, state)
        if not isinstance(result, dict) or not result:
            raise RuntimeError(getattr(driver, 'last_error', '') or '信号发生器未返回有效确认数据')
        if action == 'output':
            actual = str(result.get('输出', '')).strip().upper()
            if actual not in (('1', 'ON') if state else ('0', 'OFF')):
                raise RuntimeError('信号发生器输出状态回读不一致')
        return result
