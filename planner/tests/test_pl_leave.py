"""'Back home by' or 'Leave by' (Changes.md E): in leave mode `back_by` is the time the first vehicle home must leave the destination by."""
import copy
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from conftest import GOLDEN, draft, narrative, request
from planner import pack as packmod
from planner.cli import main
from planner.models import PlanRequest
from planner.schedule import Scheduler, required_meals
from planner.web import create_app

FAR = {"origin": {"id": "porto-campanha", "name": "Porto Campanhã", "lat": 41.14871926, "lon": -8.584835348},
       "date": "2026-10-24", "leave_after": "08:00", "back_by": "17:30", "deadline": "leave"}


def codes(res):
    return [e.code for e in res.errors]


# ---- the request --------------------------------------------------------------------------------------------------
def test_deadline_defaults_to_home_and_is_validated():
    assert request().deadline == "home"
    assert request(b="17:30", deadline="leave").deadline == "leave"
    with pytest.raises(ValueError):
        request(deadline="sometime")
    with pytest.raises(ValueError):
        request(a="12:00", b="11:00", deadline="leave")                  # the leave-by time must still follow leave_after


# ---- home mode is untouched -----------------------------------------------------------------------------------------
def test_home_mode_pack_has_no_deadline_and_asks_the_shortlist_as_before(pl, monkeypatch):
    seen = {}
    real = pl.lib.shortlist

    def spy(origin, date, leave_after, back_by, **kw):
        seen.update(back_by=back_by, leave_by=kw.get("leave_by"))
        return real(origin, date, leave_after, back_by, **kw)
    monkeypatch.setattr(pl.lib, "shortlist", spy)
    req = request()
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    assert "deadline" not in pk.request and "deadline" not in pk.dumps()
    assert seen == {"back_by": "21:00", "leave_by": None}
    same = packmod.build(pl.lib, pl.engine, request(deadline="home"), pl.cfg)
    assert same.dumps() == pk.dumps()


def test_home_mode_golden_still_has_no_errors(pl):
    res = pl.sched.schedule(request(), GOLDEN)
    assert res.errors == [] and res.home_arrive is not None


# ---- the pack in leave mode -------------------------------------------------------------------------------------------
def test_every_candidate_leaves_by_the_leave_by_time(pl):
    home = packmod.build(pl.lib, pl.engine, request(b="17:30"), pl.cfg)
    leave = packmod.build(pl.lib, pl.engine, request(b="17:30", deadline="leave"), pl.cfg)
    assert leave.request["deadline"] == "leave" and leave.request["back_by"] == "17:30"
    assert leave.candidates
    for c in leave.candidates:
        assert c["leave_by"] <= "17:30"
        for s in c.get("slots", []):
            assert s["to"] <= "17:30" and s["enter_by"] <= "17:30"
        assert all(t < "17:30" for t in c.get("fixed_entry_times", []))
    # a request for home by 17:30 is stricter than a request to leave by 17:30
    assert len(leave.candidates) >= len(home.candidates)
    wide = packmod.build(pl.lib, pl.engine, request(b="21:00"), pl.cfg)
    assert any(c["leave_by"] > "17:30" for c in wide.candidates)         # so the cap does bite


def test_leave_mode_looks_for_a_way_home_up_to_the_configured_time(pl, monkeypatch):
    seen = {}
    real = pl.lib.shortlist
    monkeypatch.setattr(pl.lib, "shortlist", lambda o, d, a, b, **kw: (seen.update(end=b, leave_by=kw["leave_by"]), real(o, d, a, b, **kw))[1])
    packmod.build(pl.lib, pl.engine, request(b="17:30", deadline="leave"), pl.cfg)
    assert seen == {"end": "23:59", "leave_by": "17:30"}


# ---- the validator ------------------------------------------------------------------------------------------------------
def test_a_first_vehicle_after_the_leave_by_time_is_left_too_late(pl):
    res = pl.sched.schedule(request(b="15:00", deadline="leave"), GOLDEN)          # the visit ends 15:04, the bus leaves 15:25
    assert codes(res) == ["left_too_late"]
    e = res.errors[0]
    assert e.place_id == "costa-nova-palheiros" and "ends at 15:04" in e.message and "leaves 15:25" in e.message
    assert "15:00 leave-by time" in e.message and e.times == {"end": "15:04", "leaves": "15:25", "leave_by": "15:00"}


def test_the_walk_to_the_stop_counts_before_the_leave_by_time(pl):
    # the visit ends 15:04 and the stop is a short walk away, but the bus is at 15:25: 15:25 is the time that is judged
    assert codes(pl.sched.schedule(request(b="15:24", deadline="leave"), GOLDEN)) == ["left_too_late"]
    assert codes(pl.sched.schedule(request(b="15:25", deadline="leave"), GOLDEN)) == []


def test_the_plan_may_get_home_after_the_leave_by_time(pl):
    req = request(b="15:30", deadline="leave")
    res = pl.sched.schedule(req, GOLDEN)
    assert res.errors == []
    assert pl.sched._leaves_at(res.journeys[-1][0]) <= pl.sched.deadlines(req, dt.date(2026, 10, 24))[1]
    last_ride = [e for e in res.timeline if e.type == "ride"][-1]                      # (a 4 m walk to the origin follows it since WalkBarriers.md)
    assert last_ride.alight_time[11:16] == "17:20"                                     # home 17:20, after 15:30


def test_but_not_after_leave_mode_home_by(pl):
    cfg = copy.deepcopy(pl.cfg)
    cfg["schedule"]["leave_mode_home_by"] = "17:00"
    sched = Scheduler(pl.engine, pl.lib, cfg, pl.router)
    res = sched.schedule(request(b="15:30", deadline="leave"), GOLDEN)
    assert codes(res) == ["home_too_late"] and "17:00 limit" in res.errors[0].message
    assert codes(pl.sched.schedule(request(b="15:30", deadline="leave"), GOLDEN)) == []      # 23:59 by default


def test_home_mode_is_still_judged_on_the_arrival_home(pl):
    assert codes(pl.sched.schedule(request(b="15:30"), GOLDEN)) == ["home_too_late"]          # the same plan: home 17:20


def test_meals_use_the_leave_by_time(pl):
    cfg = pl.lib.cfg.meals
    assert required_meals(request(a="08:00", b="18:00", deadline="leave"), cfg) == ["lunch"]
    assert required_meals(request(a="08:00", b="22:30"), cfg) == ["lunch", "dinner"]
    assert required_meals(request(a="08:00", b="22:30", deadline="leave"), cfg) == ["lunch", "dinner"]
    pk = packmod.build(pl.lib, pl.engine, request(a="08:00", b="18:00", deadline="leave"), pl.cfg)
    assert [m["meal"] for m in pk.request["day"]["meals_required"]] == ["lunch"]            # home may be 22:00 or later


# ---- last safe return and plan B ----------------------------------------------------------------------------------------
def test_last_safe_return_is_the_last_vehicle_leaving_by_the_leave_by_time(pl):
    req = request(b="17:30", deadline="leave")
    res = pl.sched.schedule(req, GOLDEN)
    assert res.errors == []
    s = pl.sched.safe_return(req, res)
    assert s.first_vehicle["route_short"] == "36" and s.first_vehicle["board_time"][11:16] == "16:25"   # the 18:10 bus is after 17:30
    later = pl.sched.safe_return(request(b="18:15", deadline="leave"), pl.sched.schedule(request(b="18:15", deadline="leave"), GOLDEN))
    assert later.first_vehicle["board_time"][11:16] == "18:10" and later.backup["first_vehicle"]["board_time"][11:16] == "16:25"
    home = pl.sched.safe_return(request(b="21:00"), pl.sched.schedule(request(b="21:00"), GOLDEN))
    assert home.depart[11:16] == "18:40"                                      # home mode: the golden value, unchanged


def test_plan_b_deadline_in_leave_mode(pl):
    req = request(b="21:00", deadline="leave")
    res = pl.sched.schedule(req, GOLDEN)
    for b in pl.sched.plan_b(req, res):
        assert b.within_deadline in (True, False)
    for b in pl.sched.plan_b(request(b="21:00"), res):
        assert b.within_deadline is None or b.target == "Porto Campanhã"


# ---- fallback, CLI, HTTP ------------------------------------------------------------------------------------------------
def test_fallback_respects_the_leave_by_time(make):
    plan = make([]).plan(request(b="17:30", deadline="leave"), use_llm=False)
    assert plan.status == "ok" and plan.source == "fallback"
    visits = [e for e in plan.timeline if e.type == "visit"]
    assert visits[-1].end[11:16] <= "17:30"
    rides = [e for e in plan.timeline if e.type == "ride"]
    assert rides[-1].board_time[11:16] <= "17:30" or rides[0].board_time[11:16] <= "17:30"
    assert plan.home_by and plan.home_by[11:16] > visits[-1].end[11:16]            # home after the last visit, maybe after 17:30
    assert plan.last_safe_return and plan.last_safe_return.first_vehicle["board_time"][11:16] <= "17:30"
    assert plan.request.deadline == "leave"


def test_llm_plan_in_leave_mode(make):
    ids = [i for i, _ in GOLDEN]
    plan = make([draft(*GOLDEN), narrative(ids)]).plan(request(b="15:30", deadline="leave"))
    assert plan.status == "ok" and plan.source == "llm" and plan.attempts == 1
    late = make([draft(*GOLDEN)] * 3).plan(request(b="15:00", deadline="leave"))
    assert late.source == "fallback" and late.attempt_errors == [["left_too_late"]] * 3


def run_cli(capsys, *args):
    code = main(["plan", "--from", "porto-campanha", "--date", "2026-10-24", "--after", "08:00", "--no-llm", "--json", *args])
    return code, json.loads(capsys.readouterr().out)


def test_cli_leave_by(capsys):
    code, out = run_cli(capsys, "--leave-by", "17:30")
    assert code == 0 and out["request"]["deadline"] == "leave" and out["request"]["back_by"] == "17:30"
    code, out = run_cli(capsys, "--back-by", "21:00")
    assert code == 0 and out["request"]["deadline"] == "home"


def test_cli_needs_exactly_one_deadline(capsys):
    base = ["plan", "--from", "porto-campanha", "--date", "2026-10-24", "--after", "08:00", "--no-llm"]
    for extra in (["--back-by", "21:00", "--leave-by", "17:30"], []):
        with pytest.raises(SystemExit) as e:
            main(base + extra)
        assert e.value.code == 2
    capsys.readouterr()


def test_http_accepts_the_deadline(make):
    c = TestClient(create_app(make([])))
    j = c.post("/plan?use_llm=false", json=FAR).json()
    assert j["status"] == "ok" and j["request"]["deadline"] == "leave"
    assert [e for e in j["timeline"] if e["type"] == "visit"][-1]["end"][11:16] <= "17:30"
    assert c.post("/plan?use_llm=false", json={**FAR, "deadline": "whenever"}).status_code == 422
    no_field = {k: v for k, v in FAR.items() if k != "deadline"}
    assert c.post("/plan?use_llm=false", json={**no_field, "back_by": "21:00"}).json()["request"]["deadline"] == "home"


def test_test_page_has_the_switch(make):
    html = TestClient(create_app(make([]))).get("/").text
    assert "Back home by" in html and "Leave by" in html and 'name="deadline"' in html and "deadline: deadline()" in html
