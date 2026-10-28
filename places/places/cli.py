"""python -m places <command>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]          # .../places
ROOT = PKG.parent                                   # repo root
DATA = str(PKG / "data")
CONFIG = str(PKG / "config")
ENGINE_CONFIG = str(ROOT / "engine" / "config")


def engine():
    sys.path.insert(0, str(ROOT / "engine"))
    import reach
    return reach.load(ENGINE_CONFIG)


def library(with_engine=True):
    from . import load
    return load(DATA, engine=engine() if with_engine else None, config_dir=CONFIG)


def hm(t):
    from reach.calendar import iso_local
    return iso_local(t)[11:16]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="places")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract")
    sub.add_parser("validate")
    r = sub.add_parser("review")
    r.add_argument("--stale", type=int, default=None, help="only records checked more than N days ago")
    a = sub.add_parser("apply-review")
    a.add_argument("csv")
    s = sub.add_parser("shortlist")
    s.add_argument("--from", dest="origin", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--after", required=True)
    s.add_argument("--back-by", required=True)
    s.add_argument("--interests", default="")
    s.add_argument("--text", default="")
    s.add_argument("--pace", default="relaxed")
    s.add_argument("--weather", default="any")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--no-food", action="store_true")
    s.add_argument("--drafts", action="store_true")
    s.add_argument("--journeys", action="store_true")
    s.add_argument("--json", action="store_true")
    q = sub.add_parser("search")
    q.add_argument("text")
    q.add_argument("-k", type=int, default=10)
    q.add_argument("--drafts", action="store_true")
    f = sub.add_parser("food-near")
    f.add_argument("lat", type=float)
    f.add_argument("lon", type=float)
    f.add_argument("--radius", type=float, default=400)
    args = ap.parse_args(argv)

    if args.cmd == "extract":
        from .extract import run_extract
        n = run_extract(CONFIG, str(PKG / "cache"), str(PKG / "build"))
        print(f"{n} OSM elements -> places/cache/osm_places.json, places/build/candidates.csv")
    elif args.cmd == "validate":
        from .validate import validate
        rep = validate(DATA, CONFIG, engine=engine())
        for i, m in rep.errors:
            print(f"ERROR   {i}: {m}")
        for i, m in rep.warnings:
            if m != "draft":
                print(f"warning {i}: {m}")
        drafts = sum(1 for i, m in rep.warnings if m == "draft")
        print(f"{len(rep.places)} records, {len(rep.errors)} errors, {len(rep.warnings) - drafts} warnings, {drafts} drafts")
        return 1 if rep.errors else 0
    elif args.cmd == "review":
        from .review import write_review
        out = PKG / "build" / "review.csv"
        n, _ = write_review(DATA, out, engine=engine(), config_dir=CONFIG, stale_days=args.stale)
        print(f"{n} rows -> {out}")
    elif args.cmd == "apply-review":
        from .review import apply_review
        changed, problems = apply_review(args.csv, DATA, CONFIG)
        print(f"{len(changed)} records updated")
        for i, m in problems:
            print(f"  {i}: {m}")
        return 1 if problems else 0
    elif args.cmd == "shortlist":
        lib = library()
        lat, lon = (float(x) for x in args.origin.split(","))
        sl = lib.shortlist(("origin", lat, lon), args.date, args.after, args.back_by,
                           interests=[x for x in args.interests.split(",") if x], text=args.text, pace=args.pace,
                           weather=args.weather, include_food=not args.no_food, limit=args.limit,
                           include_journeys=args.journeys, include_drafts=args.drafts)
        if args.json:
            print(json.dumps(sl.to_json(), ensure_ascii=False, indent=2))
            return 0
        for title, items in (("SIGHTS", sl.sights), ("FOOD AND DRINK", sl.food)):
            print(f"\n{title}")
            for i in items:
                p = i.place
                slot = ", ".join(f"{hm(s.start)}-{hm(s.end)}" for s in i.slots)
                print(f"  {i.score:6.2f}  {p['name']} ({p['municipality']})  [{i.cluster}]  slot {slot}  "
                      f"visit {i.visit_s // 60} min  there {hm(i.arrive)}, leave by {hm(i.leave)}")
                print(f"          {'; '.join(i.why)}")
                for n in i.notes:
                    print(f"          note: {n}")
        print(f"\nexcluded: {len(sl.excluded)}")
        for w in sl.warnings + list(sl.coverage.warnings):
            print("warning:", w)
    elif args.cmd == "search":
        lib = library(False)
        for pid, sc in lib.search(args.text, args.k):
            print(f"{sc:6.2f}  {pid}  {lib.get(pid).name}")
    elif args.cmd == "food-near":
        for r in library(False).food_near(args.lat, args.lon, args.radius):
            print(f"{r['distance_m']:5d} m  {r['name']} ({r['amenity']})  UNVERIFIED  {r['osm']}")
    return 0
