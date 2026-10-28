"""Dataset validation (PlacesLayer.md 4.5) and the copy check (4.6)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, load_config
from .hours import HoursError, parse
from .model import Place, RecordError, read_place
from .text import words

COPY_RUN = 8


def source_texts(cache_dir, place_id: str) -> list[str]:
    """Cached source pages for a place; the first line (URL and fetch date) is skipped."""
    out = []
    d = Path(cache_dir) / "sources"
    for p in sorted(d.glob(f"{place_id}-*.txt")):
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)
        out.append(lines[1] if len(lines) > 1 else "")
    return out


def copy_violation(description: str, sources, run: int = COPY_RUN):
    """Return the first run of `run` or more consecutive words shared with a source text, else None."""
    dw = words(description)
    if len(dw) < run:
        return None
    grams = {tuple(dw[i:i + run]): i for i in range(len(dw) - run + 1)}
    for src in sources:
        sw = words(src)
        for i in range(len(sw) - run + 1):
            g = tuple(sw[i:i + run])
            if g in grams:
                return " ".join(g)
    return None


@dataclass
class Report:
    errors: list = field(default_factory=list)       # (id, message)
    warnings: list = field(default_factory=list)
    places: list = field(default_factory=list)

    def warn_for(self, pid):
        return [m for i, m in self.warnings if i == pid]


MAX_POINTS, POINT_MAX_M, POINT_MIN_M = 6, 3000, 150


def check_points(p: Place, cfg: Config) -> list[str]:
    """Extra reference points of a long place (PlacesLayer.md 4.1): few, named, inside the area, near and apart."""
    from .network import distance_m
    a, e = cfg.area, []
    if len(p.points) > MAX_POINTS:
        e.append(f"{len(p.points)} extra points (max {MAX_POINTS})")
    pts = p.all_points()
    for i, (name, lat, lon) in enumerate(pts[1:], start=1):
        if not name.strip():
            e.append(f"point {i} has no name")
        if not p.points[i - 1]["source"].strip():
            e.append(f"point {i} ({name}) has no source")
        if not (a["lat_min"] <= lat <= a["lat_max"] and a["lon_min"] <= lon <= a["lon_max"]):
            e.append(f"point {i} ({name}) is outside the area box")
        d = distance_m(p.lat, p.lon, lat, lon)
        if d > POINT_MAX_M:
            e.append(f"point {i} ({name}) is {d:.0f} m from the main point (max {POINT_MAX_M})")
    for i, x in enumerate(pts):
        for j in range(i + 1, len(pts)):
            d = distance_m(x[1], x[2], pts[j][1], pts[j][2])
            if d < POINT_MIN_M:
                e.append(f"points {x[0]!r} and {pts[j][0]!r} are {d:.0f} m apart (min {POINT_MIN_M})")
    return e


def check_place(p: Place, cfg: Config, cache_dir=None) -> list[str]:
    """Errors for one record."""
    v, a = cfg.vocab, cfg.area
    e = []
    if p.kind not in v["kinds"]:
        e.append(f"kind {p.kind!r} not allowed")
    cats = v["food_categories"] if p.kind in ("food", "drink") else v["sight_categories"]
    if p.category not in v["sight_categories"] + v["food_categories"]:
        e.append(f"category {p.category!r} not in the vocabulary")
    elif p.category not in cats:
        e.append(f"category {p.category!r} does not suit kind {p.kind!r}")
    for t in p.tags:
        if t not in v["tags"]:
            e.append(f"tag {t!r} not in the vocabulary")
    for c in p.cuisine:
        if c not in v["cuisine"]:
            e.append(f"cuisine {c!r} not in the vocabulary")
    for m in p.meals:
        if m not in v["meals"]:
            e.append(f"meal {m!r} not allowed")
    if p.weather not in v["weather"]:
        e.append(f"weather {p.weather!r} not allowed")
    if p.price not in v["prices"]:
        e.append(f"price {p.price!r} not allowed")
    if p.booking not in v["bookings"]:
        e.append(f"booking {p.booking!r} not allowed")
    if p.status not in v["statuses"]:
        e.append(f"status {p.status!r} not allowed")
    if p.municipality not in a["municipalities"]:
        e.append(f"municipality {p.municipality!r} is outside the area")
    if not (a["lat_min"] <= p.lat <= a["lat_max"] and a["lon_min"] <= p.lon <= a["lon_max"]):
        e.append(f"coordinates {p.lat}, {p.lon} are outside the area box")
    if p.hours.strip():
        try:
            parse(p.hours)
        except HoursError as ex:
            e.append(f"hours do not parse: {ex}")
    elif p.status != "draft":
        e.append("empty hours are allowed only for drafts")
    if p.entry_times.strip():
        try:
            parse(p.entry_times, points=True)
        except HoursError as ex:
            e.append(f"entry_times do not parse: {ex}")
        if not p.hours.strip():
            e.append("entry_times need opening hours as well")
    if p.visit_min > p.visit_typical:
        e.append("visit_min is greater than visit_typical")
    if p.visit_min < 10:
        e.append("visit_min is below 10 minutes")
    if p.max_walk_m is not None and not 0 < p.max_walk_m <= 2000:
        e.append("max_walk_m must be between 1 and 2000")
    if p.status == "rejected" and not p.provenance.status_reason:
        e.append("rejected records need provenance.status_reason")
    if p.priority not in (1, 2, 3):
        e.append("priority must be 1, 2 or 3")
    n = len(p.description.split())
    if n == 0:
        e.append("description is empty")
    elif n > 60:
        e.append(f"description has {n} words (max 60)")
    elif cache_dir is not None:
        bad = copy_violation(p.description, source_texts(cache_dir, p.id))
        if bad:
            e.append(f"description copies {COPY_RUN}+ consecutive words from a source: {bad!r}")
    e += check_points(p, cfg)
    if p.status == "confirmed":
        if not p.hours.strip():
            e.append("confirmed but hours are empty")
        if p.provenance.checked is None:
            e.append("confirmed but provenance.checked is missing")
    if p.is_food is False and (p.cuisine or p.meals):
        e.append("cuisine and meals are for food and drink only")
    return e


def validate(data_dir="places/data", config_dir=None, cache_dir=None, engine=None,
             today: dt.date | None = None) -> Report:
    from .network import distance_m, nearest_stop_m

    cfg = load_config(config_dir)
    data_dir = Path(data_dir)
    cache_dir = cache_dir if cache_dir is not None else data_dir.parent / "cache"
    today = today or dt.date.today()
    rep = Report()
    seen = []
    for path in sorted((data_dir / "places").glob("*.toml")):
        try:
            p = read_place(path)
        except RecordError as ex:
            rep.errors.append((path.stem, str(ex)))
            continue
        if p.id != path.stem:
            rep.errors.append((path.stem, f"id {p.id!r} does not equal the file name"))
        for msg in check_place(p, cfg, cache_dir):
            rep.errors.append((p.id, msg))
        rep.places.append(p)
        if engine is not None:
            ms = [nearest_stop_m(engine, la, lo) for _, la, lo in p.all_points()]      # the nearest stop to any point
            m = min((x for x in ms if x is not None), default=None)
            limit = p.max_walk_m or 800
            if p.status != "rejected" and m is not None and m > limit:
                rep.warnings.append((p.id, f"{m:.0f} m from the nearest stop"
                                     + (f" (beyond its max_walk_m {p.max_walk_m})" if p.max_walk_m else " (long walk)")))
        chk = p.provenance.checked
        if chk is not None and (today - chk).days > cfg.stale_days:
            rep.warnings.append((p.id, f"hours checked {chk} ({(today - chk).days} days ago)"))
        if p.status == "draft":
            rep.warnings.append((p.id, "draft"))
        if p.is_food and not p.meals:
            rep.warnings.append((p.id, "food place has no meals"))
        seen.append(p)
    for i, a in enumerate(seen):
        for b in seen[i + 1:]:
            if distance_m(a.lat, a.lon, b.lat, b.lon) < 20:
                rep.warnings.append((a.id, f"entrance less than 20 m from {b.id}"))
                rep.warnings.append((b.id, f"entrance less than 20 m from {a.id}"))
    return rep
