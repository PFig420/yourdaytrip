"""Minimal GTFS reader: several feeds, ids prefixed with the feed id."""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field


def route_mode(route_type: int) -> tuple[str, int]:
    """Map a (possibly extended) GTFS route_type to (mode, basic GTFS type). Never rejects."""
    t = route_type
    if t in (0, 1, 2, 3, 4, 5, 6, 7, 11, 12):
        return {0: "tram", 1: "subway", 2: "rail", 3: "bus", 4: "ferry"}.get(t, "other"), t
    if 100 <= t <= 199:
        return "rail", 2
    if 200 <= t <= 299:
        return "coach", 3
    if 400 <= t <= 499:
        return "subway", 1
    if 700 <= t <= 799 or 800 <= t <= 899:
        return "bus", 3
    if 900 <= t <= 999:
        return "tram", 0
    if 1000 <= t <= 1099:
        return "ferry", 4
    return "other", t


@dataclass
class Route:
    id: str
    short: str
    long: str
    mode: str
    type: int


@dataclass
class Trip:
    id: str
    route: str          # prefixed route id
    service: str        # prefixed service id
    headsign: str
    # (stop_idx_placeholder filled later) rows: (stop_id, arr, dep, pickup_ok, dropoff_ok)
    times: list = field(default_factory=list)


@dataclass
class Feed:
    id: str
    path: str
    sha256: str
    stops: dict          # prefixed id -> (name, lat, lon)
    routes: dict         # prefixed id -> Route
    trips: dict          # prefixed id -> Trip
    calendar: dict       # service -> (weekday flags[7], start 'YYYYMMDD', end)
    added: dict          # service -> set of 'YYYYMMDD'
    removed: dict
    first_date: str
    last_date: str


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(z: zipfile.ZipFile, name: str):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def parse_gtfs_time(s: str):
    s = (s or "").strip()
    if not s:
        return None
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def read_feed(feed_id: str, path: str) -> Feed:
    p = feed_id + ":"
    with zipfile.ZipFile(path) as z:
        stops = {}
        for r in _rows(z, "stops.txt"):
            if r.get("location_type", "") not in ("", "0"):
                continue
            stops[p + r["stop_id"]] = (r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"]))
        routes = {}
        for r in _rows(z, "routes.txt"):
            mode, basic = route_mode(int(r["route_type"]))
            routes[p + r["route_id"]] = Route(p + r["route_id"], r.get("route_short_name", ""),
                                              r.get("route_long_name", ""), mode, basic)
        trips = {}
        for r in _rows(z, "trips.txt"):
            trips[p + r["trip_id"]] = Trip(p + r["trip_id"], p + r["route_id"], p + r["service_id"],
                                           r.get("trip_headsign", "") or "")
        raw = {}
        for r in _rows(z, "stop_times.txt"):
            arr = parse_gtfs_time(r["arrival_time"])
            dep = parse_gtfs_time(r["departure_time"])
            if arr is None:
                arr = dep
            if dep is None:
                dep = arr
            raw.setdefault(p + r["trip_id"], []).append((
                int(r["stop_sequence"]), p + r["stop_id"], arr, dep,
                (r.get("pickup_type") or "0").strip() != "1",
                (r.get("drop_off_type") or "0").strip() != "1"))
        for tid, rows in raw.items():
            if tid in trips:
                rows.sort(key=lambda x: x[0])
                trips[tid].times = [x[1:] for x in rows]
        calendar = {}
        for r in _rows(z, "calendar.txt"):
            days = [r[d] == "1" for d in ("monday", "tuesday", "wednesday", "thursday", "friday",
                                           "saturday", "sunday")]
            calendar[p + r["service_id"]] = (days, r["start_date"], r["end_date"])
        added, removed = {}, {}
        for r in _rows(z, "calendar_dates.txt"):
            tgt = added if r["exception_type"] == "1" else removed
            tgt.setdefault(p + r["service_id"], set()).add(r["date"])
    dates = [c[1] for c in calendar.values()] + [c[2] for c in calendar.values()]
    for s in added.values():
        dates.extend(s)
    first = min(dates) if dates else ""
    last = max(dates) if dates else ""
    return Feed(feed_id, path, sha256_file(path), stops, routes, trips, calendar, added, removed, first, last)
