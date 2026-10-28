"""Download the coordinate sources into cache/ (spec 2.4)."""
from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

from .load import sha256

BUSWAY_URL = "https://gtfs.itbusway.eu/GTFS_CIRA.zip"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
UA = "YourDayTrip-gtfsconv/0.1 (internal, non-commercial)"
QUERY = (
    '[out:json][timeout:120];('
    'node["highway"="bus_stop"]["name"](40.05,-8.95,41.00,-8.20);'
    'node["public_transport"="platform"]["name"](40.05,-8.95,41.00,-8.20);'
    'node["amenity"="ferry_terminal"](40.05,-8.95,41.00,-8.20);'
    'node["place"~"^(city|town|village|hamlet|suburb)$"]["name"](40.05,-8.95,41.00,-8.20););out body;'
)


def cache_ready(cache: Path) -> bool:
    return (cache / "GTFS_CIRA.zip").exists() and (cache / "overpass.json").exists()


def fetch(cache: Path, force: bool = True) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    info = {}
    z = cache / "GTFS_CIRA.zip"
    if force or not z.exists():
        req = urllib.request.Request(BUSWAY_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            z.write_bytes(r.read())
    info["busway_sha256"] = sha256(z)
    o = cache / "overpass.json"
    if force or not o.exists():
        data = urllib.parse.urlencode({"data": QUERY}).encode()
        req = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=240) as r:
            o.write_bytes(r.read())
    info["overpass_sha256"] = sha256(o)
    return info
