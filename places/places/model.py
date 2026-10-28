"""Place records: one TOML file per place (PlacesLayer.md 4.1)."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Provenance:
    sources: list = field(default_factory=list)
    hours_source: str = ""
    checked: date | None = None
    drafted_by: str = "agent"
    status: str = "draft"
    estimates: list = field(default_factory=list)
    status_reason: str = ""


@dataclass
class Place:
    id: str
    name: str
    kind: str
    category: str
    tags: list
    municipality: str
    lat: float
    lon: float
    hours: str
    weather: str
    visit_min: int
    visit_typical: int
    price: str
    description: str
    priority: int
    provenance: Provenance
    osm: str = ""
    website: str = ""
    hours_note: str = ""
    last_entry_min: int = 0
    daylight_only: bool = False
    ticket_eur: float | None = None
    booking: str = "no"
    max_walk_m: int | None = None
    entry_times: str = ""
    cuisine: list = field(default_factory=list)
    meals: list = field(default_factory=list)
    points: list = field(default_factory=list)       # extra reference points of a long place: [{name, lat, lon, source}]

    def all_points(self) -> list[tuple[str, float, float]]:
        """[(label, lat, lon)], the record's own point first (label "main"), then each extra point by name."""
        return [("main", self.lat, self.lon)] + [(q["name"], q["lat"], q["lon"]) for q in self.points]

    @property
    def status(self) -> str:
        return self.provenance.status

    @property
    def is_food(self) -> bool:
        return self.kind in ("food", "drink")

    def summary(self) -> dict:
        d = {"id": self.id, "name": self.name, "kind": self.kind, "category": self.category,
             "tags": list(self.tags), "municipality": self.municipality, "lat": self.lat, "lon": self.lon,
             "website": self.website, "weather": self.weather, "price": self.price,
             "description": self.description, "priority": self.priority, "status": self.status,
             "hours": self.hours, "hours_note": self.hours_note, "entry_times": self.entry_times, "booking": self.booking}
        if self.ticket_eur is not None:
            d["ticket_eur"] = self.ticket_eur
        if self.is_food:
            d["cuisine"] = list(self.cuisine)
            d["meals"] = list(self.meals)
        if self.points:
            d["points"] = [dict(q) for q in self.points]
        if self.osm:
            d["osm"] = self.osm
            d["attribution"] = "© OpenStreetMap contributors"
        return d


REQUIRED = ("id", "name", "kind", "category", "tags", "municipality", "lat", "lon", "hours", "weather",
            "visit_min", "visit_typical", "price", "description", "priority")


class RecordError(ValueError):
    pass


def from_dict(d: dict) -> Place:
    missing = [k for k in REQUIRED if k not in d]
    prov = d.get("provenance")
    if not isinstance(prov, dict) or "status" not in prov:
        missing.append("provenance.status")
    if missing:
        raise RecordError("missing field(s): " + ", ".join(missing))
    pv = Provenance(list(prov.get("sources", [])), prov.get("hours_source", ""), prov.get("checked"),
                    prov.get("drafted_by", "agent"), prov["status"], list(prov.get("estimates", [])),
                    prov.get("status_reason", ""))
    try:
        return Place(
            id=str(d["id"]), name=str(d["name"]), kind=str(d["kind"]), category=str(d["category"]),
            tags=list(d["tags"]), municipality=str(d["municipality"]), lat=float(d["lat"]), lon=float(d["lon"]),
            hours=str(d["hours"]), weather=str(d["weather"]), visit_min=int(d["visit_min"]),
            visit_typical=int(d["visit_typical"]), price=str(d["price"]), description=str(d["description"]),
            priority=int(d["priority"]), provenance=pv, osm=str(d.get("osm", "")),
            website=str(d.get("website", "")), hours_note=str(d.get("hours_note", "")),
            last_entry_min=int(d.get("last_entry_min", 0)), daylight_only=bool(d.get("daylight_only", False)),
            ticket_eur=float(d["ticket_eur"]) if d.get("ticket_eur") is not None else None,
            booking=str(d.get("booking", "no")),
            max_walk_m=int(d["max_walk_m"]) if d.get("max_walk_m") is not None else None,
            entry_times=str(d.get("entry_times", "")), cuisine=list(d.get("cuisine", [])), meals=list(d.get("meals", [])),
            points=[{"name": str(q.get("name", "")), "lat": float(q["lat"]), "lon": float(q["lon"]),
                     "source": str(q.get("source", ""))} for q in d.get("points", [])])
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        raise RecordError(f"bad field type: {e}") from e


def read_place(path) -> Place:
    with open(path, "rb") as f:
        try:
            d = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RecordError(f"TOML does not parse: {e}") from e
    return from_dict(d)


def _s(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def _list(v) -> str:
    return "[" + ", ".join(_s(x) for x in v) + "]"


def dumps(p: Place) -> str:
    """Render a record from the fixed template (so diffs stay readable)."""
    desc = p.description.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    L = [f"id = {_s(p.id)}", f"name = {_s(p.name)}", f"kind = {_s(p.kind)}", f"category = {_s(p.category)}",
         f"tags = {_list(p.tags)}", f"municipality = {_s(p.municipality)}", f"lat = {p.lat:.6f}", f"lon = {p.lon:.6f}"]
    if p.osm:
        L.append(f"osm = {_s(p.osm)}")
    if p.website:
        L.append(f"website = {_s(p.website)}")
    L += ["", f"hours = {_s(p.hours)}"]
    if p.hours_note:
        L.append(f"hours_note = {_s(p.hours_note)}")
    if p.entry_times:
        L.append(f"entry_times = {_s(p.entry_times)}")
    L += [f"last_entry_min = {p.last_entry_min}", f"daylight_only = {'true' if p.daylight_only else 'false'}",
          f"weather = {_s(p.weather)}", "", f"visit_min = {p.visit_min}", f"visit_typical = {p.visit_typical}",
          f"price = {_s(p.price)}"]
    if p.ticket_eur is not None:
        L.append(f"ticket_eur = {p.ticket_eur}")
    L.append(f"booking = {_s(p.booking)}")
    if p.max_walk_m is not None:
        L.append(f"max_walk_m = {p.max_walk_m}")
    if p.is_food:
        L += ["", f"cuisine = {_list(p.cuisine)}", f"meals = {_list(p.meals)}"]
    pv = p.provenance
    L += ["", f'description = """{desc}"""', f"priority = {p.priority}", "", "[provenance]",
          f"sources = {_list(pv.sources)}", f"hours_source = {_s(pv.hours_source)}"]
    if pv.checked:
        L.append(f"checked = {pv.checked.isoformat()}")
    L += [f"drafted_by = {_s(pv.drafted_by)}", f"status = {_s(pv.status)}", f"estimates = {_list(pv.estimates)}"]
    if pv.status_reason:
        L.append(f"status_reason = {_s(pv.status_reason)}")
    for q in p.points:
        L += ["", "[[points]]", f"name = {_s(q['name'])}", f"lat = {q['lat']:.6f}", f"lon = {q['lon']:.6f}",
              f"source = {_s(q['source'])}"]
    L.append("")
    return "\n".join(L)


def write_place(p: Place, directory) -> Path:
    path = Path(directory) / f"{p.id}.toml"
    path.write_text(dumps(p), encoding="utf-8")
    return path
