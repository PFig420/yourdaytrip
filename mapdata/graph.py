"""A small routing graph from OSM ways, A* search, snapping to the nearest allowed segment, and polyline encoding.

Display only: lengths are haversine metres, there are no turn costs, and nothing here feeds back into the engine.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

R_EARTH = 6_371_000.0
BUS, FOOT, RAIL = 1, 2, 4            # edge flags: which kind of traveller may use the edge in that direction
CELL = 0.002                         # snapping grid, degrees (about 220 m)
MAX_EXPANSIONS = 600_000
MIN_COMPONENT_NODES = 200            # snapping prefers networks at least this big over isolated pieces (platforms, courtyards)


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


# ---- polyline encoding (Google's algorithm, precision 5) ---------------------------------------------------------
def encode(coords, precision: int = 5) -> str:
    """coords: [(lat, lon), ...] -> encoded polyline."""
    f = 10 ** precision
    out, la0, lo0 = [], 0, 0
    for la, lo in coords:
        la1, lo1 = round(la * f), round(lo * f)
        for v in (la1 - la0, lo1 - lo0):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        la0, lo0 = la1, lo1
    return "".join(out)


def decode(s: str, precision: int = 5) -> list[tuple[float, float]]:
    f = 10 ** precision
    out, i, la, lo = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        la += vals[0]
        lo += vals[1]
        out.append((la / f, lo / f))
    return out


def path_length(coords) -> float:
    return sum(haversine(a[0], a[1], b[0], b[1]) for a, b in zip(coords, coords[1:]))


# ---- the graph --------------------------------------------------------------------------------------------------
@dataclass
class Snap:
    u: int                  # the segment's end nodes
    v: int
    point: tuple            # (lat, lon) of the projection on the segment
    dist: float             # metres from the query point to the projection
    d_u: float              # metres along the segment from u / v to the projection
    d_v: float
    t: float                # position along u -> v, 0..1


class Graph:
    def __init__(self):
        self.coords: dict[int, tuple[float, float]] = {}
        self.adj: dict[int, list] = {}              # node -> [(neighbour, metres, flags)]
        self._grid = None

    def add_way(self, nodes, coords, fwd: int, rev: int):
        """Join consecutive nodes. `fwd` / `rev` are the flags allowed along / against the way's direction."""
        for n, c in zip(nodes, coords):
            self.coords[n] = c
        for a, b in zip(nodes, nodes[1:]):
            if a == b:
                continue
            ca, cb = self.coords[a], self.coords[b]
            d = haversine(ca[0], ca[1], cb[0], cb[1])
            if fwd:
                self.adj.setdefault(a, []).append((b, d, fwd))
            if rev:
                self.adj.setdefault(b, []).append((a, d, rev))
        self._grid = None
        self._comp = {}

    def _components(self, mode: int) -> dict:
        """node -> size of its connected piece for `mode` (edges taken in both directions), computed once per mode."""
        if mode in self._comp:
            return self._comp[mode]
        und = {}
        for u, edges in self.adj.items():
            for v, _, f in edges:
                if f & mode:
                    und.setdefault(u, []).append(v)
                    und.setdefault(v, []).append(u)
        size = {}
        for s in und:
            if s in size:
                continue
            piece, todo = [s], [s]
            seen = {s}
            while todo:
                for m in und[todo.pop()]:
                    if m not in seen:
                        seen.add(m)
                        piece.append(m)
                        todo.append(m)
            for n in piece:
                size[n] = len(piece)
        self._comp[mode] = size
        return size

    def _edge_flags(self, u, v) -> int:
        return max((f for n, _, f in self.adj.get(u, ()) if n == v), default=0)

    # ---- snapping ----
    def _build_grid(self):
        grid = {}
        for u, edges in self.adj.items():
            cu = self.coords[u]
            for v, _, _ in edges:
                if u < v or self._edge_flags(v, u) == 0:        # each segment once (a one-way edge has no twin)
                    cv = self.coords[v]
                    steps = max(1, int(max(abs(cu[0] - cv[0]), abs(cu[1] - cv[1])) / CELL))
                    for k in range(steps + 1):
                        la = cu[0] + (cv[0] - cu[0]) * k / steps
                        lo = cu[1] + (cv[1] - cu[1]) * k / steps
                        grid.setdefault((int(la / CELL), int(lo / CELL)), set()).add((u, v))
        self._grid = {k: list(s) for k, s in grid.items()}

    def snap(self, lat, lon, mode: int, limit_m: float):
        """Nearest segment allowed for `mode` within limit_m, as a Snap, or None.

        A segment of a network with at least MIN_COMPONENT_NODES nodes wins over a nearer isolated piece; the nearest
        segment of any size is used only when no big network is within the limit (a small island stays routable)."""
        big = self._snap(lat, lon, mode, limit_m, MIN_COMPONENT_NODES)
        return big if big is not None else self._snap(lat, lon, mode, limit_m, 0)

    def _snap(self, lat, lon, mode: int, limit_m: float, min_nodes: int):
        if self._grid is None:
            self._build_grid()
        size = self._components(mode) if min_nodes else None
        coslat = math.cos(math.radians(lat))
        best, bd = None, limit_m
        reach = int(limit_m / 111_000 / CELL) + 1
        ci, cj = int(lat / CELL), int(lon / CELL)
        seen = set()
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                for seg in self._grid.get((ci + di, cj + dj), ()):
                    if seg in seen:
                        continue
                    seen.add(seg)
                    u, v = seg
                    if not ((self._edge_flags(u, v) | self._edge_flags(v, u)) & mode):
                        continue
                    if size is not None and size.get(u, 0) < min_nodes:
                        continue
                    (la1, lo1), (la2, lo2) = self.coords[u], self.coords[v]
                    x1, y1 = (lo1 - lon) * coslat * 111_320, (la1 - lat) * 110_540
                    x2, y2 = (lo2 - lon) * coslat * 111_320, (la2 - lat) * 110_540
                    dx, dy = x2 - x1, y2 - y1
                    L2 = dx * dx + dy * dy
                    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L2))
                    px, py = x1 + t * dx, y1 + t * dy
                    d = math.hypot(px, py)
                    if d < bd or (best is None and d <= bd):
                        seglen = math.sqrt(L2)
                        best, bd = Snap(u, v, (la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t), d, t * seglen, (1 - t) * seglen, t), d
        return best

    # ---- A* ----
    def route(self, a: Snap, b: Snap, mode: int):
        """Shortest allowed route from snap a to snap b: ([(lat, lon), ...], metres) or None. `mode` is the flag to follow."""
        f_ab, f_ba = self._edge_flags(a.u, a.v) & mode, self._edge_flags(a.v, a.u) & mode
        starts = {}
        if f_ab:
            starts[a.v] = a.d_v          # leaving along u -> v we reach v after d_v
        if f_ba:
            starts[a.u] = a.d_u
        g_ab, g_ba = self._edge_flags(b.u, b.v) & mode, self._edge_flags(b.v, b.u) & mode
        goals = {}
        if g_ab:
            goals[b.u] = b.d_u           # arriving at u we travel on along u -> v to the point
        if g_ba:
            goals[b.v] = b.d_v
        best_direct = None
        if {a.u, a.v} == {b.u, b.v}:     # the same segment: go straight along it if its direction allows
            tb = b.t if b.u == a.u else 1 - b.t
            if (f_ab if tb >= a.t else f_ba):
                best_direct = abs(tb - a.t) * (a.d_u + a.d_v)
        tgt = b.point
        h = lambda n: haversine(self.coords[n][0], self.coords[n][1], tgt[0], tgt[1])
        g = dict(starts)
        parent = {n: None for n in starts}
        heap = [(c + h(n), c, n) for n, c in starts.items()]
        heapq.heapify(heap)
        best, best_node, done, expanded = math.inf, None, set(), 0
        while heap:
            f, c, n = heapq.heappop(heap)
            if f >= best:
                break
            if n in done:
                continue
            done.add(n)
            if n in goals and c + goals[n] < best:
                best, best_node = c + goals[n], n
            expanded += 1
            if expanded > MAX_EXPANSIONS:
                break
            for m, d, fl in self.adj.get(n, ()):
                if not fl & mode:
                    continue
                nc = c + d
                if nc < g.get(m, math.inf):
                    g[m] = nc
                    parent[m] = n
                    heapq.heappush(heap, (nc + h(m), nc, m))
        if best_direct is not None and best_direct <= best:
            return [a.point, b.point], best_direct
        if best_node is None:
            return None
        nodes, n = [], best_node
        while n is not None:
            nodes.append(n)
            n = parent[n]
        nodes.reverse()
        return [a.point] + [self.coords[n] for n in nodes] + [b.point], best
