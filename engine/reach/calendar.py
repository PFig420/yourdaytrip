"""Service calendars and absolute instants (UTC epoch seconds)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")


def to_date(d) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


def service_day_start(d: dt.date) -> int:
    """Instant of GTFS time 00:00:00 on service date d: noon(d) - 12 h."""
    noon = dt.datetime(d.year, d.month, d.day, 12, tzinfo=TZ)
    return int(noon.timestamp()) - 12 * 3600


def to_instant(d: dt.date, gtfs_seconds: int) -> int:
    return service_day_start(d) + gtfs_seconds


def local_instant(d: dt.date, hhmm: str) -> int:
    """Wall-clock local time on date d (Europe/Lisbon); accepts 'HH:MM[:SS]' or an ISO datetime."""
    s = str(hhmm)
    if "T" in s or " " in s:
        t = dt.datetime.fromisoformat(s.replace(" ", "T"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=TZ)
        return int(t.timestamp())
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.append(0)
    t = dt.datetime(d.year, d.month, d.day, parts[0], parts[1], parts[2], tzinfo=TZ)
    return int(t.timestamp())


def iso_local(instant: int) -> str:
    return dt.datetime.fromtimestamp(instant, TZ).isoformat()


def ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def active_services(feed, d: dt.date) -> set:
    ds = ymd(d)
    out = set()
    wd = d.weekday()
    for sid, (days, start, end) in feed.calendar.items():
        if days[wd] and start <= ds <= end:
            out.add(sid)
    for sid, dates in feed.removed.items():
        if ds in dates:
            out.discard(sid)
    for sid, dates in feed.added.items():
        if ds in dates:
            out.add(sid)
    return out


def feed_covers(feed, d: dt.date) -> bool:
    ds = ymd(d)
    return bool(feed.first_date) and feed.first_date <= ds <= feed.last_date
