"""Candidate pack and SDK usage checks."""
import json

import httpx2 as httpx
import pytest

import anthropic
from conftest import GOLDEN, draft, request
from planner import pack as packmod
from planner.llm import FALLBACK_BETA, LLM, first_message
from planner.models import DayDraft


def test_pack_shape(pl):
    req = request(interests=["history", "maritime"])
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    sights = [c for c in pk.candidates if c["kind"] in ("sight", "activity")]
    food = [c for c in pk.candidates if c["kind"] in ("food", "drink")]
    assert len(sights) <= 12 and len(food) <= 6
    assert len({c["cluster"] for c in sights}) >= 2
    scores = [pk.items[c["id"]].score for c in pk.candidates]
    assert scores == sorted(scores, reverse=True)
    for c in pk.candidates:
        assert (c.get("slots") or c.get("fixed_entry_times")) and c["leave_by"] and c["arrive_from_origin"] >= 0 and len(c["notes"]) <= 2
        assert "lat" not in c and "trip_id" not in json.dumps(c)
    assert pk.hints and all(isinstance(v, int) for row in pk.hints.values() for v in row.values())


def test_pack_has_lunch_places(pl):
    pk = packmod.build(pl.lib, pl.engine, request(), pl.cfg)
    assert any("lunch" in c.get("meals", []) for c in pk.candidates)
    assert all(c["id"] in pk.items for c in pk.candidates)


def test_pack_rain_has_no_outdoor(pl):
    pk = packmod.build(pl.lib, pl.engine, request(weather="rain"), pl.cfg)
    assert all(c["weather"] != "outdoor" for c in pk.candidates)


def test_pack_without_food(pl):
    pk = packmod.build(pl.lib, pl.engine, request(include_food=False), pl.cfg)
    assert all(c["kind"] in ("sight", "activity") for c in pk.candidates)


def test_fixed_entry_times_in_pack(pl):
    pk = packmod.build(pl.lib, pl.engine, request(interests=["history"]), pl.cfg)
    egas = [c for c in pk.candidates if c["id"] == "casa-museu-egas-moniz"]
    assert egas and egas[0]["fixed_entry_times"][0] == "10:30"


# ---- the installed SDK accepts our call shape (Planner.md 2.3) ---------------------------
def _client(seen, body):
    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=body)
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _message(text):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 0}}


def test_sdk_accepts_parse_with_fallbacks_effort_and_cache():
    seen = []
    d = draft(*GOLDEN)
    llm = LLM(_client(seen, _message(d.model_dump_json())), {})
    cr = llm.draft([first_message("{}")])
    assert cr.error == "" and isinstance(cr.parsed, DayDraft) and cr.parsed.stops[0].place_id == GOLDEN[0][0]
    assert cr.usage["cache_read_tokens"] == 7 and cr.assistant["role"] == "assistant"
    req = seen[0]
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages"
    assert FALLBACK_BETA in req.headers["anthropic-beta"]
    assert body["fallbacks"] == "default" and body["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body and body["model"] == "claude-sonnet-5-5"


def test_sdk_rate_limit_is_caught():
    def handler(request):
        return httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
    c = anthropic.Anthropic(api_key="k", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    cr = LLM(c, {}).draft([first_message("{}")])
    assert cr.error == "rate_limit" and cr.parsed is None


# ---- prompt-fix additions: slot objects, closed_between, earliest_arrival, the day block ---------------------
def _mins(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


@pytest.mark.parametrize("pace", ["relaxed", "packed"])
def test_slots_are_objects_with_enter_by(pl, pace):
    pk = packmod.build(pl.lib, pl.engine, request(pace=pace), pl.cfg)
    seen = 0
    for c in pk.candidates:
        if "fixed_entry_times" in c:
            assert "slots" not in c
            continue
        p = pl.lib.get(c["id"])
        stay = p.visit_typical if pace == "relaxed" else p.visit_min
        for s in c["slots"]:
            assert set(s) == {"from", "to", "enter_by"}
            assert _mins(s["from"]) <= _mins(s["enter_by"])
            assert _mins(s["enter_by"]) + stay <= _mins(s["to"])          # the stay fits when entered by enter_by
            seen += 1
    assert seen


def test_enter_by_respects_last_entry(pl):
    pk = packmod.build(pl.lib, pl.engine, request(), pl.cfg)
    c = next(c for c in pk.candidates if c["id"] == "museu-maritimo-ilhavo")       # closes 18:00, last entry 45 min before
    assert c["slots"][-1]["to"] == "18:00" and c["slots"][-1]["enter_by"] == "16:30"    # 90 min stay: 16:30, not 17:15
    p = pl.lib.get("museu-maritimo-ilhavo")
    assert _mins(c["slots"][-1]["enter_by"]) <= _mins("18:00") - p.last_entry_min


def test_closed_between(pl):
    pk = packmod.build(pl.lib, pl.engine, request(), pl.cfg)
    by = {c["id"]: c for c in pk.candidates}
    assert by["museu-de-aveiro-santa-joana"]["closed_between"] == ["12:30-13:30"]
    for c in pk.candidates:
        assert ("closed_between" in c) == (len(c.get("slots", [])) > 1)


def test_earliest_arrival_is_a_clock_time(pl):
    req = request()
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    for c in pk.candidates:
        assert len(c["earliest_arrival"]) == 5 and c["earliest_arrival"][2] == ":"
        assert _mins(c["earliest_arrival"]) - _mins(req.leave_after) == pytest.approx(c["arrive_from_origin"], abs=1)


@pytest.mark.parametrize("kw,meals", [
    (dict(a="08:00", b="21:00"), ["lunch"]),
    (dict(a="09:30", b="22:30"), ["lunch", "dinner"]),
    (dict(a="15:30", b="22:30"), ["dinner"]),
    (dict(a="13:00", b="20:00"), []),
    (dict(a="08:00", b="21:00", include_food=False), []),
])
def test_day_block_agrees_with_the_validator(pl, kw, meals):
    """day.meals_required must name exactly the meals the validator demands, and the stop limits its own."""
    req = request(origin="aveiro-station", **kw)
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    day = pk.request["day"]
    assert [m["meal"] for m in day["meals_required"]] == meals
    assert all(m["window"] == pl.lib.cfg.meals[m["meal"]] for m in day["meals_required"])
    assert day["min_stops"] == 2 and day["max_stops"] == 4
    sights = [c["id"] for c in pk.candidates if c["kind"] in ("sight", "activity")]
    r = pl.sched.schedule(req, [(sights[0], 20), (sights[1], 20)], pack_ids=pk.ids, pool=pk)
    # the message reads "the day spans the <meal> window (...) but ...": take the word after "spans the"
    named = sorted(e.message.split("spans the ")[1].split(" window")[0] for e in r.errors if e.code == "meal_missing")
    assert named == sorted(meals)


def test_day_block_stop_limits_match_the_validator(pl):
    from planner.validate import validate_draft
    from conftest import draft
    pk = packmod.build(pl.lib, pl.engine, request(pace="packed", include_food=False), pl.cfg)
    assert pk.request["day"]["max_stops"] == 6
    req = request(origin="aveiro-station", include_food=False)
    errs, _ = validate_draft(pl.sched, req, draft(("se-de-aveiro", 30)), None)
    assert "too_few_stops" in [e.code for e in errs]
    errs, _ = validate_draft(pl.sched, req, draft(("se-de-aveiro", 30), ("museu-arte-nova-aveiro", 30)), None)
    assert "too_few_stops" not in [e.code for e in errs]
