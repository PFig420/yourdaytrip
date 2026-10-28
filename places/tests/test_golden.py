"""Golden tests on fixture places with the real engine (PlacesLayer.md 10.2)."""
import datetime as dt

import pytest

from conftest import CAMPANHA

DATE = "2026-10-24"


@pytest.fixture(scope="module")
def sl(fixture_lib):
    return fixture_lib.shortlist(CAMPANHA, DATE, "08:00", "21:00", pace="relaxed", limit=50)


def hm(t):
    from reach.calendar import iso_local
    return iso_local(t)[11:16]


def by_id(sl, pid):
    for i in sl.sights + sl.food:
        if i.place["id"] == pid:
            return i
    return None


def reason(sl, pid):
    return next((e["reason"] for e in sl.excluded if e["id"] == pid), None)


def test_p1(sl):
    i = by_id(sl, "p1-costa-nova-sa")
    assert [(hm(s.start), hm(s.end)) for s in i.slots] == [("11:00", "13:00")]
    assert hm(i.arrive) == "10:20" and hm(i.leave) == "18:45"


def test_p2(sl):
    assert reason(sl, "p2-costa-nova-evening") == "closed"


def test_p3(sl):
    assert reason(sl, "p3-sao-jacinto-weekdays") == "closed"


def test_p4(sl):
    from places.sun import sun_times
    i = by_id(sl, "p4-agueda-daylight")
    s = i.slots[0]
    assert hm(s.start) == "10:35"
    sunset = sun_times(dt.date(2026, 10, 24), 40.57749168, -8.448231459)[1]
    assert hm(s.end) == f"{sunset // 3600:02d}:{sunset % 3600 // 60:02d}"
    assert s.end < i.leave and hm(i.leave) == "18:53"


def test_p5(sl):
    assert reason(sl, "p5-costa-nova-late") == "too_short"


def test_p6(sl):
    i = by_id(sl, "p6-costa-nova-lunch")
    assert i in sl.food and [(hm(s.start), hm(s.end)) for s in i.slots] == [("12:00", "15:00")]


def test_p7(sl):
    assert by_id(sl, "p7-near-p1").cluster == by_id(sl, "p1-costa-nova-sa").cluster
    assert by_id(sl, "p4-agueda-daylight").cluster != by_id(sl, "p1-costa-nova-sa").cluster


def test_p8(fixture_lib):
    s = fixture_lib.shortlist(CAMPANHA, DATE, "08:00", "12:00", limit=50)
    assert reason(s, "p1-costa-nova-sa") == "no_window"


def test_rain_and_journeys(fixture_lib):
    s = fixture_lib.shortlist(CAMPANHA, DATE, "08:00", "21:00", weather="rain", include_journeys=True)
    assert reason(s, "p1-costa-nova-sa") == "weather"
    j = by_id(s, "p6-costa-nova-lunch").to_json()
    assert j["outbound"]["legs"] and j["return"]["legs"]
    assert s.coverage is not None


def test_max_walk_m_second_call(sl, fixture_lib):
    # same access point; only the record that sets max_walk_m (1500 > the 1,200 m default) is reachable
    assert reason(sl, "p9a-sao-jacinto-beach") == "unreachable"
    assert by_id(sl, "p9b-sao-jacinto-beach") is not None
    assert fixture_lib.get("p9b-sao-jacinto-beach").max_walk_m == 1500
    # the other places keep the default limit: P1 etc. are unchanged
    assert hm(by_id(sl, "p1-costa-nova-sa").arrive) == "10:20"


def test_fixed_entries(sl):
    # Costa Nova window is 10:20-18:45; open 09:00-17:00; tours at 09:30 (before arrival), 11:00, 13:00, 16:30.
    # Entry must be inside opening hours and the tour must end before `leave`; a tour may run past closing.
    i = by_id(sl, "p10-tour-costa-nova")
    assert [(hm(s.start), hm(s.end)) for s in i.slots] == [("11:00", "12:00"), ("13:00", "14:00"), ("16:30", "17:30")]
    assert i.to_json()["fixed_entries"] is True
    assert any("fixed entries at 11:00, 13:00, 16:30" in w for w in i.why)


def test_fixed_entries_respect_leave(fixture_lib):
    s = fixture_lib.shortlist(CAMPANHA, DATE, "08:00", "17:30", limit=50)     # leave ~15:20: the 16:30 tour is gone
    i = by_id(s, "p10-tour-costa-nova")
    assert i is None or all(hm(x.start) != "16:30" for x in i.slots)
