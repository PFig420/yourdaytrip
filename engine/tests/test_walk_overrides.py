"""Walking overrides from real OSM paths (WalkBarriers.md): the longer of the engine's estimate and the OSM path, per pair."""
import math
from pathlib import Path

import pytest

import reach
from conftest import AGUEDA, BUS_TERMINAL, CAMPANHA, COSTA_NOVA, hhmmss, make_feed_zip

SAT = "2026-10-24"
from reach.gtfs import sha256_file

LAT = 40.0
M_LON = 1 / (111_320 * math.cos(math.radians(LAT)))      # degrees of longitude per metre at this latitude


def east(m):
    return -8.0 + m * M_LON


def build(tmp_path, rows, header=True, stale=False, name="ov"):
    """A synthetic engine with stops A (0 m), B (150 m), C (300 m) east of each other and one trip A -> C."""
    root = tmp_path / name
    cfg = root / "engine" / "config"
    cfg.mkdir(parents=True)
    zp = root / "x.zip"
    stops = {"A": (LAT, east(0)), "B": (LAT, east(150)), "C": (LAT, east(300)), "D": (LAT, east(1500))}
    make_feed_zip(zp, stops, {"t1": [("A", "09:00:00", "09:00:00", True, True), ("D", "09:20:00", "09:20:00", True, True)]})
    sha = sha256_file(str(zp))
    (cfg / "feeds.toml").write_text(f'[[feeds]]\nid = "x"\npath = "{zp}"\nsha256 = "{sha}"\n')
    (cfg / "engine.toml").write_text("walk_speed_mps = 1.2\nwalk_detour = 1.3\ntransfer_max_m = 400\naccess_max_m = 1200\n"
                                     'access_max_limit_m = 2000\ntransfer_buffer_s = 120\ncache_dates = 8\n'
                                     'walk_overrides = "data/paths/walk_overrides.csv"\n')
    (cfg / "walk_barriers.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    if rows is not None:
        d = root / "data" / "paths"
        d.mkdir(parents=True)
        h = f"# built: 2026-10-28\n# feeds: x={'0' * 64 if stale else 'x:' + sha}\n".replace("x:", "")
        body = "from_id,to_id,metres,kind,note\n" + "\n".join(rows) + "\n"
        (d / "walk_overrides.csv").write_text((h if header else "") + body)
    return reach.load(str(cfg))


def secs(m):
    return math.ceil(m * 1.3 / 1.2)


def test_no_file_changes_nothing(tmp_path):
    e = build(tmp_path, None)
    a, b = e.net.index["x:A"], e.net.index["x:B"]
    assert dict(e.net.footpaths[a])[b] == secs(150) and e.overrides.state == "missing" and e.warnings == []
    assert e.health()["walk_overrides"]["state"] == "missing"


def test_real_engine_without_the_file_is_the_old_engine(eng_plain):
    assert eng_plain.overrides.state == "off" and not eng_plain.warnings
    net = eng_plain.net
    a, b = net.index["cp:94_38000"], net.index["av:terminal-rodoviario-de-aveiro"]
    assert dict(net.footpaths[a])[b] == dict(net.footpaths[b])[a] == 208            # 191 m x 1.3 / 1.2
    j = eng_plain.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA)
    assert [w.seconds for w in j.legs if w.type == "walk"] == [208] and hhmmss(j.to_json()["arrive"]) == "10:20:00"
    j = eng_plain.earliest(BUS_TERMINAL, SAT, "09:00", ("sao-jacinto", 40.660682, -8.729750))
    assert [w.seconds for w in j.legs if w.type == "walk"] == [73]


def test_a_longer_osm_path_lengthens_transfers_in_both_directions(tmp_path):
    e = build(tmp_path, ["x:A,x:B,600,osm,"])
    a, b = e.net.index["x:A"], e.net.index["x:B"]
    assert dict(e.net.footpaths[a])[b] == 500 and dict(e.net.footpaths[b])[a] == 500          # ceil(600 / 1.2), no detour factor
    assert e.health()["walk_overrides"]["state"] == "ok" and e.health()["walk_overrides"]["rows"] == 1
    assert e.net.leg_metres("x:A", "x:B", 150.0) == 600.0 and e.net.leg_metres("x:B", "x:A", 150.0) == 600.0
    assert e.net.leg_metres("x:A", "x:C", 300.0) == 300.0                                       # no row: the straight metres


def test_a_shorter_osm_path_changes_nothing(tmp_path):
    e = build(tmp_path, ["x:A,x:B,100,osm,"])
    a, b = e.net.index["x:A"], e.net.index["x:B"]
    assert dict(e.net.footpaths[a])[b] == secs(150)
    assert e.net.leg_metres("x:A", "x:B", 150.0) == 150.0
    e2 = build(tmp_path, ["x:A,x:B,162,osm,"], name="ov2")                                     # 162 m = 135 s < 163 s: still the estimate
    assert dict(e2.net.footpaths[e2.net.index["x:A"]])[e2.net.index["x:B"]] == secs(150)


def test_osm_gap_keeps_the_estimate_and_blocked_removes_the_walk(tmp_path):
    e = build(tmp_path, ["x:A,x:B,,osm-gap,OSM misses the crossing", "x:B,x:C,,blocked,no path"])
    a, b, c = (e.net.index[f"x:{k}"] for k in "ABC")
    assert dict(e.net.footpaths[a])[b] == secs(150)
    assert b not in dict(e.net.footpaths[c]) and c not in dict(e.net.footpaths[b])


def test_access_egress_and_point_to_point_walks_follow_the_override(tmp_path):
    e = build(tmp_path, ["p1,x:A,,osm-gap,", "p1,x:B,900,osm,", "p1,p2,700,osm,", "p3,x:A,,blocked,"])
    p1, p3 = ("p1", LAT, east(100)), ("p3", LAT, east(40))
    near = {e.net.ids[s]: (sec, m) for s, sec, m in e.net.near(p1[1], p1[2], 1200, "p1")}
    assert near["x:A"] == (secs(100), pytest.approx(100, abs=1)) and near["x:B"][0] == 750 and near["x:B"][1] == 900     # B: 50 m straight, 900 m real
    assert "x:A" not in {e.net.ids[s] for s, _, _ in e.net.near(p3[1], p3[2], 1200, "p3")}
    sec, m = e.net.walk_between_points(p1[1], p1[2], LAT, east(400), 1200, "p1", "p2")
    assert (sec, m) == (math.ceil(700 / 1.2), 700)
    assert e.net.walk_between_points(p1[1], p1[2], LAT, east(400), 1200, "p1", "p9")[0] == secs(300)
    j = e.earliest(p1, SAT, "08:00", ("p2", LAT, east(400)))                                    # a walk-only journey
    w = [leg for leg in j.legs if leg.type == "walk"][0]
    assert (w.seconds, w.metres) == (math.ceil(700 / 1.2), 700)


def test_distance_limits_still_use_the_straight_line(tmp_path):
    e = build(tmp_path, ["p1,x:D,100,osm,"])                         # a shorter path does not bring a far stop into range
    assert "x:D" not in {e.net.ids[s] for s, _, _ in e.net.near(LAT, east(0), 1200, "p1")}
    assert e.net.walk_between_points(LAT, east(0), LAT, east(1500), 1200, "p1", "x:D") is None


def test_stale_feed_hashes_ignore_the_whole_file_with_one_warning(tmp_path):
    e = build(tmp_path, ["x:A,x:B,600,osm,"], stale=True)
    a, b = e.net.index["x:A"], e.net.index["x:B"]
    assert dict(e.net.footpaths[a])[b] == secs(150)
    assert e.overrides.state == "stale" and e.health()["walk_overrides"]["state"] == "stale"
    stale = [w for w in e.warnings if "walking overrides" in w]
    assert len(stale) == 1 and "other feeds" in stale[0] and "mapdata.build" in stale[0]
    e2 = build(tmp_path, ["x:A,x:B,600,osm,"], header=False, name="nohead")                     # no header: also stale
    assert e2.overrides.state == "stale"


def test_unknown_ids_are_ignored_silently(tmp_path):
    e = build(tmp_path, ["x:A,x:B,600,osm,", "gone-place,x:A,900,osm,", "x:Z,x:A,5,osm,"])
    assert e.overrides.state == "ok" and not [w for w in e.warnings if "walking" in w]
    assert dict(e.net.footpaths[e.net.index["x:A"]])[e.net.index["x:B"]] == 500


# ---- with the real file ---------------------------------------------------------------------------------------------------------
REAL = Path(__file__).resolve().parents[2] / "data" / "paths" / "walk_overrides.csv"
needs_real = pytest.mark.skipif(not REAL.exists(), reason="data/paths/walk_overrides.csv is not built (python -m mapdata.overrides)")


@needs_real
def test_real_file_is_loaded_and_matches_the_feeds(eng):
    h = eng.health()["walk_overrides"]
    assert h["state"] == "ok" and h["rows"] > 5000 and h["built"] and h["hash"] and not [w for w in eng.warnings if "walking" in w]


@needs_real
def test_escola_no1_transfer_is_now_about_657_m(eng):
    net = eng.net
    a, b = net.index["av:escola-no1-a"], net.index["av:escola-no1-b"]
    assert net.stop_metres(a, b) < 50                                    # 44 m engine metres ...
    assert dict(net.footpaths[a])[b] == dict(net.footpaths[b])[a] == math.ceil(658 / 1.2) == 549   # ... 658 m on foot, both ways
    assert net.leg_metres("av:escola-no1-a", "av:escola-no1-b", net.stop_metres(a, b)) == 658       # (the review sheet says 657: the build's
    #                                                                                                  route length; the file holds the drawn polyline's)


@needs_real
def test_cacia_walk_from_bar_7_takes_the_975_m_path(eng, eng_plain):
    """Bar 7 -> Cacia station: 160 m for the engine (a 123 m straight line x 1.3 = 134 s), 975 m on OSM paths (813 s, 13.5 minutes)."""
    bar = next(p for p in ("bar-7-cacia-aveiro",))
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "places"))
    import places
    lib = places.load(str(Path(__file__).resolve().parents[2] / "places/data"), engine=eng,
                      config_dir=str(Path(__file__).resolve().parents[2] / "places/config"))
    p = lib.places[bar]
    pt = (bar, p.lat, p.lon)
    cacia = eng.net.index["cp:94_38075"]
    new = {eng.net.ids[s]: (sec, m) for s, sec, m in eng.net.near(p.lat, p.lon, 1200, bar)}["cp:94_38075"]
    old = {eng_plain.net.ids[s]: (sec, m) for s, sec, m in eng_plain.net.near(p.lat, p.lon, 1200, bar)}["cp:94_38075"]
    assert old[0] == 134 and new == (math.ceil(975 / 1.2), 975.0) and new[0] == 813
    j = eng.earliest(pt, SAT, "09:00", AGUEDA)                         # a journey that takes the train at Cacia
    assert j is not None
    first = j.legs[0]
    assert first.type == "walk" and first.to_id == "cp:94_38075" and first.seconds >= 813 and first.metres == 975
