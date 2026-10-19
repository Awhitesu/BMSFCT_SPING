import math
import re

NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
UNITS = {'', 'V', 'mV', 'kV', 'A', 'mA', 'uA', 'µA', 'μA', 'Ω', 'kΩ', 'MΩ',
         'ohm', 'Ohm', 'Hz', 'kHz', 'MHz', 's', 'ms', 'us', 'µs', 'μs', '%', '℃', '°C', 'C'}


def finite_reading(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing or invalid measurement')
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        match = re.fullmatch(r'\s*(' + NUMBER + r')\s*([^\d\s]*)\s*', str(value))
        if not match or match.group(2) not in UNITS:
            raise ValueError('Invalid numeric measurement')
        result = float(match.group(1))
    if not math.isfinite(result):
        raise ValueError('Non-finite measurement')
    return result
