"""Stops, spatial grid index, walking (haversine with detour) and barriers."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

R_EARTH = 6_371_000.0
CELL = 0.01


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class WalkParams:
    speed_mps: float = 1.2
    detour: float = 1.3
    transfer_max_m: float = 400.0
    access_max_m: float = 1200.0
    access_max_limit_m: float = 2000.0

    def seconds(self, metres: float) -> int:
        return math.ceil(metres * self.detour / self.speed_mps)


def _ccw(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_cross(p1, p2, q1, q2) -> bool:
    """Proper or touching intersection of segments p1p2 and q1q2 (x, y tuples)."""
    d1 = _ccw(*q1, *q2, *p1)
    d2 = _ccw(*q1, *q2, *p2)
    d3 = _ccw(*p1, *p2, *q1)
    d4 = _ccw(*p1, *p2, *q2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    def on(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    return ((d1 == 0 and on(q1, q2, p1)) or (d2 == 0 and on(q1, q2, p2))
            or (d3 == 0 and on(p1, p2, q1)) or (d4 == 0 and on(p1, p2, q2)))


class Barriers:
    def __init__(self, segments=None):
        # segments: ((lon, lat), (lon, lat))
        self.segments = segments or []

    @classmethod
    def from_geojson(cls, path):
        with open(path, encoding="utf-8") as f:
            gj = json.load(f)
        segs = []
        for ft in gj.get("features", []):
            g = ft["geometry"]
            lines = [g["coordinates"]] if g["type"] == "LineString" else (
                g["coordinates"] if g["type"] == "MultiLineString" else [])
            for line in lines:
                for a, b in zip(line, line[1:]):
                    segs.append(((a[0], a[1]), (b[0], b[1])))
        return cls(segs)

    def blocks(self, lat1, lon1, lat2, lon2) -> bool:
        p1, p2 = (lon1, lat1), (lon2, lat2)
        return any(segments_cross(p1, p2, a, b) for a, b in self.segments)


class Network:
    """All stops from all feeds, with transfer footpaths."""

    def __init__(self, feeds, walk: WalkParams, barriers: Barriers, overrides=None):
        self.walk = walk
        self.barriers = barriers
        self.overrides = overrides
        self.ids: list[str] = []
        self.names: list[str] = []
        self.lat: list[float] = []
        self.lon: list[float] = []
        self.index: dict[str, int] = {}
        for feed in feeds:
            for sid, (name, la, lo) in feed.stops.items():
                self.index[sid] = len(self.ids)
                self.ids.append(sid)
                self.names.append(name)
                self.lat.append(la)
                self.lon.append(lo)
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i in range(len(self.ids)):
            self.grid.setdefault(self._cell(self.lat[i], self.lon[i]), []).append(i)
        self.footpaths: list[list[tuple[int, int]]] = [[] for _ in self.ids]
        for i in range(len(self.ids)):
            for j, m in self._within(self.lat[i], self.lon[i], walk.transfer_max_m):
                if j != i and not barriers.blocks(self.lat[i], self.lon[i], self.lat[j], self.lon[j]):
                    r = self.walk_secs(self.ids[i], self.ids[j], m)
                    if r is not None:
                        self.footpaths[i].append((j, r[0]))

    def walk_secs(self, a_id, b_id, straight_m):
        """-> (seconds, metres) of a walk, or None if an override blocks it. `straight_m` is the straight-line distance.
        The engine's estimate (straight line x detour) stands unless the real OSM path for this pair is longer; then the walk
        takes ceil(path metres / speed) and reports the path's metres (WalkBarriers.md 3)."""
        base = self.walk.seconds(straight_m)
        ov = self.overrides.get(a_id, b_id) if self.overrides is not None and a_id and b_id else None
        if ov is None:
            return base, straight_m
        kind, metres = ov
        if kind == "blocked":
            return None
        if kind == "osm" and metres is not None:
            osm = math.ceil(metres / self.walk.speed_mps)
            if osm > base:
                return osm, metres
        return base, straight_m

    def leg_metres(self, a_id, b_id, straight_m):
        """The metres a walk leg reports: the OSM path's when it set the time, else the straight line's."""
        r = self.walk_secs(a_id, b_id, straight_m)
        return r[1] if r is not None else straight_m

    @staticmethod
    def _cell(lat, lon):
        return (math.floor(lat / CELL), math.floor(lon / CELL))

    def _within(self, lat, lon, radius_m):
        """Yield (stop, metres) for stops within radius_m (no barrier check)."""
        dlat = math.ceil(radius_m / (111_000 * CELL)) + 1
        dlon = math.ceil(radius_m / (111_000 * CELL * max(0.05, math.cos(math.radians(lat))))) + 1
        ci, cj = self._cell(lat, lon)
        out = []
        for a in range(ci - dlat, ci + dlat + 1):
            for b in range(cj - dlon, cj + dlon + 1):
                for s in self.grid.get((a, b), ()):
                    m = haversine(lat, lon, self.lat[s], self.lon[s])
                    if m <= radius_m:
                        out.append((s, m))
        return out

    def near(self, lat, lon, max_m=None, point_id=None):
        """Stops a point can walk to: list of (stop, seconds, metres), barrier-checked. `point_id` lets the walking overrides apply."""
        if max_m is None:
            max_m = self.walk.access_max_m
        if not 0 <= max_m <= self.walk.access_max_limit_m:
            raise ValueError(f"max_walk_m must be between 0 and {self.walk.access_max_limit_m:g}")
        res = []
        for s, m in self._within(lat, lon, max_m):
            if not self.barriers.blocks(lat, lon, self.lat[s], self.lon[s]):
                r = self.walk_secs(point_id, self.ids[s], m)
                if r is not None:
                    res.append((s, r[0], r[1]))
        res.sort()
        return res

    def walk_between_points(self, lat1, lon1, lat2, lon2, max_m, a_id=None, b_id=None):
        m = haversine(lat1, lon1, lat2, lon2)
        if m <= max_m and not self.barriers.blocks(lat1, lon1, lat2, lon2):
            return self.walk_secs(a_id, b_id, m)
        return None

    def stop_metres(self, a: int, b: int) -> float:
        return haversine(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
