"""Free time before dinner (Planner.md 6.1, 10.1)."""
from types import SimpleNamespace

from conftest import request
from planner import pack as packmod
from planner.timeutil import hm


def run(pl, a, stops, b="22:30"):
    req = request(origin="aveiro-station", a=a, b=b)
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    return pl.sched.schedule(req, stops, pack_ids=pk.ids, pool=pk), pk


def test_free_time_walk_only_with_inserted_visit(pl):
    r, _ = run(pl, "17:15", [("o-batel-aveiro", 60)])
    assert r.errors == []
    types = [e.type for e in r.timeline]
    assert "free_time" in types
    ins = [e for e in r.timeline if e.type == "visit" and e.inserted]
    assert len(ins) == 1 and ins[0].reason == "fills time before dinner"
    dinner = [e for e in r.timeline if e.type == "visit" and not e.inserted][0]
    assert hm_(dinner.start) >= "19:00" and dinner.meal == "dinner"
    p = pl.lib.get(ins[0].place_id)
    assert p.hours.strip() == "24/7"                       # always open
    if p.daylight_only:                                    # and before sunset (18:41 on 24 Oct)
        assert hm_(ins[0].end) <= "18:41"


def hm_(iso):
    return iso[11:16]


def test_gap_of_150_minutes_is_wait_too_long(pl):
    r, _ = run(pl, "16:00", [("o-batel-aveiro", 60)])           # arrives ~16:13 for 19:00: 167 minutes
    assert [e.code for e in r.errors] == ["wait_too_long"]


def test_gap_that_needs_a_ride_is_wait_too_long(pl):
    place = pl.lib.get("o-batel-aveiro")
    ride = SimpleNamespace(rides=1, legs=[SimpleNamespace(type="walk", metres=300)])
    from datetime import date
    assert pl.sched._dinner_free(place, ride, date(2026, 10, 24), _at("17:30"), False) is None
    walk = SimpleNamespace(rides=0, legs=[SimpleNamespace(type="walk", metres=300)])
    assert pl.sched._dinner_free(place, walk, date(2026, 10, 24), _at("17:30"), False) is not None
    long_walk = SimpleNamespace(rides=0, legs=[SimpleNamespace(type="walk", metres=2500)])
    assert pl.sched._dinner_free(place, long_walk, date(2026, 10, 24), _at("17:30"), False) is None


def _at(hhmm):
    import datetime as dt
    from planner.timeutil import at
    return at(dt.date(2026, 10, 24), hhmm)


def test_insertion_respects_sunset(pl):
    """An always-open place flagged daylight-only is not inserted when its visit would end after sunset."""
    r, _ = run(pl, "18:00", [("o-batel-aveiro", 60)])          # free time starts after the 18:41 sunset
    assert "wait_too_long" not in [e.code for e in r.errors]
    assert not [e for e in r.timeline if e.type == "visit" and e.inserted]


def test_dinner_day_fallback(make):
    plan = make([]).plan(request(origin="aveiro-station", a="09:30", b="22:30", pace="packed", date="2026-10-24"), use_llm=False)
    assert plan.status == "ok"
    assert {"lunch", "dinner"} <= {v.meal for v in plan.timeline if v.type == "visit"}


# ---- free time with nearby ideas (Changes.md A) ------------------------------------------------------------------
import datetime as dt

from conftest import GOLDEN, draft, narrative
from planner import freetime
from planner.models import FreeTime, Visit, Wait
from planner.schedule import Result
from planner.timeutil import at, iso

DAY = dt.date(2026, 10, 24)
COSTA_NOVA_DAY = ["museu-arte-nova-aveiro", "se-de-aveiro", "art-nouveau-route-aveiro", "canastra-do-fidalgo-costa-nova"]


def _minutes(pl, pid):
    return 90 if pid.startswith("canastra") else pl.lib.get(pid).visit_typical


def costa_nova_request(**kw):
    return request(date="2026-10-31", a="09:30", b="21:00", interests=["architecture", "beach", "history"], **kw)


def costa_nova_plan(pl, make, **kw):
    """The 31 Oct test plan of Changes.md 0: lunch at Canastra from 14:54, then a bus home that leaves at 18:10."""
    stops = [(i, _minutes(pl, i)) for i in COSTA_NOVA_DAY]
    return make([draft(*stops), narrative(COSTA_NOVA_DAY)]).plan(costa_nova_request(**kw))


def test_costa_nova_wait_becomes_free_time(pl, make):
    plan = costa_nova_plan(pl, make)
    assert plan.status == "ok" and plan.source == "llm"
    fts = [e for e in plan.timeline if e.type == "free_time"]
    assert [(hm_(e.from_), hm_(e.until), e.at, e.reason) for e in fts] == \
        [("16:24", "18:05", "Canastra do Fidalgo", "free time before the bus home")]
    assert not [e for e in plan.timeline if e.type == "wait" and e.reason.startswith("until it is time")
                and e.at == "Canastra do Fidalgo"]
    # nothing but the label changed: the ride after it is still the 18:10 bus
    i = plan.timeline.index(fts[0])
    assert plan.timeline[i + 1].type == "walk" and plan.timeline[i + 2].board_time[11:16] == "18:10"


def test_costa_nova_ideas_are_the_seafront_and_the_beach(pl, make):
    """With reference points (CurrentChanges.md P) the seafront's south point is 20 m from Canastra and the beach's boardwalk
    point, so both are ideas for the 16:24-18:05 gap; each shows the point it was reached at. Walking overrides (WalkBarriers.md):
    the beach point is 345 m from Canastra in a straight line but 478 m on foot (OSM), and that is the length shown."""
    ft = [e for e in costa_nova_plan(pl, make).timeline if e.type == "free_time"][0]
    ideas = {s["place_id"]: s for s in ft.suggestions}
    assert list(ideas) == ["costa-nova-palheiros", "praia-costa-nova"]            # shortest walk first
    sea, beach = ideas["costa-nova-palheiros"], ideas["praia-costa-nova"]
    assert (sea["walk_m"], sea["point"]) == (20, "south end of the seafront, on Avenida José Estêvão")
    assert (beach["walk_m"], beach["point"]) == (478, "south end, dune boardwalk from the Costa Nova houses")
    assert sea["open_until"] == beach["open_until"] == "17:32"                      # daylight only: sunset
    assert beach["minutes_there"] >= pl.lib.get("praia-costa-nova").visit_min


def test_the_beach_drops_out_when_the_minimum_stay_no_longer_fits_before_sunset(pl):
    """The rule that excludes it: Praia da Costa Nova needs 60 minutes and the sun sets at 17:32 on 31 Oct."""
    req = costa_nova_request()
    c = pl.lib.get("canastra-do-fidalgo-costa-nova")
    day = dt.date(2026, 10, 31)
    pl.router.for_date(day)
    taken = {"canastra-do-fidalgo-costa-nova"}
    early = freetime.suggest(pl.sched, req, (c.id, c.lat, c.lon), at(day, "16:24"), at(day, "18:05"), taken, 800)
    late = freetime.suggest(pl.sched, req, (c.id, c.lat, c.lon), at(day, "16:50"), at(day, "18:05"), taken, 800)
    assert "praia-costa-nova" in [s["place_id"] for s in early]
    assert "praia-costa-nova" not in [s["place_id"] for s in late]                  # about 35 minutes of daylight left there
    assert "costa-nova-palheiros" in [s["place_id"] for s in late]                  # visit_min of the seafront is shorter


def _ft_scenarios(pl):
    """(request, location, from, until) for a spread of places and times."""
    out = []
    for pid in ("se-de-aveiro", "o-batel-aveiro", "museu-arte-nova-aveiro", "canastra-do-fidalgo-costa-nova", "estacao-ferroviaria-aveiro"):
        p = pl.lib.get(pid)
        for a, b in (("11:00", "12:00"), ("14:00", "16:30"), ("16:30", "18:30"), ("17:30", "19:30")):
            out.append((p, at(DAY, a), at(DAY, b)))
    return out


def test_suggestions_fit_inside_the_free_time(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    pl.router.for_date(DAY)
    seen = 0
    for p, t0, t1 in _ft_scenarios(pl):
        out = freetime.suggest(pl.sched, req, (p.id, p.lat, p.lon), t0, t1, {p.id}, 800)
        assert len(out) <= 3 and sum(s["kind"] in ("food", "drink") for s in out) <= 1
        assert [(s["walk_m"], s["place_id"]) for s in out] == sorted((s["walk_m"], s["place_id"]) for s in out)
        for s in out:
            seen += 1
            x = pl.lib.get(s["place_id"])
            assert x.status == "confirmed" and s["place_id"] != p.id
            assert s["walk_m"] <= 800 and set(s) - {"point", "point_id"} == {"place_id", "name", "kind", "walk_m", "walk_min", "minutes_there",
                                                      "open_until", "description"}
            assert s["minutes_there"] >= x.visit_min
            assert 2 * s["walk_min"] + s["minutes_there"] <= (t1 - t0) / 60 + 2           # both walks and the stay fit (rounding)
            assert len(s["description"]) <= 160
            if s["open_until"]:                                   # open for the whole stay (a closing after midnight is "02:00")
                close = int(s["open_until"][:2]) * 60 + int(s["open_until"][3:])
                close += 1440 if close < 360 else 0
                arrive = (t0 - at(DAY, "00:00")) // 60 + s["walk_min"] - 1
                assert close >= arrive + s["minutes_there"] - 1
    assert seen > 10


def test_suggestions_are_ranked_by_walk_and_skip_the_plan(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    se = pl.lib.get("se-de-aveiro")
    pl.router.for_date(DAY)
    out = freetime.suggest(pl.sched, req, (se.id, se.lat, se.lon), at(DAY, "14:00"), at(DAY, "16:30"), {se.id}, 800)
    assert [s["place_id"] for s in out][0] == "museu-de-aveiro-santa-joana"
    walks = [s["walk_m"] for s in out]
    assert walks == sorted(walks)
    again = freetime.suggest(pl.sched, req, (se.id, se.lat, se.lon), at(DAY, "14:00"), at(DAY, "16:30"),
                             {se.id, "museu-de-aveiro-santa-joana"}, 800)
    assert "museu-de-aveiro-santa-joana" not in [s["place_id"] for s in again]


def test_only_one_food_place_and_confirmed_records_only(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    pl.router.for_date(DAY)
    p = pl.lib.get("praca-do-peixe-aveiro")
    out = freetime.suggest(pl.sched, req, (p.id, p.lat, p.lon), at(DAY, "11:00"), at(DAY, "15:00"), {p.id}, 2000)
    food = [s for s in out if s["kind"] in ("food", "drink")]
    assert len(food) <= 1 and all(pl.lib.get(s["place_id"]).status == "confirmed" for s in out)
    drafts = {x.id for x in pl.lib.places.values() if x.status != "confirmed"}
    wide = freetime.suggest(pl.sched, req, (p.id, p.lat, p.lon), at(DAY, "11:00"), at(DAY, "20:00"), {p.id}, 2000)
    assert drafts and not drafts & {s["place_id"] for s in wide}


def test_rain_drops_outdoor_suggestions(pl):
    ob = pl.lib.get("o-batel-aveiro")
    pl.router.for_date(DAY)
    names = {}
    for w in ("any", "rain"):
        req = request(origin="aveiro-station", a="08:00", b="22:30", weather=w)
        out = freetime.suggest(pl.sched, req, (ob.id, ob.lat, ob.lon), at(DAY, "16:30"), at(DAY, "18:30"), set(), 800)
        names[w] = [pl.lib.get(s["place_id"]).weather for s in out]
    assert "outdoor" in names["any"] and "outdoor" not in names["rain"] and len(names["rain"]) == 3


def test_daylight_places_are_suggested_only_before_sunset(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    ob = pl.lib.get("o-batel-aveiro")
    pl.router.for_date(DAY)
    out = freetime.suggest(pl.sched, req, (ob.id, ob.lat, ob.lon), at(DAY, "17:00"), at(DAY, "20:00"), set(), 2000)
    for s in out:
        x = pl.lib.get(s["place_id"])
        if x.daylight_only:
            assert s["open_until"] <= "18:41"                              # sunset on 24 Oct is 18:41
            assert hm(at(DAY, "17:00") + (s["walk_min"] + s["minutes_there"] - 1) * 60) <= "18:41"


def synthetic(pl, wait_from, wait_until, reason="until it is time to leave for the next stop", home=False):
    v = pl.lib.get("se-de-aveiro")
    tl = [Visit(place_id=v.id, name=v.name, start=iso(at(DAY, "13:00")), end=iso(at(DAY, wait_from)), entry="open"),
          Wait(at=v.name, **{"from": iso(at(DAY, wait_from))}, until=iso(at(DAY, wait_until)), reason=reason)]
    return Result([], timeline=tl, visits=[(v, 0, 0, "open", None)])


def test_short_and_transfer_waits_stay_waits(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    pl.router.for_date(DAY)
    short = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:20"))
    assert [e.type for e in short] == ["visit", "wait"]
    edge = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:29"))
    assert edge[1].type == "wait"
    transfer = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:40", reason="until the next connection"))
    assert [e.type for e in transfer] == ["visit", "wait"]
    opens = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:40", reason="until it opens"))
    assert opens[1].type == "wait"
    fixed = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:40", reason="fixed entry time"))
    assert fixed[1].type == "wait"
    long = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "14:30"))
    assert [e.type for e in long] == ["visit", "free_time"] and long[1].reason == "free time before the next stop"
    # Sé de Aveiro -> Museu de Aveiro is 94 m in a straight line but 373 m on foot (OSM path, WalkBarriers.md): 6 + 45 + 6 minutes
    # no longer fit a 30 minute gap, so there is no idea here; with an hour the museum is the first one
    assert long[1].suggestions == []
    hour = freetime.apply(pl.sched, req, synthetic(pl, "14:00", "15:00"))[1]
    assert hour.suggestions and hour.suggestions[0]["place_id"] == "museu-de-aveiro-santa-joana" and hour.suggestions[0]["walk_m"] == 373


def test_the_wait_before_a_visit_never_becomes_free_time(pl):
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    res = synthetic(pl, "14:00", "15:00")
    res.timeline.insert(0, res.timeline.pop())                  # a wait with no visit before it (the start of the day)
    assert [e.type for e in freetime.apply(pl.sched, req, res)] == ["wait", "visit"]


def test_threshold_comes_from_the_config(pl):
    import copy
    req = request(origin="aveiro-station", a="08:00", b="22:30")
    cfg = copy.deepcopy(pl.cfg)
    cfg["schedule"]["suggest_free_time_min"] = 45
    sched = type(pl.sched)(pl.engine, pl.lib, cfg, pl.router)
    assert freetime.apply(sched, req, synthetic(pl, "14:00", "14:40"))[1].type == "wait"
    assert freetime.apply(sched, req, synthetic(pl, "14:00", "14:45"))[1].type == "free_time"


def test_dinner_free_time_gets_ideas_unless_a_visit_was_inserted(pl):
    pl.router.for_date(DAY)
    for a, inserted in (("17:15", True), ("18:00", False)):
        req = request(origin="aveiro-station", a=a, b="22:30")
        pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
        res = pl.sched.schedule(req, [("o-batel-aveiro", 60)], pack_ids=pk.ids, pool=pk)
        tl = freetime.apply(pl.sched, req, res)
        ft = [e for e in tl if e.type == "free_time"]
        assert len(ft) == 1 and ft[0].reason == "free time before dinner"
        assert bool(ft[0].suggestions) is (not inserted)
        assert any(e.type == "visit" and e.inserted for e in tl) is inserted


def test_free_time_runs_for_fallback_plans_and_changes_no_time(pl, make):
    plan = make([]).plan(costa_nova_request(), use_llm=False)
    assert plan.status == "ok" and plan.source == "fallback"
    fts = [e for e in plan.timeline if e.type == "free_time"]
    assert fts and all(_e(e.until) - _e(e.from_) >= 1800 for e in fts)
    # what is left as a wait after a visit is shorter than the threshold
    assert all(_e(e.until) - _e(e.from_) < 1800 for e in plan.timeline
               if e.type == "wait" and e.reason == "until it is time to leave for the next stop" and e.at != "Porto Campanhã")
    # a free time and the entry after it join up exactly as the wait did: the plan is still one continuous day
    for a, b in zip(plan.timeline, plan.timeline[1:]):
        end = {"free_time": a.until if a.type == "free_time" else None}.get(a.type)
        if end:
            nxt = b.depart if b.type == "walk" else b.board_time if b.type == "ride" else b.start
            assert nxt >= end


def _e(s):
    return int(dt.datetime.fromisoformat(s).timestamp())
