import csv
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from conftest import CAMPANHA, FIXTURES, PLACES
from places.holidays import Holidays, easter
from places.model import dumps, read_place
from places.review import apply_review, write_review
from places.validate import validate

D = dt.date


def test_municipal_holidays():
    h = Holidays.load(PLACES / "config" / "holidays.toml")
    assert h.is_holiday(D(2026, 5, 12), "Aveiro") and not h.is_holiday(D(2026, 5, 12), "Ovar")
    assert h.is_holiday(D(2026, 4, 6), "Ílhavo")              # Easter Monday
    assert h.is_holiday(D(2026, 5, 25), "Vagos") and h.is_holiday(D(2026, 5, 25), "Águeda")
    assert h.is_holiday(D(2026, 12, 8), "Mira")               # national
    assert not h.is_holiday(D(2026, 10, 24), "Mira")
    assert easter(2026) == D(2026, 4, 5)


def test_record_round_trip():
    p = read_place(FIXTURES / "places" / "p6-costa-nova-lunch.toml")
    q = read_place_from_text(dumps(p))
    assert q == p


def read_place_from_text(text):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(text)
    return read_place(f.name)


def test_search_and_get(fixture_lib):
    assert fixture_lib.search("maritime", 3)
    assert fixture_lib.get("p1-costa-nova-sa").name.startswith("P1")
    with pytest.raises(KeyError):
        fixture_lib.get("nope")


def test_food_near_marks_unverified(real_lib):
    r = real_lib.food_near(40.6125, -8.74974, 500)
    assert r and all(x["verified"] is False and "OpenStreetMap" in x["attribution"] for x in r)
    assert r == sorted(r, key=lambda x: x["distance_m"])
    curated = {p.osm for p in real_lib.places.values() if p.osm}
    assert not any(x["osm"] in curated for x in r)


def test_review_sheet_and_apply(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(PLACES / "data", data)
    shutil.copytree(PLACES / "cache", tmp_path / "cache")
    out = tmp_path / "review.csv"
    n, rep = write_review(data, out, config_dir=PLACES / "config")
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert n == len(rows) == len(rep.places)
    assert set(rows[0]) >= {"id", "status", "hours", "hours_source", "visit_min", "description", "warnings"}
    # edit one row: confirm and set priority; and one bad row (confirmed with empty hours)
    good = next(r for r in rows if r["id"] == "museu-maritimo-ilhavo")
    bad = next(r for r in rows if not r["hours"].strip() and r["status"] != "rejected")
    # change something that differs whatever the current review state is
    good["status"] = "confirmed"
    good["priority"] = "3" if good["priority"].strip() != "3" else "2"
    new_priority = int(good["priority"])
    bad["status"] = "confirmed"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    changed, problems = apply_review(out, data, PLACES / "config")
    assert changed == ["museu-maritimo-ilhavo"]
    assert [i for i, _ in problems] == [bad["id"]]
    after = read_place(data / "places" / "museu-maritimo-ilhavo.toml")
    assert after.status == "confirmed" and after.priority == new_priority
    assert validate(data, PLACES / "config").errors == []


def test_stale_review_filter(tmp_path):
    out = tmp_path / "r.csv"
    n, _ = write_review(PLACES / "data", out, config_dir=PLACES / "config", stale_days=90, today=D(2027, 6, 1))
    assert n > 0
    n2, _ = write_review(PLACES / "data", out, config_dir=PLACES / "config", stale_days=90, today=D(2026, 10, 21))
    assert n2 == sum(1 for p in read_all() if p.provenance.checked is None)     # never-checked records always listed


def test_extract_candidates_csv(tmp_path, monkeypatch):
    from places import extract
    calls = []

    def fake_fetch(q, retries=5):
        calls.append(q)
        return {"elements": [{"type": "node", "id": 1, "lat": 40.6, "lon": -8.7,
                              "tags": {"name": "Café X", "amenity": "cafe", "opening_hours": "Mo-Su 08:00-20:00"}}]}

    monkeypatch.setattr(extract, "fetch", fake_fetch)
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    n = extract.run_extract(PLACES / "config", tmp_path / "cache", tmp_path / "build")
    assert n == 1 and len(calls) == 8                      # one query per municipality, deduplicated
    assert json.load(open(tmp_path / "cache" / "osm_places.json"))["elements"][0]["municipality"] == "Aveiro"
    rows = list(csv.DictReader(open(tmp_path / "build" / "candidates.csv", encoding="utf-8")))
    assert rows[0]["name"] == "Café X" and rows[0]["category"] == "amenity=cafe"


def test_web_api(real_lib):
    from fastapi.testclient import TestClient
    from places.web import create_app
    c = TestClient(create_app(real_lib))
    assert c.get("/health").json()["ok"]
    r = c.post("/shortlist", json={"origin": [41.14872, -8.58484], "date": "2026-10-24", "leave_after": "08:00",
                                   "back_by": "21:00", "include_drafts": True, "interests": ["beach"], "limit": 5})
    assert r.status_code == 200 and r.json()["sights"] and "attribution" in r.json()
    assert c.get("/search", params={"q": "azulejos"}).json()
    assert c.get("/places/museu-maritimo-ilhavo").json()["name"] == "Museu Marítimo de Ílhavo"
    assert c.get("/places/zzz").status_code == 404
    assert c.get("/food-near", params={"lat": 40.6125, "lon": -8.74974}).status_code == 200
    bad = c.post("/shortlist", json={"origin": [41.1, -8.5], "date": "2026-10-24", "leave_after": "21:00", "back_by": "08:00"})
    assert bad.status_code == 400


def read_all():
    return [read_place(f) for f in sorted((PLACES / "data" / "places").glob("*.toml"))]


def test_shortlist_leave_by_caps_each_place(real_lib):
    """`leave_by` caps every place's leave before the slots are computed; None changes nothing."""
    from conftest import CAMPANHA
    from places.shortlist import wall
    import datetime as dt
    day = dt.date(2026, 10, 24)
    base = real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "23:59", limit=50)
    assert real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "23:59", limit=50, leave_by=None).to_json() == base.to_json()
    cap = real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "23:59", limit=50, leave_by="15:00")
    lim = wall(day, 15 * 3600)
    items = cap.sights + cap.food
    assert items and all(i.leave <= lim and all(s.end <= lim for s in i.slots) for i in items)
    assert any(i.leave > lim for i in base.sights + base.food)               # the cap bites
    assert {i.place["id"] for i in items} <= {i.place["id"] for i in base.sights + base.food}


def test_shortlist_uses_the_reference_point_reached_first(real_lib):
    """Palheiros seafront has extra points; from Canastra do Fidalgo the first one is 20 m away, the main point 1.3 km."""
    from places.shortlist import _engine_points
    canastra = ("canastra", 40.610345, -8.749324)
    sl = real_lib.shortlist(canastra, "2026-10-31", "14:00", "20:00", limit=60)
    item = next(i for i in sl.sights if i.place["id"] == "costa-nova-palheiros")
    assert item.point and item.point[0].startswith("south end of the seafront")
    assert item.to_json()["point"]["name"] == item.point[0] and item.place["points"]
    main = real_lib.engine.windows(canastra, "2026-10-31", "14:00", "20:00", [("costa-nova-palheiros", 40.62212, -8.74999)]).windows[0]
    assert item.arrive < main.arrive                                  # the nearer point gives the earlier arrival
    plain = next(i for i in sl.sights + sl.food if i.place["id"] == "se-de-aveiro" or not real_lib.places[i.place["id"]].points)
    assert plain.point is None and "point" not in plain.to_json() and "points" not in plain.place
    pts = _engine_points([real_lib.places["costa-nova-palheiros"]])
    assert [p[0] for p in pts][:2] == ["costa-nova-palheiros", "costa-nova-palheiros#1"]


def test_clusters_use_the_closest_pair_of_points(real_lib):
    """A Praia do Tubarão (restaurant, 78 m from Canastra) and the seafront are 1.3 km apart by main points, but the
    seafront's south point is next to the restaurant, so they share a cluster."""
    sl = real_lib.shortlist(("canastra", 40.610345, -8.749324), "2026-10-31", "10:00", "23:00", limit=60)
    c = {i.place["id"]: i.cluster for i in sl.sights + sl.food}
    assert c["costa-nova-palheiros"] == c["a-praia-do-tubarao-ilhavo"]
