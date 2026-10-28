"""Agencies, routes, trips, directions, headsigns (spec 5.6)."""
from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path

from .load import BuildError
from .names import strip_side


def route_id(trip) -> str:
    if trip.mode == "bus":
        return f"bw-{trip.line}"
    if trip.mode == "ferry":
        return "ab-ferry"
    if trip.line.lower() in ("azul", "verde"):
        return f"ab-15-{trip.line.lower()}"
    return f"ab-{trip.line}"


def agency_of(trip) -> str:
    return "busway" if trip.mode == "bus" else "aveirobus"


def short_name(trip) -> str:
    if trip.mode == "ferry":
        return "Ferry"
    if trip.mode == "bus_urban" and trip.line.lower() in ("azul", "verde"):
        return f"15 {trip.line.capitalize()}"
    return trip.line


def load_routes(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def assign_directions(trips) -> None:
    """Set trip.direction_id (spec 5.6). Uses first/last stops after name clean-up."""
    by_route: dict[str, list] = {}
    for t in trips:
        by_route.setdefault(route_id(t), []).append(t)
    for rid, ts in by_route.items():
        ref = None
        if ts[0].mode != "bus" or "<->" in ts[0].route:
            groups: dict[str, list] = {}
            for t in ts:
                groups.setdefault(t.direction, []).append(t)
            first_group = next(iter(groups.values()))
            ref = Counter(t.stops[0].name for t in first_group).most_common(1)[0][0]
        for t in ts:
            if t.direction.strip().lower() == "circular":
                t.direction_id = 0
            elif ts[0].mode == "bus" and "<->" not in t.route:
                t.direction_id = 0 if t.route.strip().upper().startswith("AVEIRO") else 1
            else:
                t.direction_id = 0 if t.stops[0].name == ref else 1


def headsign(trip) -> str:
    return strip_side(trip.stops[-1].name)


def build_routes(trips, routes_cfg: dict, agencies_cfg: dict):
    agencies = []
    for aid in ("busway", "aveirobus"):
        a = agencies_cfg[aid]
        agencies.append({"agency_id": aid, "agency_name": a["name"], "agency_url": a["url"],
                         "agency_timezone": a["timezone"], "agency_lang": a["lang"]})
    routes = {}
    for t in trips:
        rid = route_id(t)
        if rid in routes:
            continue
        if rid not in routes_cfg:
            raise BuildError(f"config/routes.toml has no entry for route {rid}")
        c = routes_cfg[rid]
        routes[rid] = {
            "route_id": rid, "agency_id": agency_of(t), "route_short_name": short_name(t),
            "route_long_name": c["long_name"], "route_desc": c.get("desc", ""),
            "route_type": 4 if t.mode == "ferry" else 3,
            "route_color": c.get("color", ""), "route_text_color": c.get("text_color", ""),
        }
    return agencies, [routes[k] for k in sorted(routes)]
