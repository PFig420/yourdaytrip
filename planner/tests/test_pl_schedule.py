"""Unit tests for the scheduler and validator: every error code of Planner.md 6.3."""
from conftest import GOLDEN, draft, request
from planner.validate import check_schema, validate_draft


def codes(errs):
    return [e.code for e in errs]


def sched(pl, req, stops, **kw):
    return pl.sched.schedule(req, stops, **kw)


def test_unknown_place(pl):
    r = sched(pl, request(), [("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60)], pack_ids={"museu-de-aveiro-santa-joana"})
    assert "unknown_place" in codes(r.errors)


def test_duplicate_and_counts(pl):
    d = draft(("se-de-aveiro", 30), ("se-de-aveiro", 30))
    errs, _ = validate_draft(pl.sched, request(include_food=False), d, None)
    assert "duplicate_place" in codes(errs) and "too_few_stops" in codes(errs)
    d = draft(*[(i, 30) for i in ("se-de-aveiro", "museu-de-aveiro-santa-joana", "museu-arte-nova-aveiro", "forte-da-barra", "navio-museu-santo-andre")])
    errs, _ = validate_draft(pl.sched, request(include_food=False), d, None)
    assert "too_many_stops" in codes(errs)              # relaxed allows 4
    errs, _ = validate_draft(pl.sched, request(include_food=False, pace="packed"), d, None)
    assert "too_many_stops" not in codes(errs)


def test_schema_limits():
    seven = draft(*[(f"p{i}", 30) for i in range(7)])
    assert codes(check_schema(seven)) == ["schema"]
    d = draft(("a", 30), ("b", 30))
    d.stops[0].why = " ".join(["word"] * 30)
    assert codes(check_schema(d)) == ["schema"]
    d = draft(("a", 30), ("b", 30), title="one two three four five six seven eight nine")
    assert codes(check_schema(d)) == ["schema"]


def test_clamp_and_too_short(pl):
    p = pl.lib.get("museu-de-aveiro-santa-joana")
    r = sched(pl, request(include_food=False), [("museu-de-aveiro-santa-joana", 500), ("se-de-aveiro", 30)])
    v = [v for v in r.visits if v[0].id == p.id][0]
    assert (v[2] - v[1]) // 60 == 2 * p.visit_typical            # clamped to 2 x typical, with a warning
    assert any("shortened" in w for w in r.warnings)
    r = sched(pl, request(include_food=False), [("museu-de-aveiro-santa-joana", p.visit_min - 5), ("se-de-aveiro", 30)])
    assert "visit_too_short" in codes(r.errors)


def test_waiting_allowed_and_too_long(pl):
    r = sched(pl, request(include_food=False), [("museu-de-aveiro-santa-joana", 75), ("se-de-aveiro", 30)])
    wait = [e for e in r.timeline if e.type == "wait" and e.reason == "until it opens"][0]
    assert wait.from_[11:16] == "09:24" and wait.until[11:16] == "10:00"
    assert "wait_too_long" not in codes(r.errors)
    r = sched(pl, request(origin="aveiro-station", a="08:13"), [("museu-de-aveiro-santa-joana", 75), ("se-de-aveiro", 30)])
    e = [e for e in r.errors if e.code == "wait_too_long"][0]
    assert "08:30" in e.message and "10:00" in e.message


def test_fixed_entries(pl):
    r = sched(pl, request(origin="aveiro-station", a="09:00", include_food=False), [("farol-da-barra", 45), ("forte-da-barra", 30)])
    assert "no_departure" in codes(r.errors)             # Saturday: no climbs
    req = request(origin="aveiro-station", date="2026-10-28", a="13:00", include_food=False)
    r = sched(pl, req, [("farol-da-barra", 45), ("forte-da-barra", 30)])
    visit = [v for v in r.visits if v[0].id == "farol-da-barra"][0]
    assert visit[3] == "fixed" and r.errors == []
    from planner.timeutil import hm
    assert hm(visit[1]) in {"14:00", "14:30", "15:00", "15:30", "16:30"}


def test_entry_too_late(pl):
    """Museu Maritimo de Ilhavo: closes 18:00, last entry 45 minutes before (17:15)."""
    import datetime as dt
    from planner.timeutil import at
    d = dt.date(2026, 10, 24)
    place = pl.lib.get("museu-maritimo-ilhavo")
    start, kind, close, err = pl.sched.entry(place, d, at(d, "17:10"), False)
    assert err is None and kind == "open"
    start, kind, close, err = pl.sched.entry(place, d, at(d, "17:30"), False)
    assert err.code == "entry_too_late" and "17:30" in err.message
    start, kind, close, err = pl.sched.entry(place, d, at(d, "18:03"), False)
    assert err.code == "closed"


def test_monday_closed(pl):
    req = request(origin="aveiro-station", date="2026-10-26", a="09:00", include_food=False)
    r = sched(pl, req, [("museu-de-aveiro-santa-joana", 60), ("se-de-aveiro", 30)])
    e = [e for e in r.errors if e.code == "closed"][0]
    assert e.place_id == "museu-de-aveiro-santa-joana" and "Monday" in e.message


def test_visit_overruns_close(pl):
    # Museu de Aveiro closes its morning session at 12:30
    req = request(origin="aveiro-station", a="10:45", include_food=False)
    r = sched(pl, req, [("museu-de-aveiro-santa-joana", 90), ("se-de-aveiro", 20)])
    e = [e for e in r.errors if e.code == "visit_overruns"][0]
    assert "12:30" in e.message


def test_meal_rules(pl):
    r = sched(pl, request(), [("museu-de-aveiro-santa-joana", 75), ("costa-nova-palheiros", 75)])
    assert "meal_missing" in codes(r.errors)
    r = sched(pl, request(), GOLDEN)
    assert r.errors == []


def test_meal_outside_window(pl):
    # a restaurant reached at about 11:15 is outside its lunch window
    r = sched(pl, request(origin="aveiro-station", a="10:30", b="21:00"), [("canastra-do-fidalgo-costa-nova", 60), ("costa-nova-palheiros", 60)])
    assert "meal_outside_window" in codes(r.errors)


def test_no_journey_and_home_too_late(pl):
    r = sched(pl, request(a="08:00", b="12:00", include_food=False), GOLDEN[:1] + [("costa-nova-palheiros", 75)])
    assert {"home_too_late", "wait_too_long"} & set(codes(r.errors))
    r = sched(pl, request(a="08:00", b="15:30", include_food=False), [("museu-de-aveiro-santa-joana", 75), ("costa-nova-palheiros", 75)])
    e = [e for e in r.errors if e.code == "home_too_late"][0]
    assert "15:30" in e.message and "16:20" in e.message


def test_rain_rejects_outdoor(pl):
    req = request(weather="rain", include_food=False)
    r = sched(pl, req, [("costa-nova-palheiros", 60), ("museu-de-aveiro-santa-joana", 60)])
    assert "unknown_place" in codes(r.errors)
