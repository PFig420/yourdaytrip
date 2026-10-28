"""python -m mapdata.build: download OSM data, route every needed pair, write the path cache (MapPlan.md 10.4).

    python -m mapdata.build [--download-only] [--no-download] [--limit N]

Run it with the model server stopped (laptop memory). Raw Overpass answers are cached under mapdata/osm/ and never fetched twice.
The cache is for display only: it never changes a time, a check or a plan.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import resource
import sys
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from .graph import BUS, FOOT, RAIL, Graph, encode, haversine, path_length

ROOT = Path(__file__).resolve().parents[1]
OSM_DIR = ROOT / "mapdata" / "osm"
OUT_DIR = ROOT / "data" / "paths"
OVERPASS = "https://overpass-api.de/api/interpreter"
MIRRORS = (OVERPASS, "https://overpass.kumi.systems/api/interpreter")      # the main endpoint first, then a mirror (MapPlan.md 11.3)
RELATION_BOX = (40.40, 40.90, -8.82, -8.40)                                # the places area box: where the feed's lines run
UA = "YourDayTrip-mapdata/0.1 (palexandre09@gmail.com; research prototype)"

BOX = (40.40, 41.20, -8.82, -8.40)               # lat_min, lat_max, lon_min, lon_max: the Porto-Aveiro corridor (MapPlan.md 10.2)
TILE = 0.1                                        # degrees
OVERLAP_LAT, OVERLAP_LON = 0.027, 0.036           # about 3 km
PAUSE_S = 6                                       # between Overpass queries (fair use)
DETOUR = 1.3                                      # the engine's walk_detour
TRANSFER_M, ACCESS_M = 400, 2000                  # engine transfer_max_m, access_max_limit_m (= planner walk_between_m)
SNAP_BUS, SNAP_RAIL, SNAP_FOOT = 150, 300, 150
SNAP_FAR = 3                                      # a snap up to 3 x its limit is kept and flagged; beyond that there is no path
RIDE_RATIO, WALK_RATIO = 2.5, 1.5
WALK_MIN_EXCESS = 100                              # metres: a 15 m crossing of the road is not an underestimate worth a flag

ROAD = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service", "living_street",
        "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "busway", "bus_guideway"}
WALK = {"primary", "secondary", "tertiary", "unclassified", "residential", "service", "living_street", "pedestrian", "footway",
        "path", "steps", "cycleway", "track", "primary_link", "secondary_link", "tertiary_link", "corridor"}
KEEP_TAGS = ("highway", "oneway", "oneway:bus", "junction", "foot", "access", "bus", "psv", "service", "name", "route", "railway")
SKIP_HW = "proposed|construction|raceway|bridleway|elevator|escalator|platform|rest_area|services|bus_stop|emergency_access_point|via_ferrata|road|abandoned"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- OSM download ---------------------------------------------------------------------------------------------------
def overpass(query: str, retries: int = 6) -> dict:
    for attempt in range(retries):
        for url in MIRRORS:
            try:
                r = httpx.post(url, data={"data": query}, headers={"User-Agent": UA}, timeout=600)
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (429, 502, 503, 504):
                    r.raise_for_status()
            except (httpx.TimeoutException, httpx.TransportError):
                pass
        time.sleep(20 * (attempt + 1))
    raise SystemExit("Overpass could not be reached or kept failing: stop and ask (MapPlan.md 10.4).")


def _slim(el: dict) -> dict:
    return {"id": el["id"], "tags": {k: v for k, v in el.get("tags", {}).items() if k in KEEP_TAGS},
            "nodes": el.get("nodes", []), "geometry": [[g["lat"], g["lon"]] for g in el.get("geometry", [])]}


def fetch_file(name: str, query: str) -> Path:
    path = OSM_DIR / name
    if path.exists():
        return path
    t = time.time()
    data = overpass(query)
    els = [_slim(e) for e in data.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    OSM_DIR.mkdir(exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"fetched": dt.date.today().isoformat(), "query": query, "elements": els}, f)
    log(f"downloaded {name}: {len(els)} ways in {time.time() - t:.0f}s, {path.stat().st_size / 1e6:.1f} MB")
    time.sleep(PAUSE_S)
    return path


def tile_bbox(r: int, c: int):
    s, w = BOX[0] + r * TILE, BOX[2] + c * TILE
    return s - OVERLAP_LAT, w - OVERLAP_LON, s + TILE + OVERLAP_LAT, w + TILE + OVERLAP_LON


def cell_of(lat, lon):
    return int((lat - BOX[0]) // TILE), int((lon - BOX[2]) // TILE)


def cells_near(lat, lon, margin_m):
    """Core cells within margin_m of a point."""
    dlat, dlon = margin_m / 111_000, margin_m / (111_000 * math.cos(math.radians(lat)))
    (r0, c0), (r1, c1) = cell_of(lat - dlat, lon - dlon), cell_of(lat + dlat, lon + dlon)
    return {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}


def hw_query(r, c):
    s, w, n, e = tile_bbox(r, c)
    return (f'[out:json][timeout:300][bbox:{s:.5f},{w:.5f},{n:.5f},{e:.5f}];\n'
            f'way["highway"]["highway"!~"^({SKIP_HW})$"];\nout geom;')


RAIL_QUERY = (f'[out:json][timeout:300][bbox:{BOX[0]},{BOX[2]},{BOX[1]},{BOX[3]}];\n'
              'way["railway"~"^(rail|light_rail|narrow_gauge)$"]["service"!~"^(yard|siding|spur)$"];\nout geom;')
FERRY_QUERY = (f'[out:json][timeout:300][bbox:{BOX[0]},{BOX[2]},{BOX[1]},{BOX[3]}];\n'
               'way["route"="ferry"];\nout geom;')


def fetch_relations() -> Path:
    """The operators' bus routes as mapped in OSM (MapPlan.md 11.3): the route relations in the places area, then the member ways
    (with geometry) of the ones that match a feed line. One cached file; never fetched twice."""
    from . import relations as rel
    path = OSM_DIR / "relations.json.gz"
    if path.exists():
        return path
    s, n, w, e = RELATION_BOX
    t = time.time()
    heads = overpass(f'[out:json][timeout:180];rel["route"~"^(bus|ferry)$"]({s},{w},{n},{e});out meta;')["elements"]
    matched = [r for rs in rel.match_relations(heads).values() for r in rs]
    ids = sorted({r["id"] for r in matched})
    time.sleep(PAUSE_S)
    wy = overpass(f'[out:json][timeout:180];rel(id:{",".join(map(str, ids))});way(r);out geom;')["elements"] if ids else []
    OSM_DIR.mkdir(exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"fetched": dt.date.today().isoformat(), "relations": [r for r in heads if r["id"] in set(ids)],
                   "all_relations": len(heads), "ways": [_slim(x) for x in wy if x.get("type") == "way"]}, f)
    log(f"downloaded relations.json.gz: {len(heads)} route relations in the area, {len(ids)} match a feed line, {len(wy)} ways, "
        f"{time.time() - t:.0f}s")
    time.sleep(PAUSE_S)
    return path


# ---- tags -> edge flags ------------------------------------------------------------------------------------------------
def way_flags(tags: dict) -> tuple[int, int]:
    """(flags along the way, flags against it) for BUS and FOOT."""
    hw = tags.get("highway", "")
    acc = tags.get("access", "")
    bus_ok = hw in ROAD and tags.get("bus") != "no" and tags.get("psv") != "no" \
        and (acc not in ("no", "private") or "yes" in (tags.get("bus"), tags.get("psv"))) \
        and tags.get("service") not in ("driveway", "parking_aisle", "drive-through")
    foot_ok = hw in WALK and tags.get("foot") != "no" and (acc not in ("no", "private") or tags.get("foot") == "yes")
    ow = tags.get("oneway:bus") or tags.get("oneway", "")
    if not tags.get("oneway:bus") and tags.get("junction") in ("roundabout", "circular") and not ow:
        ow = "yes"
    fwd_bus = bus_ok and ow != "-1"
    rev_bus = bus_ok and ow not in ("yes", "1", "true")
    f = (BUS if fwd_bus else 0) | (FOOT if foot_ok else 0)
    r = (BUS if rev_bus else 0) | (FOOT if foot_ok else 0)
    return f, r


def read_ways(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        d = json.load(f)
    return d


class TileCache:
    """Parsed ways per tile, a few at a time (memory)."""

    def __init__(self, size=4):
        self.size, self.items = size, OrderedDict()

    def ways(self, r, c):
        k = (r, c)
        if k in self.items:
            self.items.move_to_end(k)
            return self.items[k]
        path = OSM_DIR / f"hw_{r}_{c}.json.gz"
        out = []
        if path.exists():
            for el in read_ways(path)["elements"]:
                f, rv = way_flags(el["tags"])
                if (f | rv) and len(el["nodes"]) == len(el["geometry"]) >= 2:
                    out.append((el["id"], f, rv, el["nodes"], [tuple(g) for g in el["geometry"]]))
        self.items[k] = out
        while len(self.items) > self.size:
            self.items.popitem(last=False)
        return out


def road_graph(cache: TileCache, cells) -> Graph:
    g, seen = Graph(), set()
    for rc in sorted(cells):
        for wid, f, rv, nodes, coords in cache.ways(*rc):
            if wid not in seen:
                seen.add(wid)
                g.add_way(nodes, coords, f, rv)
    return g


def rail_graph() -> Graph:
    g = Graph()
    for el in read_ways(OSM_DIR / "rail.json.gz")["elements"]:
        if len(el["nodes"]) == len(el["geometry"]) >= 2:
            g.add_way(el["nodes"], [tuple(x) for x in el["geometry"]], RAIL, RAIL)
    return g


# ---- what has to be drawn ----------------------------------------------------------------------------------------------
def in_box(lat, lon):
    return BOX[0] <= lat <= BOX[1] and BOX[2] <= lon <= BOX[3]


def collect(engine, lib):
    """-> stops {id: (name, lat, lon)}, points {id: (lat, lon)} (place points and origins), ride pairs, patterns."""
    from planner.models import ORIGINS
    stops, ride_pairs, patterns = {}, {}, {}
    for f in engine.feeds:
        inbox = {s for s, (n, la, lo) in f.stops.items() if in_box(la, lo)}
        for s in inbox:
            stops[s] = f.stops[s]
        for t in f.trips.values():
            mode = f.routes[t.route].mode
            ids = [r[0] for r in t.times]
            seq = []
            for a, b in zip(ids, ids[1:]):
                if a in inbox and b in inbox and a != b:
                    ride_pairs[(a, b, mode)] = True
                    seq.append((a, b))
            if seq:
                r = f.routes[t.route]
                patterns.setdefault((mode, r.short, t.headsign, tuple(seq)), 0)
                patterns[(mode, r.short, t.headsign, tuple(seq))] += 1
    points = {}
    for pid, p in lib.places.items():
        if p.status == "rejected":
            continue
        for k, (_, la, lo) in enumerate(p.all_points()):
            points[pid if k == 0 else f"{pid}#{k}"] = (la, lo)
    for oid, o in ORIGINS.items():
        points[oid] = (o["lat"], o["lon"])
    return stops, points, list(ride_pairs), patterns


def walk_pairs(stops, points):
    """Pairs the engine can walk (haversine x 1.3 within its limits), as sorted-key pairs (a, b, engine_metres)."""
    out = {}

    def add(a, pa, b, pb, limit):
        d = haversine(pa[0], pa[1], pb[0], pb[1])
        if d * DETOUR <= limit:
            k = tuple(sorted((a, b)))
            out[k] = d * DETOUR

    sl = [(s, (v[1], v[2])) for s, v in stops.items()]
    pl = list(points.items())
    grid = {}
    for s, p in sl:
        grid.setdefault((int(p[0] / 0.02), int(p[1] / 0.02)), []).append((s, p))
    for a, pa in pl:                                                       # place points and origins: to stops and to each other
        ci, cj = int(pa[0] / 0.02), int(pa[1] / 0.02)
        for di in range(-1, 2):
            for dj in range(-1, 2):
                for s, p in grid.get((ci + di, cj + dj), ()):
                    add(a, pa, s, p, ACCESS_M)
        for b, pb in pl:
            if a < b:
                add(a, pa, b, pb, ACCESS_M)
    for i, (s, p) in enumerate(sl):                                        # stop to stop (transfers)
        ci, cj = int(p[0] / 0.02), int(p[1] / 0.02)
        for di in range(-1, 2):
            for dj in range(-1, 2):
                for s2, p2 in grid.get((ci + di, cj + dj), ()):
                    if s < s2:
                        add(s, p, s2, p2, TRANSFER_M)
    return out


# ---- routing -----------------------------------------------------------------------------------------------------------
def route_pair(g: Graph, mode: int, pa, pb, limit):
    """-> (coords [(lat, lon)], metres, flags list) or (None, 0, flags). The endpoints are the real positions."""
    flags = []
    sa, sb = g.snap(pa[0], pa[1], mode, limit * SNAP_FAR), g.snap(pb[0], pb[1], mode, limit * SNAP_FAR)
    if sa is None or sb is None:
        return None, 0, ["no-snap"]
    for s, name in ((sa, "from"), (sb, "to")):
        if s.dist > limit:
            flags.append(f"snap-far {name} {s.dist:.0f} m")
    res = g.route(sa, sb, mode)
    if res is None:
        return None, 0, flags + ["no-route"]
    path, m = res
    return [tuple(pa)] + path + [tuple(pb)], m + sa.dist + sb.dist, flags


def group_cells(lat1, lon1, lat2, lon2):
    d = haversine(lat1, lon1, lat2, lon2)
    margin = max(1500, 0.5 * d)
    cells = set()
    for k in range(0, 5):                           # along the pair and around it
        t = k / 4
        cells |= cells_near(lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t, margin)
    return frozenset(cells)


def build(limit=None, download=True, download_only=False, plan_only=False):
    sys.path[:0] = [str(ROOT / "planner"), str(ROOT / "places"), str(ROOT / "engine")]
    import places
    import reach
    t0 = time.time()
    engine = reach.load(str(ROOT / "engine/config"))
    lib = places.load(str(ROOT / "places/data"), engine=engine, config_dir=str(ROOT / "places/config"))
    stops, points, ride_pairs, patterns = collect(engine, lib)
    wpairs = walk_pairs(stops, points)
    coordsof = {**{s: (v[1], v[2]) for s, v in stops.items()}, **points}
    log(f"{len(stops)} stops, {len(points)} place/origin points, {len(ride_pairs)} ride pairs, {len(wpairs)} walk pairs")
    # which tiles are needed
    ride_groups = {a_b: group_cells(*coordsof[a_b[0]], *coordsof[a_b[1]]) for a_b in ride_pairs if a_b[2] == "bus"}
    walk_groups = {k: group_cells(*coordsof[k[0]], *coordsof[k[1]]) for k in wpairs}
    needed = set().union(*ride_groups.values(), *walk_groups.values())
    needed = {rc for rc in needed if 0 <= rc[0] < round((BOX[1] - BOX[0]) / TILE) and 0 <= rc[1] < math.ceil((BOX[3] - BOX[2]) / TILE)}
    log(f"{len(needed)} OSM tiles needed: {sorted(needed)}")
    if plan_only:
        return
    if download:
        fetch_relations()
        fetch_file("rail.json.gz", RAIL_QUERY)
        fetch_file("ferry.json.gz", FERRY_QUERY)
        for rc in sorted(needed):
            fetch_file(f"hw_{rc[0]}_{rc[1]}.json.gz", hw_query(*rc))
    if download_only:
        return
    ride, walk, flagged = {}, {}, {}
    # rail
    rg = rail_graph()
    log(f"rail graph: {len(rg.coords)} nodes")
    for a, b, mode in ride_pairs:
        if mode != "rail":
            continue
        key = f"{a}|{b}|rail"
        pa, pb = coordsof[a], coordsof[b]
        path, m, fl = route_pair(rg, RAIL, pa, pb, SNAP_RAIL)
        if path and haversine(*pa, *pb) > 0 and m > RIDE_RATIO * haversine(*pa, *pb):
            fl.append(f"ride-detour {m / haversine(*pa, *pb):.1f}x")
        if path:
            ride[key] = encode(path)
        if fl:
            flagged[key] = fl
    log(f"rail done: {sum(k.endswith('|rail') for k in ride)} of {sum(1 for p in ride_pairs if p[2] == 'rail')}")
    # ferry
    ferry = read_ways(OSM_DIR / "ferry.json.gz")["elements"]
    for a, b, mode in ride_pairs:
        if mode != "ferry":
            continue
        key = f"{a}|{b}|ferry"
        pa, pb = coordsof[a], coordsof[b]
        best = None
        for el in ferry:
            geo = [tuple(g) for g in el["geometry"]]
            da, db = min(haversine(*pa, *g) for g in geo), min(haversine(*pb, *g) for g in geo)
            if da <= 1500 and db <= 1500 and (best is None or da + db < best[0]):
                ia = min(range(len(geo)), key=lambda i: haversine(*pa, *geo[i]))
                ib = min(range(len(geo)), key=lambda i: haversine(*pb, *geo[i]))
                seg = geo[ia:ib + 1] if ia <= ib else geo[ib:ia + 1][::-1]
                best = (da + db, [tuple(pa)] + seg + [tuple(pb)])
        if best:
            ride[key] = encode(best[1])
        else:
            flagged[key] = ["no-ferry-way"]
    # roads and footpaths, grouped by the tiles they need
    cache = TileCache()
    jobs = {}
    for a, b, mode in ride_pairs:
        if mode == "bus":
            jobs.setdefault(ride_groups[(a, b, mode)], []).append(("ride", a, b))
    for k in wpairs:
        jobs.setdefault(walk_groups[k], []).append(("walk", k[0], k[1]))
    done = 0
    total = sum(len(v) for v in jobs.values())
    for cells, items in sorted(jobs.items(), key=lambda kv: sorted(kv[0])):
        cells = frozenset(rc for rc in cells if (OSM_DIR / f"hw_{rc[0]}_{rc[1]}.json.gz").exists())
        g = road_graph(cache, cells)
        for kind, a, b in items:
            if limit and done >= limit:
                break
            done += 1
            pa, pb = coordsof[a], coordsof[b]
            if kind == "ride":
                key = f"{a}|{b}|bus"
                path, m, fl = route_pair(g, BUS, pa, pb, SNAP_BUS)
                if path and haversine(*pa, *pb) > 0 and m > RIDE_RATIO * haversine(*pa, *pb):
                    fl.append(f"ride-detour {m / haversine(*pa, *pb):.1f}x")
                if path:
                    ride[key] = encode(path)
            else:
                key = f"{a}|{b}"
                path, m, fl = route_pair(g, FOOT, pa, pb, SNAP_FOOT)
                eng = wpairs[(a, b)]
                if path and m > WALK_RATIO * eng and m - eng >= WALK_MIN_EXCESS:
                    fl.append(f"engine-underestimate {eng:.0f} m vs {m:.0f} m")
                if path:
                    walk[key] = encode(path)
            if fl:
                flagged[key] = fl
        if done % 500 < len(items):
            log(f"routed {done} of {total}; peak {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    relmeta = {}
    rel_path = OSM_DIR / "relations.json.gz"
    if rel_path.exists():                              # Phase 3: buses follow their mapped route where it is trustworthy
        from . import relations as rel
        data = read_ways_file(rel_path)
        pairs_by_line = line_pairs(engine, set(stops), rel.LINES)
        res = rel.overlay(ride, pairs_by_line, coordsof, data["relations"], {w["id"]: w for w in data["ways"]},
                          {r: f.routes[r].short for f in engine.feeds for r in f.routes if r in rel.LINES})
        ride = res["ride"]
        for k, v in res["flags"].items():
            flagged.setdefault(k, []).extend(v)
        relmeta = {"ride_source": res["source"], "relations": res["relations"], "relation_stats": res["stats"],
                   "relations_fetched": data["fetched"]}
        used = sum(1 for v in res["source"].values() if v.startswith("relation"))
        log(f"relations: {used} of {len(res['source'])} bus pairs follow an OSM route relation; "
            f"{sum(1 for v in res['flags'].values() for x in v if x.startswith('relation-differs'))} differ from the shortest path")
    meta = {
        "built": dt.date.today().isoformat(), "box": list(BOX), "precision": 5,
        "osm_downloaded": sorted({read_ways(p)["fetched"] for p in OSM_DIR.glob("*.json.gz")}),
        "feeds": {f.id: f.sha256 for f in engine.feeds}, "places_hash": places_hash(lib),
        "counts": {"ride": len(ride), "walk": len(walk), "ride_pairs": len(ride_pairs), "walk_pairs": len(wpairs), "flagged": len(flagged)},
        **relmeta, "flagged": flagged, "engine_walk_m": {f"{k[0]}|{k[1]}": round(v) for k, v in wpairs.items() if f"{k[0]}|{k[1]}" in flagged},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_DIR / "paths.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"meta": meta, "ride": ride, "walk": walk}, f, separators=(",", ":"))
    write_review(patterns, ride, flagged, coordsof, walk)
    size = (OUT_DIR / "paths.json.gz").stat().st_size / 1e6
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    meta["size_mb"], meta["build_s"], meta["peak_mb"] = round(size, 2), round(time.time() - t0), round(peak)
    log(f"done: {len(ride)} rides, {len(walk)} walks, {len(flagged)} flagged; {size:.1f} MB; {time.time() - t0:.0f}s; peak {peak:.0f} MB")
    from . import overrides                       # the engine's walking overrides and the exceptions sheet (WalkBarriers.md)
    overrides.run(names=overrides.names_from(engine, lib))
    (OUT_DIR / "build-summary.json").write_text(json.dumps({k: v for k, v in meta.items() if k not in ("flagged", "engine_walk_m", "ride_source")}, indent=1))


def read_ways_file(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def line_pairs(engine, inbox: set, lines) -> dict:
    """feed route id -> {(stop_a, stop_b)}: the consecutive stop pairs of every trip of the line that lie in the corridor."""
    out = {}
    for f in engine.feeds:
        for t in f.trips.values():
            if t.route in lines:
                ids = [r[0] for r in t.times]
                for a, b in zip(ids, ids[1:]):
                    if a in inbox and b in inbox and a != b:
                        out.setdefault(t.route, set()).add((a, b))
    return out


def places_hash(lib) -> str:
    h = hashlib.sha256()
    for pid in sorted(lib.places):
        p = lib.places[pid]
        h.update(f"{pid}:{p.lat:.6f},{p.lon:.6f}:{[(q['name'], q['lat'], q['lon']) for q in p.points]}".encode())
    return h.hexdigest()[:16]


def write_review(patterns, ride, flagged, coordsof, walk):
    """Every bus and rail pattern drawn whole, and every flagged path."""
    from .graph import decode
    feats = []
    for (mode, short, head, seq), n in sorted(patterns.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        coords, bad = [], []
        for a, b in seq:
            key = f"{a}|{b}|{mode}"
            if key in ride:
                seg = [(lo, la) for la, lo in decode(ride[key])]
            else:
                seg = [(coordsof[a][1], coordsof[a][0]), (coordsof[b][1], coordsof[b][0])]
                bad.append(key)
            coords += seg if not coords else seg[1:]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"kind": "pattern", "mode": mode, "route": short, "headsign": head, "stops": len(seq) + 1,
                                     "trips": n, "straight_segments": len(bad), "stroke": "#8a3fb5" if mode == "rail" else "#2c5aa0",
                                     "stroke-width": 2}})
    for key, reasons in flagged.items():
        ids = key.split("|")
        if len(ids) == 3:
            path = ride.get(key)
        else:
            path = walk.get(key)
        pts = [(lo, la) for la, lo in decode(path)] if path else [(coordsof[ids[0]][1], coordsof[ids[0]][0]), (coordsof[ids[1]][1], coordsof[ids[1]][0])]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": pts},
                      "properties": {"kind": "flagged", "key": key, "reasons": "; ".join(reasons), "stroke": "#d6322a", "stroke-width": 3}})
    (OUT_DIR / "review.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mapdata.build")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--no-download", action="store_true", help="route from the cached OSM files only")
    ap.add_argument("--plan-only", action="store_true", help="count what is needed; no network, no routing")
    ap.add_argument("--limit", type=int, help="route at most N road and footpath pairs (trial runs)")
    a = ap.parse_args(argv)
    build(limit=a.limit, download=not a.no_download, download_only=a.download_only, plan_only=a.plan_only)


if __name__ == "__main__":
    main()
