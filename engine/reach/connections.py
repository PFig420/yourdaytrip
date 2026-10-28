"""Per-date connection arrays (parallel lists, sorted by departure) with an LRU cache."""
from __future__ import annotations

import datetime as dt
from collections import OrderedDict
from dataclasses import dataclass

from .calendar import active_services, to_instant


@dataclass
class Connections:
    dep: list
    arr: list
    frm: list
    to: list
    tinst: list
    board_ok: list
    alight_ok: list
    trips: list          # tinst -> (trip_id, service_date)
    n: int


class Timetable:
    """Trip templates (stop idx + offsets) and the cached connection windows."""

    def __init__(self, feeds, network, cache_dates: int = 8):
        self.feeds = feeds
        self.net = network
        self.cache_dates = cache_dates
        self._cache: OrderedDict = OrderedDict()
        self.routes = {}
        self.trip_info = {}   # trip id -> (route id, headsign, feed id)
        self.tpl = {}         # trip id -> (service, feed, route, hops)
        for feed in feeds:
            self.routes.update(feed.routes)
            for tid, trip in feed.trips.items():
                self.trip_info[tid] = (trip.route, trip.headsign, feed.id)
                t = trip.times
                hops = []
                for k in range(len(t) - 1):
                    s0, _a0, d0, pk0, _do0 = t[k]
                    s1, a1, _d1, _pk1, do1 = t[k + 1]
                    if s0 not in network.index or s1 not in network.index:
                        continue
                    hops.append((d0, a1, network.index[s0], network.index[s1], pk0, do1, k))
                if hops:
                    self.tpl[tid] = (trip.service, feed.id, trip.route, hops)

    def _trip_allowed(self, route_id, ex_types, ex_routes):
        r = self.routes[route_id]
        return r.type not in ex_types and r.short not in ex_routes

    def window(self, d: dt.date, ex_types=frozenset(), ex_routes=frozenset()) -> Connections:
        """Connections of service dates d-1, d, d+1 (covers past-midnight trips and late deadlines)."""
        key = (d, ex_types, ex_routes)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        dates = [d - dt.timedelta(days=1), d, d + dt.timedelta(days=1)]
        dep, arr, frm, to, tin, bok, aok, keys = [], [], [], [], [], [], [], []
        trips = []
        for sd in dates:
            base = to_instant(sd, 0)
            active = {}
            for feed in self.feeds:
                for s in active_services(feed, sd):
                    active[s] = True
            allowed = {}
            for tid, (svc, _fid, route, hops) in self.tpl.items():
                if svc not in active:
                    continue
                ok = allowed.get(route)
                if ok is None:
                    ok = allowed[route] = self._trip_allowed(route, ex_types, ex_routes)
                if not ok:
                    continue
                ti = len(trips)
                trips.append((tid, sd))
                for d0, a1, f, t, pk, do, seq in hops:
                    i = len(dep)
                    dp = base + d0
                    ar = base + a1
                    dep.append(dp)
                    arr.append(ar)
                    frm.append(f)
                    to.append(t)
                    tin.append(ti)
                    bok.append(pk)
                    aok.append(do)
                    keys.append((dp, ar, ti, seq))
        order = sorted(range(len(dep)), key=keys.__getitem__)
        c = Connections([dep[i] for i in order], [arr[i] for i in order], [frm[i] for i in order],
                        [to[i] for i in order], [tin[i] for i in order], [bok[i] for i in order],
                        [aok[i] for i in order], trips, len(order))
        self._cache[key] = c
        while len(self._cache) > self.cache_dates:
            self._cache.popitem(last=False)
        return c
