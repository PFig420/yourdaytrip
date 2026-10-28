"""Rebuild journeys (legs) from scan node chains."""
from __future__ import annotations

from .models import Journey, RideLeg, WalkLeg
from .network import haversine
from .scan import ACCESS, EGRESS, FOOT, RIDE


def _chain(head, forward: bool):
    out = []
    n = head
    while n is not None:
        out.append(n)
        n = n[4]
    if forward:
        out.reverse()
    return out


class JourneyBuilder:
    def __init__(self, tt, net, cn):
        self.tt, self.net, self.cn = tt, net, cn

    def _ride(self, c0, c1):
        cn, tt, net = self.cn, self.tt, self.net
        tid = cn.trips[cn.tinst[c0]][0]
        route_id, headsign, feed = tt.trip_info[tid]
        r = tt.routes[route_id]
        b, a = cn.frm[c0], cn.to[c1]
        return RideLeg("ride", r.mode, feed, route_id, r.short, r.long, tid, headsign,
                       net.ids[b], net.names[b], cn.dep[c0], net.ids[a], net.names[a], cn.arr[c1])

    def items(self, head, forward, origin, target):
        """Items from a node chain. head: last node (forward) or first node (reverse).
        origin/target: (id, lat, lon). Item: ("walk", from, to, metres, secs) | ("ride", RideLeg)."""
        net = self.net
        items = []
        for n in _chain(head, forward):
            kind = n[0]
            if kind == ACCESS:
                items.append(("walk", origin[0], net.names[n[1]],
                              net.leg_metres(origin[0], net.ids[n[1]], haversine(origin[1], origin[2], net.lat[n[1]], net.lon[n[1]])),
                              n[3], origin[0], net.ids[n[1]]))
            elif kind == EGRESS:
                items.append(("walk", net.names[n[1]], target[0],
                              net.leg_metres(net.ids[n[1]], target[0], haversine(target[1], target[2], net.lat[n[1]], net.lon[n[1]])),
                              n[3], net.ids[n[1]], target[0]))
            elif kind == FOOT:
                items.append(("walk", net.names[n[1]], net.names[n[2]],
                              net.leg_metres(net.ids[n[1]], net.ids[n[2]], net.stop_metres(n[1], n[2])), n[3],
                              net.ids[n[1]], net.ids[n[2]]))
            elif kind == RIDE:
                items.append(("ride", self._ride(n[1], n[2])))
        return items

    def finish(self, items, origin, target, egress_walk=None, access_walk=None,
               coverage=None, anchor=None):
        """Merge walks, assign times, return a Journey."""
        net = self.net
        items = list(items)
        if access_walk:
            items.insert(0, ("walk", origin[0], net.names[access_walk[0]],
                             net.leg_metres(origin[0], net.ids[access_walk[0]],
                                            haversine(origin[1], origin[2], net.lat[access_walk[0]], net.lon[access_walk[0]])),
                             access_walk[1], origin[0], net.ids[access_walk[0]]))
        if egress_walk:
            items.append(("walk", net.names[egress_walk[0]], target[0],
                          net.leg_metres(net.ids[egress_walk[0]], target[0],
                                         haversine(target[1], target[2], net.lat[egress_walk[0]], net.lon[egress_walk[0]])),
                          egress_walk[1], net.ids[egress_walk[0]], target[0]))
        merged = []
        for it in items:
            if it[0] == "walk":
                if it[4] == 0:
                    continue
                if merged and merged[-1][0] == "walk":
                    p = merged[-1]
                    merged[-1] = ("walk", p[1], it[2], p[3] + it[3], p[4] + it[4], p[5], it[6])
                    continue
            merged.append(it)
        if not merged:   # origin and target coincide
            a = anchor[1] if anchor else 0
            return Journey(a, a, (), 0, 0, coverage, tuple(coverage.warnings) if coverage else ())
        # times
        n = len(merged)
        t = [None] * n   # (start, end) per item
        for i, it in enumerate(merged):
            if it[0] == "ride":
                t[i] = (it[1].board_time, it[1].alight_time)
        for i, it in enumerate(merged):
            if it[0] == "walk":
                secs = it[4]
                if i > 0 and t[i - 1] is not None:
                    t[i] = (t[i - 1][1], t[i - 1][1] + secs)
                elif i + 1 < n and t[i + 1] is not None:
                    t[i] = (t[i + 1][0] - secs, t[i + 1][0])
        # a walk between walks cannot occur after merging; walk-only journeys use anchor
        if n and all(x is None for x in t):
            kind, a = anchor
            for i, it in enumerate(merged):
                secs = it[4]
                t[i] = (a, a + secs) if kind == "start" else (a - secs, a)
        legs = []
        for it, tm in zip(merged, t):
            if it[0] == "ride":
                legs.append(it[1])
            else:
                legs.append(WalkLeg("walk", it[1], it[2], round(it[3]), it[4], tm[0], tm[1], it[5], it[6]))
        rides = sum(1 for l in legs if l.type == "ride")
        walk_s = sum(l.seconds for l in legs if l.type == "walk")
        depart = legs[0].board_time if legs[0].type == "ride" else legs[0].depart
        arrive = legs[-1].alight_time if legs[-1].type == "ride" else legs[-1].arrive
        cov = coverage
        return Journey(depart, arrive, tuple(legs), rides, walk_s, cov, tuple(cov.warnings) if cov else ())
