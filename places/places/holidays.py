"""Public holidays: national dates computed, municipal dates from config/holidays.toml."""
from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path


def easter(y: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(y, month, day + 1)


def national_holidays(y: int) -> set[date]:
    """Copied from converter/gtfsconv/calendars.py (national_holidays, Carnival excluded)."""
    e = easter(y)
    return {
        date(y, 1, 1), e - timedelta(days=2), e, date(y, 4, 25), date(y, 5, 1),
        e + timedelta(days=60), date(y, 6, 10), date(y, 8, 15), date(y, 10, 5),
        date(y, 11, 1), date(y, 12, 1), date(y, 12, 8), date(y, 12, 25),
    }


class Holidays:
    def __init__(self, municipal: dict[str, list[str]] | None = None):
        self.municipal = municipal or {}

    @classmethod
    def load(cls, path) -> "Holidays":
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p, "rb") as f:
            return cls(tomllib.load(f).get("municipal", {}))

    @staticmethod
    def resolve(entry: str, y: int) -> date:
        """'MM-DD' or 'easter+N' -> the date in year y."""
        if entry.startswith("easter"):
            return easter(y) + timedelta(days=int(entry[6:] or 0))
        m, dd = (int(x) for x in entry.split("-"))
        return date(y, m, dd)

    def for_year(self, y: int, municipality: str | None = None) -> set[date]:
        out = set(national_holidays(y))
        for entry in self.municipal.get(municipality or "", ()):
            out.add(self.resolve(entry, y))
        return out

    def is_holiday(self, d: date, municipality: str | None = None) -> bool:
        return d in self.for_year(d.year, municipality)
