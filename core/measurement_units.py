"""Units for IT6722A readings (the driver returns volts and amperes)."""

import math
import re


VOLTAGE_UNITS = ("V", "mV")
CURRENT_UNITS = ("A", "mA", "μA")
_FACTORS = {"V": 1.0, "mV": 1000.0, "A": 1.0, "mA": 1000.0, "μA": 1000000.0}
_UNIT_PARAM = re.compile(r"(?:^|[/；，])\s*UNIT\s*[:：]\s*([^/；，]*)", re.IGNORECASE)


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


def convert_reading(value, is_voltage, unit):
    allowed = VOLTAGE_UNITS if is_voltage else CURRENT_UNITS
    if unit not in allowed:
        raise ValueError(f"回读单位 {unit!r} 与读取量不匹配；可选单位: {', '.join(allowed)}")
    converted = float(value) * _FACTORS[unit]
    if not math.isfinite(converted):
        raise ValueError("回读结果不是有效数值")
    return MeasurementValue(converted, unit)
