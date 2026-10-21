"""Confirmed simulator operations shared by debug, sequence and shutdown."""
from core.instrument_control import instrument_transaction, ensure_active, confirm_result
from core.numeric_validation import finite_reading
import time


def simulator_configuration(value):
    """Validate one recipe configuration before sending any hardware command."""
    if (not isinstance(value, dict) or not value or
            set(value) - {'voltage', 'current', 'range', 'output'} or
            type(value.get('output')) is not bool):
        raise ValueError('模拟器快捷配置必须包含明确的输出状态')
    result = dict(value)
    for key in ('voltage', 'current'):
        if key in result:
            result[key] = finite_reading(result[key])
            if result[key] < 0:
                raise ValueError('模拟器电压和限流不能为负数')
    if 'range' in result and result['range'] not in ('HIGH', 'LOW', 'AUTO'):
        raise ValueError('模拟器量程无效')
    if result.get('range') == 'LOW' and result.get('current', 0) > .001000001:
        raise ValueError('LOW range current limit must not exceed 1 mA')
    return result


def configure_simulator(driver, channels, configuration, *, logger=None, cancelled=lambda: False):
    """Apply recipe settings together; verify live voltage only after final ON."""
    channels = list(channels)
    if (not channels or len(channels) != len(set(channels)) or
            any(type(ch) is not int or not 1 <= ch <= 24 for ch in channels)):
        raise ValueError('模拟器通道无效或重复')
    configuration = simulator_configuration(configuration)
    handler = getattr(type(driver), 'configure_confirmed', None)
    if callable(handler):
        started = time.monotonic()
        if logger:logger(f'Simulator control BEGIN action=configure, channels={channels}, value={configuration}, batch=True')
        try:
            result = driver.configure_confirmed(channels, configuration, logger=logger, cancelled=cancelled)
            confirm_result(result, driver)
        except Exception as exc:
            if logger:logger(f'Simulator control FAILED action=configure, elapsed={time.monotonic()-started:.3f}s, reason={exc}')
            raise
        if logger:logger(f'Simulator control CONFIRMED action=configure, elapsed={time.monotonic()-started:.3f}s')
        return True
    # Retain the existing path for drivers without the combined operation.
    order = ('current', 'range', 'voltage') if configuration.get('range') == 'LOW' else ('range', 'current', 'voltage')
    try:
        for key in order:
            if key in configuration:
                control_simulator(driver, key, channels, configuration[key], logger=logger,
                                  cancelled=cancelled, batch=True)
        if configuration['output']:
            return control_simulator(driver, 'output', channels, True, logger=logger,
                                     cancelled=cancelled, batch=True)
        return True
    finally:
        if configuration['output'] is False:
            control_simulator(driver, 'output', channels, False, logger=logger, batch=True)


def control_simulator(driver, action, channels, value=None, *, logger=None,
                      cancelled=lambda: False, batch=False):
    channels = list(channels)
    if (not channels or len(channels) != len(set(channels)) or
            any(type(ch) is not int or not 1 <= ch <= 24 for ch in channels)):
        raise ValueError('模拟器通道无效或重复')
    if action not in ('voltage', 'current', 'range', 'output', 'voltage_on'):
        raise ValueError('模拟器操作无效')
    if action in ('voltage', 'current', 'voltage_on'):
        value = finite_reading(value)
        if value < 0:
            raise ValueError('模拟器电压和限流不能为负数')
    elif action == 'output' and type(value) is not bool:
        raise ValueError('模拟器输出状态必须为布尔值')
    elif action == 'range' and value not in ('HIGH', 'LOW', 'AUTO'):
        raise ValueError('模拟器量程无效')
    handler=getattr(type(driver),'apply_confirmed',None)
    if callable(handler):
        started=time.monotonic()
        if logger:logger(f'Simulator control BEGIN action={action}, channels={channels}, value={value}, batch={batch}')
        try:
            result=driver.apply_confirmed(action,channels,value,logger=logger,cancelled=cancelled,batch=batch)
        except Exception as exc:
            if logger:logger(f'Simulator control FAILED action={action}, channels={channels}, value={value}, elapsed={time.monotonic()-started:.3f}s, reason={exc}')
            raise
        if logger:logger(f'Simulator control CONFIRMED action={action}, channels={channels}, value={value}, elapsed={time.monotonic()-started:.3f}s')
        return result
    with instrument_transaction(driver):
        if not (action == 'output' and value is False):
            ensure_active(cancelled)
        if action in ('voltage', 'voltage_on'):
            result = (driver.set_voltage(channels[0], value, logger) if len(channels) == 1 and not batch
                      else driver.set_voltages(value, channels, logger))
            confirm_result(result, driver)
            if action == 'voltage_on':
                ensure_active(cancelled)
                confirm_result(driver.output_controls(True, channels, logger), driver)
        elif action == 'output':
            result = (driver.output_control(channels[0], value, logger) if len(channels) == 1 and not batch
                      else driver.output_controls(value, channels, logger))
            confirm_result(result, driver)
        else:
            method = driver.set_range if action == 'range' else driver.set_current_limit
            for channel in channels:
                ensure_active(cancelled)
                confirm_result(method(channel, value, logger=logger), driver)
    return True


def read_simulator(driver,quantity,channel,logger=None,cancelled=lambda:False):
    if callable(getattr(type(driver),'read_stable',None)):
        return driver.read_stable(channel,quantity,logger,cancelled)
    ensure_active(cancelled)
    return (driver.measure_voltage(channel,logger) if quantity=='voltage'
            else driver.measure_current(channel,logger))
