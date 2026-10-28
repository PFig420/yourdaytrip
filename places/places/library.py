"""The Library: loads the dataset, the config and the engine; answers search, lookup and shortlist calls."""
from __future__ import annotations

import json
from pathlib import Path

from .config import load_config
from .model import Place, RecordError, read_place
from .network import distance_m
from .retrieval import BM25, doc_text
from .text import fold

FOOD_AMENITIES = ("restaurant", "cafe", "bar", "pub", "ice_cream", "fast_food", "biergarten")
ATTRIBUTION = "© OpenStreetMap contributors"


class Library:
    def __init__(self, places: list[Place], cfg, engine=None, cache_dir=None):
        self.places = {p.id: p for p in places}
        self.cfg = cfg
        self.engine = engine
        self.cache_dir = Path(cache_dir) if cache_dir else None
        live = [p for p in places if p.status != "rejected"]
        self.index = BM25({p.id: doc_text(p) for p in live})
        self._osm = None

    # ---- lookups -------------------------------------------------------
    def get(self, place_id: str) -> Place:
        try:
            return self.places[place_id]
        except KeyError:
            raise KeyError(f"unknown place {place_id!r}") from None

    def search(self, text: str, k: int = 10) -> list[tuple[str, float]]:
        return self.index.search(text, k)

    # ---- unverified food fallback (5.8) --------------------------------
    def _osm_elements(self):
        if self._osm is None:
            self._osm = []
            if self.cache_dir and (self.cache_dir / "osm_places.json").exists():
                with open(self.cache_dir / "osm_places.json", encoding="utf-8") as f:
                    self._osm = json.load(f).get("elements", [])
        return self._osm

    def food_near(self, lat: float, lon: float, radius_m: float = 400) -> list[dict]:
        curated_osm = {p.osm for p in self.places.values() if p.osm}
        curated = [(fold(p.name), p.lat, p.lon) for p in self.places.values() if p.is_food]
        out = []
        for el in self._osm_elements():
            tags = el.get("tags", {})
            if tags.get("amenity") not in FOOD_AMENITIES or not tags.get("name"):
                continue
            ref = f"{el['type']}/{el['id']}"
            if ref in curated_osm:
                continue
            d = distance_m(lat, lon, el["lat"], el["lon"])
            if d > radius_m:
                continue
            if any(n == fold(tags["name"]) and distance_m(la, lo, el["lat"], el["lon"]) < 50 for n, la, lo in curated):
                continue
            out.append({"name": tags["name"], "amenity": tags["amenity"], "cuisine": tags.get("cuisine", ""),
                        "lat": el["lat"], "lon": el["lon"], "distance_m": round(d), "osm": ref,
                        "opening_hours": tags.get("opening_hours", ""), "website": tags.get("website", ""),
                        "verified": False, "note": "From OpenStreetMap; hours are not guaranteed.",
                        "attribution": ATTRIBUTION})
        out.sort(key=lambda r: (r["distance_m"], r["osm"]))
        return out

    # ---- shortlist (5.4 - 5.6) -----------------------------------------
    def shortlist(self, origin, date, leave_after, back_by, **kw):
        from .shortlist import build
        return build(self, origin, date, leave_after, back_by, **kw)


def load(data_dir="places/data", engine=None, config_dir=None, cache_dir=None) -> Library:
    data_dir = Path(data_dir)
    cfg = load_config(config_dir)
    places = []
    for path in sorted((data_dir / "places").glob("*.toml")):
        try:
            places.append(read_place(path))
        except RecordError as e:
            raise RecordError(f"{path.name}: {e}") from e
    return Library(places, cfg, engine, cache_dir if cache_dir else data_dir.parent / "cache")
