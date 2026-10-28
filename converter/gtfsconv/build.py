"""The pipeline: load -> names -> changes -> times -> calendars -> stops -> GTFS (spec 5)."""
from __future__ import annotations

import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import calendars, names, routes as routes_mod, stops as stops_mod, times as times_mod
from .fetch import cache_ready, fetch
from .load import BuildError, load_trips, read_csv_config, sha256
from .stops import dist_km

EXPECTED_SHA = "cb0e4432daf109914c401868eb3e49236e92e4b36ec282f83f0033c21a0e2130"


@dataclass
class Result:
    trips: list = field(default_factory=list)          # final trips (with .stops, .service_id, .gtfs_stops)
    tables: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    unplaced: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    stops: list = field(default_factory=list)
    errors: list = field(default_factory=list)          # strict-mode failures collected for review files
    calendar_trips: list = field(default_factory=list)  # trips with service dates, before placement drops
    name_clusters: list = field(default_factory=list)
    stop_sources: dict = field(default_factory=dict)


def hms(t: int) -> str:
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _parse_date(s: str):
    return dt.date.fromisoformat(s) if s else None


def apply_service_changes(trips, config_dir: Path, cal, aliases, rep):
    """Spec 5.2a. Returns the list of applied replace_stop changes."""
    applied = []
    rep["service_changes"] = []
    for r in read_csv_config(config_dir / "service_changes.csv"):
        line, kind = r["line"].strip(), r["kind"].strip()
        a, b = _parse_date(r.get("from_date", "").strip()), _parse_date(r.get("to_date", "").strip())
        entry = {"line": line, "kind": kind, "from": str(a or ""), "to": str(b or ""),
                 "evidence": r.get("evidence", "")}
        if (b and b < cal.start) or (a and a > cal.end):
            entry["status"] = "not applicable (date range does not overlap the feed window)"
            rep["service_changes"].append(entry)
            continue
        if kind == "info":
            entry["status"] = "info only (no stop change)"
            rep["service_changes"].append(entry)
            continue
        if kind != "replace_stop":
            raise BuildError(f"service_changes.csv: unknown kind {kind!r}")
        old = names.canonical(r["old_stop"], aliases)
        new = names.canonical(r["new_stop"], aliases)
        changed = []
        for t in trips:
            if t.line != line:
                continue
            if not any(s.name == old for s in t.stops):
                continue
            out = []
            for i, s in enumerate(t.stops):
                if s.name != old:
                    out.append(s)
                    continue
                prev_n = out[-1].name if out else None
                nxt_n = t.stops[i + 1].name if i + 1 < len(t.stops) else None
                if new in (prev_n, nxt_n):
                    continue  # new stop already adjacent: the old stop_time is deleted
                s.name = new
                out.append(s)
            t.stops = out
            changed.append(t.trip)
        entry["status"] = f"applied: {old!r} -> {new!r} on {len(changed)} trips"
        entry["trips"] = changed
        entry["old"], entry["new"] = old, new
        rep["service_changes"].append(entry)
        applied.append({"line": line, "old": old, "new": new, "trips": changed})
    return applied


def service_ids(trips_dates):
    """trips_dates: list of (trip, dates, prefix-parts). Returns {trip.key: service_id}, {service_id: dates}."""
    by_set: dict[frozenset, list[str]] = defaultdict(list)
    for tr, dates, name in trips_dates:
        by_set[dates].append(name)
    used: dict[str, frozenset] = {}
    set_to_id: dict[frozenset, str] = {}
    for dates in sorted(by_set, key=lambda d: (min(by_set[d]), sorted(d)[:1])):
        base = min(by_set[dates])
        sid, n = base, 2
        while sid in used and used[sid] != dates:
            sid = f"{base}-{n}"
            n += 1
        used[sid] = dates
        set_to_id[dates] = sid
    return set_to_id, used


def build(csv_path: Path, out_zip: Path | None, config_dir: Path, cache_dir: Path, build_dir: Path,
          drop_unplaced: bool = False, build_date: dt.date | None = None,
          log=lambda *_: None) -> Result:
    res = Result()
    rep = res.report
    rep["csv_path"] = str(csv_path)
    rep["csv_sha256"] = sha256(csv_path)
    rep["csv_hash_matches"] = rep["csv_sha256"] == EXPECTED_SHA
    rep["drop_unplaced"] = drop_unplaced
    build_date = build_date or dt.date.today()

    # ---- 5.1 load
    trips, info = load_trips(csv_path, config_dir)
    rep["rows"], rep["trips_loaded"] = info["rows"], len(trips)
    rep["time_fixes"], rep["exclusions"] = info["fixes"], info["exclusions"]
    rep["extra_stop_times"] = info["extra_stop_times"]
    rep["raw_stop_names"] = len({s.raw for t in trips for s in t.stops})

    # ---- 5.2 names
    aliases = names.load_aliases(config_dir / "stop_aliases.csv")
    by_line: dict[str, Counter] = defaultdict(Counter)
    allnames: Counter = Counter()
    for t in trips:
        for s in t.stops:
            s.name = names.canonical(s.raw, aliases)
            by_line[t.line][s.name] += 1
            allnames[s.name] += 1
    by_line["(all lines)"] = allnames
    rep["canonical_names"] = len(allnames)
    res.name_clusters = names.clusters(by_line)
    rep["fused_names"] = names.find_fused(set(allnames))

    cal, feed_cfg = calendars.load_calendar(config_dir / "feed.toml")
    rep["window"] = (str(cal.start), str(cal.end))
    rep["changes_applied"] = apply_service_changes(trips, config_dir, cal, aliases, rep)
    routes_mod.assign_directions(trips)

    # ---- 5.3 times
    dropped_times = []
    keep = []
    for t in trips:
        times_mod.rollover(t.stops)
        bad = times_mod.non_monotonic(t.stops)
        while bad:
            if not drop_unplaced:
                times_mod.check_trip(t)
            i = bad[0]
            if i == len(t.stops) - 1 or len(t.stops) <= 2:
                dropped_times.append((t.trip, f"trip dropped: last stop {t.stops[i].raw!r} {hms(t.stops[i].t)} precedes previous time"))
                t.stops = []
                break
            dropped_times.append((t.trip, f"stop_time dropped: {t.stops[i].raw!r} {hms(t.stops[i].t)} (previous {hms(t.stops[i-1].t)}); source PDF itself prints this time, no confirmed fix"))
            del t.stops[i]
            bad = times_mod.non_monotonic(t.stops)
        if t.stops:
            keep.append(t)
    trips = keep
    rep["time_drops"] = dropped_times

    # ---- 5.5 calendars (before placement so we know which trips exist)
    dated = []
    zero = []
    for t in trips:
        dates, dts, ss, nslugs = calendars.service_dates(t, cal, routes_mod.agency_of(t))
        if not dates:
            zero.append(t.trip)
            continue
        prefix = "bw" if t.mode == "bus" else "ab"
        parts = [prefix, dts] + ([ss] if t.mode == "bus" and ss else []) + nslugs
        dated.append((t, dates, "-".join(parts)))
    rep["zero_date_trips"] = zero
    trips = [d[0] for d in dated]
    set_to_id, sid_dates = service_ids(dated)
    for t, dates, _ in dated:
        t.service_id = set_to_id[dates]
        t.dates = dates
    res.calendar_trips = list(trips)

    # ---- 5.4 stops
    if not cache_ready(cache_dir):
        log("cache empty: fetching coordinate sources")
        rep["fetch"] = fetch(cache_dir, force=False)
    busway = stops_mod.load_busway_stops(cache_dir / "GTFS_CIRA.zip")
    osm_bus, osm_ferry, places = stops_mod.load_osm(cache_dir / "overpass.json")
    rep["sources"] = {"busway_stops": len(busway), "osm_bus_stops": len(osm_bus),
                      "osm_ferry_terminals": len(osm_ferry), "osm_places": len(places),
                      "busway_sha256": sha256(cache_dir / "GTFS_CIRA.zip"),
                      "overpass_sha256": sha256(cache_dir / "overpass.json")}
    manual = stops_mod.load_manual(config_dir / "stop_coords.csv")
    cands = stops_mod.Candidates(manual, stops_mod.Source(busway, "busway"), stops_mod.Source(osm_bus, "osm"), osm_ferry)

    # Verify service-change distances (5.2a)
    rep["change_checks"] = []
    for ch in rep["changes_applied"]:
        o = [c for tier in cands.tiers(ch["old"]) for c in tier]
        n = [c for tier in cands.tiers(ch["new"]) for c in tier]
        if o and n:
            d = min(dist_km((a.lat, a.lon), (b.lat, b.lon)) for a in o for b in n) * 1000
            ok = d <= 500
            rep["change_checks"].append((ch["old"], ch["new"], round(d), ok))
            if not ok:
                res.errors.append(f"service change on line {ch['line']}: {ch['old']!r} and {ch['new']!r} are "
                                  f"{d:.0f} m apart (> 500 m); carried-over times would be wrong. Ask a human.")
        else:
            rep["change_checks"].append((ch["old"], ch["new"], None, None))

    # operator-timetable impossibilities a human decided to keep (config/accepted_legs.csv)
    accepted: dict[tuple, set] = defaultdict(set)
    for r in read_csv_config(config_dir / "accepted_legs.csv"):
        accepted[(r["source"], r["trip"])].add((names.canonical(r["from_stop"], aliases), names.canonical(r["to_stop"], aliases)))
    rep["accepted_legs"] = []
    place_cache: dict = {}
    for t in trips:
        nm = [s.name for s in t.stops]
        tm = [s.t for s in t.stops]
        skip = frozenset(accepted.get(t.key, ()))
        k = (t.mode == "ferry", tuple(nm), tuple(tm), skip)
        if k not in place_cache:
            place_cache[k] = stops_mod.place_trip(nm, tm, lambda n, m=t.mode: cands.tiers(n, m), skip)
        t.placed, t.pruned = place_cache[k]

    # unplaced bookkeeping
    unplaced: dict[str, dict] = {}
    for t in trips:
        for i, (s, c) in enumerate(zip(t.stops, t.placed)):
            if c is None:
                u = unplaced.setdefault(s.name, {"lines": set(), "trips": [], "examples": [], "pruned": 0})
                if i in t.pruned:
                    u["pruned"] += 1
                u["lines"].add(t.line)
                u["trips"].append(t.trip)
                if len(u["examples"]) < 3:
                    prev = next(((t.stops[j].name, t.placed[j]) for j in range(i - 1, -1, -1) if t.placed[j]), None)
                    nxt = next(((t.stops[j].name, t.placed[j]) for j in range(i + 1, len(t.stops)) if t.placed[j]), None)
                    u["examples"].append((t.trip, prev, nxt))
    for n, u in unplaced.items():
        u["best"] = cands.best_names(n)
    res.unplaced = unplaced
    if unplaced and not drop_unplaced:
        res.errors.append(f"{len(unplaced)} stop names could not be placed (see build/unplaced_stops.csv); "
                          f"add them to config/stop_coords.csv, or build with --drop-unplaced")
    for n in sorted(allnames):
        pass

    # apply drops
    drops = []
    final = []
    for t in trips:
        if any(c is None for c in t.placed):
            if t.placed[0] is None or t.placed[-1] is None:
                which = t.stops[0].name if t.placed[0] is None else t.stops[-1].name
                drops.append(("trip", t.trip, t.line, f"first/last stop unplaced: {which!r}"))
                continue
            ns, np_ = [], []
            for s, c in zip(t.stops, t.placed):
                if c is None:
                    drops.append(("stop", t.trip, t.line, s.name))
                else:
                    ns.append(s)
                    np_.append(c)
            t.stops, t.placed = ns, np_
        if len(t.stops) < 2:
            drops.append(("trip", t.trip, t.line, "fewer than 2 placed stops"))
            continue
        final.append(t)
    rep["placement_drops"] = drops
    trips = final

    # ---- merge into GTFS stops
    placements: dict[str, Counter] = defaultdict(Counter)
    for t in trips:
        for s, c in zip(t.stops, t.placed):
            placements[s.name][(c.lat, c.lon)] += 1
    gstops, lookup, conflicts = stops_mod.merge_stops(placements, places, snap_conflicts=drop_unplaced)
    res.conflicts = conflicts
    if conflicts and not drop_unplaced:
        res.errors.append(f"{len(conflicts)} stops have placements 50-300 m apart under one name "
                          f"(see report); resolve them in config/stop_coords.csv")
    src_of: dict[tuple, Counter] = defaultdict(Counter)
    for t in trips:
        for s, c in zip(t.stops, t.placed):
            src_of[s.name][c.source] += 1
    placed_by_source = Counter()
    for n, cnt in src_of.items():
        placed_by_source[cnt.most_common(1)[0][0]] += 1
    rep["placed_by_source"] = dict(placed_by_source)
    rep["gtfs_stops"] = len(gstops)
    for t in trips:
        t.gstops = [lookup[(s.name, c.lat, c.lon)] for s, c in zip(t.stops, t.placed)]
    res.stops = gstops
    stop_xy = {g["stop_id"]: (g["lat"], g["lon"]) for g in gstops}
    res.stop_sources = {n: dict(c) for n, c in src_of.items()}

    # sanity checks (fail)
    bad_legs = []
    for t in trips:
        for i, g in enumerate(t.gstops):
            if not stops_mod.in_bbox(*stop_xy[g]):
                res.errors.append(f"stop {g} outside bounding box")
            if i:
                d = dist_km(stop_xy[t.gstops[i - 1]], stop_xy[g])
                mins = (t.stops[i].t - t.stops[i - 1].t) / 60
                if d / (max(mins, 1) / 60) > stops_mod.MAX_KMH + 1e-9:
                    if (t.stops[i - 1].name, t.stops[i].name) in accepted.get(t.key, ()):
                        rep["accepted_legs"].append((t.trip, t.line, t.stops[i - 1].name, t.stops[i].name, round(d, 2), mins))
                        continue
                    bad_legs.append((t.trip, t.line, t.stops[i - 1].name, t.stops[i].name, round(d, 2), mins))
                if mins == 0 and d > 1.5:
                    pass
    rep["bad_legs"] = bad_legs
    if bad_legs:
        res.errors.append(f"{len(bad_legs)} legs imply more than {stops_mod.MAX_KMH:.0f} km/h "
                          f"(first: trip {bad_legs[0][0]}, {bad_legs[0][2]!r} -> {bad_legs[0][3]!r}, "
                          f"{bad_legs[0][4]} km in {bad_legs[0][5]:.0f} min)")

    # ---- routes, trips, tables
    agencies_cfg = {k: v for k, v in feed_cfg["agencies"].items()}
    agencies, route_rows = routes_mod.build_routes(trips, routes_mod.load_routes(config_dir / "routes.toml"), agencies_cfg)
    tid_seen = set()
    trip_rows, st_rows = [], []
    for t in sorted(trips, key=lambda x: (routes_mod.route_id(x), x.trip)):
        rid = routes_mod.route_id(t)
        tid = f"{rid}-{t.trip}"
        if tid in tid_seen:
            raise BuildError(f"duplicate trip_id {tid}")
        tid_seen.add(tid)
        t.trip_id = tid
        trip_rows.append([rid, t.service_id, tid, routes_mod.headsign(t), t.direction_id, "", ""])
        for n, (s, g) in enumerate(zip(t.stops, t.gstops), 1):
            st_rows.append([tid, hms(s.t), hms(s.t), g, n, 1])
    used_stops = {g for t in trips for g in t.gstops}
    stop_rows = [[g["stop_id"], g["name"], f"{g['lat']:.6f}", f"{g['lon']:.6f}"]
                 for g in sorted(gstops, key=lambda g: g["stop_id"]) if g["stop_id"] in used_stops]
    used_services = {t.service_id for t in trips}
    cd_rows = [[sid, d.strftime("%Y%m%d"), 1] for sid in sorted(used_services) for d in sorted(sid_dates[sid])]
    fv = f"{build_date:%Y%m%d}-{rep['csv_sha256'][:8]}"
    fi = [["YourDayTrip (unofficial, internal use)", "https://example.com", "pt",
           f"{cal.start:%Y%m%d}", f"{cal.end:%Y%m%d}", fv]]
    tables = {
        "agency.txt": (["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"],
                       [[a["agency_id"], a["agency_name"], a["agency_url"], a["agency_timezone"], a["agency_lang"]] for a in agencies]),
        "stops.txt": (["stop_id", "stop_name", "stop_lat", "stop_lon"], stop_rows),
        "routes.txt": (["route_id", "agency_id", "route_short_name", "route_long_name", "route_desc", "route_type",
                        "route_color", "route_text_color"],
                       [[r["route_id"], r["agency_id"], r["route_short_name"], r["route_long_name"], r["route_desc"],
                         r["route_type"], r["route_color"], r["route_text_color"]] for r in route_rows]),
        "trips.txt": (["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "block_id", "shape_id"], trip_rows),
        "stop_times.txt": (["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint"], st_rows),
        "calendar_dates.txt": (["service_id", "date", "exception_type"], cd_rows),
        "feed_info.txt": (["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"], fi),
    }
    res.tables, res.trips = tables, trips
    rep["trips_final"], rep["routes"], rep["services"] = len(trips), len(route_rows), len(used_services)
    rep["stop_times"] = len(st_rows)
    rep["feed_version"] = fv
    rep["service_dates"] = {sid: sorted(sid_dates[sid]) for sid in used_services}
    return res
