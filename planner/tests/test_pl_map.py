"""The map of the planned day (MapPlan.md): ids on timeline entries, mapview.build, the HTTP flag and --geojson."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from conftest import GOLDEN, draft, narrative, request
from planner import mapview
from planner import narrative as nar
from planner.cli import main
from planner.models import DayPlan, Ride, Visit, Walk
from planner.web import create_app
from places.network import distance_m
from test_pl_freetime import costa_nova_plan, costa_nova_request, COSTA_NOVA_DAY, _minutes

CANASTRA = "canastra-do-fidalgo-costa-nova"


def golden_plan(pl):
    res = pl.sched.schedule(request(), GOLDEN)
    assert res.errors == []
    return DayPlan(request=request(), source="llm", timeline=res.timeline)


def kinds(m, kind):
    return [f for f in m["features"] if f["properties"]["kind"] == kind]


# ---- 3.1 ids on the timeline entries ---------------------------------------------------------------------------------------
def test_every_ride_walk_and_visit_carries_its_ids(pl):
    tl = golden_plan(pl).timeline
    for e in tl:
        if e.type == "ride":
            assert e.feed in ("cp", "av") and e.board_stop_id.startswith(e.feed + ":") and e.alight_stop_id.startswith(e.feed + ":")
        elif e.type == "walk":
            assert e.from_id and e.to_id
        elif e.type == "visit":
            assert e.point_id
    sea = next(e for e in tl if e.type == "visit" and e.place_id == "costa-nova-palheiros")
    assert sea.point_id == "costa-nova-palheiros#1"


def test_free_time_and_ideas_carry_a_point_id(pl, make):
    plan = costa_nova_plan(pl, make)
    ft = next(e for e in plan.timeline if e.type == "free_time")
    assert ft.point_id == CANASTRA
    assert ft.suggestions and all(s["point_id"] for s in ft.suggestions)
    assert [s["point_id"] for s in ft.suggestions] == ["costa-nova-palheiros#1", "praia-costa-nova#1"]


def test_the_narrative_payload_does_not_change(pl):
    tl = golden_plan(pl).timeline
    blank = []
    for e in tl:
        z = {"feed": "", "board_stop_id": "", "alight_stop_id": "", "from_id": "", "to_id": "", "point_id": ""}
        blank.append(e.model_copy(update={k: v for k, v in z.items() if hasattr(e, k)}))
    whys = {"museu-de-aveiro-santa-joana": "x"}
    assert nar.payload(tl, whys, "2026-10-24T18:20:00+01:00") == nar.payload(blank, whys, "2026-10-24T18:20:00+01:00")


# ---- 3.2 the collection --------------------------------------------------------------------------------------------------
def test_golden_plan_map(pl):
    plan = golden_plan(pl)
    m = mapview.build(plan, pl.engine, pl.lib, paths=False)
    assert m["type"] == "FeatureCollection" and m["properties"]["warnings"] == []
    visits = kinds(m, "visit")
    assert [f["properties"]["n"] for f in visits] == [1, 2, 3]
    assert [f["properties"]["name"][:6] for f in visits] == ["Museu ", "Canast", "Costa "]
    canastra = pl.lib.get(CANASTRA)
    sea = visits[2]
    assert sea["properties"]["point_name"] == "south end of the seafront, on Avenida José Estêvão"
    lon, lat = sea["geometry"]["coordinates"]
    assert distance_m(canastra.lat, canastra.lon, lat, lon) < 50                      # the 20 m walk
    assert visits[0]["properties"]["point_name"] == ""
    assert len(kinds(m, "origin")) == 1 and len(kinds(m, "walk")) == sum(e.type == "walk" for e in plan.timeline)
    rides = kinds(m, "ride")
    assert len(rides) == 4
    stops = {sid: (la, lo) for f in pl.engine.feeds for sid, (n, la, lo) in f.stops.items()}
    trips = {tid: t for f in pl.engine.feeds for tid, t in f.trips.items()}
    for f in rides:
        e = plan.timeline[f["properties"]["step"]]
        c = f["geometry"]["coordinates"]
        assert c[0] == [stops[e.board_stop_id][1], stops[e.board_stop_id][0]] and c[-1] == [stops[e.alight_stop_id][1], stops[e.alight_stop_id][0]]
        ids = [r[0] for r in trips[e.trip_id].times]
        assert f["properties"]["stops"] == ids.index(e.alight_stop_id) - ids.index(e.board_stop_id) + 1 == len(c)
    assert len(kinds(m, "stop")) == 8
    want = {"visit": "visit", "ride": "ride", "stop": "ride", "walk": "walk", "free_time": "free_time", "idea": "free_time"}
    for f in m["features"]:
        if f["properties"]["kind"] != "origin":
            assert plan.timeline[f["properties"]["step"]].type == want[f["properties"]["kind"]]


def test_every_coordinate_is_inside_the_stop_box(pl):
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib, paths=False)
    pts = [(la, lo) for f in pl.engine.feeds for (n, la, lo) in f.stops.values()]
    la0, la1, lo0, lo1 = min(p[0] for p in pts) - .05, max(p[0] for p in pts) + .05, min(p[1] for p in pts) - .05, max(p[1] for p in pts) + .05
    for f in m["features"]:
        cs = f["geometry"]["coordinates"] if f["geometry"]["type"] == "LineString" else [f["geometry"]["coordinates"]]
        for lon, lat in cs:
            assert la0 <= lat <= la1 and lo0 <= lon <= lo1


def test_bounds(pl):
    p = golden_plan(pl).request
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib, paths=False)
    a, d = m["properties"]["bounds_all"], m["properties"]["bounds_destination"]
    assert a[0] <= d[0] and a[1] <= d[1] and a[2] >= d[2] and a[3] >= d[3]
    assert a[3] >= p.origin.lat and d[3] < p.origin.lat - 0.3                          # the destination view leaves Porto out


def test_free_time_plan_has_a_free_time_feature_and_an_idea_each(pl, make):
    plan = costa_nova_plan(pl, make)
    m = mapview.build(plan, pl.engine, pl.lib, paths=False)
    ft = next(e for e in plan.timeline if e.type == "free_time")
    fts = kinds(m, "free_time")
    assert len(fts) == 1 and fts[0]["properties"]["until"] == "18:05"
    ideas = kinds(m, "idea")
    assert [i["properties"]["name"] for i in ideas] == [s["name"] for s in ft.suggestions]
    assert all(i["properties"]["step"] == fts[0]["properties"]["step"] for i in ideas)
    assert ideas[1]["properties"]["point_name"] == "south end, dune boardwalk from the Costa Nova houses"
    assert m["properties"]["bounds_destination"] and m["properties"]["warnings"] == []


def test_a_plan_read_back_from_json_gives_the_same_map(pl, make):
    plan = costa_nova_plan(pl, make)
    assert mapview.build(plan.to_json(), pl.engine, pl.lib, paths=False) == mapview.build(plan, pl.engine, pl.lib, paths=False)


def synthetic(rows, board_t, alight_t, board, alight):
    stops = {s: (s, 40.0 + i * .01, -8.0) for i, s in enumerate("ABCDE")}
    stops = {"x:" + k: v for k, v in stops.items()}
    trip = SimpleNamespace(times=[("x:" + s, a, d, True, True) for s, a, d in rows])
    engine = SimpleNamespace(feeds=[SimpleNamespace(stops=stops, trips={"x:t1": trip})])
    e = Ride(mode="bus", route_short="15", route_long="", headsign="", trip_id="x:t1", board_stop="b", board_time=f"2026-10-24T{board_t}:00+01:00",
             alight_stop="a", alight_time=f"2026-10-24T{alight_t}:00+01:00", feed="x", board_stop_id="x:" + board, alight_stop_id="x:" + alight)
    plan = DayPlan(request=request(), source="llm", timeline=[e])
    lib = SimpleNamespace(places={})
    return mapview.build(plan, engine, lib, paths=False)


def test_a_circular_line_draws_only_the_stops_between_boarding_and_alighting():
    h = lambda hh, mm: hh * 3600 + mm * 60
    rows = [("A", h(9, 0), h(9, 0)), ("B", h(9, 10), h(9, 10)), ("C", h(9, 20), h(9, 20)), ("A", h(9, 30), h(9, 30)),
            ("D", h(9, 40), h(9, 40)), ("E", h(9, 50), h(9, 50))]
    second = synthetic(rows, "09:30", "09:40", "A", "D")                  # boards A the second time
    line = kinds(second, "ride")[0]
    assert line["properties"]["stops"] == 2 and len(line["geometry"]["coordinates"]) == 2
    first = synthetic(rows, "09:00", "09:40", "A", "D")                   # boards A the first time: the whole loop
    assert kinds(first, "ride")[0]["properties"]["stops"] == 5


def test_an_unknown_id_is_skipped_with_a_warning(pl):
    plan = golden_plan(pl)
    tl = list(plan.timeline)
    i = next(k for k, e in enumerate(tl) if e.type == "walk")
    tl[i] = tl[i].model_copy(update={"to_id": "nowhere-at-all"})
    m = mapview.build(DayPlan(request=plan.request, source="llm", timeline=tl), pl.engine, pl.lib, paths=False)
    assert any("nowhere-at-all" in w for w in m["properties"]["warnings"])
    assert len(kinds(m, "walk")) == sum(e.type == "walk" for e in tl) - 1


# ---- 3.3 HTTP and CLI -----------------------------------------------------------------------------------------------------
BODY = {"origin": {"id": "porto-campanha", "name": "Porto Campanhã", "lat": 41.14871926, "lon": -8.584835348},
        "date": "2026-10-24", "leave_after": "08:00", "back_by": "21:00"}


def test_map_flag(make):
    ids = [i for i, _ in GOLDEN]
    c = TestClient(create_app(make([draft(*GOLDEN), narrative(ids), draft(*GOLDEN), narrative(ids)])))
    assert c.post("/plan", json=BODY).json()["map"] is None
    j = c.post("/plan?map=true", json=BODY).json()
    assert j["map"]["type"] == "FeatureCollection" and len(kinds(j["map"], "visit")) == 3


def test_no_plan_has_no_map(make):
    c = TestClient(create_app(make([])))
    j = c.post("/plan?use_llm=false&map=true", json={**BODY, "date": "2026-12-19", "leave_after": "21:30", "back_by": "21:45"}).json()
    assert j["status"] == "no_plan" and j["map"] is None


def test_meta_serves_the_tile_settings(make):
    m = TestClient(create_app(make([]))).get("/meta").json()
    assert m["tile_url"].startswith("https://") and "{z}" in m["tile_url"] and "OpenStreetMap" in m["tile_attribution"]


def test_cli_geojson(tmp_path, capsys):
    out = tmp_path / "day.geojson"
    code = main(["plan", "--from", "porto-campanha", "--date", "2026-10-24", "--after", "08:00", "--back-by", "21:00", "--no-llm",
                 "--geojson", str(out)])
    capsys.readouterr()
    g = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and g["type"] == "FeatureCollection" and {f["properties"]["kind"] for f in g["features"]} >= {"origin", "visit", "ride", "walk"}


# ---- 5.2 the page ----------------------------------------------------------------------------------------------------------
import glob
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(__file__)
PAGE = os.path.join(HERE, "..", "planner", "web", "static", "index.html")


def _node():
    found = shutil.which("node") or next(iter(sorted(glob.glob(os.path.expanduser("~/.vscode-server/bin/*/node")))), None)
    if not found:
        pytest.skip("no Node available")
    return found


def test_page_script_is_valid_javascript(tmp_path):
    import re
    html = open(PAGE, encoding="utf-8").read()
    js = tmp_path / "page.js"
    js.write_text("\n".join(re.findall(r"<script>(.*?)</script>", html, re.S)), encoding="utf-8")
    r = subprocess.run([_node(), "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "leaflet/1.9.4/leaflet.min.js" in html and "leaflet/1.9.4/leaflet.min.css" in html


def _run_page(plan, tmp_path):
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(plan.to_json()), encoding="utf-8")
    r = subprocess.run([_node(), os.path.join(HERE, "page_harness.js"), PAGE, str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_page_draws_every_feature_and_links_rows_to_the_map(pl, make, tmp_path):
    """Phase 2 page, fed a plan whose first two bus 36 pairs have cached paths and everything else has none."""
    plan = costa_nova_plan(pl, make)
    paths, _, _ = fixture_paths(pl, plan, ride_pairs=2)
    plan.map = mapview.build(plan, pl.engine, pl.lib, paths=paths)
    out = _run_page(plan, tmp_path)
    m = plan.map
    n = lambda k: len(kinds(m, k))
    assert out["solid"] == 1 and out["dashed"] == 0                                  # only the cached part of bus 36 is a real path
    straight = [f for f in m["features"] if f["properties"]["kind"] in ("ride", "walk", "idea_walk")]
    assert out["dotted"] >= len(straight) - 1                                        # every leg without a path is dotted grey
    assert out["stops"] == n("stop") and out["origin"] == 1 and out["clocks"] == n("free_time") == 1 and out["ideas"] == n("idea") == 2
    visits = n("visit")
    assert out["numbered"] == [str(i + 1) for i in range(visits)] and out["food"] == 1      # numbered in order; Canastra is the food place
    assert out["popups"] and out["hidden"] is False and out["views"] == ["whole", "dest"]
    assert out["attribution"] == "paths derived from OpenStreetMap"
    steps = {f["properties"]["step"] for f in m["features"] if "step" in f["properties"]}
    assert set(out["stepsWithFeatures"]) == steps and set(out["rows"]) == set(range(len(plan.timeline)))
    assert out["rowClick"] == {"selected": True, "popupOpened": True, "raised": True}
    assert out["mapClick"] == {"selected": True, "scrolled": True, "previousCleared": True} and out["keyboard"] is True
    assert "train" in out["legend"] and "bus" in out["legend"] and "walk" in out["legend"] and "no path found" in out["legend"]
    assert "follow their route as mapped in OpenStreetMap where available" in out["legend"] and "not the street route" not in out["legend"]
    assert "otherwise the shortest road path between stops" in out["legend"] and "Buses follow the roads" not in out["legend"]
    assert out["hiddenWithoutMap"] is True


def test_page_highlights_the_selected_path_and_dims_the_rest(pl, make, tmp_path):
    plan = costa_nova_plan(pl, make)
    paths, _, _ = fixture_paths(pl, plan, ride_pairs=2)
    plan.map = mapview.build(plan, pl.engine, pl.lib, paths=paths)
    out = _run_page(plan, tmp_path)
    assert out["highlight"] == {"casing": True, "casingWhite": True, "casingWider": True, "strongerStroke": True, "onTop": True,
                                "dimmed": True, "otherDimmed": True}
    assert out["clearedByClick"] == {"casing": True, "dimmed": 0, "row": True}
    assert out["clearedByEscape"] == {"casing": True, "dimmed": 0, "row": True}


def test_page_draws_a_day_with_no_paths_as_dotted_grey_lines(pl, make, tmp_path):
    plan = costa_nova_plan(pl, make)
    plan.map = mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({}))
    out = _run_page(plan, tmp_path)
    assert out["solid"] == 0 and out["dashed"] == 0 and out["dotted"] > 0                # a guess never looks like a real route
    assert out["rowClick"]["selected"] is True


# ---- Phase 2: paths from a cache (MapPlan.md 10.7), with a small fixture cache ------------------------------------------------
import gzip
import sys as _sys

_sys.path.insert(0, str(os.path.dirname(os.path.dirname(HERE))))
from mapdata.graph import decode as _dec, encode as _enc

from planner import paths as paths_mod


def _line(a, b, bend=0.0005):
    """A bent polyline between two (lat, lon) points, so a cached path is visibly not the straight one."""
    mid = ((a[0] + b[0]) / 2 + bend, (a[1] + b[1]) / 2 + bend)
    return [a, mid, b]


def _stop(pl, sid):
    for f in pl.engine.feeds:
        if sid in f.stops:
            return f.stops[sid][1:]
    raise KeyError(sid)


def fixture_paths(pl, plan, ride_pairs=2, walk_keys=()):
    """Cache the first `ride_pairs` stop pairs of the bus 36 ride and the given walks (key -> (a, b))."""
    from planner.mapview import _Resolver
    rs = _Resolver(plan, pl.engine, pl.lib)
    ride = next(e for e in plan.timeline if e.type == "ride" and e.route_short == "36")
    ids = rs.ride_stops(ride)
    data = {"meta": {"precision": 5}, "ride": {}, "walk": {}}
    for a, b in list(zip(ids, ids[1:]))[:ride_pairs]:
        data["ride"][f"{a}|{b}|bus"] = _enc(_line(_stop(pl, a), _stop(pl, b)))
    for key, (pa, pb) in walk_keys:
        data["walk"][key] = _enc(_line(pa, pb))
    return paths_mod.Paths(data), ride, ids


def test_a_ride_uses_the_cached_pairs_in_order_and_falls_back_per_pair(pl):
    plan = golden_plan(pl)
    paths, ride, ids = fixture_paths(pl, plan, ride_pairs=2)
    m = mapview.build(plan, pl.engine, pl.lib, paths=paths)
    f = next(x for x in kinds(m, "ride") if x["properties"]["route_short"] == "36" and x["properties"]["board_time"] == "11:34")
    p = f["properties"]
    assert p["path"] == "partial" and p["stops"] == len(ids)
    c = f["geometry"]["coordinates"]
    # two cached pairs of 3 points each (sharing the joint), then three straight pairs
    assert len(c) == 5 + 3 and p["gaps"] == [[4, 7]]
    first = _stop(pl, ids[0])
    assert c[0] == [first[1], first[0]]
    assert c[1] != [(_stop(pl, ids[0])[1] + _stop(pl, ids[1])[1]) / 2] * 2              # the bend of the cached path is there
    last = _stop(pl, ids[-1])
    assert c[-1] == [last[1], last[0]]
    full, _, _ = fixture_paths(pl, plan, ride_pairs=len(ids) - 1)
    f2 = next(x for x in kinds(mapview.build(plan, pl.engine, pl.lib, paths=full), "ride") if x["properties"]["board_time"] == "11:34")
    assert f2["properties"]["path"] == "osm" and f2["properties"]["gaps"] == []
    f3 = next(x for x in kinds(mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({})), "ride") if x["properties"]["board_time"] == "11:34")
    assert f3["properties"]["path"] == "straight" and f3["properties"]["gaps"] == [[0, len(ids) - 1]]


def test_a_walk_is_found_under_the_reversed_key_and_reversed(pl):
    plan = golden_plan(pl)
    w = next(e for e in plan.timeline if e.type == "walk" and e.to_id == "costa-nova-palheiros#1")
    a = pl.lib.get(CANASTRA)
    b = pl.lib.get("costa-nova-palheiros").all_points()[1]
    stored = _line((b[1], b[2]), (a.lat, a.lon), bend=0.0003)              # stored the other way round
    paths = paths_mod.Paths({"walk": {f"{w.to_id}|{w.from_id}": _enc(stored)}})
    m = mapview.build(plan, pl.engine, pl.lib, paths=paths)
    f = next(x for x in kinds(m, "walk") if x["properties"]["to"].endswith("Avenida José Estêvão)"))
    assert f["properties"]["path"] == "osm" and f["properties"]["gaps"] == []
    got = f["geometry"]["coordinates"]
    assert [round(x, 4) for x in got[0]] == [round(a.lon, 4), round(a.lat, 4)] and got[-1][1] == round(b[1], 5)
    assert f["properties"]["path_m"] >= f["properties"]["metres"] - 5
    others = [x for x in kinds(m, "walk") if x is not f]
    assert others and all(x["properties"]["path"] == "straight" and x["properties"]["gaps"] == [[0, 1]] for x in others)


def test_path_property_values(pl):
    plan = golden_plan(pl)
    none = mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({}))
    assert {f["properties"]["path"] for f in kinds(none, "ride") + kinds(none, "walk")} == {"straight"}
    half, _, _ = fixture_paths(pl, plan, ride_pairs=1)
    assert "partial" in {f["properties"]["path"] for f in kinds(mapview.build(plan, pl.engine, pl.lib, paths=half), "ride")}


def test_no_cache_means_straight_lines_and_one_warning(pl, monkeypatch, tmp_path):
    monkeypatch.setattr(paths_mod, "DEFAULT", tmp_path / "missing.json.gz")
    monkeypatch.setattr(paths_mod, "_cache", {})
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib)
    assert len(m["properties"]["warnings"]) == 1 and "straight lines" in m["properties"]["warnings"][0]
    assert {f["properties"]["path"] for f in kinds(m, "ride")} == {"straight"}


def test_a_cache_built_for_other_feeds_is_ignored_with_a_warning(pl, monkeypatch, tmp_path):
    f = tmp_path / "paths.json.gz"
    with gzip.open(f, "wt", encoding="utf-8") as h:
        json.dump({"meta": {"feeds": {"cp": "0" * 64, "av": "1" * 64}}, "ride": {"x|y|bus": "_p~iF~ps|U"}, "walk": {}}, h)
    monkeypatch.setattr(paths_mod, "DEFAULT", f)
    monkeypatch.setattr(paths_mod, "_cache", {})
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib)
    assert len(m["properties"]["warnings"]) == 1 and "other timetable feeds" in m["properties"]["warnings"][0]
    assert {x["properties"]["path"] for x in kinds(m, "ride")} == {"straight"}
    feeds = {x.id: x.sha256 for x in pl.engine.feeds}
    with gzip.open(f, "wt", encoding="utf-8") as h:
        json.dump({"meta": {"feeds": feeds}, "ride": {}, "walk": {}}, h)
    monkeypatch.setattr(paths_mod, "_cache", {})
    assert paths_mod.load(f, feeds)[0] is not None and paths_mod.load(f, feeds)[1] is None


def test_an_idea_has_its_own_walk_in_the_ideas_layer(pl, make):
    plan = costa_nova_plan(pl, make)
    m = mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({}))
    walks = kinds(m, "idea_walk")
    assert [w["properties"]["name"] for w in walks] == [i["properties"]["name"] for i in kinds(m, "idea")]
    assert all(w["properties"]["path"] == "straight" for w in walks)


REAL = paths_mod.DEFAULT


@pytest.mark.skipif(not REAL.exists(), reason="the path cache is not built (python -m mapdata.build)")
def test_with_the_real_cache_the_golden_and_costa_nova_days_follow_osm_paths(pl, make):
    for plan in (golden_plan(pl), costa_nova_plan(pl, make)):
        m = mapview.build(plan, pl.engine, pl.lib)
        assert m["properties"]["warnings"] == []
        legs = kinds(m, "ride") + kinds(m, "walk") + kinds(m, "idea_walk")
        bad = [(f["properties"]["kind"], f["properties"].get("route_short") or f["properties"].get("to") or f["properties"].get("name"),
                f["properties"]["path"]) for f in legs if f["properties"]["path"] != "osm"]
        assert bad == []


@pytest.mark.skipif(not REAL.exists(), reason="the path cache is not built (python -m mapdata.build)")
def test_bus_36_stays_on_the_road(pl):
    from mapdata.build import OSM_DIR, TileCache, cell_of
    from mapdata.graph import BUS, haversine
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib)
    line = next(f for f in kinds(m, "ride") if f["properties"]["route_short"] == "36")["geometry"]["coordinates"]
    assert len(line) > 8                                             # many road vertices, not six stop-to-stop points
    inner = line[2:-2]
    pts = [inner[len(inner) * k // 4] for k in (1, 2, 3)]            # three spot-checks
    cache = TileCache()
    if not any(OSM_DIR.glob("hw_*.json.gz")):
        pytest.skip("the raw OSM tiles are not on this machine")
    for lon, lat in pts:
        r, c = cell_of(lat, lon)
        best = min((haversine(lat, lon, *co) for _, f, rv, nodes, coords in cache.ways(r, c) if (f | rv) & BUS for co in coords), default=1e9)
        assert best < 50                                              # within 50 m of an OSM road vertex it runs on


# ---- Phase 3: buses follow their mapped route where OSM has it (MapPlan.md 11) ---------------------------------------------------
def _bus_ride(pl, plan, route_short="36"):
    return next(e for e in plan.timeline if e.type == "ride" and e.route_short == route_short)


def sourced_paths(pl, plan, sources):
    """A cache with the bus 36 ride's pairs all stored, and `sources` = their ride_source values in order."""
    from planner.mapview import _Resolver
    ride = _bus_ride(pl, plan)
    ids = _Resolver(plan, pl.engine, pl.lib).ride_stops(ride)
    data = {"meta": {"precision": 5, "ride_source": {}}, "ride": {}, "walk": {}}
    for (a, b), src in zip(zip(ids, ids[1:]), sources):
        data["ride"][f"{a}|{b}|bus"] = _enc(_line(_stop(pl, a), _stop(pl, b)))
        if src:
            data["meta"]["ride_source"][f"{a}|{b}|bus"] = src
    return paths_mod.Paths(data), len(ids) - 1


def test_route_source_is_relation_mixed_or_shortest(pl):
    plan = golden_plan(pl)
    _, n = sourced_paths(pl, plan, [])
    def source(srcs):
        paths, _ = sourced_paths(pl, plan, srcs)
        m = mapview.build(plan, pl.engine, pl.lib, paths=paths)
        return next(f for f in kinds(m, "ride") if f["properties"]["route_short"] == "36" and f["properties"]["board_time"] == "11:34")["properties"]
    assert source(["relation:7"] * n)["route_source"] == "relation"
    assert source(["relation:7"] + ["shortest"] * (n - 1))["route_source"] == "mixed"
    assert source(["shortest"] * n)["route_source"] == "shortest"
    assert source([""] * n)["route_source"] == ""                                      # an older cache, no claim
    assert source(["relation:7"] * (n - 1))["route_source"] == "mixed"                  # a pair with no cached path is not a relation pair
    rail = next(f for f in kinds(mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({})), "ride") if f["properties"]["mode"] == "rail")
    assert rail["properties"]["route_source"] == ""                                     # rail and the ferry keep the Phase 2 behaviour
    assert {f["properties"]["route_source"] for f in kinds(mapview.build(plan, pl.engine, pl.lib, paths=False), "ride")} == {""}   # no cache: no claim


def test_the_popup_and_legend_follow_the_route_source(pl, make, tmp_path):
    plan = golden_plan(pl)
    for srcs, text in ((["relation:7"], "Route: as mapped in OpenStreetMap"),
                       (["relation:7", "shortest"], "Route: partly as mapped in OpenStreetMap, partly the shortest road path"),
                       (["shortest"], "Route: shortest road path between stops (this line is not mapped in OpenStreetMap)")):
        _, n = sourced_paths(pl, plan, [])
        paths, _ = sourced_paths(pl, plan, [srcs[0]] * n if len(srcs) == 1 else [srcs[0]] + [srcs[1]] * (n - 1))
        plan.map = mapview.build(plan, pl.engine, pl.lib, paths=paths)
        out = _run_page(plan, tmp_path)
        popups = [p for p in out["ridePopups"] if "bus 36" in p.lower() or "36" in p]
        assert any(text in p for p in popups), (text, popups)
        if text.startswith("Route: as"):
            assert not any("shortest road path" in p for p in popups)
    assert all("Route:" not in p for p in _run_page(_plain(plan, pl), tmp_path)["ridePopups"] if "rail" in p.lower())      # no line for trains


def _plain(plan, pl):
    plan.map = mapview.build(plan, pl.engine, pl.lib, paths=paths_mod.Paths({}))
    return plan


@pytest.mark.skipif(not REAL.exists(), reason="the path cache is not built (python -m mapdata.build)")
def test_real_cache_line_12_is_a_relation_line_18_is_shortest_and_the_golden_bus_reports_what_the_build_chose(pl):
    paths, why = paths_mod.load(feeds={f.id: f.sha256 for f in pl.engine.feeds})
    assert paths is not None and why is None and paths.meta["ride_source"] and paths.meta["relations"]
    f = next(f for f in pl.engine.feeds if f.id == "av")

    def ride_on(route, want):
        """A Ride over the longest run of consecutive stop pairs of one trip whose cache source is `want`."""
        best = None
        for t in f.trips.values():
            if t.route != route:
                continue
            ids = [r[0] for r in t.times]
            run = []
            for i, (a, b) in enumerate(zip(ids, ids[1:])):
                if paths.ride_source(a, b) == want and paths.ride(a, b, "bus"):
                    run.append(i)
                    if best is None or len(run) > best[0]:
                        best = (len(run), t, run[0], i + 1)
                else:
                    run = []
        assert best, f"no {want} pairs on {route}"
        n, t, i0, i1 = best
        r0, r1 = t.times[i0], t.times[i1]
        hh = lambda s: f"2026-10-24T{s // 3600 % 24:02d}:{s % 3600 // 60:02d}:{s % 60:02d}+01:00"
        return Ride(mode="bus", route_short=f.routes[route].short, route_long="", headsign=t.headsign, trip_id=t.id, board_stop="b", board_time=hh(r0[2]),
                    alight_stop="a", alight_time=hh(r1[1]), feed="av", board_stop_id=r0[0], alight_stop_id=r1[0])
    for route, want in (("av:ab-12", "relation"), ("av:bw-18", "shortest")):
        e = ride_on(route, want)
        m = mapview.build(DayPlan(request=request(), source="llm", timeline=[e]), pl.engine, pl.lib)
        r = kinds(m, "ride")[0]["properties"]
        assert r["route_source"] == want and r["path"] == "osm", (route, r)
    m = mapview.build(golden_plan(pl), pl.engine, pl.lib)
    got = {f["properties"]["board_time"]: f["properties"]["route_source"] for f in kinds(m, "ride") if f["properties"]["mode"] == "bus"}
    assert set(got.values()) <= {"relation", "mixed", "shortest"} and got            # whichever the build chose: the report states it
