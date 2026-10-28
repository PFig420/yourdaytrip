"""Holidays, school calendar and per-trip service dates (spec 5.5)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .load import BuildError
from .names import fold


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


def national_holidays(y: int, carnival: bool = False) -> set[date]:
    e = easter(y)
    out = {
        date(y, 1, 1), e - timedelta(days=2), e, date(y, 4, 25), date(y, 5, 1),
        e + timedelta(days=60), date(y, 6, 10), date(y, 8, 15), date(y, 10, 5),
        date(y, 11, 1), date(y, 12, 1), date(y, 12, 8), date(y, 12, 25),
    }
    if carnival:
        out.add(e - timedelta(days=47))
    return out


def nth_sunday(y: int, month: int, n: int) -> date:
    d = date(y, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


@dataclass
class Calendar:
    start: date
    end: date
    municipal: dict = field(default_factory=dict)  # agency -> set of (month, day)
    carnival: bool = False
    terms: list = field(default_factory=list)  # [(start, end)]

    def days(self):
        d = self.start
        while d <= self.end:
            yield d
            d += timedelta(days=1)

    def holidays(self, agency: str) -> set[date]:
        out: set[date] = set()
        for y in range(self.start.year, self.end.year + 1):
            out |= national_holidays(y, self.carnival)
            for m, dd in self.municipal.get(agency, ()):
                out.add(date(y, m, dd))
        return out

    def in_term(self, d: date) -> bool:
        return any(a <= d <= b for a, b in self.terms)

    def first_term_day(self) -> date:
        return min(a for a, _ in self.terms)


def load_calendar(feed_toml: Path) -> tuple[Calendar, dict]:
    with open(feed_toml, "rb") as f:
        cfg = tomllib.load(f)
    w = cfg["window"]
    muni = {}
    for agency, lst in (cfg.get("holidays", {}).get("municipal", {}) or {}).items():
        muni[agency] = {tuple(int(x) for x in s.split("-")) for s in lst}
    terms = [(t["start"], t["end"]) for t in (cfg.get("school", {}) or {}).get("terms", [])]
    cal = Calendar(w["start"], w["end"], muni, bool(cfg.get("holidays", {}).get("carnival_is_holiday", False)), terms)
    return cal, cfg


# --------------------------------------------------------------------------- rules

def _dow(*days):
    return lambda d, hol: d.weekday() in days and d not in hol


DAY_TYPES = {
    # normalised day_type -> (slug, predicate(d, holidays))
    "dias uteis": ("du", lambda d, h: d.weekday() < 5 and d not in h),
    "dias uteis (exceto agosto)": ("du-exago", lambda d, h: d.weekday() < 5 and d not in h and d.month != 8),
    "3a feiras (du)": ("ter-du", lambda d, h: d.weekday() == 1 and d not in h),
    "sabados": ("sab", lambda d, h: d.weekday() == 5 and d not in h),
    "domingos": ("dom", lambda d, h: d.weekday() == 6),
    "domingos e feriados": ("dom-fer", lambda d, h: d.weekday() == 6 or d in h),
    "fim-de-semana e feriados": ("fds-fer", lambda d, h: d.weekday() >= 5 or d in h),
    "sabados, domingos e feriados": ("fds-fer", lambda d, h: d.weekday() >= 5 or d in h),
    "domingos ou 2.a feira se feriado": (
        "dom-ou-seg-feriado",
        lambda d, h: (d.weekday() == 6 and (d + timedelta(days=1)) not in h)
        or (d.weekday() == 0 and d in h)),
    "todos os dias": ("tod", lambda d, h: True),
    "todos os dias (julho e agosto)": ("tod-jul-ago", lambda d, h: d.month in (7, 8)),
}

SEASONS = {
    "": ("", lambda d, cal: True),
    "A": ("A", lambda d, cal: True),
    "C": ("C", lambda d, cal: d.month >= 9 or d.month <= 6),
    "D": ("D", lambda d, cal: d.month in (7, 8)),
    "E": ("E", lambda d, cal: 6 <= d.month <= 9),
    "EA": ("EA", lambda d, cal: d.month != 8),
    "PE": ("PE", lambda d, cal: cal.in_term(d)),
    "FE": ("FE", lambda d, cal: not cal.in_term(d)),
}
SCHOOL_SEASONS = {"PE", "FE"}


def classify_note(note: str):
    """Map a note to (kind, slug); kind is None for informational notes. Fails when unrecognised."""
    n = fold(note)
    if "inicio da epoca escolar" in n:
        return "first_term_to_jun30", "ini-esc-jun"
    if "2.o domingo de setembro" in n:
        return "sep_sunday_to_jun30", "dom-set-jun"
    if "4.a e 6.a" in n:
        return "school_wed_fri", "escolar-qua-sex"
    if "2.a, 3.a e 5.a" in n:
        return "school_mon_tue_thu", "escolar-seg-ter-qui"
    if "escolar" in n:
        return "school", "escolar"
    if "ligac" in n:
        return None, None
    raise BuildError(f"unrecognised note (add a rule to calendars.classify_note): {note!r}")


def _note_pred(kind: str, cal: Calendar):
    if kind == "school":
        return lambda d: cal.in_term(d)
    if kind == "school_wed_fri":
        return lambda d: cal.in_term(d) and d.weekday() in (2, 4)
    if kind == "school_mon_tue_thu":
        return lambda d: cal.in_term(d) and d.weekday() in (0, 1, 3)
    if kind == "sep_sunday_to_jun30":
        def p(d):
            for y in (d.year - 1, d.year):
                if nth_sunday(y, 9, 2) <= d <= date(y + 1, 6, 30):
                    return True
            return False
        return p
    if kind == "first_term_to_jun30":
        def p2(d):
            s = cal.first_term_day()
            return s <= d <= date(s.year + 1, 6, 30)
        return p2
    raise AssertionError(kind)


def trip_rule(trip, cal: Calendar):
    """Return (slug_parts, predicate(d, holidays)) for one trip."""
    dt = fold(trip.day_type).strip()
    if dt not in DAY_TYPES:
        raise BuildError(f"unknown day_type {trip.day_type!r} (trip {trip.trip}, {trip.source})")
    dt_slug, dt_pred = DAY_TYPES[dt]
    if trip.season not in SEASONS:
        raise BuildError(f"unknown season code {trip.season!r} (trip {trip.trip}, {trip.source})")
    s_slug, s_pred = SEASONS[trip.season]
    needs_school = trip.season in SCHOOL_SEASONS
    note_preds, slugs = [], []
    for note in trip.notes:
        kind, slug = classify_note(note)
        if kind is None:
            continue
        if kind in ("school", "school_wed_fri", "school_mon_tue_thu", "first_term_to_jun30"):
            needs_school = True
        note_preds.append(_note_pred(kind, cal))
        slugs.append(slug)
    if needs_school and not cal.terms:
        raise BuildError(
            "config/feed.toml [school] is empty but trips use season PE/FE or a school-term note "
            f"(e.g. {trip.source} trip {trip.trip}, line {trip.line}). Supply the 2026/27 term periods "
            "from the official DGEstE despacho as [[school.terms]] start/end dates covering "
            f"{cal.start} to {cal.end}")

    def pred(d, hol):
        return dt_pred(d, hol) and s_pred(d, cal) and all(p(d) for p in note_preds)

    return dt_slug, s_slug, sorted(set(slugs)), pred


def service_dates(trip, cal: Calendar, agency: str):
    dt_slug, s_slug, slugs, pred = trip_rule(trip, cal)
    hol = cal.holidays(agency)
    dates = frozenset(d for d in cal.days() if pred(d, hol))
    return dates, dt_slug, s_slug, slugs
