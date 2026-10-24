"""回读来源单位及测试项最终结果的标准单位换算。"""

import math
import re
from decimal import Decimal
from core.numeric_validation import NUMBER


VOLTAGE_UNITS = ("V", "mV")
CURRENT_UNITS = ("A", "mA", "μA")
_FACTORS = {"V": 1.0, "mV": 1000.0, "A": 1.0, "mA": 1000.0, "μA": 1000000.0}
_UNIT_PARAM = re.compile(r"(?:^|[/；，])\s*UNIT\s*[:：]\s*([^/；，]*)", re.IGNORECASE)
_MEASUREMENT = re.compile(r'\s*(' + NUMBER + r')\s*([^\d\s]+)\s*')
_UNIT_SCALES = {
    unit: (quantity, scale)
    for quantity, scales in (
        ('电压', {'kV': 1e3, 'V': 1, 'mV': 1e-3, 'uV': 1e-6, 'nV': 1e-9}),
        ('电流', {'A': 1, 'mA': 1e-3, 'uA': 1e-6, 'nA': 1e-9}),
        ('电阻', {'Ω': 1, 'ohm': 1, 'Ohm': 1, 'kΩ': 1e3, 'MΩ': 1e6, 'mΩ': 1e-3, 'uΩ': 1e-6}),
        ('频率', {'Hz': 1, 'kHz': 1e3, 'MHz': 1e6, 'GHz': 1e9}),
        ('时间', {'s': 1, 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9}),
        ('温度', {'℃': 1, '°C': 1, 'C': 1}),
        ('百分比', {'%': 1, '%RH': 1}),
    )
    for unit, scale in scales.items()
}


def supports_read_units(device):
    return any(name in str(device).lower() for name in ("it6722", "dut power", "被测物供电", "main power", "主机板"))


def configured_read_unit(params):
    match = _UNIT_PARAM.search(str(params))
    unit = match.group(1).strip() if match else ""
    return {"uA": "μA", "µA": "μA"}.get(unit, unit)


def is_voltage_read(params):
    # The measurement kind comes from the read command, not the unit suffix.
    # RW is an opaque base64 payload; its letters must never select a sensor.
    command = re.split(r'(?:^|\s/\s)RW:', str(params), maxsplit=1)[0]
    command = _UNIT_PARAM.sub("", command)
    if "电流" in command or "current" in command.lower():
        return False
    return "电压" in command or "volt" in command.lower() or "V" in command.upper()


class MeasurementValue(float):
    """A numeric result that carries its unit through worker signals and judgment."""

    def __new__(cls, value, unit=""):
        result = super().__new__(cls, value)
        result.unit = unit
        return result


def convert_result(value, target_unit):
    """仅在来源明确时换算，纯数值及状态文本保持原处理。"""
    target = str(target_unit or '').strip()
    if target.upper() in ('', 'NULL', 'NONE', '--'):
        return value
    source = str(getattr(value, 'unit', '') or '').strip()
    number = value
    if not source and isinstance(value, str):
        match = _MEASUREMENT.fullmatch(value)
        if match and match[2].replace('µ', 'u').replace('μ', 'u') in _UNIT_SCALES:
            number, source = match.groups()
    if not source:
        return value
    if source == target:
        return value
    source_scale = _UNIT_SCALES.get(source.replace('µ', 'u').replace('μ', 'u'))
    target_scale = _UNIT_SCALES.get(target.replace('µ', 'u').replace('μ', 'u'))
    if not source_scale or not target_scale or source_scale[0] != target_scale[0]:
        raise ValueError(f'不能将 {source} 换算为 {target}')
    converted = float(Decimal(str(number)) * Decimal(str(source_scale[1])) / Decimal(str(target_scale[1])))
    if not math.isfinite(converted):
        raise ValueError('单位换算结果不是有效数值')
    return MeasurementValue(converted, target)


def convert_reading(value, is_voltage, unit):
    allowed = VOLTAGE_UNITS if is_voltage else CURRENT_UNITS
    if unit not in allowed:
        raise ValueError(f"回读单位 {unit!r} 与读取量不匹配；可选单位: {', '.join(allowed)}")
    converted = float(value) * _FACTORS[unit]
    if not math.isfinite(converted):
        raise ValueError("回读结果不是有效数值")
    return MeasurementValue(converted, unit)
