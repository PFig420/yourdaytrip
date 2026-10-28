"""Local-time helpers. All instants are UTC epoch seconds; display is Europe/Lisbon wall clock."""
from __future__ import annotations

import datetime as dt

from reach.calendar import TZ, iso_local, local_instant


def hm(t: int) -> str:
    return dt.datetime.fromtimestamp(t, TZ).strftime("%H:%M")


def hms(t: int) -> str:
    return dt.datetime.fromtimestamp(t, TZ).strftime("%H:%M:%S")


def iso(t: int) -> str:
    return iso_local(t)


def at(d: dt.date, hhmm: str) -> int:
    return local_instant(d, hhmm)


def wall(d: dt.date, secs: int) -> int:
    """Instant of `secs` after local midnight of d (wall clock, so DST days stay right)."""
    day = d + dt.timedelta(days=secs // 86400)
    s = secs % 86400
    return local_instant(day, f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")


def minutes(a: int, b: int) -> int:
    return round((b - a) / 60)
