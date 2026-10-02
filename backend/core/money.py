import re
from decimal import Decimal, InvalidOperation

_NON_NUMERIC = re.compile(r"[^0-9.\-]")


def to_cents(value) -> int | None:
    # parses "$1,234.50", "1234.5", 1234.5 into integer cents, none if unparseable
    if value is None:
        return None
    if isinstance(value, int):
        return value * 100
    cleaned = _NON_NUMERIC.sub("", str(value).replace(",", ""))
    if cleaned in ("", "-", "."):
        return None
    try:
        return int((Decimal(cleaned) * 100).to_integral_value())
    except InvalidOperation:
        return None


def fmt_cents(cents: int | None) -> str:
    if cents is None:
        return "n/a"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"
