"""Recipe-controlled numeric display; never change measurements or judgments."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import re

from core.numeric_validation import NUMBER, UNITS


MAX_DECIMAL_PLACES = 15
_READING = re.compile(r'\s*(' + NUMBER + r')(\s*[^\d\s]*)\s*')


def decimal_places(value):
    """Missing/legacy settings retain the original display."""
    if type(value) is int and 0 <= value <= MAX_DECIMAL_PLACES:
        return value
    return None


def format_result(value, item=None):
    item = item or {}
    original = str(value)
    places = decimal_places(item.get('decimal_places'))
    if (places is None or isinstance(value, bool) or value is None or
            item.get('standard_type', '数值') in ('字符串', '变量')):
        return original
    match = _READING.fullmatch(original)
    if not match or match[2].strip() not in UNITS:
        return original
    try:
        number = Decimal(match[1])
        if not number.is_finite():
            return original
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits), number.adjusted() + places + 2)
            truncated = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
        if truncated.is_zero():
            truncated = abs(truncated)
        return format(truncated, f'.{places}f') + match[2]
    except (InvalidOperation, ValueError):
        return original
