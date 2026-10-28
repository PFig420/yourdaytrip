"""Candidate lookup, per-trip placement and merging into GTFS stops (spec 5.4)."""
from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .load import BuildError
from .names import ekey, fold, key, split_side, strip_paren

LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 40.05, 41.00, -8.95, -8.20
FUZZY = 0.88
MAX_KMH = 90.0


def in_bbox(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


@dataclass(frozen=True)
class Cand:
    lat: float
    lon: float
    source: str   # manual | busway-exact | busway-fuzzy | osm-exact | osm-fuzzy | osm-ferry
    label: str    # name in the source
    sim: float = 1.0


def dist_km(a, b) -> float:
    """Great-circle distance between two (lat, lon) pairs, km."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ coordinate sources

def load_busway_stops(zip_path: Path) -> list[tuple[str, float, float]]:
    out = []
    with zipfile.ZipFile(zip_path) as z:
        with z.open("stops.txt") as f:
            import io
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                try:
                    lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
                except (KeyError, ValueError):
                    continue
                if r.get("location_type") not in ("", "0", None):
                    continue
                if in_bbox(lat, lon) and r["stop_name"].strip():
                    out.append((r["stop_name"].strip(), lat, lon))
    return out


def load_osm(json_path: Path):
    """Return (bus_stops, ferry_terminals, places) as lists of tuples."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    bus, ferry, places = [], [], []
    for e in data["elements"]:
        t = e.get("tags", {})
        if "lat" not in e or not t.get("name") and t.get("amenity") != "ferry_terminal":
            continue
        lat, lon = e["lat"], e["lon"]
        if not in_bbox(lat, lon):
            continue
        if t.get("amenity") == "ferry_terminal":
            ferry.append((t.get("name", ""), lat, lon, t.get("operator", "")))
        elif "place" in t:
            places.append((t["name"], lat, lon))
        elif t.get("highway") == "bus_stop" or t.get("public_transport") == "platform":
            bus.append((t["name"], lat, lon))
    return bus, ferry, places


def load_manual(path: Path) -> dict[str, list[Cand]]:
    out: dict[str, list[Cand]] = defaultdict(list)
    if not path.exists():
        return out
    from .load import read_csv_config
    for r in read_csv_config(path):
        n = (r.get("canonical") or "").strip()
        if not n:
            continue
        lat, lon = float(r["lat"]), float(r["lon"])
        if not in_bbox(lat, lon):
            raise BuildError(f"stop_coords.csv: {n!r} at {lat},{lon} is outside the bounding box")
        out[n].append(Cand(lat, lon, "manual", r.get("source", ""), 1.0))
    return out


class Source:
    """A set of named points indexed by normalised key, with exact and fuzzy lookup."""

    def __init__(self, items, tag: str):
        self.tag = tag
        self.by_key: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for it in items:
            for k in {key(it[0]), ekey(it[0])}:
                self.by_key[k].append(it)
        self.keys = sorted(self.by_key)
        self._len = {k: len(k) for k in self.keys}
        self._cache: dict[str, list[Cand]] = {}

    def exact(self, qkeys) -> list[Cand]:
        out = []
        for qk in qkeys:
            for it in self.by_key.get(qk, ()):
                out.append(Cand(it[1], it[2], f"{self.tag}-exact", it[0], 1.0))
        return _dedupe(out)

    def fuzzy(self, qkeys) -> list[Cand]:
        out = []
        for qk in qkeys:
            if len(qk) < 5:
                continue
            for k in self.keys:
                if abs(self._len[k] - len(qk)) > max(3, len(qk) * 0.15):
                    continue
                sm = SequenceMatcher(None, qk, k, autojunk=False)
                if sm.real_quick_ratio() < FUZZY or sm.quick_ratio() < FUZZY:
                    continue
                r = sm.ratio()
                if r >= FUZZY and k != qk and re.sub(r"\D", "", k) == re.sub(r"\D", "", qk):
                    for it in self.by_key[k]:
                        out.append(Cand(it[1], it[2], f"{self.tag}-fuzzy", it[0], round(r, 3)))
        return _dedupe(out)


def _dedupe(c: list[Cand]) -> list[Cand]:
    seen, out = set(), []
    for x in c:
        k = (round(x.lat, 6), round(x.lon, 6))
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def ferry_key(name: str) -> str:
    s = fold(name)
    s = re.sub(r"\(ferry\)", "", s)
    s = re.sub(r"\bs\.\s*", "sao ", s)
    return key(s)


class Candidates:
    """Tiered candidates per canonical name (spec 5.4)."""

    def __init__(self, manual, busway: Source, osm: Source, ferry_terminals):
        self.manual, self.busway, self.osm = manual, busway, osm
        self.ferry = ferry_terminals
        self._cache: dict[tuple, list[list[Cand]]] = {}

    def tiers(self, name: str, mode: str = "bus") -> list[list[Cand]]:
        k = (name, mode == "ferry")
        if k in self._cache:
            return self._cache[k]
        if name in self.manual:
            res = [list(self.manual[name])]
        elif mode == "ferry":
            res = [self._ferry(name)]
        else:
            base, side = split_side(name)
            full = {key(name), ekey(name)}
            if side:
                full |= {key(base), ekey(base)}
            q = strip_paren(name)
            qual = set()
            if q != name:
                qb = split_side(q)[0]
                qual = {key(q), ekey(q), key(qb), ekey(qb)} - full
            qual = sorted(qual)
            full = sorted(full)
            res = [self.busway.exact(full), self.busway.exact(qual) if qual else [], self.busway.fuzzy(full),
                   self.osm.exact(full), self.osm.exact(qual) if qual else [], self.osm.fuzzy(full)]
        res = [t for t in res if t]
        self._cache[k] = res
        return res

    def best_names(self, name: str, k: int = 3) -> list[tuple[float, str, str, float, float]]:
        """Closest source names by similarity, for the human review files: (ratio, source, name, lat, lon)."""
        q = key(name)
        scored = []
        for tag, src in (("busway", self.busway), ("osm", self.osm)):
            for kk in src.keys:
                sm = SequenceMatcher(None, q, kk, autojunk=False)
                if sm.real_quick_ratio() < 0.6 or sm.quick_ratio() < 0.6:
                    continue
                scored.append((sm.ratio(), tag, kk))
        scored.sort(reverse=True)
        out, seen = [], set()
        for r, tag, kk in scored:
            src = self.busway if tag == "busway" else self.osm
            for it in src.by_key[kk][:2]:
                if (tag, it[0], it[1], it[2]) in seen:
                    continue
                seen.add((tag, it[0], it[1], it[2]))
                out.append((round(r, 2), tag, it[0], it[1], it[2]))
            if len(out) >= k:
                break
        return out[:k]

    def _ferry(self, name: str) -> list[Cand]:
        q = ferry_key(name)
        hits = [t for t in self.ferry if ferry_key(t[0]) == q]
        pref = [t for t in hits if "aveirobus" in fold(t[3]).replace(" ", "")]
        return [Cand(t[1], t[2], "osm-ferry", t[0], 1.0) for t in (pref or hits)]


# ------------------------------------------------------------------ per-trip placement

def leg_cost(a, b, minutes: int) -> float:
    d = dist_km(a, b)
    v = d / (max(minutes, 1) / 60.0)
    return d + (1000.0 + (v - MAX_KMH) if v > MAX_KMH else 0.0)


def viterbi(cands: list[list[Cand]], minutes: list[int]):
    """Choose one candidate per stop minimising total leg cost.

    `cands[i]` is a non-empty list; `minutes[i]` is the printed time from stop i-1 to i
    (minutes[0] ignored). Returns (chosen list, total cost).
    """
    n = len(cands)
    cost = [0.0] * len(cands[0])
    back: list[list[int]] = []
    for i in range(1, n):
        new, bk = [], []
        for c in cands[i]:
            best, bj = None, 0
            for j, p in enumerate(cands[i - 1]):
                v = cost[j] + leg_cost((p.lat, p.lon), (c.lat, c.lon), minutes[i])
                if best is None or v < best:
                    best, bj = v, j
            new.append(best)
            bk.append(bj)
        cost = new
        back.append(bk)
    j = min(range(len(cost)), key=cost.__getitem__)
    total = cost[j]
    out = [None] * n
    for i in range(n - 1, -1, -1):
        out[i] = cands[i][j]
        if i > 0:
            j = back[i - 1][j]
    return out, total


def implausible_legs(coords, times) -> list[int]:
    """Indices i (leg i-1 -> i) that exceed MAX_KMH. coords may hold None (skipped)."""
    bad, prev = [], None
    for i, c in enumerate(coords):
        if c is None:
            continue
        if prev is not None:
            pi, pc = prev
            mins = (times[i] - times[pi]) / 60.0
            d = dist_km(pc, c)
            if d / (max(mins, 1) / 60.0) > MAX_KMH:
                bad.append(i)
        prev = (i, c)
    return bad


RANK = {"manual": 0, "osm-ferry": 0, "busway-exact": 1, "osm-exact": 2, "busway-fuzzy": 3, "osm-fuzzy": 4}


def _leg_violations(chosen, times, names=None, skip=frozenset()):
    """Legs between consecutive placed stops that imply more than MAX_KMH: list of (i, j)."""
    out, prev = [], None
    for i, c in enumerate(chosen):
        if c is None:
            continue
        if prev is not None:
            pi = prev
            if names is not None and (names[pi], names[i]) in skip:
                prev = i
                continue
            mins = (times[i] - times[pi]) / 60.0
            if dist_km((chosen[pi].lat, chosen[pi].lon), (c.lat, c.lon)) / (max(mins, 1) / 60.0) > MAX_KMH:
                out.append((pi, i))
        prev = i
    return out


def place_trip(stop_names, times, tiers_of, skip=frozenset()):
    """Place one trip. `tiers_of(name)` -> list of candidate tiers.

    Returns (chosen, pruned): chosen has a Cand or None (unplaced) per stop; `pruned` is the set of
    indices that had candidates but were removed because every candidate implied an impossible
    speed against the rest of the trip (the candidates were in another town).
    """
    tiers = [tiers_of(n) for n in stop_names]
    idx = [i for i, t in enumerate(tiers) if t]
    chosen: list = [None] * len(stop_names)
    if not idx:
        return chosen, set()

    def run(level_all: bool):
        cl = []
        for i in idx:
            cl.append(tiers[i][0] if not level_all else [c for t in tiers[i] for c in t])
        mins = [0] + [(times[idx[k]] - times[idx[k - 1]]) // 60 for k in range(1, len(idx))]
        res, _ = viterbi(cl, mins)
        out: list = [None] * len(stop_names)
        for k, i in enumerate(idx):
            out[i] = res[k]
        return out

    chosen = run(False)
    if _leg_violations(chosen, times, stop_names, skip) and any(len(tiers[i]) > 1 for i in idx):
        alt = run(True)
        if len(_leg_violations(alt, times, stop_names, skip)) < len(_leg_violations(chosen, times, stop_names, skip)):
            chosen = alt
    pruned: set = set()
    while True:
        viol = _leg_violations(chosen, times, stop_names, skip)
        if not viol:
            break
        count = defaultdict(int)
        for i, j in viol:
            count[i] += 1
            count[j] += 1
        victim = max(count, key=lambda i: (count[i], RANK.get(chosen[i].source, 5), i))
        pruned.add(victim)
        chosen[victim] = None
    return chosen, pruned


# ------------------------------------------------------------------ merging into stops

def slug(s: str) -> str:
    s = fold(s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "stop"


def cluster_points(points: list[tuple[float, float]]):
    """Single-linkage clusters of points within 50 m. Returns list of lists of indices."""
    parent = list(range(len(points)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if dist_km(points[i], points[j]) * 1000 < 50:
                parent[find(i)] = find(j)
    g = defaultdict(list)
    for i in range(len(points)):
        g[find(i)].append(i)
    return sorted(g.values(), key=lambda m: min(m))


def nearest_place(lat, lon, places) -> str:
    best, bn = 1e9, ""
    for n, la, lo in places:
        d = dist_km((lat, lon), (la, lo))
        if d < best:
            best, bn = d, n
    return bn


def merge_stops(placements: dict, places, snap_conflicts: bool = False):
    """placements: canonical name -> {(lat, lon): number of stop_times}. Returns (stops, lookup, conflicts).

    stops: list of dict(stop_id, name, lat, lon); lookup[(name, lat, lon)] -> stop_id.
    conflicts: list of (name, distance_m, p, q) for clusters 50-300 m apart. With `snap_conflicts`
    (interim builds) the smaller cluster is merged into the larger one instead of being left apart.
    """
    stops, lookup, conflicts = [], {}, []
    used_ids: set[str] = set()
    for name in sorted(placements):
        pts = sorted(placements[name])
        groups = cluster_points(pts)
        weight = lambda g: sum(placements[name][pts[i]] for i in g)
        # 50-300 m apart: conflict (nearest members)
        changed = True
        while changed:
            changed = False
            for a in range(len(groups)):
                for b in range(a + 1, len(groups)):
                    dmin = min(dist_km(pts[i], pts[j]) for i in groups[a] for j in groups[b]) * 1000
                    if dmin <= 300:
                        conflicts.append((name, round(dmin), pts[groups[a][0]], pts[groups[b][0]]))
                        if snap_conflicts:
                            big, small = (a, b) if weight(groups[a]) >= weight(groups[b]) else (b, a)
                            groups[big] = groups[big] + groups[small]
                            del groups[small]
                            changed = True
                            break
                if changed:
                    break
        base = slug(name)
        centres = []
        for g in groups:
            tot = weight(g)
            lat = sum(pts[i][0] * placements[name][pts[i]] for i in g) / tot
            lon = sum(pts[i][1] * placements[name][pts[i]] for i in g) / tot
            centres.append((lat, lon, g))
        for lat, lon, g in centres:
            sid = base
            if len(centres) > 1:
                town = slug(nearest_place(lat, lon, places)) if places else ""
                sid = f"{base}-{town}" if town else base
            n = 2
            cand = sid
            while cand in used_ids:
                cand = f"{sid}-{n}"
                n += 1
            used_ids.add(cand)
            stops.append({"stop_id": cand, "name": name, "lat": round(lat, 6), "lon": round(lon, 6)})
            for i in g:
                lookup[(name, pts[i][0], pts[i][1])] = cand
    return stops, lookup, conflicts
