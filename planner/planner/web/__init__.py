"""HTTP API (Planner.md 9.3). Local only until the launch conditions in Issues.md #1 are met."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .. import mapview
from ..models import ORIGINS, DayPlan, PlanRequest

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = "© OpenStreetMap contributors"
PAGE = Path(__file__).with_name("static") / "index.html"


def create_app(planner=None, config="planner/config.toml", backend="api") -> FastAPI:
    import planner as pkg
    if backend == "claude-code" or getattr(planner, "backend", "api") == "claude-code":
        # Planner.md 15: the Claude Code backend is Pedro's local test tool, never a served one
        raise RuntimeError("the HTTP API refuses to run on the claude-code backend (local testing only)")
    pl = planner or pkg.load(config=config, backend=backend)
    app = FastAPI(title="YourDayTrip planner", version="0.1.0")
    cache = {"t": 0.0, "ok": None}

    @app.get("/", response_class=HTMLResponse)
    def page():
        # the test page: a form, then the checked plan as a timeline (local only, like the API)
        return PAGE.read_text(encoding="utf-8")

    @app.get("/meta")
    def meta():
        web = pl.cfg.get("web", {})
        return {"backend": pl.backend, "model": pl.model, "origins": list(ORIGINS.values()),
                "interests": sorted(pl.lib.cfg.vocab.get("tags", [])),
                "tile_url": web.get("tile_url", TILE_URL), "tile_attribution": web.get("tile_attribution", TILE_ATTRIBUTION)}

    @app.post("/plan")
    def plan(req: PlanRequest, use_llm: bool = True, debug: bool = False, map: bool = False):
        try:
            out = pl.plan(req, use_llm=use_llm, debug=debug)
            if map and out.status == "ok":
                out.map = mapview.build(out, pl.engine, pl.lib)
            return out.to_json()
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/health")
    def health():
        if time.time() - cache["t"] > 300:        # one cheap call, cached for five minutes
            try:
                if pl.backend == "local":
                    from ..local import LocalBackend
                    cache["ok"] = LocalBackend(pl.cfg.get("local", {}), pl.llm._runner).health()
                else:
                    pl.llm.client.models.retrieve(pl.model)
                    cache["ok"] = True
            except Exception as e:                # any failure means "not reachable"; never echo the message
                cache["ok"] = False
                cache["why"] = type(e).__name__
            cache["t"] = time.time()
        feeds = [{"id": f["id"], "sha256": f["sha256"]} for f in pl.engine.health()["feeds"]]
        return {"model": pl.model, "feeds": feeds, "places": len(pl.lib.places),
                "confirmed_places": sum(1 for p in pl.lib.places.values() if p.status == "confirmed"),
                "api_reachable": cache["ok"]}

    return app
