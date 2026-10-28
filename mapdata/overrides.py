"""python -m mapdata.overrides: the engine's walking overrides and Pedro's exceptions sheet (WalkBarriers.md 2 and 4).

Reads the path cache (data/paths/paths.json.gz) and the review sheet (data/paths/underestimates-review.csv), then:
1. rewrites the review sheet from the cache's "engine-underestimate" flags, carrying existing verdicts and notes over by pair
   (a reviewed row whose flag has gone keeps its verdict, with its note prefixed "flag gone:");
2. writes data/paths/walk_overrides.csv, one row per cached walk pair, deterministic.

Verdicts in the sheet: empty (the OSM length applies), `osm-gap` (OSM misses a crossing: keep the engine's estimate) or `blocked`
(no reasonable walk exists: refuse the walk). The file is derived from OpenStreetMap (ODbL).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
from pathlib import Path

from .graph import decode, path_length

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "paths" / "paths.json.gz"
REVIEW = ROOT / "data" / "paths" / "underestimates-review.csv"
OUT = ROOT / "data" / "paths" / "walk_overrides.csv"
VERDICTS = ("", "osm-gap", "blocked")
REVIEW_COLS = ["excess_m", "engine_m", "path_m", "from_id", "to_id", "from", "to", "verdict", "note"]
OUT_COLS = ["from_id", "to_id", "metres", "kind", "note"]
FLAG = re.compile(r"engine-underestimate (\d+) m vs (\d+) m")


def key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def load_cache(path=CACHE) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def read_review(path=REVIEW) -> dict:
    """-> {pair key: row dict}. A missing sheet is an empty one. An unknown verdict is an error (a typo must not pass silently)."""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    with open(p, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get("verdict") or "").strip().lower()
            if v not in VERDICTS:
                raise ValueError(f"{p.name}: unknown verdict {r.get('verdict')!r} for {r['from_id']} -> {r['to_id']} "
                                 f"(use osm-gap, blocked or leave it empty)")
            r["verdict"], r["note"] = v, (r.get("note") or "").strip()
            out[key(r["from_id"], r["to_id"])] = r
    return out


def names_from(engine, lib) -> dict:
    """id -> a name for the sheet: 'Name (stop)' for stops, the place's name (with the point's name for a reference point)."""
    names = {}
    for f in engine.feeds:
        for sid, (n, *_rest) in f.stops.items():
            names[sid] = f"{n} (stop)"
    for pid, p in lib.places.items():
        names[pid] = p.name
        for k, (label, _, _) in enumerate(p.all_points()):
            if k:
                names[f"{pid}#{k}"] = f"{p.name} ({label})"
    from planner.models import ORIGINS
    for oid, o in ORIGINS.items():
        names[oid] = o["name"]
    return names


def default_names() -> dict:
    sys.path[:0] = [str(ROOT / "planner"), str(ROOT / "places"), str(ROOT / "engine")]
    import places
    import reach
    eng = reach.load(str(ROOT / "engine/config"))
    return names_from(eng, places.load(str(ROOT / "places/data"), engine=eng, config_dir=str(ROOT / "places/config")))


def review_rows(cache: dict, names: dict, old: dict) -> list[dict]:
    """The sheet rebuilt from the cache's flags, biggest gap first, with the old verdicts and notes carried over."""
    rows, seen = [], set()
    for k, reasons in cache["meta"]["flagged"].items():
        if k.count("|") != 1:
            continue
        m = next((FLAG.match(r) for r in reasons if FLAG.match(r)), None)
        if not m:
            continue
        a, b = k.split("|")
        eng, path = int(m[1]), int(m[2])
        kk = key(a, b)
        seen.add(kk)
        prev = old.get(kk, {})
        rows.append({"excess_m": path - eng, "engine_m": eng, "path_m": path, "from_id": a, "to_id": b,
                     "from": names.get(a, a), "to": names.get(b, b), "verdict": prev.get("verdict", ""), "note": prev.get("note", "")})
    rows.sort(key=lambda r: (-r["excess_m"], r["from_id"], r["to_id"]))
    for kk, r in sorted(old.items()):                      # reviewed, but the flag is gone: keep the decision
        if kk not in seen and (r["verdict"] or r["note"]):
            note = r["note"] if r["note"].startswith("flag gone:") else ("flag gone: " + r["note"]).strip()
            rows.append({"excess_m": "", "engine_m": "", "path_m": "", "from_id": r["from_id"], "to_id": r["to_id"],
                         "from": r["from"], "to": r["to"], "verdict": r["verdict"], "note": note})
    return rows


def render_csv(cols: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue()


def review_hash(sheet: list[dict]) -> str:
    lines = sorted(f"{'|'.join(key(r['from_id'], r['to_id']))}|{r['verdict']}|{r['note']}" for r in sheet)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def override_rows(cache: dict, sheet: list[dict]) -> tuple[list[dict], list[str]]:
    """-> (rows, pairs with no route). One row per cached walk pair; sorted by key so a re-run gives the same bytes."""
    verdicts = {key(r["from_id"], r["to_id"]): r for r in sheet if r["verdict"]}
    rows = []
    for k, poly in cache["walk"].items():
        a, b = k.split("|")
        r = verdicts.get(key(a, b))
        if r is None:
            rows.append({"from_id": a, "to_id": b, "metres": round(path_length(decode(poly, cache["meta"].get("precision", 5)))),
                         "kind": "osm", "note": ""})
        else:
            rows.append({"from_id": a, "to_id": b, "metres": "", "kind": r["verdict"], "note": r["note"]})
    rows.sort(key=lambda r: key(r["from_id"], r["to_id"]) + (r["from_id"],))
    nopath = sorted(k for k, v in cache["meta"]["flagged"].items() if k.count("|") == 1 and k not in cache["walk"])
    return rows, nopath


def render_overrides(cache: dict, sheet: list[dict]) -> tuple[str, list[dict], list[str]]:
    rows, nopath = override_rows(cache, sheet)
    meta = cache["meta"]
    head = [f"# built: {meta.get('built', '')}",
            "# feeds: " + " ".join(f"{k}={v}" for k, v in sorted(meta.get("feeds", {}).items())),
            f"# places_hash: {meta.get('places_hash', '')}",
            f"# review_hash: {review_hash(sheet)}",
            "# derived from OpenStreetMap (ODbL); written by python -m mapdata.overrides"]
    return "\n".join(head) + "\n" + render_csv(OUT_COLS, rows), rows, nopath


def run(cache_path=CACHE, review_path=REVIEW, out_path=OUT, names=None, quiet=False) -> dict:
    cache = load_cache(cache_path)
    old = read_review(review_path)
    names = names if names is not None else default_names()
    sheet = review_rows(cache, names, old)
    Path(review_path).write_text(render_csv(REVIEW_COLS, sheet), encoding="utf-8")
    text, rows, nopath = render_overrides(cache, sheet)
    Path(out_path).write_text(text, encoding="utf-8")
    kinds = {k: sum(r["kind"] == k for r in rows) for k in ("osm", "osm-gap", "blocked")}
    out = {"rows": len(rows), **kinds, "no_route_pairs": len(nopath), "review_rows": len(sheet),
           "carried_over": sum(1 for r in sheet if r["verdict"] or r["note"])}
    if not quiet:
        print(json.dumps(out))
        if nopath:
            print(f"{len(nopath)} pairs have no cached route and keep the engine's estimate (mark them blocked in the sheet if "
                  f"no walk exists):")
            for k in nopath:
                a, b = k.split("|")
                print(f"  {a} -> {b}  ({names.get(a, a)} -> {names.get(b, b)})")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mapdata.overrides")
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--review", default=str(REVIEW))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    run(a.cache, a.review, a.out)


if __name__ == "__main__":
    main()
