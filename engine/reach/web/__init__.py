"""FastAPI app: python -m reach.web  (port 8100, local use only)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import load
from ..engine import BadInput, ReachError

STATIC = Path(__file__).parent / "static"


def _opts(max_walk_m, transfer_buffer_s, min_stay_s, exclude_route_types, exclude_routes,
          include_journeys, strict_coverage):
    o = {}
    if max_walk_m is not None:
        o["max_walk_m"] = max_walk_m
    if transfer_buffer_s is not None:
        o["transfer_buffer_s"] = transfer_buffer_s
    if min_stay_s is not None:
        o["min_stay_s"] = min_stay_s
    if exclude_route_types:
        try:
            o["exclude_route_types"] = [int(x) for x in exclude_route_types.split(",") if x]
        except ValueError as e:
            raise BadInput("exclude_route_types must be comma-separated integers") from e
    if exclude_routes:
        o["exclude_routes"] = [x for x in exclude_routes.split(",") if x]
    if include_journeys:
        o["include_journeys"] = True
    if strict_coverage:
        o["strict_coverage"] = True
    return o


def _latlon(s, name):
    try:
        a, b = s.split(",")
        return (name, float(a), float(b))
    except ValueError as e:
        raise BadInput(f"{name} must be 'lat,lon'") from e


def create_app(config_dir: str | None = None) -> FastAPI:
    config_dir = config_dir or os.environ.get("REACH_CONFIG", "engine/config")
    eng = load(config_dir)
    app = FastAPI(title="YourDayTrip reachability engine")
    app.state.engine = eng

    @app.exception_handler(ReachError)
    async def _reach_error(_req: Request, exc: ReachError):
        return JSONResponse({"error": exc.code, "detail": str(exc)}, status_code=exc.http)

    @app.exception_handler(ValueError)
    async def _value_error(_req: Request, exc: ValueError):
        return JSONResponse({"error": "bad_input", "detail": str(exc)}, status_code=400)

    @app.get("/health")
    def health():
        return eng.health()

    @app.get("/coverage")
    def coverage(date: str):
        try:
            return eng.coverage(date).to_json()
        except ValueError as e:
            raise BadInput(f"bad date: {date}") from e

    @app.get("/journey")
    def journey(date: str, to: str, request: Request, after: str | None = None, back_by: str | None = None,
                max_walk_m: float | None = None, transfer_buffer_s: int | None = None,
                exclude_route_types: str | None = None, exclude_routes: str | None = None,
                strict_coverage: bool = False):
        src = request.query_params.get("from")
        if src is None:
            raise BadInput("missing 'from'")
        if (after is None) == (back_by is None):
            raise BadInput("give exactly one of 'after' (earliest) or 'back_by' (latest departure)")
        o = _opts(max_walk_m, transfer_buffer_s, None, exclude_route_types, exclude_routes, False,
                  strict_coverage)
        a, b = _latlon(src, "origin"), _latlon(to, "target")
        if after is not None:
            j = eng.earliest(a, date, after, b, **o)
        else:
            j = eng.latest(a, date, b, back_by, **o)
        cov = eng.coverage(date)
        return {"journey": j.to_json() if j else None, "coverage": cov.to_json(),
                "warnings": list(cov.warnings)}

    @app.post("/windows")
    async def windows(request: Request):
        try:
            body = await request.json()
            origin = body["origin"]
            pts = [{"id": p.get("id", f"p{i}"), "lat": p["lat"], "lon": p["lon"]}
                   for i, p in enumerate(body["points"])]
            date, la, bb = body["date"], body["leave_after"], body["back_by"]
        except (KeyError, TypeError, ValueError) as e:
            raise BadInput(f"bad request body: {e!r}") from e
        opts = {k: v for k, v in body.items() if k in (
            "max_walk_m", "transfer_buffer_s", "min_stay_s", "exclude_route_types", "exclude_routes",
            "include_journeys", "strict_coverage")}
        origin = {"id": origin.get("id", "origin"), "lat": origin["lat"], "lon": origin["lon"]}
        return eng.windows(origin, date, la, bb, pts, **opts).to_json()

    @app.get("/stops/windows")
    def stop_windows(date: str, after: str, back_by: str, request: Request, max_walk_m: float | None = None,
                     transfer_buffer_s: int | None = None, exclude_route_types: str | None = None,
                     exclude_routes: str | None = None, strict_coverage: bool = False):
        src = request.query_params.get("from")
        if src is None:
            raise BadInput("missing 'from'")
        o = _opts(max_walk_m, transfer_buffer_s, None, exclude_route_types, exclude_routes, False,
                  strict_coverage)
        res = eng.stop_windows(_latlon(src, "origin"), date, after, back_by, **o)
        cov = eng.coverage(date)
        feats = []
        for w in res:
            j = w.to_json()
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [w.lon, w.lat]},
                          "properties": {k: v for k, v in j.items() if k not in ("lat", "lon")}})
        return {"type": "FeatureCollection", "features": feats, "coverage": cov.to_json(),
                "warnings": list(cov.warnings)}

    @app.get("/map")
    def map_page():
        return FileResponse(STATIC / "map.html")

    return app



def __getattr__(name):
    # lazy default app so `uvicorn reach.web:app` works without loading feeds at import time of the package
    global app
    if name == "app":
        app = create_app()
        return app
    raise AttributeError(name)
