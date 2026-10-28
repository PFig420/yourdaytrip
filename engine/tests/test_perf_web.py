import time

import pytest
from fastapi.testclient import TestClient

from conftest import CAMPANHA, COSTA_NOVA, CONFIG
from reach.web import create_app

SAT = "2026-10-24"


def test_load_time_and_memory():
    import resource
    import reach
    t = time.perf_counter()
    reach.load(CONFIG)
    assert time.perf_counter() - t < 2 * 3.0
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 < 2 * 300      # MB


def test_scan_and_windows_speed(eng):
    eng.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA)          # builds the connection window
    t = time.perf_counter()
    eng.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA)
    eng.latest(COSTA_NOVA, SAT, CAMPANHA, "21:00")
    assert time.perf_counter() - t < 2 * 0.5
    import random
    rnd = random.Random(1)
    pts = []
    for i in range(200):
        s = rnd.randrange(len(eng.net.ids))
        pts.append((f"p{i}", eng.net.lat[s] + 0.001, eng.net.lon[s]))
    t = time.perf_counter()
    eng.windows(CAMPANHA, SAT, "08:00", "21:00", pts)
    assert time.perf_counter() - t < 2 * 1.0


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(CONFIG))


def test_health(client):
    r = client.get("/health").json()
    assert {f["id"] for f in r["feeds"]} == {"cp", "av"}


def test_coverage_endpoint(client):
    r = client.get("/coverage", params={"date": "2026-12-13"}).json()
    assert r["missing_feeds"] == ["cp"] and r["warnings"]
    assert client.get("/coverage", params={"date": "nonsense"}).status_code == 400


def test_journey_endpoint(client):
    p = {"from": "41.14872,-8.58484", "to": "40.6125,-8.74974", "date": SAT}
    r = client.get("/journey", params={**p, "after": "08:00"})
    assert r.status_code == 200 and r.json()["journey"]["arrive"]["local"].startswith("2026-10-24T10:20")
    r = client.get("/journey", params={"from": "40.6125,-8.74974", "to": "41.14872,-8.58484",
                                       "date": SAT, "back_by": "21:00"})
    assert r.status_code == 200 and r.json()["journey"]["depart"]["local"][11:16] == "18:45"
    assert client.get("/journey", params=p).status_code == 400
    assert client.get("/journey", params={**p, "after": "08:00", "back_by": "21:00"}).status_code == 400
    r = client.get("/journey", params={**p, "after": "08:00", "max_walk_m": 9999})
    assert r.status_code == 400 and r.json()["error"] == "bad_input"


def test_strict_coverage_422(client):
    p = {"from": "41.14872,-8.58484", "to": "40.6125,-8.74974", "date": "2026-12-19", "after": "08:00"}
    r = client.get("/journey", params=p)
    assert r.status_code == 200 and r.json()["journey"] is None and r.json()["warnings"]
    r = client.get("/journey", params={**p, "strict_coverage": "true"})
    assert r.status_code == 422 and r.json()["error"] == "strict_coverage"


def test_windows_endpoint(client):
    body = {"origin": {"lat": 41.14872, "lon": -8.58484}, "date": SAT, "leave_after": "08:00", "back_by": "21:00",
            "points": [{"id": "costa-nova", "lat": 40.6125, "lon": -8.74974}], "include_journeys": True}
    r = client.post("/windows", json=body)
    assert r.status_code == 200
    w = r.json()["windows"][0]
    assert w["status"] == "ok" and w["outbound"]["legs"] and w["return"]["legs"]
    assert client.post("/windows", json={"date": SAT}).status_code == 400


def test_stop_windows_and_map(client):
    r = client.get("/stops/windows", params={"from": "41.14872,-8.58484", "date": SAT, "after": "08:00",
                                              "back_by": "21:00"})
    assert r.status_code == 200
    feats = r.json()["features"]
    assert any(f["properties"]["available_s"] and f["properties"]["available_s"] > 6 * 3600 for f in feats)
    m = client.get("/map")
    assert m.status_code == 200 and "leaflet" in m.text.lower()
