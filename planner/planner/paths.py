"""The display cache of ride and walk paths (MapPlan.md 10.5): read-only, behind a small interface.

`data/paths/paths.json.gz` is built offline by `python -m mapdata.build` from OpenStreetMap. Nothing here changes a time, a check
or a plan. A routing server could replace this class (same two calls) without touching mapview.py.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "data" / "paths" / "paths.json.gz"


def decode(s: str, precision: int = 5) -> list[tuple[float, float]]:
    """Encoded polyline -> [(lat, lon), ...] (the same algorithm as mapdata.graph)."""
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


class Paths:
    """ride(a, b, mode) and walk(a, b) -> [(lat, lon), ...] or None. Polylines are decoded only when asked for."""

    def __init__(self, data: dict):
        self.meta = data.get("meta", {})
        self._ride, self._walk = data.get("ride", {}), data.get("walk", {})
        self.precision = int(self.meta.get("precision", 5))
        self._source = self.meta.get("ride_source", {})

    def ride(self, a: str, b: str, mode: str):
        s = self._ride.get(f"{a}|{b}|{mode}")
        return decode(s, self.precision) if s else None

    def ride_source(self, a: str, b: str, mode: str = "bus") -> str:
        """'relation' (the operator's route as mapped in OSM), 'shortest' (the shortest road path), or '' when the cache has no
        source for the pair (rail, ferry, an older cache)."""
        v = self._source.get(f"{a}|{b}|{mode}", "")
        return "relation" if v.startswith("relation") else ("shortest" if v else "")

    def walk(self, a: str, b: str):
        s = self._walk.get(f"{a}|{b}")
        if s:
            return decode(s, self.precision)
        s = self._walk.get(f"{b}|{a}")                      # one direction is stored; walking is the same both ways
        return decode(s, self.precision)[::-1] if s else None


_cache: dict = {}


def load(path=None, feeds: dict | None = None):
    """-> (Paths or None, warning or None). Loaded on first use and kept. `feeds` is {feed id: sha256} of the engine in use: a
    cache built for other feeds is ignored. A missing file gives (None, warning): the map falls back to straight lines."""
    path = Path(path) if path else DEFAULT
    key = (str(path), tuple(sorted((feeds or {}).items())))
    if key in _cache:
        return _cache[key]
    if not path.exists():
        res = (None, "No map path cache was found (data/paths/paths.json.gz), so rides and walks are drawn as straight lines.")
    else:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                p = Paths(json.load(f))
        except (OSError, ValueError):
            return (None, "The map path cache could not be read, so rides and walks are drawn as straight lines.")
        built = p.meta.get("feeds", {})
        if feeds is not None and any(built.get(k) != v for k, v in feeds.items()):
            res = (None, "The map path cache was built for other timetable feeds and is ignored; rides and walks are drawn as straight lines.")
        else:
            res = (p, None)
    _cache[key] = res
    return res
