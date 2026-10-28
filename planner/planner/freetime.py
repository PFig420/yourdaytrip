"""Free time with nearby ideas (Planner.md 6.1): a post-pass over a valid schedule.

A wait of 30 minutes or more straight after a visit is shown as free time, with up to 3 optional things to do on foot.
Nothing here changes a time, a journey or a check; it only relabels a wait and attaches suggestions. It runs after
validation, so it cannot make a plan fail, and it searches the engine for walking only.
"""
from __future__ import annotations

import datetime as dt
import math

from .models import FreeTime, PlanRequest, Visit, Wait
from .pack import clip
from .schedule import Result, Scheduler
from .timeutil import hm, iso, wall
from places.shortlist import point_id

AFTER_VISIT = "until it is time to leave for the next stop"
DESCRIPTION_MAX = 160
MAX_SUGGESTIONS, MAX_FOOD = 3, 1
HOME_MODES = {"rail": "train", "bus": "bus"}


def _epoch(s: str) -> int:
    return int(dt.datetime.fromisoformat(s).timestamp())


def _walk(j) -> tuple[int, int]:
    """(metres, seconds) of a journey that is only walking; (-1, 0) when it needs a ride."""
    if j is None or j.rides:
        return -1, 0
    legs = [l for l in j.legs if l.type == "walk"]
    return sum(l.metres for l in legs), sum(l.arrive - l.depart for l in legs)


def _home_word(res: Result) -> str | None:
    """'bus' / 'train' for the first ride of the journey home, '' when it has no ride, None if the day has no way home."""
    if res.home_idx is None or not res.journeys:
        return None
    ride = next((l for l in res.journeys[-1][0].legs if l.type == "ride"), None)
    return "" if ride is None else HOME_MODES.get(ride.mode, ride.mode)


def _opening(sched: Scheduler, place, d: dt.date, holiday: bool):
    """Open intervals as (start, end) instants clipped to daylight, with the end that is a sunset flagged; None if unusable."""
    if place.entry_times.strip():
        return None                                    # tours and departures cannot be dropped into
    iv = sched._intervals(place, d, holiday)
    if not iv:
        return None
    sunset = None
    if place.daylight_only:
        from places.sun import sun_times
        sun = sun_times(d, place.lat, place.lon)
        if sun is None:
            return None
        lo, hi = wall(d, sun[0]), wall(d, sun[1])
        iv = [(max(s, lo), min(e, hi)) for s, e in iv if max(s, lo) < min(e, hi)]
        sunset = hi
    return [(s, e, e == sunset) for s, e in sorted(iv)]


def suggest(sched: Scheduler, req: PlanRequest, loc, t0: int, t1: int, taken: set, walk_m: int) -> list[dict]:
    """Up to 3 things to do on foot between t0 and t1 around `loc` = (place_id, lat, lon); see the module docstring."""
    from places.network import distance_m
    d = dt.date.fromisoformat(req.date)
    router = sched.router.for_date(d)
    found = []
    for x in sched.lib.places.values():
        if x.status != "confirmed" or x.id in taken or x.id == loc[0]:
            continue
        if req.weather == "rain" and x.weather == "outdoor":
            continue
        if not req.include_food and x.is_food:
            continue
        if min(distance_m(loc[1], loc[2], la, lo) for _, la, lo in x.all_points()) > walk_m:   # a walk is never shorter than the crow-fly line
            continue
        # a long place has several points: the one nearest on foot (the record's own point wins a tie)
        xp, m1, w1, label = None, -1, 0, None
        for k, (name, la, lo) in enumerate(x.all_points()):
            cand = (point_id(x.id, k), la, lo)
            mk, wk = _walk(router.earliest(loc, cand, t0, walk_m))
            if 0 <= mk <= walk_m and (xp is None or wk < w1):
                xp, m1, w1, label = cand, mk, wk, (name if k else None)
        if xp is None:
            continue
        arrive = t0 + w1
        holiday = sched.lib.cfg.holidays.is_holiday(d, x.municipality)
        opening = _opening(sched, x, d, holiday)
        if opening is None:
            continue
        visit_s = x.visit_min * 60
        j2 = router.earliest(xp, loc, arrive + visit_s, walk_m)
        m2, w2 = _walk(j2)
        if not 0 <= m2 <= walk_m:
            continue
        for s, e, is_sunset in opening:
            if not s <= arrive < e or arrive > e - x.last_entry_min * 60:
                continue
            stay_end = min(t1 - w2, e)                  # leave early enough to walk back, and before it closes
            if stay_end - arrive < visit_s:
                continue
            back = _walk(router.earliest(xp, loc, stay_end, walk_m))
            if not 0 <= back[0] <= walk_m or stay_end + back[1] > t1:
                continue
            always = x.hours.strip() == "24/7"
            found.append((m1, x.id, {
                "place_id": x.id, "name": x.name, "kind": x.kind, "walk_m": m1, "walk_min": math.ceil(w1 / 60),
                "point_id": xp[0], "minutes_there": (stay_end - arrive) // 60,
                "open_until": None if always and not x.daylight_only else hm(e),
                "description": clip(x.description, DESCRIPTION_MAX), **({"point": label} if x.points else {})}, x.is_food))
            break
    found.sort(key=lambda f: (f[0], f[1]))
    out, food = [], 0
    for _, _, s, is_food in found:
        if is_food and food >= MAX_FOOD:
            continue
        out.append(s)
        food += is_food
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def apply(sched: Scheduler, req: PlanRequest, res: Result) -> list:
    """The timeline with long waits after a visit turned into free time, and suggestions on every free time that has room."""
    sc = sched.cfg.get("schedule", {})
    min_s = int(sc.get("suggest_free_time_min", 30)) * 60
    walk_m = min(int(sc.get("suggest_walk_m", 800)), 2000)      # 2,000 m is the engine limit
    taken = {p.id for p, *_ in res.visits}
    home = _home_word(res)
    out, tl = [], res.timeline
    for i, e in enumerate(tl):
        prev = tl[i - 1] if i else None
        if isinstance(e, Wait) and e.reason == AFTER_VISIT and isinstance(prev, Visit) and _epoch(e.until) - _epoch(e.from_) >= min_s:
            at_home = home is not None and i >= res.home_idx
            reason = ("free time before the " + (f"{home} home" if home else "walk home")) if at_home else "free time before the next stop"
            loc = _where(sched, res, prev.place_id)
            ft = FreeTime(at=e.at, **{"from": e.from_}, until=e.until, reason=reason, point_id=loc[0])
            ft.suggestions = suggest(sched, req, loc, _epoch(e.from_), _epoch(e.until), taken, walk_m)
            out.append(ft)
        elif isinstance(e, FreeTime) and not e.suggestions and not _filled(tl, i):
            nxt = next((x for x in tl[i + 1:] if isinstance(x, Visit)), None)
            if nxt is not None:
                loc = _where(sched, res, nxt.place_id)       # the free time is spent at the dinner place's door
                e.suggestions = suggest(sched, req, loc, _epoch(e.from_), _epoch(e.until), taken, walk_m)
            out.append(e)
        else:
            out.append(e)
    return out


def _where(sched: Scheduler, res: Result, place_id: str):
    """(id, lat, lon) of the point the visit used: the reference point reached first for a long place, else the record's point."""
    if place_id in res.visit_points:
        return res.visit_points[place_id][0]
    p = sched.lib.get(place_id)
    return (p.id, p.lat, p.lon)


def _filled(tl, i) -> bool:
    """True when a visit was inserted into this gap (the entries since the last visit lead back to one marked `inserted`)."""
    for x in reversed(tl[:i]):
        if isinstance(x, Visit):
            return x.inserted
    return False
