"""build/report.md and the review files (spec 8)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .stops import dist_km


def _fmt_best(b, prev, nxt) -> str:
    ratio, tag, name, lat, lon = b
    ds = []
    for lbl, nb in (("prev", prev), ("next", nxt)):
        if nb:
            ds.append(f"{lbl} {dist_km((lat, lon), (nb[1].lat, nb[1].lon)) * 1000:.0f} m")
    return f"{tag}:{name!r} sim {ratio} @{lat:.5f},{lon:.5f}" + (f" ({', '.join(ds)})" if ds else "")


def write_review_files(res, build_dir: Path, cands=None) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    with open(build_dir / "name_clusters.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line", "suggested_canonical", "variants"])
        for c in res.name_clusters:
            srcs = getattr(res, "stop_sources", {})
            # prefer the variant that was matched to a Busway/OSM name, else the most frequent
            pref = max(c["members"], key=lambda m: ("busway-exact" in srcs.get(m, {}) or "osm-exact" in srcs.get(m, {}),
                                                    sum(srcs.get(m, {}).values())))
            w.writerow([c["line"], pref, " | ".join(c["members"])])
    with open(build_dir / "unplaced_stops.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["canonical", "lines", "trips", "example_trip", "previous_placed", "previous_lat", "previous_lon",
                    "next_placed", "next_lat", "next_lon", "best_candidates"])
        for name, u in sorted(res.unplaced.items()):
            for trip, prev, nxt in u["examples"][:3]:
                w.writerow([name, " ".join(sorted(u["lines"])), len(u["trips"]), trip,
                            prev[0] if prev else "", f"{prev[1].lat:.6f}" if prev else "", f"{prev[1].lon:.6f}" if prev else "",
                            nxt[0] if nxt else "", f"{nxt[1].lat:.6f}" if nxt else "", f"{nxt[1].lon:.6f}" if nxt else "",
                            "; ".join(_fmt_best(b, prev, nxt) for b in u.get("best", []))])
    feats = []
    for name, u in sorted(res.unplaced.items()):
        for trip, prev, nxt in u["examples"][:1]:
            pts = [p[1] for p in (prev, nxt) if p]
            if not pts:
                continue
            lat = sum(p.lat for p in pts) / len(pts)
            lon = sum(p.lon for p in pts) / len(pts)
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                          "properties": {"canonical": name, "lines": sorted(u["lines"]), "example_trip": trip,
                                         "previous": prev[0] if prev else None, "next": nxt[0] if nxt else None,
                                         "note": "midpoint of its neighbours; drag to the real stop, copy to config/stop_coords.csv"}})
    (build_dir / "unplaced_stops.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")
    sf = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
           "properties": {"stop_id": s["stop_id"], "name": s["name"]}} for s in res.stops]
    (build_dir / "stops.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": sf}, ensure_ascii=False), encoding="utf-8")


def write_report(res, build_dir: Path, validator: dict | None, status: str) -> None:
    r = res.report
    L = []
    a = L.append
    a("# GTFS build report\n")
    a(f"**Status:** {status}\n")
    a("## Input\n")
    a(f"- CSV: `{r['csv_path']}`")
    a(f"- SHA-256: `{r['csv_sha256']}`" + ("" if r["csv_hash_matches"] else "  **WARNING: differs from the hash this spec was written against**"))
    a(f"- Rows: {r['rows']}; trips: {r['trips_loaded']}; raw stop-name strings: {r['raw_stop_names']}; canonical names after clean-up: {r['canonical_names']}")
    s = r.get("sources", {})
    a(f"- Busway GTFS stops: {s.get('busway_stops')} (sha256 `{s.get('busway_sha256','')[:16]}…`); OSM bus stops: {s.get('osm_bus_stops')}, ferry terminals: {s.get('osm_ferry_terminals')}, places: {s.get('osm_places')} (sha256 `{s.get('overpass_sha256','')[:16]}…`)")
    a(f"- Feed window: {r['window'][0]} to {r['window'][1]}; mode: {'--drop-unplaced (interim)' if r['drop_unplaced'] else 'strict'}\n")
    a("## Counts per stage\n")
    a(f"- Trips loaded: {r['trips_loaded']}; after time checks and calendars: see below; in feed: **{r.get('trips_final')}**")
    a(f"- Routes: {r.get('routes')}; services: {r.get('services')}; GTFS stops: {r.get('gtfs_stops')}; stop_times: {r.get('stop_times')}")
    a(f"- GTFS stops (canonical names) placed, by source of most placements: {r.get('placed_by_source')}\n")
    a("## Time fixes applied\n")
    a("\n".join(f"- {t} seq {q} `{n}`: {o} -> {nw} ({ev})" for t, q, n, o, nw, ev in r["time_fixes"]) or "- none")
    a("\n## Extra stop times added (config/extra_stop_times.csv)\n")
    a(f"- {len(r.get('extra_stop_times', []))} stop_times inserted: " + (", ".join(sorted({f"{t[2]} on {sum(1 for u in r['extra_stop_times'] if u[2] == t[2] and u[0][:5] == t[0][:5])} trips ({t[0][:5]})" for t in r['extra_stop_times']})) or "none"))
    a("\n## Time problems handled by interim mode\n")
    a("\n".join(f"- {t}: {m}" for t, m in r["time_drops"]) or "- none")
    a("\n## Exclusions\n")
    a("\n".join(f"- {t}: {m}" for t, m in r["exclusions"]) or "- none")
    a("\n## Operator service changes (5.2a)\n")
    for e in r["service_changes"]:
        a(f"- line {e['line']} `{e['kind']}` {e['from'] or '…'} to {e['to'] or '…'}: {e['status']}")
        if e.get("trips"):
            a(f"  - time carried over from the closed stop on {len(e['trips'])} trips: {', '.join(e['trips'])}")
    for old, new, d, ok in r.get("change_checks", []):
        a(f"- distance check {old!r} -> {new!r}: " + ("cannot verify (a stop has no candidate)" if d is None else f"{d} m ({'ok' if ok else 'TOO FAR'})"))
    a("\n## Fused stop names (need a human decision)\n")
    a("\n".join(f"- {f!r} ends with known stop {m!r}" for f, m in r["fused_names"]) or "- none")
    a("\n## Unplaced stops\n")
    a("\n".join(f"- {n}: lines {', '.join(sorted(u['lines']))}, {len(u['trips'])} trips" + (f" ({u['pruned']} pruned: every candidate implies an impossible speed on that trip)" if u.get("pruned") else "") for n, u in sorted(res.unplaced.items())) or "- none")
    a("\n## Dropped by placement (--drop-unplaced)\n")
    nd = [d for d in r["placement_drops"] if d[0] == "trip"]
    sd = [d for d in r["placement_drops"] if d[0] == "stop"]
    a(f"- trips dropped: {len(nd)}; stop_times removed: {len(sd)}")
    for d in nd:
        a(f"  - trip {d[1]} (line {d[2]}): {d[3]}")
    a("\n## Trips with zero service dates\n")
    a(", ".join(r["zero_date_trips"]) or "- none")
    a("\n## Stops with placements 50-300 m apart (strict: must be resolved; interim: snapped to the majority position)\n")
    a("\n".join(f"- {n}: {d} m between {p} and {q}" for n, d, p, q in res.conflicts) or "- none")
    a("\n## Legs above 90 km/h\n")
    a("\n".join(f"- trip {t} (line {l}): {x!r} -> {y!r}, {d} km in {m:.0f} min" for t, l, x, y, d, m in r["bad_legs"]) or "- none")
    a("\n## Kept impossible legs (operator timetable issue, config/accepted_legs.csv)\n")
    a("\n".join(f"- trip {t} (line {l}): {x!r} -> {y!r}, {d} km in {m:.0f} min (~{d / (max(m, 1) / 60):.0f} km/h); both stops kept as printed" for t, l, x, y, d, m in r.get("accepted_legs", [])) or "- none")
    a("\n## Assumptions\n")
    a("- Every Aveirobus sheet (lines 1-12, Azul, Verde) and the ferry are treated as valid for the whole feed window, although lines 1-7, 10, 12 read as June 2025 and 2 and 6 as February 2026.")
    a("- Informational notes (\"Ligação à linha ...\") have no calendar effect and are kept here only.")
    a("- Aveirobus services observe the Aveiro municipal holiday (12 May, to be verified); Busway has none.")
    a("- Times printed as 00:xx after 23:xx are treated as after-midnight times of the same service day.")
    if validator is not None:
        a("\n## MobilityData validator\n")
        if not validator.get("ran"):
            a(f"- not run: {validator.get('reason')}")
        else:
            a(f"- errors: **{validator['errors']}**, warnings: {validator['warnings']}, infos: {validator['infos']}")
            for sev, code, n in validator["by_code"]:
                a(f"  - {sev} `{code}`: {n}")
    if res.errors:
        a("\n## Unresolved issues (strict build fails)\n")
        a("\n".join(f"- {e}" for e in res.errors))
    (build_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
