"""The planned day as GeoJSON for a map (MapPlan.md 3.2).

A picture of the checked plan, built from the plan's own timeline and request only: it adds no times, routes or checks. Every
position is a stop of the engine's feeds or a place record (its reference points included); an id it cannot place is skipped
with a warning, never guessed. Rides are drawn through the stops the trip serves between boarding and alighting (there is no
shapes.txt in the feeds); walks are straight lines (the engine has no street path). Coordinates are [lon, lat].
"""
from __future__ import annotations

import math

from . import paths as paths_mod
from .models import DayPlan


def _hm(iso: str) -> str:
    return iso[11:16] if iso else ""


def _secs(iso: str) -> int:
    """Seconds after local midnight from a local ISO time ('2026-10-24T09:07:00+01:00')."""
    return int(iso[11:13]) * 3600 + int(iso[14:16]) * 60 + int(iso[17:19] or 0)


class _Resolver:
    def __init__(self, plan: DayPlan, engine, lib):
        self.stops = {}
        self.trips = {}
        for f in engine.feeds:
            self.stops.update(f.stops)
            self.trips.update(f.trips)
        self.lib, self.origin = lib, plan.request.origin
        self.warnings: list[str] = []

    def point(self, pid: str):
        """-> ([lon, lat], label) or None. `label` is the reference point's name, '' for a main point or a stop."""
        if not pid:
            return None
        if pid in self.stops:
            n, la, lo = self.stops[pid]
            return [lo, la], ""
        if pid == self.origin.id:
            return [self.origin.lon, self.origin.lat], ""
        base, _, k = pid.partition("#")
        p = self.lib.places.get(base)
        if p is not None:
            pts = p.all_points()
            i = int(k) if k.isdigit() else 0
            if i < len(pts):
                label, la, lo = pts[i]
                return [lo, la], ("" if i == 0 else label)
        self.warn(f"could not place '{pid}' on the map")
        return None

    def warn(self, msg: str):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def ride_stops(self, e):
        """Stop ids from boarding to alighting inclusive. A trip that serves a stop twice (a circular line) is read by the time:
        the occurrence whose departure (arrival) matches the ride's board (alight) time."""
        trip = self.trips.get(e.trip_id)
        if trip is None or not trip.times:
            self.warn(f"trip '{e.trip_id}' not found; its ride is drawn as a straight line")
            return [e.board_stop_id, e.alight_stop_id]
        rows = trip.times                                  # (stop_id, arr, dep, pickup_ok, dropoff_ok)
        day = 86400
        bt, at_ = _secs(e.board_time), _secs(e.alight_time)
        boards = [i for i, r in enumerate(rows) if r[0] == e.board_stop_id]
        i0 = next((i for i in boards if rows[i][2] is not None and rows[i][2] % day == bt), boards[0] if boards else None)
        if i0 is None:
            self.warn(f"trip '{e.trip_id}' does not serve '{e.board_stop_id}'")
            return [e.board_stop_id, e.alight_stop_id]
        alights = [i for i in range(i0 + 1, len(rows)) if rows[i][0] == e.alight_stop_id]
        i1 = next((i for i in alights if rows[i][1] is not None and rows[i][1] % day == at_), alights[0] if alights else None)
        if i1 is None:
            self.warn(f"trip '{e.trip_id}' does not reach '{e.alight_stop_id}' after boarding")
            return [e.board_stop_id, e.alight_stop_id]
        return [r[0] for r in rows[i0:i1 + 1]]


def _metres(coords) -> int:
    """Length of a [lon, lat] polyline, in metres."""
    R, m = 6_371_000.0, 0.0
    for a, b in zip(coords, coords[1:]):
        p1, p2 = math.radians(a[1]), math.radians(b[1])
        x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
        m += 2 * R * math.asin(min(1.0, math.sqrt(x)))
    return round(m)


def _ride_line(e, ids, rs, paths):
    """-> (coords [lon, lat], gaps, path). The cached path of each consecutive stop pair; a pair with none is a straight segment
    (a gap), so a guess never looks like a real route. `gaps` are index ranges [i, j] of the coordinates that are straight."""
    coords, gaps, cached, sources = [], [], 0, []
    for a, b in zip(ids, ids[1:]):
        seg = paths.ride(a, b, e.mode) if paths is not None else None
        if seg:
            pts = [[lo, la] for la, lo in seg]
            cached += 1
            sources.append(paths.ride_source(a, b, e.mode))
        else:
            pa, pb = rs.point(a), rs.point(b)
            if pa is None or pb is None:
                continue
            pts = [pa[0], pb[0]]
        start = len(coords) - 1 if coords else 0
        coords += pts if not coords else pts[1:]
        if not seg:
            if gaps and gaps[-1][1] == start:
                gaps[-1][1] = len(coords) - 1
            else:
                gaps.append([start, len(coords) - 1])
    n = len(ids) - 1
    how = "osm" if cached == n and n > 0 else "partial" if cached else "straight"
    known = [s for s in sources if s]
    if e.mode != "bus" or not known:
        source = ""                                     # rail and the ferry keep the Phase 2 behaviour; no cache, no claim
    else:
        source = "relation" if all(s == "relation" for s in known) and len(known) == n else \
                 "shortest" if not any(s == "relation" for s in known) else "mixed"
    return coords, gaps, how, source


def _walk_line(a_id, b_id, pa, pb, paths):
    """-> (coords [lon, lat], gaps, path): the cached footpath, or the straight line as one gap."""
    seg = paths.walk(a_id, b_id) if paths is not None and a_id and b_id else None
    if seg:
        return [[lo, la] for la, lo in seg], [], "osm"
    return [pa, pb], [[0, 1]], "straight"


def _bounds(coords):
    if not coords:
        return None
    lons, lats = [c[0] for c in coords], [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]     # [west, south, east, north]


def build(plan, engine, lib, paths=None) -> dict:
    """A GeoJSON FeatureCollection for a DayPlan (or its JSON). Properties: bounds_all, bounds_destination, warnings.

    `paths`: a paths.Paths (rides and walks follow the roads, rails and footpaths of the cache), False (straight lines, no
    warning), or None to load data/paths/paths.json.gz (missing or stale: straight lines and one warning)."""
    if not isinstance(plan, DayPlan):
        plan = DayPlan.model_validate(plan)
    rs = _Resolver(plan, engine, lib)
    if paths is None:
        paths, why = paths_mod.load(feeds={f.id: f.sha256 for f in engine.feeds})
        if why:
            rs.warn(why)
    elif paths is False:
        paths = None
    feats, per_step = [], []                       # per_step: (step, kind, coords) for the bounds

    def add(kind, geom, step=None, **props):
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"kind": kind, **({"step": step} if step is not None else {}), **props}})
        coords = geom["coordinates"] if geom["type"] == "LineString" else [geom["coordinates"]]
        per_step.append((step, kind, coords))

    o = plan.request.origin
    add("origin", {"type": "Point", "coordinates": [o.lon, o.lat]}, name=o.name, role="start and end")
    n = 0
    for i, e in enumerate(plan.timeline):
        if e.type == "visit":
            hit = rs.point(e.point_id or e.place_id)
            n += 1
            if hit is None:
                continue
            add("visit", {"type": "Point", "coordinates": hit[0]}, i, n=n, name=e.name, start=_hm(e.start), end=_hm(e.end),
                meal=e.meal or "", entry=e.entry, inserted=e.inserted, reason=e.reason, point_name=hit[1])
        elif e.type == "ride":
            ids = rs.ride_stops(e)
            line, gaps, how, source = _ride_line(e, [s for s in ids if rs.point(s) is not None], rs, paths)
            if len(line) >= 2:
                add("ride", {"type": "LineString", "coordinates": line}, i, mode=e.mode, route_short=e.route_short,
                    headsign=e.headsign, board_stop=e.board_stop, board_time=_hm(e.board_time), alight_stop=e.alight_stop,
                    alight_time=_hm(e.alight_time), stops=len([s for s in ids if rs.point(s) is not None]), path=how, gaps=gaps,
                    route_source=source)
            for role, sid, name, t in (("board", e.board_stop_id, e.board_stop, e.board_time),
                                       ("alight", e.alight_stop_id, e.alight_stop, e.alight_time)):
                hit = rs.point(sid)
                if hit is not None:
                    add("stop", {"type": "Point", "coordinates": hit[0]}, i, name=name, role=role, time=_hm(t), mode=e.mode)
        elif e.type == "walk":
            a, b = rs.point(e.from_id), rs.point(e.to_id)
            if a is not None and b is not None:
                line, gaps, how = _walk_line(e.from_id, e.to_id, a[0], b[0], paths)
                add("walk", {"type": "LineString", "coordinates": line}, i, metres=e.metres, depart=_hm(e.depart),
                    arrive=_hm(e.arrive), to=e.to, path=how, path_m=_metres(line), gaps=gaps)
        elif e.type == "free_time":
            hit = rs.point(e.point_id)
            if hit is not None:
                add("free_time", {"type": "Point", "coordinates": hit[0]}, i, at=e.at, **{"from": _hm(e.from_)},
                    until=_hm(e.until), reason=e.reason)
            for s in e.suggestions:
                h2 = rs.point(s.get("point_id", ""))
                if h2 is not None:
                    here = rs.point(e.point_id)
                    if here is not None:
                        wl, wg, how = _walk_line(e.point_id, s.get("point_id", ""), here[0], h2[0], paths)
                        add("idea_walk", {"type": "LineString", "coordinates": wl}, i, name=s["name"], path=how, path_m=_metres(wl),
                            gaps=wg)
                    add("idea", {"type": "Point", "coordinates": h2[0]}, i, name=s["name"], place_kind=s["kind"],
                        walk_m=s["walk_m"], walk_min=s["walk_min"], minutes_there=s["minutes_there"],
                        open_until=s["open_until"] or "", point_name=h2[1], description=s.get("description", ""))
        # waits happen at the previous feature's position: no feature
    visit_steps = [i for i, e in enumerate(plan.timeline) if e.type == "visit"]
    allc = [c for _, _, cs in per_step for c in cs]
    dest = []
    if visit_steps:
        lo, hi = visit_steps[0], visit_steps[-1]
        dest = [c for st, kd, cs in per_step if st is not None and lo <= st <= hi and kd != "idea" for c in cs]
    props = {"bounds_all": _bounds(allc), "bounds_destination": _bounds(dest) or _bounds(allc), "warnings": rs.warnings}
    return {"type": "FeatureCollection", "features": feats, "properties": props}
