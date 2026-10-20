"""Number formatting shared by API and notification text (``2.4m``, ``92.7m``, ``204k``)."""

from __future__ import annotations


def short_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    for suffix, size in (("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if v >= size:
            s = f"{v / size:.{digits}f}".rstrip("0").rstrip(".")
            return f"{sign}{s}{suffix}"
    if v >= 100:
        return f"{sign}{v:.0f}"
    return f"{sign}{v:.{digits}f}".rstrip("0").rstrip(".") if v else "0"


def short_usd(value: float | None) -> str:
    s = short_number(value)
    return s if s == "n/a" else f"${s}"


def pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"
