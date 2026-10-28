"""The shortlist: places that are open, reachable and worth doing in the engine's time windows."""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
from dataclasses import dataclass, field

from .config import parse_window
from .hours import HoursError, entry_points, parse
from .network import distance_m
from .sun import sun_times

CLUSTER_SKIP_M = 900


def _local_instant():
    from reach.calendar import local_instant
    return local_instant


def wall(d: dt.date, secs: int) -> int:
    """Epoch seconds of `secs` after local midnight of d (wall clock, so DST days stay right)."""
    day = d + dt.timedelta(days=secs // 86400)
    s = secs % 86400
    return _local_instant()(day, f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")


def _iso(t):
    from reach.models import T
    return T(t)


@dataclass(frozen=True)
class Slot:
    start: int
    end: int
    latest_start: int

    def to_json(self):
        return {"start": _iso(self.start), "end": _iso(self.end), "latest_start": _iso(self.latest_start)}


def _clip(intervals, lo, hi):
    out = []
    for s, e, close in intervals:
        s2, e2 = max(s, lo), min(e, hi)
        if s2 < e2:
            out.append((s2, e2, close))
    return out


def _intersect(a, b):
    out = []
    for s, e, close in a:
        for s2, e2 in b:
            lo, hi = max(s, s2), min(e, e2)
            if lo < hi:
                out.append((lo, hi, close))
    return _merge(out)


def _merge(parts):
    """Sort and join touching or overlapping parts that share a closing time."""
    out = []
    for s, e, close in sorted(parts):
        if out and out[-1][2] == close and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e), close)
        else:
            out.append((s, e, close))
    return out


def usable_slots(place, d: dt.date, arrive: int, leave: int, visit_s: int, holiday: bool, cfg):
    """Usable slots for a place in [arrive, leave] on date d.

    Returns (slots, reason): `slots` are the intervals where a visit of visit_s fits (entry before
    closing - last_entry_min, visit over before closing and before leave), `reason` explains an empty list
    (closed | daylight | meal_time | too_short | no_hours | bad_hours).
    """
    if not place.hours.strip():
        return [], "no_hours"
    try:
        opening = parse(place.hours).intervals(d, holiday)
    except HoursError:
        return [], "bad_hours"
    cur = [(wall(d, s), wall(d, e), wall(d, e)) for s, e in opening]      # (start, end, closing)
    cur = _clip(cur, arrive, leave)
    if not cur:
        return [], "closed"
    if place.daylight_only:
        sun = sun_times(d, place.lat, place.lon)
        if sun is None:
            return [], "daylight"
        cur = _clip(cur, wall(d, sun[0]), wall(d, sun[1]))
        if not cur:
            return [], "daylight"
    if place.is_food:
        meals = [wall_window(d, cfg.meals[m]) for m in place.meals if m in cfg.meals]
        cur = _intersect(cur, meals)
        if not cur:
            return [], "meal_time"
    if place.entry_times.strip():
        # Fixed entries (guided tours, departures): one slot per departure that fits inside a usable interval.
        try:
            pts = [wall(d, t) for t in entry_points(place.entry_times, d, holiday)]
        except HoursError:
            return [], "bad_hours"
        slots = [Slot(t, t + visit_s, t) for t in pts
                 if t + visit_s <= leave
                 and any(s <= t < e and t <= close - place.last_entry_min * 60 for s, e, close in cur)]
        return slots, ("" if slots else "no_departure")
    slots = []
    for s, e, close in cur:
        latest = min(e - visit_s, close - place.last_entry_min * 60)
        if s <= latest and e - s >= visit_s:
            slots.append(Slot(s, e, latest))
    return slots, ("" if slots else "too_short")


def wall_window(d, text):
    a, b = parse_window(text)
    return wall(d, a), wall(d, b)


@dataclass
class Item:
    place: dict
    arrive: int
    leave: int
    slots: list
    visit_s: int
    score: float
    why: list
    notes: list = field(default_factory=list)
    cluster: str = ""
    outbound: object = None
    return_: object = None
    _dur: int = 0
    point: tuple | None = None       # (label, lat, lon) of the reference point reached first; None = the record's own point

    def to_json(self):
        d = {"place": self.place, "fixed_entries": bool(self.place.get("entry_times")), "arrive": _iso(self.arrive), "leave": _iso(self.leave),
             "slots": [s.to_json() for s in self.slots], "visit_s": self.visit_s,
             "score": round(self.score, 4), "why": self.why, "notes": self.notes, "cluster": self.cluster}
        if self.point is not None:
            d["point"] = {"name": self.point[0], "lat": self.point[1], "lon": self.point[2]}
        if self.outbound is not None:
            d["outbound"] = self.outbound.to_json()
        if self.return_ is not None:
            d["return"] = self.return_.to_json()
        return d


@dataclass
class Shortlist:
    origin: dict
    date: str
    leave_after: int
    back_by: int
    coverage: object
    sights: list = field(default_factory=list)
    food: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_json(self):
        return {"origin": self.origin, "date": self.date, "leave_after": _iso(self.leave_after),
                "back_by": _iso(self.back_by), "coverage": self.coverage.to_json(),
                "sights": [i.to_json() for i in self.sights], "food": [i.to_json() for i in self.food],
                "excluded": self.excluded, "warnings": self.warnings,
                "attribution": "© OpenStreetMap contributors (coordinates)"}


def point_id(place_id: str, k: int) -> str:
    """Engine id of a place's k-th point: 0 is the record's own point (the plain id), the extra points are "<id>#<k>"."""
    return place_id if k == 0 else f"{place_id}#{k}"


def _engine_points(places):
    return [(point_id(p.id, k), la, lo) for p in places for k, (_, la, lo) in enumerate(p.all_points())]


def _first_point(p, win):
    """-> (window, point) for the point of `p` with the earliest arrival (the record's own point wins ties). A place without
    extra points gives (its window, None). Points the origin cannot reach are skipped; if none is ok the main window stands."""
    best, pt = win[p.id], None
    for k, (label, la, lo) in enumerate(p.all_points()):
        if k == 0:
            continue
        w = win.get(point_id(p.id, k))
        if w is not None and w.status == "ok" and (best.status != "ok" or w.arrive < best.arrive):
            best, pt = w, (label, la, lo)
    return best, pt


def _origin(o):
    if isinstance(o, dict):
        return (str(o.get("id", "origin")), float(o["lat"]), float(o["lon"]))
    if len(o) == 2:
        return ("origin", float(o[0]), float(o[1]))
    return (str(o[0]), float(o[1]), float(o[2]))


def _hm(t):
    return dt.datetime.fromtimestamp(t, tz=_tz()).strftime("%H:%M")


def _tz():
    from reach.calendar import TZ
    return TZ


def clusters(lib, items):
    """Single-link clustering of items whose entrances are within cluster_minutes' walk (engine walking rule)."""
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    limit_s = lib.cfg.cluster_minutes * 60
    pts = [[(la, lo) for _, la, lo in lib.places[it.place["id"]].all_points()] for it in items]     # a long place has several
    for i in range(n):
        for j in range(i + 1, n):
            pairs = sorted(((distance_m(*p, *q), p, q) for p in pts[i] for q in pts[j]), key=lambda x: x[0])
            for d, p, q in pairs:               # the shortest distance between any two points of the two places
                if d > CLUSTER_SKIP_M:
                    break
                w = lib.engine.walk_seconds(p, q)
                if w is not None and w <= limit_s:
                    parent[find(i)] = find(j)
                    break
    names, out = {}, []
    for i in range(n):
        out.append(names.setdefault(find(i), f"c{len(names) + 1}"))
    return out


def build(lib, origin, date, leave_after, back_by, *, interests=(), text="", pace="relaxed", weather="any",
          include_food=True, limit=20, include_journeys=False, include_drafts=False, leave_by=None):
    """`leave_by` ("HH:MM", optional): the visitor must be done at each place by then. A place's `leave` is capped at it
    before the slots are computed, so a visit ends by `leave_by` and `latest_start` follows. None leaves the shortlist unchanged."""
    from reach.calendar import to_date

    if pace not in ("relaxed", "packed"):
        raise ValueError("pace must be 'relaxed' or 'packed'")
    if weather not in ("any", "rain", "dry"):
        raise ValueError("weather must be 'any', 'rain' or 'dry'")
    if lib.engine is None:
        raise ValueError("this library was loaded without an engine")
    d = to_date(date)
    cap = _local_instant()(d, leave_by) if leave_by else None
    o = _origin(origin)
    interests = {i.strip() for i in interests if i.strip()}
    allowed = ("confirmed", "draft") if include_drafts else ("confirmed",)
    cands = [p for p in lib.places.values() if p.status in allowed and (include_food or not p.is_food)]
    cands.sort(key=lambda p: p.id)
    # One engine call for the default walking limit. Places that set max_walk_m (up to 2000) are asked in a
    # further call per distinct value, so a long access walk never loosens the rule for the other places.
    default = [p for p in cands if p.max_walk_m is None]
    res = lib.engine.windows(o, d, leave_after, back_by, _engine_points(default), include_journeys=True)
    win = {w.id: w for w in res.windows}
    for mw in sorted({p.max_walk_m for p in cands if p.max_walk_m is not None}):
        group = [p for p in cands if p.max_walk_m == mw]
        extra = lib.engine.windows(o, d, leave_after, back_by, _engine_points(group),
                                   include_journeys=True, max_walk_m=mw)
        win.update({w.id: w for w in extra.windows})
    chosen = {}                       # place id -> the reference point reached first
    for p in cands:
        w, pt = _first_point(p, win)
        win[p.id] = w
        if pt is not None:
            chosen[p.id] = pt
    sl = Shortlist(res.origin, res.date, res.leave_after, res.back_by, res.coverage)
    sl.warnings.extend(res.warnings)
    if include_drafts and any(p.status == "draft" for p in cands):
        sl.warnings.append("draft records are included; their hours and descriptions are not confirmed")

    bm = lib.index.scores(text) if text.strip() else {}
    found, stale = [], []
    for p in cands:
        w = win[p.id]
        if w.status != "ok":
            sl.excluded.append({"id": p.id, "name": p.name, "reason": w.status})
            continue
        if weather == "rain" and p.weather == "outdoor":
            sl.excluded.append({"id": p.id, "name": p.name, "reason": "weather"})
            continue
        visit_s = (p.visit_typical if pace == "relaxed" else p.visit_min) * 60
        holiday = lib.cfg.holidays.is_holiday(d, p.municipality)
        slots, reason = usable_slots(p, d, w.arrive, w.leave if cap is None else min(w.leave, cap), visit_s, holiday, lib.cfg)
        if not slots:
            sl.excluded.append({"id": p.id, "name": p.name, "reason": reason})
            continue
        chk = p.provenance.checked
        if chk is not None and (d - chk).days > lib.cfg.stale_days:
            stale.append(p.name)
        matched = sorted(interests & (set(p.tags) | set(p.cuisine) | {p.category}))
        longest = max(s.end - s.start for s in slots)
        fit = min(1.0, longest / visit_s) - 1
        out_rides = w.outbound.rides if w.outbound else 0
        why = [f"matches {t}" for t in matched]
        if p.entry_times:
            why.append("fixed entries at " + ", ".join(_hm(x.start) for x in slots))
        else:
            why.append(f"open {_hm(slots[0].start)}-{_hm(slots[0].end)}")
        if p.weather == "mixed" and weather == "rain":
            why.append("partly outdoors: check the weather")
        if p.daylight_only:
            why.append("daylight only")
        if out_rides:
            why.append(f"{out_rides} ride(s) from the origin")
        found.append((p, w, slots, visit_s, matched, fit, out_rides, why))

    top = max((bm.get(f[0].id, 0.0) for f in found), default=0.0)
    items = []
    for p, w, slots, visit_s, matched, fit, rides, why in found:
        rel = (bm.get(p.id, 0.0) / top) if top > 0 else 0.0
        score = 3 * len(matched) + 2 * rel + (4 - p.priority) + fit - 0.5 * rides
        if rel > 0:
            why.append(f"matches \"{text.strip()}\"")
        it = Item(p.summary(), w.arrive, w.leave if cap is None else min(w.leave, cap), slots, visit_s, score, why, [p.hours_note] if p.hours_note else [])
        it.point = chosen.get(p.id)
        if include_journeys:
            it.outbound, it.return_ = w.outbound, w.return_
        it._dur = (w.outbound.arrive - w.outbound.depart) if w.outbound else 0
        items.append(it)
    for it, cl in zip(items, clusters(lib, items)):
        it.cluster = cl

    def rank(group):
        group.sort(key=lambda i: (-i.score, i._dur, i.place["id"]))
        return group[:limit]

    sl.sights = rank([i for i in items if i.place["kind"] in ("sight", "activity")])
    sl.food = rank([i for i in items if i.place["kind"] in ("food", "drink")])
    if stale:
        sl.warnings.append(f"hours last checked over {lib.cfg.stale_days} days before {d}: " + ", ".join(stale))
    return sl
