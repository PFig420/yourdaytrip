"""The deterministic fallback plan (Planner.md 7): highest-scoring places that schedule cleanly, meals when due."""
from __future__ import annotations

import datetime as dt

from .schedule import Scheduler, required_meals
from .timeutil import at
from .validate import MAX_STOPS

MARGIN_S = 45 * 60    # a sight may not end later than this before a still-owed meal window closes
LEAD_S = 90 * 60      # a meal is "due" when the current time is within this of its window opening


def _minutes(c, pace, stretch):
    if c["kind"] in ("food", "drink"):
        return c["visit_typical"] if pace == "relaxed" else c["visit_min"]
    if stretch:       # second pass: longer stays so that a late meal window can be reached
        return 2 * c["visit_typical"]
    return c["visit_typical"] if pace == "relaxed" else c["visit_min"]


def _feasible(sched, req, trial, ids):
    """The trial schedules and gets home; a missing meal is allowed while the plan is still being built."""
    r = sched.schedule(req, trial, pack_ids=ids)
    return r, all(e.code == "meal_missing" for e in r.errors)


def _have(sched, req, stops, m, ids=None, pool=None):
    """Is meal m already served by a stop, judged by when that stop is actually visited?"""
    if not stops:
        return False
    r = sched.schedule(req, stops, pack_ids=ids, check_plan=False, pool=pool)
    return any(v[4] == m for v in r.visits)


def _build(sched, pack, req, stretch):
    from places.shortlist import wall_window
    d = dt.date.fromisoformat(req.date)
    meals_cfg = sched.lib.cfg.meals
    need = required_meals(req, meals_cfg)
    cap = MAX_STOPS[req.pace]
    ids = pack.ids
    sights = [c for c in pack.candidates if c["kind"] in ("sight", "activity")]
    foods = [c for c in pack.candidates if c["kind"] in ("food", "drink")]
    stops, done = [], set()
    now = at(d, req.leave_after)
    last = None

    def leaves_room(r, missing):
        """A sight must not swallow the whole window of a meal still owed (it may end before the window opens)."""
        for m in missing:
            a, b = wall_window(d, meals_cfg[m])
            if r.end_t > a and r.end_t > b - MARGIN_S:
                return False
        return True

    def add_first_feasible(pool, missing=()):
        nonlocal now, last
        for c in pool:
            if c["id"] in done:
                continue
            trial = stops + [(c["id"], _minutes(c, req.pace, stretch))]
            r, ok = _feasible(sched, req, trial, ids)
            if ok and leaves_room(r, missing):
                stops.append(trial[-1])
                done.add(c["id"])
                now, last = r.end_t, r
                return True
        return False

    while len(stops) < cap:
        missing = [m for m in need if not _have(sched, req, stops, m, ids, pack)]
        due = next((m for m in missing if now + LEAD_S >= wall_window(d, meals_cfg[m])[0]), None)
        if due:
            if add_first_feasible([c for c in foods if due in c.get("meals", [])]):
                continue
        elif len(stops) >= cap - len(missing):
            break                               # keep room for the meals still owed
        if not add_first_feasible(sights, missing):
            break
    # meals still owed: put one food stop in the best position; if none fits, end the sights earlier
    for m in [m for m in need if not _have(sched, req, stops, m, ids, pack)]:
        placed = False
        for drop in range(0, len(stops)):
            base = stops[: len(stops) - drop]
            if drop and sched.lib.get(stops[len(stops) - drop][0]).is_food:
                break                           # never drop a meal already placed
            for c in [c for c in foods if m in c.get("meals", []) and c["id"] not in done]:
                for pos in range(len(base), -1, -1):
                    trial = base[:pos] + [(c["id"], _minutes(c, req.pace, stretch))] + base[pos:]
                    r = sched.schedule(req, trial, pack_ids=ids, check_plan=True, pool=pack)
                    if not (all(e.code == "meal_missing" for e in r.errors) and any(v[4] == m for v in r.visits)):
                        trial = _fill_gap(sched, req, trial, pos, c, m, ids, d, meals_cfg)
                        r = sched.schedule(req, trial, pack_ids=ids, check_plan=True, pool=pack) if trial else None
                    if r is not None and len(trial) >= 2 and all(e.code == "meal_missing" for e in r.errors) \
                            and any(v[4] == m for v in r.visits):
                        stops, placed = trial, True
                        done.add(c["id"])
                        break
                if placed:
                    break
            if placed:
                break
    if len(stops) < 2:
        return None, last
    final = sched.schedule(req, stops, pack_ids=ids, pool=pack)
    return (None, final) if final.errors else (stops, final)


def _fill_gap(sched, req, trial, pos, c, m, ids, d, meals_cfg):
    """The meal place is reached before its window opens: stay longer at the stop before it (up to 2 x typical)."""
    from places.shortlist import wall_window
    if pos == 0:
        return None
    r = sched.schedule(req, trial, pack_ids=ids, check_plan=False)
    v = next((v for v in r.visits if v[0].id == c["id"]), None)
    if v is None:
        return None
    a, _ = wall_window(d, meals_cfg[m])
    if v[1] >= a:
        return None
    prev_id, prev_min = trial[pos - 1]
    prev = sched.lib.get(prev_id)
    if prev.is_food:
        return None
    extra = -(-(a - v[1]) // 60)
    new = min(prev_min + extra, 2 * prev.visit_typical)
    if new <= prev_min:
        return None
    return trial[: pos - 1] + [(prev_id, new)] + trial[pos:]


def build(sched: Scheduler, pack, req):
    """-> (stops, Result) with a result that has no errors, or (None, last Result) when none can be built."""
    stops, res = _build(sched, pack, req, stretch=False)
    if stops is None and required_meals(req, sched.lib.cfg.meals):
        stops2, res2 = _build(sched, pack, req, stretch=True)
        if stops2 is not None:
            return stops2, res2
    return stops, res
