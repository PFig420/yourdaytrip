"""Command line (spec 9)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build as build_mod, report, validate
from .fetch import fetch
from .load import BuildError

HERE = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gtfsconv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "review", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--csv", type=Path, default=HERE.parent / "schedules" / "schedules.csv")
        p.add_argument("--out", type=Path, default=HERE.parent / "data" / "gtfs" / "aveiro.zip")
        p.add_argument("--config", type=Path, default=HERE / "config")
        p.add_argument("--cache", type=Path, default=HERE / "cache")
        p.add_argument("--build-dir", type=Path, default=HERE / "build")
        p.add_argument("--validator-jar", type=Path, default=None)
        p.add_argument("--build-date", default=None, help="YYYY-MM-DD, for reproducible feed_version")
        p.add_argument("--drop-unplaced", action="store_true")
        p.add_argument("--no-validate", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print(fetch(a.cache))
        return 0
    import datetime as dt
    bd = dt.date.fromisoformat(a.build_date) if a.build_date else None
    try:
        res = build_mod.build(a.csv, a.out, a.config, a.cache, a.build_dir, a.drop_unplaced, bd, log=print)
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 1
    report.write_review_files(res, a.build_dir)
    if a.cmd == "review":
        report.write_report(res, a.build_dir, None, "review only (no zip written)")
        print(f"review files written to {a.build_dir}")
        return 0
    if res.errors:
        report.write_report(res, a.build_dir, None, "FAILED")
        print("BUILD FAILED:", file=sys.stderr)
        for e in res.errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"see {a.build_dir / 'report.md'}", file=sys.stderr)
        return 1
    ierr = validate.internal_checks(res.tables)
    if ierr:
        print("INTERNAL CHECKS FAILED:\n  " + "\n  ".join(ierr[:20]), file=sys.stderr)
        return 1
    from .write import write_zip
    write_zip(a.out, res.tables)
    vres = None
    if not a.no_validate:
        vres = validate.run_validator(a.out, a.build_dir / "validator", a.validator_jar)
    status = "OK (interim feed, --drop-unplaced)" if a.drop_unplaced else "OK"
    if vres and vres.get("ran") and vres["errors"]:
        status = f"FAILED: validator reports {vres['errors']} errors"
    report.write_report(res, a.build_dir, vres, status)
    print(f"wrote {a.out}: {res.report['trips_final']} trips, {res.report['gtfs_stops']} stops")
    if vres:
        print("validator:", {k: vres.get(k) for k in ("ran", "errors", "warnings", "reason")})
    return 1 if status.startswith("FAILED") else 0


if __name__ == "__main__":
    sys.exit(main())
