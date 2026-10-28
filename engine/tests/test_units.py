import datetime as dt
import math

import pytest

from reach import BadInput
from reach.calendar import active_services, iso_local, to_instant
from reach.gtfs import route_mode
from reach.network import haversine

from conftest import BUS_TERMINAL, COSTA_NOVA, FORTE_FERRY, SAO_JACINTO, make_engine

UTC = dt.timezone.utc


def utc(*a):
    return int(dt.datetime(*a, tzinfo=UTC).timestamp())


# ---- time -----------------------------------------------------------------
def test_time_past_midnight_summer():
    d = dt.date(2026, 10, 24)
    assert to_instant(d, 24 * 3600 + 30 * 60) == utc(2026, 10, 24, 23, 30)
    assert iso_local(to_instant(d, 24 * 3600 + 30 * 60)).startswith("2026-10-25T00:30:00+01:00")
    assert to_instant(d, 25 * 3600 + 43 * 60) == utc(2026, 10, 25, 0, 43)
    assert iso_local(to_instant(d, 25 * 3600 + 43 * 60)).startswith("2026-10-25T01:43:00+01:00")


def test_time_after_clocks_go_back():
    d = dt.date(2026, 10, 25)
    assert to_instant(d, 0) == utc(2026, 10, 25, 0, 0)          # noon - 12 h = 00:00 UTC (01:00 summer time)
    assert to_instant(d, 9 * 3600 + 35 * 60) == utc(2026, 10, 25, 9, 35)
    assert iso_local(to_instant(d, 9 * 3600 + 35 * 60)).startswith("2026-10-25T09:35:00+00:00")


def test_time_march_2027_change():
    d = dt.date(2027, 3, 28)    # clocks go forward at 01:00 UTC
    assert to_instant(d, 0) == utc(2027, 3, 27, 23, 0)          # noon(local WEST) = 11:00 UTC, minus 12 h
    assert to_instant(d, 9 * 3600 + 35 * 60) == utc(2027, 3, 28, 8, 35)
    assert iso_local(to_instant(d, 9 * 3600 + 35 * 60)).startswith("2027-03-28T09:35:00+01:00")
    d0 = dt.date(2027, 3, 27)
    assert to_instant(d0, 25 * 3600) == utc(2027, 3, 28, 1, 0)      # 25:00 on a winter-time day = 01:00 UTC = 02:00 local summer


# ---- calendars ------------------------------------------------------------
def test_calendar_styles(eng):
    cp = next(f for f in eng.feeds if f.id == "cp")
    av = next(f for f in eng.feeds if f.id == "av")
    assert cp.calendar and not av.calendar
    # type 2 removal: pick a service that is removed on a date its weekday/date range would otherwise allow
    removed_hit = added_hit = None
    for sid, dates in cp.removed.items():
        for ds in sorted(dates):
            d = dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
            days, start, end = cp.calendar.get(sid, ([False] * 7, "9", "0"))
            if days[d.weekday()] and start <= ds <= end:
                removed_hit = (sid, d)
                break
        if removed_hit:
            break
    assert removed_hit, "no type 2 removal found in cp"
    sid, d = removed_hit
    assert sid not in active_services(cp, d)
    for sid, dates in cp.added.items():
        for ds in sorted(dates):
            d = dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
            days, start, end = cp.calendar.get(sid, ([False] * 7, "9", "0"))
            if not (days[d.weekday()] and start <= ds <= end):
                added_hit = (sid, d)
                break
        if added_hit:
            break
    if added_hit:   # only present if cp has type 1 rows outside its calendar
        assert added_hit[0] in active_services(cp, added_hit[1])
    # a plain calendar.txt service is active on its date
    sid, (days, start, end) = next((k, v) for k, v in cp.calendar.items() if any(v[0]))
    d = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    while not days[d.weekday()]:
        d += dt.timedelta(days=1)
    if d.strftime("%Y%m%d") not in cp.removed.get(sid, ()):
        assert sid in active_services(cp, d)


def test_calendar_dates_only_feed(eng):
    av = next(f for f in eng.feeds if f.id == "av")
    sid, dates = next(iter(av.added.items()))
    ds = sorted(dates)[0]
    d = dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
    assert sid in active_services(av, d)
    assert sid not in active_services(av, d - dt.timedelta(days=400))


def test_extended_route_types():
    assert route_mode(109) == ("rail", 2)
    assert route_mode(2) == ("rail", 2)
    assert route_mode(4) == ("ferry", 4) and route_mode(1000) == ("ferry", 4)
    assert route_mode(714) == ("bus", 3) and route_mode(204) == ("coach", 3)
    assert route_mode(1701)[0] == "other"        # never rejected


def test_measured_facts(eng):
    cp = next(f for f in eng.feeds if f.id == "cp")
    assert (len(cp.stops), len(cp.routes), len(cp.trips)) == (454, 186, 1945)
    assert sum(len(t.times) for t in cp.trips.values()) == 30780
    assert (cp.first_date, cp.last_date) == ("20251214", "20261212")
    assert {r.type for r in cp.routes.values()} == {2}


# ---- walking --------------------------------------------------------------
def test_walk_time_formula(eng):
    net = eng.net
    a, b = net.index["cp:94_38000"], net.index["av:terminal-rodoviario-de-aveiro"]
    m = net.stop_metres(a, b)
    assert 190 < m < 192
    assert eng.walk.seconds(m) == math.ceil(m * 1.3 / 1.2) == 208                # the engine's own estimate
    # with the walking overrides (WalkBarriers.md) this transfer takes the longer OSM path, 261 m = 218 s, in both directions
    want = 218 if eng.overrides.state == "ok" else 208
    assert dict(net.footpaths[a])[b] == want
    assert dict(net.footpaths[b])[a] == want


def test_haversine():
    assert haversine(0, 0, 0, 1) == pytest.approx(111194.9, rel=1e-4)


def test_barrier_cases(eng):
    net = eng.net
    # Forte da Barra ferry quay -> Sao Jacinto crosses the channel; Farol -> Costa Nova does not.
    assert net.barriers.blocks(FORTE_FERRY[1], FORTE_FERRY[2], SAO_JACINTO[1], SAO_JACINTO[2])
    farol = net.index["av:farol-forte-da-barra"]
    assert not net.barriers.blocks(net.lat[farol], net.lon[farol], COSTA_NOVA[1], COSTA_NOVA[2])
    # no access edge from the Forte quay point to Sao Jacinto stops
    near = {net.ids[s] for s, _sec, _m in net.near(FORTE_FERRY[1], FORTE_FERRY[2], 2000)}
    assert "av:s-jacinto" not in near
    assert "av:forte-da-barra-ferry" in near


def test_access_radius_zero(eng):
    net = eng.net
    s = net.index["av:terminal-rodoviario-de-aveiro"]
    assert [x[0] for x in net.near(net.lat[s], net.lon[s], 0)].count(s) == 1
    assert net.near(net.lat[s] + 0.0005, net.lon[s], 0) == []
    with pytest.raises(ValueError):
        net.near(net.lat[s], net.lon[s], 2001)


# ---- synthetic feeds --------------------------------------------------------
A, B, C = (40.0000, -8.0000), (40.0100, -8.0000), (40.0200, -8.0000)


def _eng2(tmp_path, change_dep):
    stops = {"A": A, "B": B, "C": C}
    trips = {"t1": [("A", "10:00:00", "10:00:00", 0, 0), ("B", "10:30:00", "10:30:00", 0, 0)],
             "t2": [("B", change_dep, change_dep, 0, 0), ("C", "11:30:00", "11:30:00", 0, 0)]}
    return make_engine(tmp_path, {"x": (stops, trips)})


def _q(e):
    return e.earliest(("o", *A), "2026-10-24", "10:00", ("d", *C), max_walk_m=0)


def test_transfer_buffer(tmp_path):
    assert _q(_eng2(tmp_path / "ok", "10:32:00")) is not None                    # exactly 120 s
    assert _q(_eng2(tmp_path / "short", "10:31:59")) is None                     # 119 s
    j = _q(_eng2(tmp_path / "ok2", "10:32:00"))
    assert j.rides == 2 and j.arrive == j.legs[-1].alight_time


def test_first_boarding_needs_no_buffer(tmp_path):
    e = _eng2(tmp_path, "10:32:00")
    assert e.earliest(("o", *A), "2026-10-24", "10:00:00", ("d", *B), max_walk_m=0).rides == 1
    assert e.earliest(("o", *A), "2026-10-24", "10:00:01", ("d", *B), max_walk_m=0) is None


def test_pickup_dropoff_rules(tmp_path):
    stops = {"A": A, "B": B, "C": C}
    trips = {"t": [("A", "10:00:00", "10:00:00", 1, 0),      # no boarding at A
                   ("B", "10:10:00", "10:10:00", 0, 1),      # no alighting at B
                   ("C", "10:20:00", "10:20:00", 0, 0)]}
    e = make_engine(tmp_path, {"x": (stops, trips)})
    assert e.earliest(("o", *A), "2026-10-24", "09:00", ("d", *C), max_walk_m=0) is None   # A not boardable
    assert e.earliest(("o", *B), "2026-10-24", "09:00", ("d", *C), max_walk_m=0).arrive == \
        e.earliest(("o", *B), "2026-10-24", "09:00", ("d", *C), max_walk_m=0).legs[0].alight_time
    stops2 = {"A": A, "B": B, "C": C}
    trips2 = {"t": [("A", "10:00:00", "10:00:00", 0, 0), ("B", "10:10:00", "10:10:00", 0, 1),
                    ("C", "10:20:00", "10:20:00", 0, 0)]}
    e2 = make_engine(tmp_path / "b", {"x": (stops2, trips2)})
    # B is not alightable, but staying on the trip to C is fine
    assert e2.earliest(("o", *A), "2026-10-24", "09:00", ("d", *C), max_walk_m=0).rides == 1
    assert e2.earliest(("o", *A), "2026-10-24", "09:00", ("d", *B), max_walk_m=0) is None


def test_id_prefixing(tmp_path):
    stops = {"S1": A, "S2": B}
    trips = {"T": [("S1", "10:00:00", "10:00:00", 0, 0), ("S2", "10:10:00", "10:10:00", 0, 0)]}
    e = make_engine(tmp_path, {"f1": (stops, trips), "f2": (stops, trips)})
    assert "f1:S1" in e.net.index and "f2:S1" in e.net.index
    assert len(e.net.ids) == 4 and len(e.tt.tpl) == 2
    assert set(e.tt.tpl) == {"f1:T", "f2:T"}


def test_past_midnight_trip_and_overnight_query(tmp_path):
    stops = {"A": A, "B": B}
    trips = {"t": [("A", "24:10:00", "24:10:00", 0, 0), ("B", "24:40:00", "24:40:00", 0, 0)]}
    e = make_engine(tmp_path, {"x": (stops, trips)})
    # service date 2026-10-24; query on the 25th at 00:05 local finds it (D-1 trips)
    j = e.earliest(("o", *A), "2026-10-25", "00:05", ("d", *B), max_walk_m=0)
    assert j is not None and j.to_json()["arrive"]["local"].startswith("2026-10-25T00:40:00")


def test_bad_options(eng):
    with pytest.raises(BadInput):
        eng.earliest(("o", 40.6, -8.6), "2026-10-24", "08:00", ("d", 40.7, -8.6), max_walk_m=5000)
    with pytest.raises(BadInput):
        eng.earliest(("o", 40.6, -8.6), "2026-10-24", "08:00", ("d", 40.7, -8.6), bogus=1)


def test_walk_seconds(eng):
    # 1000 m straight line * 1.3 detour / 1.2 m/s, rounded up
    a = (40.6125, -8.74974)
    b = (a[0] + 1000 / 111_195, a[1])
    assert abs(eng.walk_seconds(a, b) - math.ceil(haversine(*a, *b) * 1.3 / 1.2)) == 0
    assert eng.walk_seconds(a, a) == 0
    # the Forte ferry quay and São Jacinto are separated by a barrier (the ria)
    assert eng.walk_seconds(FORTE_FERRY[1:], SAO_JACINTO[1:]) is None
