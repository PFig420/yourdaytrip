"""python -m reach earliest | latest | windows | coverage"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import load
from .engine import ReachError


def _latlon(s):
    a, b = s.split(",")
    return ("point", float(a), float(b))


def _points(path):
    pts = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row or row[0].strip().startswith("#"):
                continue
            try:
                if len(row) >= 3:
                    pts.append((row[0].strip(), float(row[1]), float(row[2])))
                else:
                    pts.append((f"p{i}", float(row[0]), float(row[1])))
            except ValueError:
                if i == 0:
                    continue  # header
                raise
    return pts


def _fmt_journey(j):
    d = j.to_json()
    lines = [f"depart {d['depart']['local'][11:19]}  arrive {d['arrive']['local'][11:19]}  "
             f"rides {d['rides']}  walk {d['walk_s']} s"]
    for l in d["legs"]:
        if l["type"] == "ride":
            lines.append(f"  {l['board_time']['local'][11:19]} {l['mode']:5} {l['route_short'] or l['route_long']} "
                         f"({l['trip_id']}) {l['board_stop']} -> {l['alight_stop']} {l['alight_time']['local'][11:19]}")
        else:
            lines.append(f"  walk {l['metres']} m / {l['seconds']} s: {l['from']} -> {l['to']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reach")
    ap.add_argument("--config", default="engine/config")
    ap.add_argument("--json", action="store_true", help="print JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--date", required=True)
        p.add_argument("--max-walk", type=float, dest="max_walk_m")
        p.add_argument("--buffer", type=int, dest="transfer_buffer_s")
        p.add_argument("--exclude-route-types", type=lambda s: [int(x) for x in s.split(",")])
        p.add_argument("--exclude-routes", type=lambda s: s.split(","))
        p.add_argument("--strict", action="store_true", dest="strict_coverage")

    e = sub.add_parser("earliest"); common(e)
    e.add_argument("--from", dest="src", required=True, type=_latlon)
    e.add_argument("--to", dest="dst", required=True, type=_latlon)
    e.add_argument("--after", required=True)
    l = sub.add_parser("latest"); common(l)
    l.add_argument("--from", dest="src", required=True, type=_latlon)
    l.add_argument("--to", dest="dst", required=True, type=_latlon)
    l.add_argument("--back-by", required=True)
    w = sub.add_parser("windows"); common(w)
    w.add_argument("--from", dest="src", required=True, type=_latlon)
    w.add_argument("--after", required=True)
    w.add_argument("--back-by", required=True)
    w.add_argument("--points", required=True)
    w.add_argument("--min-stay", type=int, dest="min_stay_s")
    w.add_argument("--journeys", action="store_true", dest="include_journeys")
    c = sub.add_parser("coverage"); c.add_argument("--date", required=True)
    a = ap.parse_args(argv)

    opts = {k: v for k, v in vars(a).items()
            if k in ("max_walk_m", "transfer_buffer_s", "exclude_route_types", "exclude_routes",
                     "strict_coverage", "min_stay_s", "include_journeys") and v not in (None, False)}
    try:
        eng = load(a.config)
        for w_ in eng.warnings:
            print("warning:", w_, file=sys.stderr)
        if a.cmd == "coverage":
            r = eng.coverage(a.date)
            print(json.dumps(r.to_json(), indent=2) if a.json else
                  f"{r.date}: " + (", ".join(r.warnings) or "all feeds cover this date"))
        elif a.cmd in ("earliest", "latest"):
            if a.cmd == "earliest":
                j = eng.earliest(a.src, a.date, a.after, a.dst, **opts)
            else:
                j = eng.latest(a.src, a.date, a.dst, a.back_by, **opts)
            if j is None:
                for w_ in eng.coverage(a.date).warnings:
                    print("warning:", w_, file=sys.stderr)
                print("no journey found")
                return 1
            for w_ in j.warnings:
                print("warning:", w_, file=sys.stderr)
            print(json.dumps(j.to_json(), indent=2) if a.json else _fmt_journey(j))
        else:
            r = eng.windows(a.src, a.date, a.after, a.back_by, _points(a.points), **opts)
            if a.json:
                print(json.dumps(r.to_json(), indent=2))
            else:
                for w_ in r.warnings:
                    print("warning:", w_, file=sys.stderr)
                for x in r.windows:
                    d = x.to_json()
                    f = lambda t: t["local"][11:16] if t else "--:--"
                    av = f"{x.available_s // 3600}h{x.available_s % 3600 // 60:02d}" if x.available_s is not None else "-"
                    print(f"{x.id:24} {x.status:11} arrive {f(d['arrive'])} leave {f(d['leave'])} available {av}")
    except ReachError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0
