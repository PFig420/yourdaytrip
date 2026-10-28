"""Step 2-3 input: the candidate pack built from the places shortlist (Planner.md 4)."""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field

from .timeutil import at, hm
from places.shortlist import point_id


@dataclass
class Pack:
    candidates: list            # list of dict, sorted by score
    items: dict                 # id -> places.shortlist.Item
    hints: dict                 # id -> {id: minutes}
    shortlist: object
    request: dict
    warnings: list = field(default_factory=list)

    @property
    def ids(self):
        return {c["id"] for c in self.candidates}

    def to_json(self) -> dict:
        return {"request": self.request, "candidates": self.candidates, "travel_minutes": self.hints}

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, separators=(",", ":"))


def _pick_sights(items: list, n: int, min_clusters: int) -> list:
    chosen = items[:n]
    have = {i.cluster for i in chosen}
    others = [i for i in items[n:] if i.cluster not in have]
    while len(have) < min_clusters and others and chosen:
        # swap the lowest-ranked item of the biggest cluster for the best item of a new cluster
        counts = {}
        for i in chosen:
            counts[i.cluster] = counts.get(i.cluster, 0) + 1
        big = max(counts, key=lambda c: (counts[c], c))
        if counts[big] < 2:
            break
        drop = [i for i in chosen if i.cluster == big][-1]
        add = others.pop(0)
        chosen = [i for i in chosen if i is not drop] + [add]
        chosen.sort(key=lambda i: (-i.score, i.place["id"]))
        have = {i.cluster for i in chosen}
        others = [i for i in items[n:] if i.cluster not in have and i is not add]
    return chosen


def _has_lunch_slot(item, d, cfg) -> bool:
    from places.shortlist import wall_window
    if "lunch" not in item.place.get("meals", []):
        return False
    a, b = wall_window(d, cfg.meals["lunch"])
    return any(s.start < b and s.end > a for s in item.slots)


def _pick_food(items: list, n: int, d, cfg) -> list:
    lunch = [i for i in items if _has_lunch_slot(i, d, cfg)][:n]
    rest = [i for i in items if i not in lunch]
    chosen = lunch + rest[: max(0, n - len(lunch))]
    chosen.sort(key=lambda i: (-i.score, i.place["id"]))
    return chosen


def clip(text: str, n: int) -> str:
    """At most n characters, cut at a word boundary (compact profile only)."""
    if len(text) <= n:
        return text
    head = text[: n - 1]
    if text[n - 1] != " ":          # the cut fell inside a word: drop that word
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(",;:. ") + "…"


def candidate_json(item, place, leave_after: int, description_max: int = 0) -> dict:
    fixed = bool(place.entry_times.strip())
    desc = clip(place.description, description_max) if description_max else place.description
    c = {
        "id": place.id, "name": place.name, "kind": place.kind, "category": place.category,
        "tags": list(place.tags), "cluster": item.cluster, "description": desc,
    }
    if fixed:
        # tours and departures: only the listed entry times exist, so no slot ranges
        c["fixed_entry_times"] = [hm(s.start) for s in item.slots]
    else:
        # enter_by is Slot.latest_start: the latest entry that fits a stay of visit_typical (relaxed) or visit_min
        # (packed) and respects last_entry_min. The shortlist already dropped slots shorter than that stay.
        c["slots"] = [{"from": hm(s.start), "to": hm(s.end), "enter_by": hm(s.latest_start)} for s in item.slots]
        gaps = [f"{hm(a.end)}-{hm(b.start)}" for a, b in zip(item.slots, item.slots[1:])]
        if gaps:
            c["closed_between"] = gaps
    c.update({
        "visit_min": place.visit_min, "visit_typical": place.visit_typical,
        "price": place.price, "weather": place.weather,
        "notes": list(item.notes)[:2],
        "arrive_from_origin": max(0, round((item.arrive - leave_after) / 60)),
        "earliest_arrival": hm(item.arrive),
        "leave_by": hm(item.leave),
    })
    if place.is_food:
        c["meals"] = list(place.meals)
        c["cuisine"] = list(place.cuisine)
    return c


def hints(engine, d, cands: list, leave_after: int, back_by: int, cfg: dict, scan_end: int | None = None) -> dict:
    """Minutes between candidates from one engine.windows scan each, at a reference time (estimates only).
    `scan_end`: how late a hinted journey may arrive (leave mode: the latest arrival home); default `back_by`."""
    from .timeutil import hms
    pc = cfg.get("pack", {})
    ref = leave_after + int(pc.get("hint_reference_hours", 3)) * 3600
    if ref >= back_by - 1800:
        ref = leave_after + (back_by - leave_after) // 2
    cap = int(pc.get("hint_max_min", 120))
    out = {}
    for a in cands:
        # a long place has several points (places.shortlist.point_id): from the point the visitor reaches first, to every point
        # of the others, keeping the shortest per pair. Without extra points this is the plain main-point call.
        origin = (a.get("_fid", a["id"]),) + a.get("_from", (a["lat"], a["lon"]))      # the chosen point's own id (walk overrides)
        others = [(point_id(b["id"], k), la, lo) for b in cands if b["id"] != a["id"]
                  for k, (la, lo) in enumerate(b.get("_pts") or [(b["lat"], b["lon"])])]
        res = engine.windows(origin, d, hms(ref), hms(scan_end or back_by), others, max_walk_m=2000)
        row = {}
        for w in res.windows:
            if w.arrive is not None:
                m = math.ceil((w.arrive - ref) / 60)
                key = w.id.split("#")[0]
                if m <= cap:
                    row[key] = min(row.get(key, cap), max(0, m))
        out[a["id"]] = row
    return out


def build(lib, engine, req, cfg: dict, compact: bool = False) -> Pack:
    """compact: the [pack.compact] profile, for the local backend only (Execution.md M5); Claude always gets the full pack."""
    d = dt.date.fromisoformat(req.date)
    leave_after, back_by = at(d, req.leave_after), at(d, req.back_by)
    o = req.origin
    leave_mode = req.deadline == "leave"
    # leave mode: the engine looks as far as the latest arrival home, and each place's `leave` is capped at the leave-by time
    end = max(req.back_by, cfg.get("schedule", {}).get("leave_mode_home_by", "23:59")) if leave_mode else req.back_by
    sl = lib.shortlist({"id": o.id, "lat": o.lat, "lon": o.lon}, req.date, req.leave_after, end,
                       leave_by=req.back_by if leave_mode else None, interests=req.interests, text=req.text, pace=req.pace, weather=req.weather,
                       include_food=req.include_food, limit=40)
    pc = cfg.get("pack", {})
    if compact:
        pc = {**pc, **pc.get("compact", {})}
        cfg = {**cfg, "pack": pc}          # hints() reads hint_max_min from here
    sights = _pick_sights(sl.sights, int(pc.get("sights", 12)), int(pc.get("min_clusters", 2)))
    food = _pick_food(sl.food, int(pc.get("food", 6)), d, lib.cfg) if req.include_food else []
    items, cands = {}, []
    for it in sights + food:
        p = lib.get(it.place["id"])
        # the shortlist already drops places whose slots are all too short; assert it
        need = (p.visit_typical if req.pace == "relaxed" else p.visit_min) * 60
        assert any(s.end - s.start >= min(need, p.visit_min * 60) for s in it.slots), p.id
        items[p.id] = it
    for it in sorted(items.values(), key=lambda i: (-i.score, i.place["id"])):
        p = lib.get(it.place["id"])
        c = candidate_json(it, p, leave_after, int(pc.get("description_max", 0)) if compact else 0)
        c["lat"], c["lon"] = p.lat, p.lon
        if p.points:
            c["_from"] = (it.point[1], it.point[2]) if it.point else (p.lat, p.lon)     # the point reached first
            if it.point:
                c["_fid"] = point_id(p.id, [lb for lb, _, _ in p.all_points()].index(it.point[0]))
            c["_pts"] = [(la, lo) for _, la, lo in p.all_points()]
        cands.append(c)
    hint = hints(engine, d, cands, leave_after, back_by, cfg, at(d, end) if leave_mode else None) if len(cands) > 1 else {}
    for c in cands:
        del c["lat"], c["lon"]
        c.pop("_from", None), c.pop("_pts", None), c.pop("_fid", None)
    from .schedule import required_meals
    from .validate import MAX_STOPS
    meals = required_meals(req, lib.cfg.meals)     # the validator's own rule (Planner.md 6.2)
    day = {"min_stops": 2, "max_stops": MAX_STOPS[req.pace],
           "meals_required": [{"meal": m, "window": lib.cfg.meals[m]} for m in meals]}
    r = {"origin": o.name, "date": req.date, "weekday": d.strftime("%A"), "leave_after": req.leave_after,
         "back_by": req.back_by, "interests": req.interests, "text": req.text, "pace": req.pace,
         "weather": req.weather, "include_food": req.include_food, "day": day}
    if leave_mode:
        r["deadline"] = "leave"        # only in leave mode: a home-mode pack stays byte-identical to the one before this field
    pack = Pack(cands, items, hint, sl, r)
    pack.warnings = list(sl.warnings)
    return pack
