"""Forward (earliest arrival) and reverse (latest departure) Connection Scan.

Nodes are immutable tuples forming linked chains used to rebuild journeys:
    (kind, a, b, secs, prev, legs, walk_s, trip_id)
kind 0 ACCESS  a=stop                      first walk, origin point -> stop
kind 1 RIDE    a=board conn, b=alight conn
kind 2 FOOT    a=from stop, b=to stop      transfer footpath
kind 3 EGRESS  a=stop                      last walk, stop -> target point
`prev` points toward the origin in the forward scan and toward the target in the reverse scan.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

INF = 1 << 60
NEG = -(1 << 60)

ACCESS, RIDE, FOOT, EGRESS = 0, 1, 2, 3


def _rank(node):
    """Tie-break key: fewer vehicle legs, less walking, smallest trip id."""
    return (node[5], node[6], node[7])


class Forward:
    def __init__(self, nstops):
        self.ao = [INF] * nstops        # arrival on foot from the origin (no boarding buffer)
        self.ao_node = [None] * nstops
        self.av = [INF] * nstops        # arrival by vehicle (+ footpath); boarding needs the buffer
        self.av_node = [None] * nstops


def forward_scan(cn, foot, access, t0, end, buf, nstops, egress=None):
    """access: [(stop, secs)]; egress: {stop: secs} for early termination (optional)."""
    F = Forward(nstops)
    ao, ao_node, av, av_node = F.ao, F.ao_node, F.av, F.av_node
    for s, secs in access:
        if t0 + secs < ao[s]:
            ao[s] = t0 + secs
            ao_node[s] = (ACCESS, s, 0, secs, None, 0, secs, "")
    dep, arr, frm, to, tin, bok, aok = cn.dep, cn.arr, cn.frm, cn.to, cn.tinst, cn.board_ok, cn.alight_ok
    trips = cn.trips
    entered = {}
    best = INF
    for c in range(bisect_left(dep, t0), cn.n):
        d = dep[c]
        if d > end or d >= best:
            break
        ti = tin[c]
        ent = entered.get(ti)
        if ent is None:
            if not bok[c]:
                continue
            f = frm[c]
            node = None
            if ao[f] <= d:
                node = ao_node[f]
            if av[f] + buf <= d:
                cand = av_node[f]
                if node is None or _rank(cand) < _rank(node):
                    node = cand
            if node is None:
                continue
            ent = entered[ti] = (c, node)
        if not aok[c]:
            continue
        a = arr[c]
        t = to[c]
        cur = av[t]
        if a > cur:
            continue
        pn = ent[1]
        tid = trips[ti][0]
        node = (RIDE, ent[0], c, 0, pn, pn[5] + 1, pn[6], tid)
        if a == cur and _rank(node) >= _rank(av_node[t]):
            continue
        av[t] = a
        av_node[t] = node
        if egress is not None:
            e = egress.get(t)
            if e is not None and a + e < best:
                best = a + e
        for nb, sec in foot[t]:
            x = a + sec
            if x > av[nb]:
                continue
            fn = (FOOT, t, nb, sec, node, node[5], node[6] + sec, node[7])
            if x == av[nb] and _rank(fn) >= _rank(av_node[nb]):
                continue
            av[nb] = x
            av_node[nb] = fn
            if egress is not None:
                e = egress.get(nb)
                if e is not None and x + e < best:
                    best = x + e
    return F


class Reverse:
    def __init__(self, nstops):
        self.lx = [NEG] * nstops        # latest time to be at the stop after alighting (walk to target)
        self.lx_node = [None] * nstops
        self.bc = [NEG] * nstops        # latest boarding time here when changing (buffer applies)
        self.bc_node = [None] * nstops
        self.bd = [NEG] * nstops        # latest boarding time reached directly from the origin side
        self.bd_node = [None] * nstops


def reverse_scan(cn, foot, egress, deadline, start, buf, nstops, access=None):
    """egress: [(stop, secs)] walking from stop to target; access: {stop: secs} for early stop."""
    R = Reverse(nstops)
    lx, lx_node, bc, bc_node, bd, bd_node = R.lx, R.lx_node, R.bc, R.bc_node, R.bd, R.bd_node
    for s, secs in egress:
        if deadline - secs > lx[s]:
            lx[s] = deadline - secs
            lx_node[s] = (EGRESS, s, 0, secs, None, 0, secs, "")
    direct = [(s, lx[s], lx_node[s]) for s, _ in egress if lx_node[s] is not None]
    for s, v, nd in direct:
        for nb, sec in foot[s]:
            x = v - sec
            fn = (FOOT, nb, s, sec, nd, 0, nd[6] + sec, "")
            if x > lx[nb] or (x == lx[nb] and _rank(fn) < _rank(lx_node[nb])):
                lx[nb] = x
                lx_node[nb] = fn
    dep, arr, frm, to, tin, bok, aok = cn.dep, cn.arr, cn.frm, cn.to, cn.tinst, cn.board_ok, cn.alight_ok
    trips = cn.trips
    useful = {}
    best = NEG
    for c in range(bisect_right(dep, deadline) - 1, -1, -1):
        d = dep[c]
        if d < start or d <= best:
            break
        ti = tin[c]
        ent = useful.get(ti)
        if ent is None:
            if not aok[c]:
                continue
            t = to[c]
            a = arr[c]
            cont = None
            if a <= lx[t]:
                cont = lx_node[t]
            if a + buf <= bc[t]:
                cand = bc_node[t]
                if cont is None or _rank(cand) < _rank(cont):
                    cont = cand
            if cont is None:
                continue
            ent = useful[ti] = (c, cont)
        if not bok[c]:
            continue
        f = frm[c]
        cont = ent[1]
        node = (RIDE, c, ent[0], 0, cont, cont[5] + 1, cont[6], trips[ti][0])
        if d > bc[f] or (d == bc[f] and _rank(node) < _rank(bc_node[f])):
            bc[f] = d
            bc_node[f] = node
        if d > bd[f] or (d == bd[f] and _rank(node) < _rank(bd_node[f])):
            bd[f] = d
            bd_node[f] = node
            if access is not None:
                e = access.get(f)
                if e is not None and d - e > best:
                    best = d - e
        for nb, sec in foot[f]:
            x = d - sec
            if x < bc[nb]:
                continue
            fn = (FOOT, nb, f, sec, node, node[5], node[6] + sec, node[7])
            if x == bc[nb] and _rank(fn) >= _rank(bc_node[nb]):
                continue
            bc[nb] = x
            bc_node[nb] = fn
    return R
