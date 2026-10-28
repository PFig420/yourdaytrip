"""Reference points of long places in the planner (CurrentChanges.md P): scheduler, pack hints."""
import datetime as dt

from conftest import GOLDEN, request
from planner import pack as packmod
from planner.models import Origin, PlanRequest

SEA = "costa-nova-palheiros"
CANASTRA = "canastra-do-fidalgo-costa-nova"


def test_a_visit_routes_to_the_point_reached_first_and_leaves_from_it(pl):
    res = pl.sched.schedule(request(), GOLDEN)
    assert res.errors == []
    tl = res.timeline
    i = next(k for k, e in enumerate(tl) if e.type == "visit" and e.place_id == SEA)
    point = "Costa Nova seafront and the striped palheiros (south end of the seafront, on Avenida José Estêvão)"
    arrive = tl[i - 1]
    assert arrive.type == "walk" and arrive.to == point and arrive.metres < 50      # 20 m from Canastra, not 1.3 km to the main point
    assert tl[i].name == "Costa Nova seafront and the striped palheiros"            # the visit keeps the plain name
    leave = next(e for e in tl[i + 1:] if e.type == "walk")
    assert leave.from_ == point                                                      # and the way out starts at the same point
    assert res.visit_points[SEA][0][0] == f"{SEA}#1" and res.last_point[0] == f"{SEA}#1"


def test_plan_b_and_safe_return_work_from_a_point(pl):
    req = request()
    res = pl.sched.schedule(req, GOLDEN)
    s = pl.sched.safe_return(req, res)
    assert s.from_place == "Costa Nova seafront and the striped palheiros" and s.first_vehicle
    pl.sched.plan_b(req, res)                                                        # does not raise on "<id>#1" targets


def test_a_place_without_points_is_routed_exactly_as_before(pl):
    res = pl.sched.schedule(request(), GOLDEN[:2])
    assert all(not pid.endswith(tuple("#123456")) for pid in (res.last_point[0],)) and res.visit_points[CANASTRA][0][0] == CANASTRA
    walk = [e for e in res.timeline if e.type == "walk" and e.to == "Canastra do Fidalgo"]
    assert walk and "(" not in walk[0].to


def test_hints_use_the_points_of_both_places(pl):
    o = Origin(id="aveiro-station", name="Aveiro station", lat=40.64342677, lon=-8.640761068)
    req = PlanRequest(origin=o, date="2026-10-31", leave_after="09:00", back_by="21:00")
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    ids = {c["id"] for c in pk.candidates}
    assert SEA in ids and CANASTRA in ids
    # before the points: 1.3 km on foot between the main points (about 20 minutes). Now Canastra is 20 m from the seafront's south
    # point; the other way starts at the seafront point reached first from the origin (the one by the bus stop), a short walk away
    assert pk.hints[CANASTRA][SEA] <= 2 and pk.hints[SEA][CANASTRA] <= 8
    assert '"_from"' not in pk.dumps() and '"_pts"' not in pk.dumps() and "#" not in pk.dumps()


def test_the_pack_still_names_places_by_their_plain_id(pl):
    req = request(origin="aveiro-station", date="2026-10-31", a="09:00", b="21:00")
    pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    for row in pk.hints.values():
        assert all("#" not in k for k in row)
