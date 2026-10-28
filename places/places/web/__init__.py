"""FastAPI app: python -m places.web  (port 8200, local use only: Issues.md #1)."""
from __future__ import annotations

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import cli


class ShortlistBody(BaseModel):
    origin: list[float] = Field(min_length=2, max_length=2, description="[lat, lon]")
    date: str
    leave_after: str
    back_by: str
    interests: list[str] = []
    text: str = ""
    pace: str = "relaxed"
    weather: str = "any"
    include_food: bool = True
    limit: int = 20
    include_journeys: bool = False
    include_drafts: bool = False


def create_app(lib=None) -> FastAPI:
    lib = lib or cli.library()
    app = FastAPI(title="YourDayTrip places layer")
    app.state.lib = lib

    @app.exception_handler(ValueError)
    async def _bad(_r: Request, exc: ValueError):
        return JSONResponse({"error": "bad_input", "detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _missing(_r: Request, exc: KeyError):
        return JSONResponse({"error": "not_found", "detail": str(exc.args[0])}, status_code=404)

    @app.get("/health")
    def health():
        return {"ok": True, "places": len(lib.places),
                "by_status": {s: sum(1 for p in lib.places.values() if p.status == s)
                              for s in ("draft", "confirmed", "rejected")},
                "engine_warnings": lib.engine.warnings if lib.engine else []}

    @app.post("/shortlist")
    def shortlist(b: ShortlistBody):
        from reach.engine import ReachError
        try:
            sl = lib.shortlist(("origin", b.origin[0], b.origin[1]), b.date, b.leave_after, b.back_by,
                               interests=b.interests, text=b.text, pace=b.pace, weather=b.weather,
                               include_food=b.include_food, limit=b.limit, include_journeys=b.include_journeys,
                               include_drafts=b.include_drafts)
        except ReachError as e:
            return JSONResponse({"error": e.code, "detail": str(e)}, status_code=e.http)
        return sl.to_json()

    @app.get("/search")
    def search(q: str, k: int = 10):
        return [{"id": i, "score": round(s, 4), "name": lib.get(i).name} for i, s in lib.search(q, k)]

    @app.get("/places/{place_id}")
    def place(place_id: str):
        return lib.get(place_id).summary()

    @app.get("/food-near")
    def food_near(lat: float, lon: float, radius_m: float = Query(400, gt=0, le=3000)):
        return lib.food_near(lat, lon, radius_m)

    return app
