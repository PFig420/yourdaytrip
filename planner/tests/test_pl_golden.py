"""Golden tests against the real engine and places (Planner.md 10.2, 10.3)."""
import json
import pathlib

import pytest

from conftest import GOLDEN, ROOT, draft, narrative, request
from planner import pack as packmod

try:
    import tomllib
    feeds = tomllib.loads((ROOT / "engine/config/feeds.toml").read_text())["feeds"]
except Exception:      # pragma: no cover
    feeds = []


def _skip_if_feeds_differ(pl):
    for f in pl.engine.feeds:
        want = next(x["sha256"] for x in feeds if x["id"] == f.id)
        if f.sha256 != want:
            pytest.skip(f"feed '{f.id}' differs from engine/config/feeds.toml")


def hm(s):
    return s[11:16]


def test_golden_scripted_draft(make, pl):
    _skip_if_feeds_differ(pl)
    p = make([draft(*GOLDEN), narrative(["museu-de-aveiro-santa-joana", "canastra-do-fidalgo-costa-nova", "costa-nova-palheiros"])])
    plan = p.plan(request())
    assert plan.status == "ok" and plan.source == "llm" and plan.attempts == 1
    tl = plan.timeline
    rides = [e for e in tl if e.type == "ride"]
    assert [r.trip_id for r in rides] == ["cp:15715_20260907", "av:bw-36-p20t2c6", "av:bw-36-p21t2c10", "cp:15637_20251214"]
    assert (hm(rides[0].board_time), hm(rides[0].alight_time)) == ("08:10", "09:07")
    assert (hm(rides[1].board_time), hm(rides[1].alight_time)) == ("11:34", "12:15")
    assert hm(rides[2].board_time) == "15:25" and hm(rides[3].board_time) == "16:23"
    visits = [e for e in tl if e.type == "visit"]
    assert [(hm(v.start), hm(v.end)) for v in visits] == [("10:00", "11:15"), ("12:19", "13:49"), ("13:49", "15:04")]
    assert visits[1].meal == "lunch"
    waits = [e for e in tl if e.type == "wait" and e.reason == "until it opens"]
    assert (hm(waits[0].from_), hm(waits[0].until)) == ("09:24", "10:00")
    # CurrentChanges.md P (2026-10-27): the seafront has reference points, and the first one reached is 20 m from Canastra,
    # so the 1.3 km stroll (13:49 -> 14:13, visit 14:13-15:28, home 18:20) is now a 20 m walk. Everything before is unchanged.
    i = tl.index(visits[1])
    assert tl[i + 1].type == "walk" and tl[i + 2] is visits[2] and tl[i + 1].metres < 50
    assert tl[i + 1].to.endswith("(south end of the seafront, on Avenida José Estêvão)")
    assert (hm(tl[i + 1].depart), hm(tl[i + 1].arrive)) == ("13:49", "13:49")
    assert hm(plan.home_by) == "17:20"
    s = plan.last_safe_return
    assert hm(s.depart) == "18:40" and s.first_vehicle["route_short"] == "36" and hm(s.first_vehicle["board_time"]) == "18:45"
    assert s.backup and s.backup["depart"] < s.depart
    assert len(plan.disclaimers) == 3 and plan.disclaimers[2] == "Map data © OpenStreetMap contributors."
    assert plan.narrative and plan.narrative["stop_lines"]


def test_every_time_comes_from_the_engine(make):
    """The plan holds no time the model could have chosen: visit starts are arrivals/openings, rides are trips."""
    p = make([draft(*GOLDEN), narrative([i for i, _ in GOLDEN])])
    plan = p.plan(request())
    for e in plan.timeline:
        if e.type == "ride":
            assert e.trip_id
    first = plan.usage.calls[0]
    assert first["label"] == "draft"


def test_fallback_golden(make):
    p = make([])
    a = p.plan(request(), use_llm=False)
    b = p.plan(request(), use_llm=False)
    assert a.status == "ok" and a.source == "fallback" and a.model is None and a.attempts == 0
    visits = [e for e in a.timeline if e.type == "visit"]
    assert 2 <= len(visits) <= 4
    assert any(v.meal == "lunch" for v in visits)
    ja, jb = a.to_json(), b.to_json()
    ja.pop("usage"), jb.pop("usage")           # latency differs between runs
    assert ja == jb
    assert any("without the language model" in w for w in a.warnings)
    assert p._client.calls == []


def test_fallback_packed_and_dinner(make):
    p = make([])
    plan = p.plan(request(origin="aveiro-station", a="10:00", b="18:00", pace="packed"), use_llm=False)
    assert plan.status == "ok"
    visits = [v for v in plan.timeline if v.type == "visit"]
    assert 2 <= len(visits) <= 6 and any(v.meal == "lunch" for v in visits)
    # a day that must include dinner either gets one or is refused; it is never shown unchecked
    plan = p.plan(request(origin="aveiro-station", a="10:00", b="22:30", pace="packed"), use_llm=False)
    if plan.status == "ok":
        assert {"lunch", "dinner"} <= {v.meal for v in plan.timeline if v.type == "visit"}
    else:
        assert plan.timeline == [] and plan.errors and plan.errors[0]["code"] == "meal_missing"


def test_fixed_entry_in_plan_wednesday(make, pl):
    req = request(origin="aveiro-station", date="2026-10-28", a="13:00", b="21:00", include_food=False)
    other = next(c["id"] for c in packmod.build(pl.lib, pl.engine, req, pl.cfg).candidates if c["id"] != "farol-da-barra")
    p = make([draft(("farol-da-barra", 45), (other, 40)), narrative(["farol-da-barra", other])])
    plan = p.plan(req)
    assert plan.status == "ok", plan.errors
    assert plan.status == "ok"
    v = [e for e in plan.timeline if e.type == "visit"][0]
    assert v.entry == "fixed" and hm(v.start) in {"14:00", "14:30", "15:00", "15:30", "16:30"}
