"""Step 4: deterministic scheduler and validator (Planner.md 6).

The draft carries place ids and stay lengths only. Every time, walk and ride below comes from the engine.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

from .models import FreeTime, PlanB, PlanRequest, Ride, SafeReturn, Visit, Wait, Walk
from .timeutil import at, hm, hms, iso, wall
from places.shortlist import point_id

DEFAULT_WAIT_MIN = 45


@dataclass
class Err:
    code: str
    message: str
    place_id: str = ""
    times: dict = field(default_factory=dict)

    def to_json(self):
        return {"code": self.code, "place_id": self.place_id, "times": self.times, "message": self.message}

    def line(self):
        return f"{self.code}: {self.message}"


class Router:
    """Engine calls with a cache; the planner repeats many identical legs across retries and the fallback."""

    def __init__(self, engine, max_walk_m=2000):
        self.engine = engine
        self.max_walk_m = max_walk_m
        self.d = None
        self._e, self._l = {}, {}

    def for_date(self, d):
        self.d = d
        return self

    def earliest(self, a, b, t, max_walk=None):
        key = (a, b, t, max_walk)
        if key not in self._e:
            opts = {} if max_walk is None else {"max_walk_m": max_walk}
            self._e[key] = self.engine.earliest(a, self.d, hms(t), b, **opts)
        return self._e[key]

    def latest(self, a, b, back_by, max_walk=None):
        key = (a, b, back_by, max_walk)
        if key not in self._l:
            opts = {} if max_walk is None else {"max_walk_m": max_walk}
            self._l[key] = self.engine.latest(a, self.d, b, hms(back_by), **opts)
        return self._l[key]


@dataclass
class Result:
    errors: list
    timeline: list = field(default_factory=list)
    visits: list = field(default_factory=list)      # (place, start, end, entry, meal)
    journeys: list = field(default_factory=list)    # (journey, target_point)
    home_arrive: int | None = None
    home_idx: int | None = None                     # timeline index where the journey home starts
    visit_points: dict = field(default_factory=dict)   # place id -> (point (id, lat, lon), display name) the visit used
    end_t: int | None = None
    last_point: tuple | None = None
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def required_meals(req: PlanRequest, meals_cfg: dict) -> list[str]:
    """Meals the day must include: lunch when the day spans 12:00-15:00, dinner when it runs to 22:00 or later."""
    if not req.include_food:
        return []
    need = []
    if req.leave_after <= "12:00" and req.back_by >= "15:00":
        need.append("lunch")
    if req.back_by >= "22:00" and req.leave_after <= "19:00":
        need.append("dinner")
    return need


class Scheduler:
    def __init__(self, engine, lib, cfg: dict, router: Router | None = None):
        self.engine = engine
        self.lib = lib
        self.cfg = cfg
        self.router = router or Router(engine)
        self.max_wait = int(cfg.get("schedule", {}).get("max_wait_min", DEFAULT_WAIT_MIN)) * 60
        self.walk_m = int(cfg.get("schedule", {}).get("walk_between_m", 2000))
        self.change_s = int(cfg.get("schedule", {}).get("plan_b_change_min", 10)) * 60
        self.max_free = int(cfg.get("schedule", {}).get("max_free_time_min", 120)) * 60
        self.fill_min = int(cfg.get("schedule", {}).get("fill_free_time_min", 45)) * 60
        self.leave_home_by = cfg.get("schedule", {}).get("leave_mode_home_by", "23:59")
        self._stops = None

    # ---- helpers --------------------------------------------------------
    def deadlines(self, req: PlanRequest, d):
        """-> (latest arrival home, leave-by or None). Home mode: `back_by` is the arrival deadline. Leave mode: `back_by` is the
        time the last visit must end by, and the way home may arrive as late as `leave_mode_home_by` (Planner.md 3.1)."""
        back = at(d, req.back_by)
        if req.deadline == "leave":
            return max(at(d, self.leave_home_by), back), back
        return back, None

    def stop_point(self, stop_id: str):
        if self._stops is None:
            self._stops = {sid: v for f in self.engine.feeds for sid, v in f.stops.items()}
        n, la, lo = self._stops[stop_id]
        return (stop_id, la, lo)

    def _meal_windows(self, d, place):
        from places.shortlist import wall_window
        out = {}
        for m in place.meals:
            if m in self.lib.cfg.meals:
                out[m] = wall_window(d, self.lib.cfg.meals[m])
        return out

    def _intervals(self, place, d, holiday):
        from places.hours import HoursError, parse
        if not place.hours.strip():
            return None
        try:
            return [(wall(d, s), wall(d, e)) for s, e in parse(place.hours).intervals(d, holiday)]
        except HoursError:
            return None

    def _reach(self, cur, place, t, mw):
        """-> (point, journey, display name): the point of `place` reached first from `cur` (the record's own point wins a tie).
        A place without extra points is one engine call, as before."""
        best = None
        for k, (label, la, lo) in enumerate(place.all_points()):
            cand = (point_id(place.id, k), la, lo)
            j = self.router.earliest(cur, cand, t, mw)
            if j is not None and (best is None or j.arrive < best[1].arrive):
                best = (cand, j, place.name if k == 0 else f"{place.name} ({label})")
        return best if best is not None else ((place.id, place.lat, place.lon), None, place.name)

    def point_place(self, point_id_):
        """The place a routing point belongs to (its id, or "<id>#<k>")."""
        return self.lib.get(point_id_.split("#")[0])

    # ---- entry time -----------------------------------------------------
    def entry(self, place, d, arrival, holiday):
        """-> (start, kind, close, error). `close` is the closing time of the interval used (None for tours)."""
        from places.hours import HoursError, entry_points
        le = place.last_entry_min * 60
        iv = self._intervals(place, d, holiday)
        if iv is None:
            return None, "open", None, Err("closed", f"{place.id} has no usable opening hours", place.id)
        if place.daylight_only:
            from places.sun import sun_times
            sun = sun_times(d, place.lat, place.lon)
            if sun is None:
                return None, "open", None, Err("closed", f"{place.id} is daylight only and the sun does not rise that day", place.id)
            lo, hi = wall(d, sun[0]), wall(d, sun[1])
            iv = [(max(s, lo), min(e, hi)) for s, e in iv if max(s, lo) < min(e, hi)]
        if place.entry_times.strip():
            try:
                pts = [wall(d, x) for x in entry_points(place.entry_times, d, holiday)]
            except HoursError:
                return None, "fixed", None, Err("no_departure", f"{place.id} has unreadable entry times", place.id)
            ok = [p for p in pts if any(s <= p < e and p <= e - le for s, e in iv)]
            cand = [p for p in ok if p >= arrival]
            if not cand:
                listing = ", ".join(hm(p) for p in ok) or "none"
                return None, "fixed", None, Err(
                    "no_departure",
                    f"{place.id} has no fixed entry at or after {hm(arrival)} (entries that day: {listing})",
                    place.id, {"arrive": hm(arrival)})
            return cand[0], "fixed", None, None
        late = False
        for s, e in sorted(iv):
            if e <= arrival:
                continue
            start = max(s, arrival)
            if start > e - le:
                late = True
                continue
            return start, "open", e, None
        if late:
            ends = [hm(e) for s, e in sorted(iv) if e > arrival]
            return None, "open", None, Err(
                "entry_too_late",
                f"you reach {place.id} at {hm(arrival)}, after the last entry (closes {ends[-1]}, last entry "
                f"{place.last_entry_min} min before)", place.id, {"arrive": hm(arrival)})
        if not iv:
            return None, "open", None, Err("closed", f"{place.id} is closed all day on {d.strftime('%A')}s", place.id)
        shown = ", ".join(f"{hm(s)}-{hm(e)}" for s, e in sorted(iv))
        return None, "open", None, Err(
            "closed", f"{place.id} is closed after {hm(arrival)} that day (open {shown})", place.id,
            {"arrive": hm(arrival)})

    # ---- free time before dinner (Planner.md 6.1) ------------------------------
    @staticmethod
    def _walk_only(j) -> bool:
        return j.rides == 0 and sum(l.metres for l in j.legs if l.type == "walk") <= 2000

    def _dinner_free(self, place, j, d, arrival, holiday):
        """-> (start, 'open', close) when this is a dinner place reached on foot before its window, else None."""
        if not place.is_food or "dinner" not in place.meals or not self._walk_only(j):
            return None
        wins = self._meal_windows(d, place)
        if any(a <= arrival < b for a, b in wins.values()):
            return None                                    # it can be eaten at now: the normal rules apply
        wa, wb = wins["dinner"]
        if not 0 < wa - arrival:
            return None
        start, kind, close, err = self.entry(place, d, max(arrival, wa), holiday)
        if err is not None or not wa <= start < wb or start - arrival > self.max_free:
            return None
        return start, kind, close

    def _fill(self, res, pool, place, cur, pt, t, start, d, names, plan_ids):
        """One extra visit to fill free time of 45 minutes or more: the best-scoring always-open place in the dinner
        place's cluster that fits with walk-only legs (and before sunset when daylight only)."""
        if pool is None:
            return None
        direct = self.router.earliest(cur, pt, t, self.walk_m)
        if direct is None or start - direct.arrive < self.fill_min:
            return None
        from places.hours import HoursError, parse
        cluster = pool.items[place.id].cluster if place.id in pool.items else None
        if cluster is None:
            return None
        taken = plan_ids | {v[0].id for v in res.visits}
        for c in pool.candidates:
            if c["id"] in taken or c["cluster"] != cluster or c["kind"] not in ("sight", "activity"):
                continue
            x = self.lib.get(c["id"])
            try:
                if not parse(x.hours).always:
                    continue
            except HoursError:
                continue
            xp, j1, xname = None, None, x.name
            for k, (label, la, lo) in enumerate(x.all_points()):     # the point reached first on foot
                cand = (point_id(x.id, k), la, lo)
                jk = self.router.earliest(cur, cand, t, self.walk_m)
                if jk is not None and self._walk_only(jk) and (j1 is None or jk.arrive < j1.arrive):
                    xp, j1, xname = cand, jk, x.name if k == 0 else f"{x.name} ({label})"
            if j1 is None:
                continue
            vs, ve = j1.arrive, j1.arrive + x.visit_min * 60
            if x.daylight_only:
                from places.sun import sun_times
                sun = sun_times(d, x.lat, x.lon)
                if sun is None or ve > wall(d, sun[1]) or vs < wall(d, sun[0]):
                    continue
            j2 = self.router.earliest(xp, pt, ve, self.walk_m)
            if j2 is None or not self._walk_only(j2) or j2.arrive > start:
                continue
            names[x.id] = x.name
            return None, (x, vs, ve), None, j1, j2, xp, xname
        return None

    # ---- timeline entries -----------------------------------------------
    def _legs(self, j, names, cur_name, t):
        """Journey -> timeline entries. Gaps become waits."""
        out = []
        pos = t
        where = cur_name
        for leg in j.legs:
            if leg.type == "walk":
                start, end = leg.depart, leg.arrive
                frm, to = names.get(leg.from_, leg.from_), names.get(leg.to, leg.to)
            else:
                start, end = leg.board_time, leg.alight_time
                frm, to = leg.board_stop, leg.alight_stop
            if start - pos >= 60:
                reason = "until the next connection" if out else "until it is time to leave for the next stop"
                out.append(Wait(at=where, **{"from": iso(pos)}, until=iso(start), reason=reason))
            if leg.type == "walk":
                out.append(Walk(**{"from": frm}, to=to, metres=leg.metres, depart=iso(start), arrive=iso(end),
                                from_id=leg.from_id, to_id=leg.to_id))
            else:
                out.append(Ride(mode=leg.mode, route_short=leg.route_short, route_long=leg.route_long,
                                headsign=leg.headsign, trip_id=leg.trip_id, board_stop=leg.board_stop,
                                board_time=iso(leg.board_time), alight_stop=leg.alight_stop,
                                alight_time=iso(leg.alight_time), feed=leg.feed, board_stop_id=leg.board_stop_id,
                                alight_stop_id=leg.alight_stop_id))
            pos, where = end, to
        return out

    # ---- main -----------------------------------------------------------
    def schedule(self, req: PlanRequest, stops: list[tuple[str, int]], pack_ids=None, check_plan=True, pool=None) -> Result:
        """stops: [(place_id, visit_minutes)] in order."""
        d = dt.date.fromisoformat(req.date)
        self.router.for_date(d)
        o = req.origin
        origin = (o.id, o.lat, o.lon)
        leave0 = at(d, req.leave_after)
        back_by, leave_by = self.deadlines(req, d)
        res = Result([])
        names = {o.id: o.name}
        cur, cur_name, t = origin, o.name, leave0
        broken = False
        for idx, (pid, minutes) in enumerate(stops):
            try:
                place = self.lib.get(pid)
            except KeyError:
                res.errors.append(Err("unknown_place", f"{pid} is not a known place", pid))
                continue
            names[pid] = place.name
            if pack_ids is not None and pid not in pack_ids:
                res.errors.append(Err("unknown_place", f"{pid} is not in the candidate list", pid))
                continue
            if req.weather == "rain" and place.weather == "outdoor":
                res.errors.append(Err("unknown_place", f"{pid} is outdoors and the request is for rain", pid))
                continue
            if broken:
                continue
            mw = place.max_walk_m if idx == 0 else self.walk_m     # first stop: the place's own limit, else the engine default
            if t >= back_by:
                res.errors.append(Err("home_too_late", f"the day is already over at {hm(t)} before {pid}", pid))
                broken = True
                continue
            pt, j, disp = self._reach(cur, place, t, mw)      # a long place: the reference point reached first
            names[pt[0]] = disp
            res.visit_points[pid] = (pt, disp)
            if j is None:
                res.errors.append(Err("no_journey", f"no journey from {cur_name} to {pid} leaving after {hm(t)}", pid,
                                      {"leave_after": hm(t)}))
                broken = True
                continue
            if j.arrive > back_by:
                res.errors.append(Err("home_too_late",
                                      f"the first journey to {pid} arrives {hm(j.arrive)}, after the {hm(back_by)} limit",
                                      pid, {"arrive": hm(j.arrive), "back_by": hm(back_by)}))
                broken = True
                continue
            holiday = self.lib.cfg.holidays.is_holiday(d, place.municipality)
            free = self._dinner_free(place, j, d, j.arrive, holiday)
            if free is not None:
                # a walk-only leg to a dinner place that is not open yet: free time, perhaps with one inserted visit
                start, kind, close = free
                ins = self._fill(res, pool, place, cur, pt, t, start, d, names, plan_ids={p for p, _ in stops})
                if ins is not None:
                    _, vis, _, j1, j2, xp, xname = ins
                    names[xp[0]] = xname
                    res.journeys += [(j1, xp), (j2, pt)]
                    res.timeline += self._legs(j1, names, cur_name, t)
                    res.timeline.append(Visit(place_id=vis[0].id, name=vis[0].name, start=iso(vis[1]), end=iso(vis[2]),
                                              entry="open", inserted=True, reason="fills time before dinner",
                                              point_id=xp[0]))
                    res.visits.append((vis[0], vis[1], vis[2], "open", None))
                    res.timeline += self._legs(j2, names, xname, vis[2])
                    arrival = j2.arrive
                else:
                    res.journeys.append((j, pt))
                    res.timeline.extend(self._legs(j, names, cur_name, t))
                    arrival = j.arrive
                if start - arrival >= 60:
                    res.timeline.append(FreeTime(at=place.name, **{"from": iso(arrival)}, until=iso(start),
                                                 reason="free time before dinner", point_id=pt[0]))
                wait = 0
            else:
                res.journeys.append((j, pt))
                res.timeline.extend(self._legs(j, names, cur_name, t))
                arrival = j.arrive
                start, kind, close, err = self.entry(place, d, arrival, holiday)
                if err:
                    res.errors.append(err)
                    # not visited: stay where we are in time, but we did travel there
                    cur, cur_name, t = pt, disp, arrival
                    continue
                wait = start - arrival
                if wait > self.max_wait:
                    res.errors.append(Err(
                        "wait_too_long",
                        f"you reach {pid} at {hm(arrival)} but it admits you at {hm(start)}, a {round(wait / 60)} minute wait "
                        f"(limit {self.max_wait // 60})", pid, {"arrive": hm(arrival), "entry": hm(start)}))
                if wait >= 60:
                    res.timeline.append(Wait(at=place.name, **{"from": iso(arrival)}, until=iso(start),
                                             reason="fixed entry time" if kind == "fixed" else "until it opens"))
            vmin = int(minutes)
            if vmin < place.visit_min:
                res.errors.append(Err("visit_too_short", f"{pid}: {vmin} minutes is below the {place.visit_min} minute minimum",
                                      pid, {"visit_min": place.visit_min}))
                vmin = place.visit_min
            cap = 2 * place.visit_typical
            if vmin > cap:
                res.warnings.append(f"Stay at {place.name} shortened from {vmin} to {cap} minutes.")
                vmin = cap
            end = start + vmin * 60
            if kind == "open" and close is not None and end > close:
                res.errors.append(Err("visit_overruns", f"{pid}: a {vmin} minute visit from {hm(start)} ends {hm(end)}, after it closes at {hm(close)}",
                                      pid, {"start": hm(start), "end": hm(end), "close": hm(close)}))
            if place.daylight_only:
                from places.sun import sun_times
                sun = sun_times(d, place.lat, place.lon)
                if sun and end > wall(d, sun[1]):
                    res.errors.append(Err("visit_overruns", f"{pid}: the visit ends {hm(end)}, after sunset at {hm(wall(d, sun[1]))}",
                                          pid, {"end": hm(end), "sunset": hm(wall(d, sun[1]))}))
            meal = None
            if place.is_food:
                wins = self._meal_windows(d, place)
                inside = [m for m, (a, b) in wins.items() if a <= start < b]
                if not inside:
                    shown = ", ".join(f"{m} {hm(a)}-{hm(b)}" for m, (a, b) in wins.items()) or "none"
                    res.errors.append(Err("meal_outside_window", f"{pid}: a visit starting {hm(start)} is outside its meal windows ({shown})",
                                          pid, {"start": hm(start)}))
                else:
                    meal = next((m for m in ("lunch", "dinner") if m in inside), None)
            res.visits.append((place, start, end, kind, meal))
            res.timeline.append(Visit(place_id=pid, name=place.name, start=iso(start), end=iso(end),
                                      entry=kind, meal=meal, point_id=pt[0]))
            cur, cur_name, t = pt, disp, end
        if broken or not check_plan:
            res.last_point, res.end_t = cur, t
            return res
        # meals
        for m in required_meals(req, self.lib.cfg.meals):
            from places.shortlist import wall_window
            a, b = wall_window(d, self.lib.cfg.meals[m])
            if not any(p.is_food and m in p.meals and a <= s < b for p, s, e, k, ml in res.visits):
                if not any(e.code == "meal_outside_window" for e in res.errors):
                    res.errors.append(Err("meal_missing", f"the day spans the {m} window ({hm(a)}-{hm(b)}) but no stop is a {m} place starting in it",
                                          "", {"window": f"{hm(a)}-{hm(b)}"}))
        # the way home
        res.last_point, res.end_t = cur, t
        if not stops or cur == origin:
            return res
        j = self.router.earliest(cur, origin, t, self.walk_m)
        if j is None:
            res.errors.append(Err("no_journey", f"no journey home from {cur_name} leaving after {hm(t)}", stops[-1][0], {"leave_after": hm(t)}))
            return res
        res.journeys.append((j, origin))
        res.home_idx = len(res.timeline)
        leaves = self._leaves_at(j)
        if leave_by is not None and leaves > leave_by:
            res.errors.append(Err(
                "left_too_late",
                f"after {stops[-1][0]} ends at {hm(t)} the first vehicle home leaves {hm(leaves)}, after the {hm(leave_by)} leave-by time",
                stops[-1][0], {"end": hm(t), "leaves": hm(leaves), "leave_by": hm(leave_by)}))
        if j.arrive > back_by:
            res.errors.append(Err(
                "home_too_late",
                f"after {stops[-1][0]} ends at {hm(t)} the first journey home arrives {hm(j.arrive)}, after the {hm(back_by)} limit",
                stops[-1][0], {"end": hm(t), "arrive": hm(j.arrive), "back_by": hm(back_by)}))
        res.timeline.extend(self._legs(j, names, cur_name, t))
        res.home_arrive = j.arrive
        return res

    # ---- the numbers around a valid plan ----------------------------------
    @staticmethod
    def _leaves_at(jj) -> int:
        """When the visitor leaves the destination: the first vehicle's boarding time (a walk-only journey: its departure)."""
        r = next((x for x in jj.legs if x.type == "ride"), None)
        return r.board_time if r is not None else jj.depart

    @staticmethod
    def _vehicle(jj):
        r = next((x for x in jj.legs if x.type == "ride"), None)
        if r is None:
            return None
        return {"mode": r.mode, "route_short": r.route_short, "headsign": r.headsign,
                "board_stop": r.board_stop, "board_time": iso(r.board_time)}

    def safe_return(self, req, res: Result, names=None) -> SafeReturn | None:
        d = dt.date.fromisoformat(req.date)
        self.router.for_date(d)
        o = req.origin
        origin = (o.id, o.lat, o.lon)
        back_by, leave_by = self.deadlines(req, d)
        last = res.last_point
        if last is None or last == origin:
            return None
        names = {o.id: o.name, last[0]: self.point_place(last[0]).name}
        vehicle = self._vehicle
        if leave_by is not None:
            return self._safe_return_leave(res, origin, last, names[last[0]], leave_by, back_by)
        j = self.router.latest(last, origin, back_by, self.walk_m)
        if j is None:
            return None
        backup = None
        limit = j.arrive
        for _ in range(4):
            limit -= 1
            b = self.router.latest(last, origin, limit, self.walk_m)
            if b is None:
                break
            if b.depart < j.depart:
                backup = {"depart": iso(b.depart), "first_vehicle": vehicle(b), "arrive_home": iso(b.arrive)}
                break
            limit = min(limit, b.arrive)
        return SafeReturn(from_place=names[last[0]], depart=iso(j.depart), first_vehicle=vehicle(j),
                          arrive_home=iso(j.arrive), backup=backup)

    def _safe_return_leave(self, res, origin, last, name, leave_by, home_by) -> SafeReturn | None:
        """Leave mode: the latest vehicle home that still leaves the destination by the leave-by time (`engine.latest` only takes
        arrival deadlines). From the end of the last visit, take each journey home in turn (the next one starts a minute after
        the previous journey's departure) while its first vehicle leaves by the leave-by time and it gets home by the latest
        arrival; the last such journey is the safe return and the one before it the backup."""
        t = res.end_t if res.end_t is not None else leave_by
        found = []
        for _ in range(60):
            j = self.router.earliest(last, origin, t, self.walk_m)
            if j is None or self._leaves_at(j) > leave_by or j.arrive > home_by:
                break
            found.append(j)
            t = j.depart + 60
        if not found:
            return None
        j = found[-1]
        backup = None
        if len(found) > 1:
            b = found[-2]
            backup = {"depart": iso(b.depart), "first_vehicle": self._vehicle(b), "arrive_home": iso(b.arrive)}
        return SafeReturn(from_place=name, depart=iso(j.depart), first_vehicle=self._vehicle(j),
                          arrive_home=iso(j.arrive), backup=backup)

    def plan_b(self, req, res: Result) -> list[PlanB]:
        d = dt.date.fromisoformat(req.date)
        self.router.for_date(d)
        o = req.origin
        back_by, leave_by = self.deadlines(req, d)
        out = []
        for j, target in res.journeys:
            rides = [x for x in j.legs if x.type == "ride"]
            is_home = target[0] == o.id
            for a, b in zip(rides, rides[1:]):
                if b.board_time - a.alight_time >= self.change_s:
                    continue
                try:
                    src = self.stop_point(b.board_stop_id)
                except KeyError:
                    continue
                nj = self.router.earliest(src, target, b.board_time + 60, self.walk_m)
                tname = o.name if is_home else self.point_place(target[0]).name
                missed = {"route_short": b.route_short, "headsign": b.headsign, "board_time": iso(b.board_time)}
                if nj is None:
                    out.append(PlanB(change_at=b.board_stop, missed=missed, target=tname,
                                     note="No later journey that day.",
                                     within_deadline=False if is_home or leave_by is not None else None))
                    continue
                names = {src[0]: b.board_stop, target[0]: tname, o.id: o.name}
                entries = self._legs(nj, names, b.board_stop, b.board_time + 60)
                out.append(PlanB(change_at=b.board_stop, missed=missed, target=tname,
                                 next_journey=[e.model_dump(mode="json", by_alias=True) for e in entries],
                                 arrive=iso(nj.arrive),
                                 within_deadline=self._within(nj, is_home, back_by, leave_by)))
        return out

    @staticmethod
    def _within(nj, is_home, back_by, leave_by):
        """Plan B against the deadline. Home mode: judged on the way home only. Leave mode: legs before the last visit are judged
        against the leave-by time (the next journey must still arrive in time to visit), the way home against the latest arrival."""
        if is_home:
            return nj.arrive <= back_by
        return None if leave_by is None else nj.arrive <= leave_by

    def accepted_leg_warnings(self, res: Result, path) -> list[str]:
        p = Path(path) if path else None
        if not p or not p.exists():
            return []
        with open(p, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        out, seen = [], set()
        for j, _ in res.journeys:
            for leg in j.legs:
                if leg.type != "ride":
                    continue
                for r in rows:
                    if leg.trip_id.endswith(r["trip"]) and (r["trip"], leg.board_time) not in seen:
                        seen.add((r["trip"], leg.board_time))
                        out.append(f"The {leg.route_short} {hm(leg.board_time)} trip includes a very fast leg "
                                   f"({r['from_stop']} to {r['to_stop']}) kept as printed in the operator's timetable.")
        return out
