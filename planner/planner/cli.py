"""python -m planner plan|eval (Planner.md 9.2)."""
from __future__ import annotations

import argparse
import json
import sys

from .models import ORIGINS, Origin, PlanRequest


def _origin(s: str) -> Origin:
    if s in ORIGINS:
        return Origin(**ORIGINS[s])
    try:
        lat, lon = (float(x) for x in s.split(","))
    except ValueError:
        raise SystemExit(f"--from must be lat,lon or one of {', '.join(ORIGINS)}")
    return Origin(id="origin", name="Start", lat=lat, lon=lon)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="planner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--from", dest="origin", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--after", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--back-by", help="be back at the start by this time")
    g.add_argument("--leave-by", help="the first vehicle home leaves by this time; the journey may arrive later")
    p.add_argument("--interests", default="")
    p.add_argument("--pace", default="relaxed", choices=["relaxed", "packed"])
    p.add_argument("--weather", default="any", choices=["any", "rain"])
    p.add_argument("--no-food", action="store_true")
    p.add_argument("--text", default="")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--geojson", metavar="PATH", help="write the day as a GeoJSON map to PATH (open it in geojson.io)")
    p.add_argument("--debug", action="store_true", help="testing: keep the model's notes and failed drafts in the plan")
    p.add_argument("--config", default="planner/config.toml")
    p.add_argument("--backend", default="api", choices=["api", "claude-code", "local"])
    e = sub.add_parser("eval")
    e.add_argument("--set", default="planner/eval/requests.jsonl")
    e.add_argument("--model")
    e.add_argument("--effort")
    e.add_argument("--estimate", action="store_true", help="offline token estimate only; makes no API call")
    e.add_argument("--approved", action="store_true", help="Pedro has approved the budget; required for a live run")
    e.add_argument("--limit", type=int)
    e.add_argument("--config", default="planner/config.toml")
    e.add_argument("--backend", default="api", choices=["api", "claude-code", "local"])
    e.add_argument("--tag", default="", help="label for the log file; a tagged run writes no report")
    e.add_argument("--resume", action="store_true", help="skip requests already logged for this model, effort and date")
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        import planner
        from .render import render
        req = PlanRequest(origin=_origin(a.origin), date=a.date, leave_after=a.after,
                          back_by=a.leave_by or a.back_by, deadline="leave" if a.leave_by else "home",
                          interests=[x for x in a.interests.split(",") if x], text=a.text, pace=a.pace,
                          weather=a.weather, include_food=not a.no_food)
        pl = planner.load(config=a.config, backend=a.backend)
        plan = pl.plan(req, use_llm=not a.no_llm, debug=a.debug)
        if a.geojson:
            if plan.status == "ok":
                from . import mapview
                plan.map = mapview.build(plan, pl.engine, pl.lib)
                with open(a.geojson, "w", encoding="utf-8") as f:
                    json.dump(plan.map, f, ensure_ascii=False, indent=1)
            else:
                print("No plan, so no map was written.", file=sys.stderr)
        print(json.dumps(plan.to_json(), ensure_ascii=False, indent=2) if a.json else render(plan))
        return 0 if plan.status == "ok" else 2
    from . import eval as ev
    return ev.main(a)


if __name__ == "__main__":
    sys.exit(main())
