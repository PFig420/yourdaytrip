"""Draft validation: schema limits, pace limits, then the scheduler (Planner.md 5.1, 6.2, 6.3)."""
from __future__ import annotations

from .schedule import Err, Result

MAX_STOPS = {"relaxed": 4, "packed": 6}


def _words(s: str) -> int:
    return len(s.split())


def check_schema(draft) -> list[Err]:
    errs = []
    if _words(draft.title) > 8:
        errs.append(Err("schema", f"title has {_words(draft.title)} words; the limit is 8"))
    if _words(draft.notes) > 40:
        errs.append(Err("schema", f"notes has {_words(draft.notes)} words; the limit is 40"))
    n = len(draft.stops)
    if n == 0 or n > 6:
        errs.append(Err("schema", f"stops has {n} items; give between 2 and 6"))
    for s in draft.stops:
        if _words(s.why) > 25:
            errs.append(Err("schema", f"the why for {s.place_id} has {_words(s.why)} words; the limit is 25", s.place_id))
        if s.visit_minutes <= 0:
            errs.append(Err("schema", f"visit_minutes for {s.place_id} must be positive", s.place_id))
    return errs


def validate_draft(sched, req, draft, pack_ids, pool=None) -> tuple[list[Err], Result | None]:
    errs = check_schema(draft)
    if errs:
        return errs, None
    seen, stops = set(), []
    for s in draft.stops:
        if s.place_id in seen:
            errs.append(Err("duplicate_place", f"{s.place_id} appears more than once", s.place_id))
            continue
        seen.add(s.place_id)
        stops.append((s.place_id, s.visit_minutes))
    cap = MAX_STOPS[req.pace]
    if len(draft.stops) > cap:
        errs.append(Err("too_many_stops", f"a {req.pace} day has at most {cap} stops; the draft has {len(draft.stops)}"))
    if len(stops) < 2:
        errs.append(Err("too_few_stops", f"a day needs at least 2 different stops; the draft has {len(stops)}"))
    res = sched.schedule(req, stops, pack_ids=pack_ids, pool=pool)
    return errs + res.errors, res
