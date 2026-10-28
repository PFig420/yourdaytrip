"""Result dataclasses (frozen, with to_json)."""
from __future__ import annotations

import dataclasses as dc
from dataclasses import dataclass, field

from .calendar import iso_local


def T(instant):
    """Time as {local, utc}."""
    if instant is None:
        return None
    return {"local": iso_local(instant), "utc": instant}


def _plain(v):
    if dc.is_dataclass(v) and not isinstance(v, type):
        return v.to_json()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


class _Json:
    def to_json(self):
        out = {}
        for f in dc.fields(self):
            v = getattr(self, f.name)
            name = f.name.rstrip("_")
            if f.name in ("depart", "arrive", "leave", "board_time", "alight_time", "earliest", "latest"):
                out[name] = T(v)
            else:
                out[name] = _plain(v)
        return out


@dataclass(frozen=True)
class RideLeg(_Json):
    type: str
    mode: str
    feed: str
    route_id: str
    route_short: str
    route_long: str
    trip_id: str
    headsign: str
    board_stop_id: str
    board_stop: str
    board_time: int
    alight_stop_id: str
    alight_stop: str
    alight_time: int


@dataclass(frozen=True)
class WalkLeg(_Json):
    type: str
    from_: str
    to: str
    metres: int
    seconds: int
    depart: int
    arrive: int
    from_id: str = ""
    to_id: str = ""


@dataclass(frozen=True)
class Journey(_Json):
    depart: int
    arrive: int
    legs: tuple
    rides: int
    walk_s: int
    coverage: "Coverage | None" = None
    warnings: tuple = ()

    def to_json(self):
        d = super().to_json()
        d["duration_s"] = self.arrive - self.depart
        return d


@dataclass(frozen=True)
class Coverage(_Json):
    date: str
    missing_feeds: tuple
    warnings: tuple


@dataclass(frozen=True)
class Window(_Json):
    id: str
    lat: float
    lon: float
    arrive: int | None
    leave: int | None
    available_s: int | None
    status: str
    outbound: Journey | None = None
    return_: Journey | None = None


@dataclass(frozen=True)
class WindowResult(_Json):
    origin: dict
    date: str
    leave_after: int
    back_by: int
    windows: tuple
    coverage: Coverage
    warnings: tuple = ()


@dataclass(frozen=True)
class StopWindow(_Json):
    stop_id: str
    name: str
    lat: float
    lon: float
    arrive: int | None
    leave: int | None
    available_s: int | None
