"""Confirmed simulator operations shared by debug, sequence and shutdown."""
from core.instrument_control import instrument_transaction, ensure_active, confirm_result
from core.numeric_validation import finite_reading
import time


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
