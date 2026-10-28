"""OSM candidate extraction (PlacesLayer.md 2.2, 4.3): Overpass -> cache/osm_places.json -> build/candidates.csv."""
from __future__ import annotations

import csv
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import load_config

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "YourDayTrip-places/0.1 (palexandre09@gmail.com; research prototype)"

SELECTORS = [
    '["amenity"~"^(restaurant|cafe|bar|pub|ice_cream)$"]',
    '["shop"="pastry"]',
    '["historic"]',
    '["leisure"~"^(park|nature_reserve|marina|garden)$"]',
    '["tourism"~"^(gallery|artwork|attraction|museum|viewpoint)$"]',
    '["natural"="beach"]',
]


def query(municipality: str, bbox) -> str:
    s, w, n, e = bbox
    body = "".join(f"  nwr(area.a)({s},{w},{n},{e})[\"name\"]{sel};\n" for sel in SELECTORS)
    return (f'[out:json][timeout:240];\n'
            f'area["boundary"="administrative"]["admin_level"="7"]["name"="{municipality}"]->.a;\n'
            f'(\n{body});\nout center tags;')


def fetch(q: str, retries: int = 5) -> dict:
    data = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(OVERPASS, data=data, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 504) or attempt == retries - 1:
                raise
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def category(tags: dict) -> str:
    for k in ("amenity", "shop", "historic", "leisure", "tourism", "natural"):
        if k in tags:
            return f"{k}={tags[k]}"
    return ""


def run_extract(config_dir=None, cache_dir="places/cache", build_dir="places/build") -> int:
    cfg = load_config(config_dir)
    a = cfg.area
    bbox = (a["lat_min"], a["lon_min"], a["lat_max"], a["lon_max"])
    seen, elements = set(), []
    for muni in a["municipalities"]:
        res = fetch(query(muni, bbox))
        for el in res.get("elements", []):
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if lat is None:
                continue
            elements.append({"type": el["type"], "id": el["id"], "lat": lat, "lon": lon, "tags": el.get("tags", {}),
                             "municipality": muni})
        time.sleep(2)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(cache_dir) / "osm_places.json", "w", encoding="utf-8") as f:
        json.dump({"fetched": dt.date.today().isoformat(), "bbox": bbox, "source": OVERPASS,
                   "attribution": "© OpenStreetMap contributors (ODbL)", "elements": elements}, f,
                  ensure_ascii=False)
    write_candidates(elements, build_dir)
    return len(elements)


def write_candidates(elements, build_dir="places/build"):
    Path(build_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(build_dir) / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["osm", "name", "category", "lat", "lon", "municipality", "opening_hours", "website", "wikidata",
                    "cuisine"])
        for el in sorted(elements, key=lambda e: (e["municipality"], category(e["tags"]), e["tags"].get("name", ""))):
            t = el["tags"]
            w.writerow([f"{el['type']}/{el['id']}", t.get("name", ""), category(t), f"{el['lat']:.6f}",
                        f"{el['lon']:.6f}", el["municipality"], t.get("opening_hours", ""),
                        t.get("website") or t.get("contact:website", ""), t.get("wikidata", ""), t.get("cuisine", "")])
