"""Golden journeys on the real feeds. Skipped (naming the feed) if a feed hash differs from conftest.GOLDEN_HASHES.

G2, G4, G8, G9 equal the values in ReachabilityEngine.md §10.2. G1, G3, G5, G6, G7, G10 differ from the spec
because aveiro.zip was rebuilt after the spec was written (Issues.md #12: Costa Nova times restored); the
expected values here were recomputed and cross-checked against the feed rows (see README "Golden values").
"""
import datetime as dt

import pytest

from conftest import (AGUEDA, BUS_TERMINAL, CAMPANHA, COSTA_NOVA, SAO_JACINTO, hhmmss)

SAT = "2026-10-24"


def rides(j):
    return [l for l in j.legs if l.type == "ride"]


def walks(j):
    return [l for l in j.legs if l.type == "walk"]


def test_g1_bus_terminal_to_costa_nova(golden_eng):
    j = golden_eng.earliest(BUS_TERMINAL, SAT, "09:00", COSTA_NOVA)
    assert hhmmss(j.to_json()["arrive"]) == "09:40:00"       # spec: 09:31:00 (that was the Farol time)
    r = rides(j)
    assert len(r) == 1 and r[0].route_short == "36" and r[0].trip_id == "av:bw-36-p20t2c3"
    assert r[0].board_stop == "Terminal Rodoviário de Aveiro" and hhmmss(r[0].to_json()["board_time"]) == "09:00:00"


def test_g2_bus_terminal_to_sao_jacinto(golden_eng):
    j = golden_eng.earliest(BUS_TERMINAL, SAT, "09:00", SAO_JACINTO)
    assert hhmmss(j.to_json()["arrive"]) == "10:35:00"
    r, w = rides(j), walks(j)
    assert [x.trip_id for x in r] == ["av:bw-36-p20t2c4", "av:ab-ferry-b4t3"]
    assert (hhmmss(r[0].to_json()["board_time"]), hhmmss(r[0].to_json()["alight_time"])) == ("09:35:00", "09:57:00")
    assert r[0].alight_stop == "Forte da Barra - Ferry"
    assert (hhmmss(r[1].to_json()["board_time"]), hhmmss(r[1].to_json()["alight_time"])) == ("10:20:00", "10:35:00")
    assert r[1].board_stop == "FORTE DA BARRA (FERRY)" and r[1].alight_stop == "S. JACINTO"
    # 76 s with the walking overrides, 73 s without (test_walk_overrides.py). The one walk is the transfer between the two Forte da
    # Barra ferry stops (av:forte-da-barra-ferry-2 -> av:forte-da-barra-ferry): about 67 m in a straight line (x 1.3 = 73 s), 91 m on
    # the OSM footpath (= 76 s)
    assert [x.seconds for x in w] == [76]
    # no walking leg crosses the channel
    net = golden_eng.net
    for leg in w:
        a, b = net.index[leg.from_id], net.index[leg.to_id]
        assert not net.barriers.blocks(net.lat[a], net.lon[a], net.lat[b], net.lon[b])


def test_g3_campanha_to_costa_nova(golden_eng):
    j = golden_eng.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA)
    assert hhmmss(j.to_json()["arrive"]) == "10:20:00"       # spec: 10:12:00
    r, w = rides(j), walks(j)
    assert r[0].route_long == "Linha de Aveiro" or r[0].route_short == "Linha de Aveiro"
    assert (r[0].board_stop, hhmmss(r[0].to_json()["board_time"]), r[0].alight_stop,
            hhmmss(r[0].to_json()["alight_time"])) == ("Porto Campanha", "08:10:00", "Aveiro", "09:07:00")
    # 218 s with the walking overrides, 208 s without: the Terminal Rodoviário -> Aveiro station transfer is 191 m in a straight line
    # (x 1.3 = 248 m = 208 s) but the OSM footpath is 261 m (= 218 s)
    assert [x.seconds for x in w] == [218]
    assert r[1].trip_id == "av:bw-36-p20t2c4" and hhmmss(r[1].to_json()["board_time"]) == "09:35:00"


def test_g4_campanha_to_agueda(golden_eng):
    j = golden_eng.earliest(CAMPANHA, SAT, "08:00", AGUEDA)
    assert hhmmss(j.to_json()["arrive"]) == "10:35:00"
    r = rides(j)
    assert len(r) == 2 and walks(j) == []
    assert (hhmmss(r[0].to_json()["board_time"]), r[0].alight_stop, hhmmss(r[0].to_json()["alight_time"])) == \
        ("08:10:00", "Aveiro", "09:07:00")
    assert r[1].route_short == "R" and hhmmss(r[1].to_json()["board_time"]) == "09:58:00"


def test_g5_latest_departures(golden_eng):
    e = golden_eng
    c = e.latest(COSTA_NOVA, SAT, CAMPANHA, "21:00")
    assert hhmmss(c.to_json()["depart"]) == "18:45:00"       # spec: 18:10:00
    assert rides(c)[0].trip_id == "av:bw-36-p21t2c14"
    s = e.latest(SAO_JACINTO, SAT, CAMPANHA, "21:00")
    assert hhmmss(s.to_json()["depart"]) == "18:45:00"       # spec: 18:00:00
    assert rides(s)[0].trip_id == "av:ab-ferry-b3t11"
    a = e.latest(AGUEDA, SAT, CAMPANHA, "21:00")
    assert hhmmss(a.to_json()["depart"]) == "18:53:00" and rides(a)[0].route_short == "R"
    b = e.latest(BUS_TERMINAL, SAT, CAMPANHA, "21:00")
    assert hhmmss(b.to_json()["depart"]) == "20:11:32"
    assert [x.seconds for x in walks(b)] == [208] and rides(b)[0].route_short == "AP"
    assert hhmmss(rides(b)[0].to_json()["board_time"]) == "20:15:00"


def test_g6_windows(golden_eng):
    w = golden_eng.windows(CAMPANHA, SAT, "08:00", "21:00", [COSTA_NOVA, SAO_JACINTO, AGUEDA])
    got = {x.id: (hhmmss(x.to_json()["arrive"]), hhmmss(x.to_json()["leave"]), x.available_s, x.status)
           for x in w.windows}
    assert got == {
        "costa-nova": ("10:20:00", "18:45:00", 8 * 3600 + 25 * 60, "ok"),       # spec: 10:12 -> 18:10 (7 h 58)
        "sao-jacinto": ("10:35:00", "18:45:00", 8 * 3600 + 10 * 60, "ok"),      # spec: 10:35 -> 18:00 (7 h 25)
        "agueda": ("10:35:00", "18:53:00", 8 * 3600 + 18 * 60, "ok"),
    }


def test_g7_no_window(golden_eng):
    w = golden_eng.windows(CAMPANHA, SAT, "08:00", "12:00", [COSTA_NOVA]).windows[0]
    j = w.to_json()
    assert (hhmmss(j["arrive"]), hhmmss(j["leave"]), w.status) == ("10:20:00", "09:45:00", "no_window")


def test_g8_coverage(eng):
    assert eng.coverage("2026-12-12").missing_feeds == ()
    c = eng.coverage("2026-12-13")
    assert c.missing_feeds == ("cp",)
    assert c.warnings == ("No CP train data after 2026-12-12; trains are not included.",)
    assert eng.coverage("2026-10-18").missing_feeds == ("av",)      # av starts on 2026-10-19
    assert eng.coverage("2027-09-12").missing_feeds == ("cp", "av") or "cp" in eng.coverage("2027-09-12").missing_feeds


def test_g8_warning_reaches_results(eng):
    j = eng.windows(CAMPANHA, "2026-12-19", "08:00", "21:00", [COSTA_NOVA])
    assert j.coverage.missing_feeds == ("cp",) and j.warnings
    assert j.windows[0].status == "unreachable"      # no trains, and the bus feed alone cannot get there


def test_g9_dst_night_connections(eng):
    cn = eng.tt.window(dt.date(2026, 10, 25))
    s = eng.net.index["cp:94_2006"]
    midnight = int(dt.datetime(2026, 10, 24, 23, 0, tzinfo=dt.timezone.utc).timestamp())
    got = sorted(dt.datetime.fromtimestamp(cn.arr[i], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                 for i in range(cn.n)
                 if cn.to[i] == s and cn.trips[cn.tinst[i]][1] == dt.date(2026, 10, 24) and cn.arr[i] >= midnight)
    assert got == ["2026-10-24T23:09:30Z", "2026-10-24T23:30:00Z", "2026-10-25T00:43:00Z"]


def test_g10_sunday_costa_nova(golden_eng):
    """Was xfail(strict) for Issues.md #12 (20:25 bus only). #12 is resolved: now a plain regression test."""
    j = golden_eng.earliest(BUS_TERMINAL, "2026-10-25", "09:00", COSTA_NOVA)
    assert j is not None
    assert j.to_json()["arrive"]["local"][11:19] < "10:30:00"
