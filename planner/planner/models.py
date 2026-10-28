"""Request, result and LLM schemas (Planner.md 3, 5.1, 8)."""
from __future__ import annotations

import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

ORIGINS = {
    "porto-campanha": {"id": "porto-campanha", "name": "Porto Campanhã", "lat": 41.14871926, "lon": -8.584835348},
    "porto-sao-bento": {"id": "porto-sao-bento", "name": "Porto São Bento", "lat": 41.14556679, "lon": -8.610221145},
    "aveiro-station": {"id": "aveiro-station", "name": "Aveiro station", "lat": 40.64342677, "lon": -8.640761068},
}

DISCLAIMERS = [
    "Times are the operators' published schedules and indicative; check with the operator before your return journey.",
    "Data as of {feeds}.",
    "Map data © OpenStreetMap contributors.",
]


class Origin(BaseModel):
    id: str = "origin"
    name: str = "Start"
    lat: float
    lon: float


class PlanRequest(BaseModel):
    origin: Origin
    date: str
    leave_after: str
    back_by: str                   # the deadline time: back home by it ("home") or the first vehicle home leaves by it ("leave")
    deadline: Literal["home", "leave"] = "home"
    interests: list[str] = Field(default_factory=list)
    text: str = Field(default="", max_length=200)
    pace: Literal["relaxed", "packed"] = "relaxed"
    weather: Literal["any", "rain"] = "any"
    include_food: bool = True

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        import datetime as dt
        try:
            dt.date.fromisoformat(v)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD") from None
        return v

    @field_validator("leave_after", "back_by")
    @classmethod
    def _hhmm(cls, v):
        if not HHMM.match(v):
            raise ValueError("time must be HH:MM")
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.back_by <= self.leave_after:
            raise ValueError("back_by must be after leave_after")
        return self

    def check_interests(self, vocabulary_tags) -> None:
        bad = [t for t in self.interests if t not in vocabulary_tags]
        if bad:
            raise ValueError(f"unknown interest tag(s): {', '.join(bad)}")


# ---- timeline -----------------------------------------------------------
class Walk(BaseModel):
    type: Literal["walk"] = "walk"
    from_: str = Field(alias="from")
    to: str
    metres: int
    depart: str
    arrive: str
    from_id: str = ""      # a stop id, a place or point id (<id>, <id>#<k>) or the origin's id: where the map draws the ends
    to_id: str = ""
    model_config = {"populate_by_name": True}


class Ride(BaseModel):
    type: Literal["ride"] = "ride"
    mode: str
    route_short: str
    route_long: str
    headsign: str
    trip_id: str
    board_stop: str
    board_time: str
    alight_stop: str
    alight_time: str
    feed: str = ""         # ids for the map; the engine's own, feed-prefixed
    board_stop_id: str = ""
    alight_stop_id: str = ""


class Wait(BaseModel):
    type: Literal["wait"] = "wait"
    at: str
    from_: str = Field(alias="from")
    until: str
    reason: str
    model_config = {"populate_by_name": True}


class Visit(BaseModel):
    type: Literal["visit"] = "visit"
    place_id: str
    name: str
    start: str
    end: str
    entry: Literal["open", "fixed"] = "open"
    meal: Optional[Literal["lunch", "dinner"]] = None
    inserted: bool = False
    reason: str = ""
    point_id: str = ""     # the point the visit used: the place id, or <id>#<k> for a reference point


class FreeTime(BaseModel):
    type: Literal["free_time"] = "free_time"
    at: str
    from_: str = Field(alias="from")
    until: str
    reason: str
    point_id: str = ""     # where the free time is spent
    # optional ideas on foot, {place_id, name, kind, walk_m, walk_min, minutes_there, open_until, description}
    suggestions: list[dict] = Field(default_factory=list)
    model_config = {"populate_by_name": True}


TimelineEntry = Union[Walk, Ride, Wait, FreeTime, Visit]


class SafeReturn(BaseModel):
    from_place: str
    depart: str
    first_vehicle: Optional[dict] = None
    arrive_home: str
    backup: Optional[dict] = None


class PlanB(BaseModel):
    change_at: str
    missed: dict
    next_journey: Optional[list[dict]] = None
    arrive: Optional[str] = None
    target: str
    within_deadline: Optional[bool] = None
    note: str = ""


class StopLine(BaseModel):
    place_id: str
    line: str


class Usage(BaseModel):
    calls: list[dict] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0


class DayPlan(BaseModel):
    request: PlanRequest
    status: Literal["ok", "no_plan"] = "ok"
    source: Literal["llm", "fallback"]
    model: Optional[str] = None
    backend: str = "api"
    attempts: int = 0
    title: str = ""
    timeline: list[TimelineEntry] = Field(default_factory=list)
    home_by: Optional[str] = None
    last_safe_return: Optional[SafeReturn] = None
    plan_b: list[PlanB] = Field(default_factory=list)
    narrative: Optional[dict] = None
    disclaimers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)      # set when status == "no_plan"
    attempt_errors: list[list[str]] = Field(default_factory=list)   # error codes of each failed planner draft
    map: Optional[dict] = None         # a GeoJSON FeatureCollection of the day (mapview.build); only with POST /plan?map=true
    debug: Optional[dict] = None       # testing only (Planner.md 9.3): {draft, attempt_drafts}; filled when plan(debug=True)
    usage: Usage = Field(default_factory=Usage)

    def to_json(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


# ---- LLM output (Planner.md 5.1) ------------------------------------------
class DraftStop(BaseModel):
    place_id: str
    visit_minutes: int
    why: str


class DayDraft(BaseModel):
    title: str
    stops: list[DraftStop]
    notes: str


class NarrativeOut(BaseModel):
    summary: str
    stop_lines: list[StopLine]
