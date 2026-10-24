"""Shared confirmed write operations for debug, sequences and shutdown."""
from core.instrument_control import instrument_transaction, ensure_active
from core.numeric_validation import finite_reading


def control_board(driver, action, *, channel=1, channels=None, state=None, resistance=0,
                  cancelled=lambda: False):
    if action not in ('relay', 'all_relays', 'resistance', 'resistances', 'insulation'):
        raise ValueError('板卡操作无效')
    if type(channel) is not int or channel < 1:
        raise ValueError('板卡通道必须为正整数')
    if action not in ('resistance', 'resistances') and type(state) is not bool:
        raise ValueError('板卡输出状态必须为布尔值')
    closing = ((action not in ('resistance', 'resistances') and state is False) or
               (action in ('resistance', 'resistances') and type(resistance) is int and resistance == 0))
    # An explicit insulation OFF must not be prevented by an invalid old setpoint.
    if action == 'insulation' and state is False:
        resistance = 0
    if action in ('resistance', 'resistances', 'insulation'):
        value = finite_reading(resistance)
        if value < 0 or value != int(value):
            raise ValueError('阻值必须为非负整数')
        resistance = int(value)
    if action == 'resistances':
        channels = list(channels or [])
        if (not channels or len(channels) != len(set(channels)) or
                any(type(ch) is not int or not 1 <= ch <= 6 for ch in channels)):
            raise ValueError('电阻通道未配置或重复')
    with instrument_transaction(driver):
        if not closing:
            ensure_active(cancelled)
        if action == 'resistance':
            result = driver.set_resistance(resistance, channel)
            if type(result) is not int or result != resistance:
                raise RuntimeError('板卡阻值设置未确认')
        elif action == 'resistances':
            result = driver.set_resistances(resistance, channels,
                                           cancelled=(lambda: False) if closing else cancelled)
            if (not isinstance(result, dict) or set(result) != set(channels) or
                    any(type(value) is not int or value != resistance for value in result.values())):
                raise RuntimeError('板卡批量阻值设置未确认')
        elif action == 'insulation':
            result = driver.set_resistance_output(state, resistance)
            if not isinstance(result, dict) or result.get('output') is not state:
                raise RuntimeError('绝缘板输出状态未确认')
        else:
            result = (driver.set_relay(channel, state) if action == 'relay'
                      else driver.all_relays(state))
            if not isinstance(result, (list, tuple)) or not result:
                raise RuntimeError('板卡继电器状态未确认')
            setting = getattr(driver, 'setting', {})
            count = setting.get('channels') if isinstance(setting, dict) else None
            if type(count) is int and len(result) != count:
                raise RuntimeError('板卡继电器回读通道不完整')
            if any(type(value) is not bool for value in result):
                raise RuntimeError('板卡继电器回读类型无效')
            if action == 'relay':
                matched = channel <= len(result) and result[channel - 1] is state
            else:
                matched = all(value is state for value in result)
            if not matched:
                raise RuntimeError('板卡继电器回读不一致')
        return result
