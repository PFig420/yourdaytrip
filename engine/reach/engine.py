"""The Engine: loads feeds once, answers earliest / latest / windows queries."""
from __future__ import annotations

import datetime as dt
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import coverage as cov
from .calendar import local_instant, to_date
from .connections import Timetable
from .gtfs import read_feed, sha256_file
from .journeys import JourneyBuilder
from .models import StopWindow, Window, WindowResult
from .network import Barriers, Network, WalkParams, haversine
from .overrides import Overrides
from .scan import INF, NEG, forward_scan, reverse_scan


class ReachError(Exception):
    code = "bad_input"
    http = 400


class BadInput(ReachError):
    pass


class CoverageError(ReachError):
    code = "strict_coverage"
    http = 422


@dataclass(frozen=True)
class Options:
    max_walk_m: float
    transfer_buffer_s: int
    min_stay_s: int = 0
    exclude_route_types: frozenset = frozenset()
    exclude_routes: frozenset = frozenset()
    include_journeys: bool = False
    strict_coverage: bool = False


_OPT_NAMES = {"max_walk_m", "transfer_buffer_s", "min_stay_s", "exclude_route_types", "exclude_routes",
              "include_journeys", "strict_coverage"}


def _point(p):
    if isinstance(p, dict):
        return (str(p.get("id", "point")), float(p["lat"]), float(p["lon"]))
    pid, lat, lon = p
    return (str(pid), float(lat), float(lon))


class Engine:
    def __init__(self, config_dir="engine/config"):
        cfg = Path(config_dir)
        with open(cfg / "engine.toml", "rb") as f:
            self.cfg = tomllib.load(f)
        with open(cfg / "feeds.toml", "rb") as f:
            feeds_cfg = tomllib.load(f)["feeds"]
        root = cfg.resolve().parent.parent
        self.warnings: list[str] = []
        self.feeds = []
        for fc in feeds_cfg:
            path = Path(fc["path"])
            if not path.is_absolute():
                path = root / path
            feed = read_feed(fc["id"], str(path))
            if feed.sha256 != fc["sha256"]:
                self.warnings.append(f"Feed '{fc['id']}' hash {feed.sha256[:12]}... differs from "
                                     f"feeds.toml {fc['sha256'][:12]}...; the feed has changed.")
            self.feeds.append(feed)
        self.walk = WalkParams(self.cfg["walk_speed_mps"], self.cfg["walk_detour"], self.cfg["transfer_max_m"],
                               self.cfg["access_max_m"], self.cfg["access_max_limit_m"])
        ov_path = self.cfg.get("walk_overrides")
        if ov_path and not Path(ov_path).is_absolute():
            ov_path = root / ov_path
        self.overrides, warn = Overrides.load(ov_path, self.feeds)
        if warn:
            self.warnings.append(warn)
        self.net = Network(self.feeds, self.walk, Barriers.from_geojson(cfg / "walk_barriers.geojson"), self.overrides)
        self.tt = Timetable(self.feeds, self.net, self.cfg.get("cache_dates", 8))

    # ---- helpers -------------------------------------------------------
    def health(self):
        return {"feeds": [{"id": f.id, "path": f.path, "sha256": f.sha256, "stops": len(f.stops),
                           "routes": len(f.routes), "trips": len(f.trips),
                           "first_date": f.first_date, "last_date": f.last_date} for f in self.feeds],
                "walk_overrides": self.overrides.health(), "warnings": self.warnings}

    def coverage(self, date):
        return cov.check(self.feeds, date)

    def _opts(self, kw) -> Options:
        bad = set(kw) - _OPT_NAMES
        if bad:
            raise BadInput(f"unknown option(s): {', '.join(sorted(bad))}")
        mw = float(kw.get("max_walk_m", self.walk.access_max_m))
        if not 0 <= mw <= self.walk.access_max_limit_m:
            raise BadInput(f"max_walk_m must be between 0 and {self.walk.access_max_limit_m:g}")
        buf = int(kw.get("transfer_buffer_s", self.cfg["transfer_buffer_s"]))
        if buf < 0:
            raise BadInput("transfer_buffer_s must be >= 0")
        return Options(mw, buf, int(kw.get("min_stay_s", 0)),
                       frozenset(int(x) for x in kw.get("exclude_route_types") or ()),
                       frozenset(str(x) for x in kw.get("exclude_routes") or ()),
                       bool(kw.get("include_journeys", False)), bool(kw.get("strict_coverage", False)))

    def _prepare(self, date, kw):
        try:
            d = to_date(date)
        except ValueError as e:
            raise BadInput(f"bad date: {date}") from e
        o = self._opts(kw)
        c = self.coverage(d)
        if o.strict_coverage and c.missing_feeds:
            raise CoverageError("; ".join(c.warnings))
        cn = self.tt.window(d, o.exclude_route_types, o.exclude_routes)
        return d, o, c, cn

    @staticmethod
    def _time(d, s, what):
        try:
            return local_instant(d, s)
        except (ValueError, TypeError) as e:
            raise BadInput(f"bad {what}: {s}") from e

    def _near(self, p, o):
        return self.net.near(p[1], p[2], o.max_walk_m, p[0])

    def _direct_walk(self, a, b, o):
        return self.net.walk_between_points(a[1], a[2], b[1], b[2], o.max_walk_m, a[0], b[0])

    def walk_seconds(self, a, b):
        """Walking time in seconds between two (lat, lon) points (the engine's speed and detour factor),
        or None if a walking barrier blocks the straight line. No distance cap."""
        if self.net.barriers.blocks(a[0], a[1], b[0], b[1]):
            return None
        return self.walk.seconds(haversine(a[0], a[1], b[0], b[1]))

    # ---- queries -------------------------------------------------------
    def earliest(self, origin, date, leave_after, target, **opts):
        origin, target = _point(origin), _point(target)
        d, o, c, cn = self._prepare(date, opts)
        t0 = self._time(d, leave_after, "leave_after")
        access = [(s, sec) for s, sec, _m in self._near(origin, o)]
        egress = {s: sec for s, sec, _m in self._near(target, o)}
        F = forward_scan(cn, self.net.footpaths, access, t0, INF, o.transfer_buffer_s, len(self.net.ids), egress)
        best = self._best_egress(F, egress)
        dw = self._direct_walk(origin, target, o)
        jb = JourneyBuilder(self.tt, self.net, cn)
        if dw and (best is None or t0 + dw[0] <= best[0]):
            return jb.finish([("walk", origin[0], target[0], dw[1], dw[0], origin[0], target[0])], origin, target,
                             coverage=c, anchor=("start", t0))
        if best is None:
            return None
        return self._forward_journey(jb, F, best, origin, target, c)

    @staticmethod
    def _best_egress(F, egress):
        best = None
        for s, sec in egress.items():
            nd = F.av_node[s]
            if nd is None:
                continue
            key = (F.av[s] + sec, nd[5], nd[6] + sec, nd[7])
            if best is None or key < best[0:1] + best[1:4]:
                best = key + (s, sec)
        return best

    def _forward_journey(self, jb, F, best, origin, target, c):
        s, sec = best[4], best[5]
        items = jb.items(F.av_node[s], True, origin, target)
        return jb.finish(items, origin, target, egress_walk=(s, sec), coverage=c)

    def latest(self, origin, date, target, back_by, **opts):
        origin, target = _point(origin), _point(target)
        d, o, c, cn = self._prepare(date, opts)
        deadline = self._time(d, back_by, "back_by")
        start = local_instant(d, "00:00")
        access = {s: sec for s, sec, _m in self._near(origin, o)}
        egress = [(s, sec) for s, sec, _m in self._near(target, o)]
        R = reverse_scan(cn, self.net.footpaths, egress, deadline, start, o.transfer_buffer_s,
                         len(self.net.ids), access)
        best = self._best_access(R, access)
        dw = self._direct_walk(origin, target, o)
        jb = JourneyBuilder(self.tt, self.net, cn)
        if dw and (best is None or deadline - dw[0] >= best[0]):
            return jb.finish([("walk", origin[0], target[0], dw[1], dw[0], origin[0], target[0])], origin, target,
                             coverage=c, anchor=("end", deadline))
        if best is None:
            return None
        return self._reverse_journey(jb, R, best, origin, target, c)

    @staticmethod
    def _best_access(R, access):
        best = None
        for s, sec in access.items():
            nd = R.bd_node[s]
            if nd is None:
                continue
            key = (R.bd[s] - sec, nd[5], nd[6] + sec, nd[7])
            if best is None or (-key[0], key[1], key[2], key[3]) < (-best[0], best[1], best[2], best[3]):
                best = key + (s, sec)
        return best

    def _reverse_journey(self, jb, R, best, origin, target, c):
        s, sec = best[4], best[5]
        items = jb.items(R.bd_node[s], False, origin, target)
        return jb.finish(items, origin, target, access_walk=(s, sec), coverage=c)

    def windows(self, origin, date, leave_after, back_by, points, **opts):
        origin = _point(origin)
        pts = [_point(p) for p in points]
        d, o, c, cn = self._prepare(date, opts)
        t0 = self._time(d, leave_after, "leave_after")
        deadline = self._time(d, back_by, "back_by")
        if deadline <= t0:
            raise BadInput("back_by must be after leave_after")
        net = self.net
        n = len(net.ids)
        o_near = self._near(origin, o)
        F = forward_scan(cn, net.footpaths, [(s, sec) for s, sec, _ in o_near], t0, deadline,
                         o.transfer_buffer_s, n)
        R = reverse_scan(cn, net.footpaths, [(s, sec) for s, sec, _ in o_near], deadline, t0,
                         o.transfer_buffer_s, n)
        jb = JourneyBuilder(self.tt, net, cn)
        out = []
        for p in pts:
            near = self._near(p, o)
            egress = {s: sec for s, sec, _ in near}
            fb = self._best_egress(F, egress)
            rb = self._best_access(R, egress)
            dw = self._direct_walk(origin, p, o)
            arrive = fb[0] if fb else None
            leave = rb[0] if rb else None
            walk_out = walk_back = False
            if dw:
                if arrive is None or t0 + dw[0] <= arrive:
                    arrive, walk_out = t0 + dw[0], True
                if leave is None or deadline - dw[0] >= leave:
                    leave, walk_back = deadline - dw[0], True
            avail = leave - arrive if arrive is not None and leave is not None else None
            if arrive is None:
                status = "unreachable"
            elif leave is None:
                status = "no_return"
            else:
                status = "ok" if avail >= o.min_stay_s else "no_window"
            outj = retj = None
            if o.include_journeys:
                if arrive is not None:
                    outj = (jb.finish([("walk", origin[0], p[0], dw[1], dw[0], origin[0], p[0])], origin, p, coverage=c,
                                      anchor=("start", t0)) if walk_out
                            else self._forward_journey(jb, F, fb, origin, p, c))
                if leave is not None:
                    # the return journey runs from the point to the origin
                    retj = (jb.finish([("walk", p[0], origin[0], dw[1], dw[0], p[0], origin[0])], p, origin, coverage=c,
                                      anchor=("end", deadline)) if walk_back
                            else self._reverse_journey(jb, R, rb, p, origin, c))
            out.append(Window(p[0], p[1], p[2], arrive, leave, avail, status, outj, retj))
        return WindowResult({"id": origin[0], "lat": origin[1], "lon": origin[2]}, d.isoformat(), t0, deadline,
                            tuple(out), c, tuple(c.warnings))

    def stop_windows(self, origin, date, leave_after, back_by, **opts):
        origin = _point(origin)
        d, o, c, cn = self._prepare(date, opts)
        t0 = self._time(d, leave_after, "leave_after")
        deadline = self._time(d, back_by, "back_by")
        if deadline <= t0:
            raise BadInput("back_by must be after leave_after")
        net = self.net
        n = len(net.ids)
        o_near = self._near(origin, o)
        F = forward_scan(cn, net.footpaths, [(s, sec) for s, sec, _ in o_near], t0, deadline,
                         o.transfer_buffer_s, n)
        R = reverse_scan(cn, net.footpaths, [(s, sec) for s, sec, _ in o_near], deadline, t0,
                         o.transfer_buffer_s, n)
        home = {s: sec for s, sec, _ in o_near}
        res = []
        for s in range(n):
            arrive = min(F.ao[s], F.av[s])
            arrive = None if arrive >= INF else arrive
            leave = R.bc[s] if R.bc[s] > NEG else None
            if s in home and (leave is None or deadline - home[s] > leave):
                leave = deadline - home[s]
            if arrive is None and leave is None:
                continue
            avail = leave - arrive if arrive is not None and leave is not None else None
            res.append(StopWindow(net.ids[s], net.names[s], net.lat[s], net.lon[s], arrive, leave, avail))
        return res
