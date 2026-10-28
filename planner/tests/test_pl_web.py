from fastapi.testclient import TestClient

from conftest import GOLDEN, draft, narrative
from planner.web import create_app


def body(**kw):
    b = {"origin": {"id": "porto-campanha", "name": "Porto Campanhã", "lat": 41.14871926, "lon": -8.584835348},
         "date": "2026-10-24", "leave_after": "08:00", "back_by": "21:00"}
    b.update(kw)
    return b


def test_plan_and_health(make):
    p = make([draft(*GOLDEN), narrative([i for i, _ in GOLDEN])])
    c = TestClient(create_app(p))
    r = c.post("/plan", json=body())
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["source"] == "llm" and len(j["disclaimers"]) == 3
    assert {e["type"] for e in j["timeline"]} >= {"visit", "ride", "walk", "wait"}
    h = c.get("/health").json()
    assert h["model"] == "claude-sonnet-5-5" and h["confirmed_places"] > 50 and h["api_reachable"] is True


def test_debug_flag(make):
    g = [i for i, _ in GOLDEN]
    c = TestClient(create_app(make([draft(*GOLDEN, notes="No beach today."), narrative(g), draft(*GOLDEN, notes="No beach today."), narrative(g)])))
    assert c.post("/plan", json=body()).json()["debug"] is None
    j = c.post("/plan?debug=true", json=body()).json()
    assert j["debug"]["draft"]["notes"] == "No beach today." and j["debug"]["attempt_drafts"] == []
    f = c.post("/plan?use_llm=false&debug=true", json=body()).json()
    assert f["debug"]["draft"] is None


def test_plan_no_llm_and_validation(make):
    c = TestClient(create_app(make([])))
    assert c.post("/plan?use_llm=false", json=body()).json()["source"] == "fallback"
    assert c.post("/plan", json=body(back_by="07:00")).status_code == 422
    assert c.post("/plan?use_llm=false", json=body(interests=["nope"])).status_code == 422


def test_test_page_and_meta(make):
    c = TestClient(create_app(make([])))
    r = c.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "YourDayTrip planner test" in r.text and "/plan?use_llm=" in r.text and "debug=true" in r.text and "Model notes (testing)" in r.text
    m = c.get("/meta").json()
    assert m["backend"] == "api" and m["model"] == "claude-sonnet-5-5"
    assert {o["id"] for o in m["origins"]} == {"porto-campanha", "porto-sao-bento", "aveiro-station"}
    assert "history" in m["interests"] and m["interests"] == sorted(m["interests"])


def test_local_backend_is_served(pl):
    import planner
    p = planner.Planner(pl.engine, pl.lib, pl.cfg, accepted_legs=pl.accepted_legs, backend="local")
    m = TestClient(create_app(p)).get("/meta").json()
    assert m["backend"] == "local" and m["model"] == pl.cfg["local"]["model_label"]
