"""Opening hours: the OSM `opening_hours` subset of PlacesLayer.md 5.1.

Rules are separated by `;` and applied left to right; a later rule overrides earlier ones for the days it
names. `PH` rules take precedence on public holidays. Intervals are seconds since local midnight of the
queried date; an end past midnight is above 86400 and belongs to the opening day.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_D = "(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_M = "(?:" + "|".join(MONTHS) + ")"

_DATE_RE = re.compile(rf"^(?P<m1>{_M})(?:\s+(?P<d1>\d{{1,2}}))?(?:-(?P<m2>{_M})(?:\s+(?P<d2>\d{{1,2}}))?)?(?=\s|$)")
_DAYSEL_RE = re.compile(rf"^(?P<sel>(?:{_D}|PH)(?:-{_D})?(?:,(?:{_D}|PH)(?:-{_D})?)*)(?=\s|$)")
_TIME = r"(?:[01]?\d|2[0-4]):[0-5]\d"
_RANGE_RE = re.compile(rf"^({_TIME})-({_TIME})$")


class HoursError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    season: tuple | None        # ((m1, d1), (m2, d2)) inclusive; d None = whole month
    weekdays: frozenset | None  # None = every day
    ph: bool
    times: tuple | None         # None = off; tuple of (start_s, end_s)


@dataclass(frozen=True)
class Hours:
    rules: tuple
    always: bool = False

    def intervals(self, d: date, holiday: bool = False) -> list[tuple[int, int]]:
        if self.always:
            return [(0, 86400)]
        ph_rules = [r for r in self.rules if r.ph and _in_season(r.season, d)]
        cur: tuple = ()
        if holiday and ph_rules:
            for r in ph_rules:
                cur = r.times or ()
            return _merge(cur)
        for r in self.rules:
            if not _in_season(r.season, d):
                continue
            # a PH-only rule has an empty weekday set, so it never matches a non-holiday
            if r.weekdays is None or d.weekday() in r.weekdays:
                cur = r.times or ()
        return _merge(cur)


def _merge(times):
    out = []
    for s, e in sorted(times):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _in_season(season, d: date) -> bool:
    if season is None:
        return True
    (m1, d1), (m2, d2) = season
    cur = (d.month, d.day)
    start = (m1, d1 or 1)
    end = (m2, d2 or 31)
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end            # wraps around the year end


def _sec(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 3600 + int(m) * 60


def _parse_points(s: str, rule: str):
    """Fixed entry times: 'HH:MM,HH:MM' or 'off' -> tuple of (t, t)."""
    if s == "off":
        return None
    out = []
    for part in s.split(","):
        part = part.strip()
        if not re.fullmatch(_TIME, part):
            raise HoursError(f"unsupported entry time {part!r} in rule {rule!r}")
        t = _sec(part)
        if t >= 24 * 3600:
            raise HoursError(f"entry time out of range in {part!r}")
        out.append((t, t))
    return tuple(out)


def _parse_times(s: str, rule: str):
    if s == "off":
        return None
    out = []
    for part in s.split(","):
        m = _RANGE_RE.match(part.strip())
        if not m:
            raise HoursError(f"unsupported time range {part!r} in rule {rule!r}")
        a, b = _sec(m.group(1)), _sec(m.group(2))
        if a > 24 * 3600 or b > 24 * 3600 or a == 24 * 3600:
            raise HoursError(f"time out of range in {part!r}")
        if b == a:
            raise HoursError(f"empty time range {part!r}")
        if b < a:
            b += 24 * 3600                       # runs past midnight, belongs to the opening day
        out.append((a, b))
    return tuple(out)


def _parse_days(sel: str):
    days, ph = set(), False
    for item in sel.split(","):
        if item == "PH":
            ph = True
        elif "-" in item:
            a, b = item.split("-")
            i, j = DAYS.index(a), DAYS.index(b)
            days |= {(i + k) % 7 for k in range(((j - i) % 7) + 1)}
        else:
            days.add(DAYS.index(item))
    return frozenset(days), ph


def parse(text: str, points: bool = False) -> Hours:
    """Parse an opening_hours string of the supported subset; raise HoursError otherwise.
    With points=True the times are single entry times (fixed departures) instead of ranges."""
    s = (text or "").strip()
    if not s:
        raise HoursError("empty opening hours")
    if s == "24/7" and not points:
        return Hours((), always=True)
    rules = []
    for raw in s.split(";"):
        rule = raw.strip()
        if not rule:
            raise HoursError(f"empty rule in {text!r}")
        rest = rule
        season = None
        m = _DATE_RE.match(rest)
        if m:
            m1 = MONTHS.index(m["m1"]) + 1
            d1 = int(m["d1"]) if m["d1"] else None
            if m["m2"]:
                m2, d2 = MONTHS.index(m["m2"]) + 1, int(m["d2"]) if m["d2"] else None
            else:
                m2, d2 = m1, d1
            for dd in (d1, d2):
                if dd is not None and not 1 <= dd <= 31:
                    raise HoursError(f"bad day in {rule!r}")
            season = ((m1, d1), (m2, d2))
            rest = rest[m.end():].strip()
        weekdays, ph = None, False
        m = _DAYSEL_RE.match(rest)
        if m:
            weekdays, ph = _parse_days(m["sel"])
            rest = rest[m.end():].strip()
        if not rest:
            raise HoursError(f"missing times in {rule!r}")
        if rest == "24/7":
            raise HoursError(f"24/7 must stand alone: {rule!r}")
        times = _parse_points(rest, rule) if points else _parse_times(rest, rule)
        if weekdays is None and ph:
            weekdays = frozenset()
        rules.append(Rule(season, weekdays, ph, times))
    return Hours(tuple(rules))


def intervals(hours: str, d: date, holidays=frozenset()) -> list[tuple[int, int]]:
    """Local-time intervals (seconds since local midnight of d) the place is open on date d.
    `holidays` is a set of dates, or a bool (True if d is a public holiday)."""
    h = parse(hours)
    is_ph = holidays if isinstance(holidays, bool) else d in holidays
    return h.intervals(d, is_ph)


def entry_points(entry_times: str, d: date, holidays=frozenset()) -> list[int]:
    """Fixed entry times (guided tours, departures) on date d, as seconds since local midnight, sorted.
    Same selectors as opening hours: 'Mar-Oct Tu-Sa 10:30,11:30; Nov-Feb Tu-Sa 09:30; PH off'."""
    h = parse(entry_times, points=True)
    is_ph = holidays if isinstance(holidays, bool) else d in holidays
    return [a for a, _ in h.intervals(d, is_ph)]
