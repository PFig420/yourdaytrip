import datetime as dt

import pytest

from places.hours import HoursError, intervals, parse
from places.retrieval import BM25
from places.shortlist import usable_slots, wall
from places.sun import sun_times
from places.validate import check_place, copy_violation
from places.config import load_config
from places.model import from_dict

D = dt.date


def hrs(iv):
    return [(a / 3600, b / 3600) for a, b in iv]


def test_hours_examples():
    assert hrs(intervals("Tu-Su 10:00-12:30,14:00-17:30; Mo off; PH off", D(2026, 10, 24))) == [(10, 12.5), (14, 17.5)]
    assert intervals("Tu-Su 10:00-12:30,14:00-17:30; Mo off; PH off", D(2026, 10, 26)) == []
    assert hrs(intervals("24/7", D(2026, 10, 24))) == [(0, 24)]
    assert hrs(intervals("Mo-Sa 12:00-15:00,19:00-22:30; Su 12:00-15:00", D(2026, 10, 25))) == [(12, 15)]
    assert hrs(intervals("Mo-Sa 12:00-15:00,19:00-22:30; Su 12:00-15:00", D(2026, 10, 24))) == [(12, 15), (19, 22.5)]


def test_past_midnight_belongs_to_opening_day():
    assert hrs(intervals("Fr-Sa 21:00-02:00", D(2026, 10, 24))) == [(21, 26)]      # Saturday
    assert intervals("Fr-Sa 21:00-02:00", D(2026, 10, 25)) == []                    # Sunday


def test_public_holiday_off():
    h = "Tu-Su 10:00-12:30,14:00-17:30; Mo off; PH off"
    assert intervals(h, D(2026, 12, 8), {D(2026, 12, 8)}) == []                      # Tuesday, 8 Dec
    assert intervals(h, D(2026, 12, 8), False) != []
    assert hrs(intervals("Mo-Su 10:00-18:00; PH 10:00-13:00", D(2026, 12, 8), True)) == [(10, 13)]


def test_seasons():
    h = "Jun-Sep Mo-Su 10:00-19:00; Oct-May Tu-Su 10:00-17:00"
    assert hrs(intervals(h, D(2026, 10, 24))) == [(10, 17)]
    assert hrs(intervals(h, D(2027, 7, 17))) == [(10, 19)]
    assert intervals(h, D(2026, 10, 26)) == []                                       # Monday in Oct-May
    wrap = "Oct-Mar 10:00-16:00"
    assert intervals(wrap, D(2027, 1, 5)) and not intervals(wrap, D(2027, 6, 5))
    assert intervals("Mo-Su 9:00-18:00; Dec 24 off; Dec 25 off", D(2026, 12, 25)) == []
    assert intervals("Jul 01-Aug 31 10:00-12:00", D(2027, 8, 31)) and not intervals("Jul 01-Aug 31 10:00-12:00", D(2027, 9, 1))


@pytest.mark.parametrize("bad", ["", "sunrise-sunset", "Mo-Fr 10:00-12:00 by appointment", "week 1 10:00-12:00",
                                 "Mo 25:00-26:00", "Mo-Fr", "Mo 10:00-10:00", "Mo-Fr 24/7"])
def test_unsupported_rejected(bad):
    with pytest.raises(HoursError):
        parse(bad)


def test_daylight_against_almanac():
    # sunrise-sunset.org API, lat 40.6405 lng -8.6538 (Aveiro):
    #   https://api.sunrise-sunset.org/json?lat=40.6405&lng=-8.6538&date=2026-10-24&formatted=0
    #   2026-10-24 06:54:19Z / 17:43:16Z, 2027-06-21 05:02:12Z / 20:10:35Z (local = UTC+1 in summer time)
    for d, rise, sset in ((D(2026, 10, 24), 7 * 3600 + 54 * 60 + 19, 18 * 3600 + 43 * 60 + 16),
                          (D(2027, 6, 21), 6 * 3600 + 2 * 60 + 12, 21 * 3600 + 10 * 60 + 35)):
        r, s = sun_times(d, 40.6405, -8.6538)
        assert abs(r - rise) <= 180 and abs(s - sset) <= 180


class P:
    """Minimal place stand-in for usable_slots."""
    def __init__(self, **kw):
        self.hours, self.daylight_only, self.is_food, self.meals, self.entry_times = "", False, False, [], ""
        self.last_entry_min, self.lat, self.lon = 0, 40.64, -8.65
        self.__dict__.update(kw)


def test_meal_window_in_long_window(fixture_lib):
    d = D(2026, 10, 24)
    p = P(hours="Mo-Su 11:00-23:00", is_food=True, meals=["lunch"])
    slots, _ = usable_slots(p, d, wall(d, 10 * 3600), wall(d, 20 * 3600), 3600, False, fixture_lib.cfg)
    assert [(s.start, s.end) for s in slots] == [(wall(d, 12 * 3600), wall(d, 15 * 3600))]


def test_last_entry(fixture_lib):
    d = D(2026, 10, 24)
    p = P(hours="Mo-Su 10:00-18:00", last_entry_min=30)
    slots, _ = usable_slots(p, d, wall(d, 8 * 3600), wall(d, 21 * 3600), 3600, False, fixture_lib.cfg)
    assert slots[0].latest_start == wall(d, 17 * 3600)
    # arriving 17:15 is too late for a 60 minute visit
    slots, why = usable_slots(p, d, wall(d, 17 * 3600 + 900), wall(d, 21 * 3600), 3600, False, fixture_lib.cfg)
    assert not slots and why == "too_short"


def test_bm25():
    docs = {
        "a": "Igreja Matriz azulejos church", "b": "Beach Costa Nova sea", "c": "Museum of salt and ria",
        "d": "Maritime museum boats", "e": "Park garden trees",
    }
    idx = BM25(docs)
    assert idx.search("salt")[0][0] == "c"
    assert idx.search("azulejo")[0][0] == "a"          # accent/plural folding
    assert idx.search("Azulejos")[0][0] == "a"


def test_copy_check():
    src = "the old lighthouse stands above the dunes and shows the way for ships at night"
    seven = "the old lighthouse stands above the dunes pretty"
    eight = "the old lighthouse stands above the dunes and good"
    assert copy_violation(seven, [src]) is None
    assert copy_violation(eight, [src]) is not None
    assert copy_violation("The OLD lighthouse, stands above the dunes, and!", [src]) is not None   # punctuation ignored


BASE = dict(id="x", name="X", kind="sight", category="museum", tags=["history"], municipality="Aveiro",
            lat=40.64, lon=-8.65, hours="Tu-Su 10:00-18:00", weather="indoor", visit_min=30, visit_typical=60,
            price="€", description="A short plain description.", priority=2,
            provenance={"status": "draft", "sources": []})


def errs(**kw):
    d = {**BASE, **kw}
    return check_place(from_dict(d), load_config())


def test_validator_fixtures():
    assert errs() == []
    assert any("tag" in e for e in errs(tags=["bogus"]))
    assert any("category" in e for e in errs(category="bogus"))
    assert any("hours" in e for e in errs(hours="sometimes"))
    assert errs(hours="") == []                                            # draft
    assert any("empty hours" in e for e in errs(hours="", provenance={"status": "confirmed", "checked": D(2026, 1, 1)}))
    assert any("outside the area" in e for e in errs(lat=41.2))
    assert any("visit_min" in e for e in errs(visit_min=90, visit_typical=60))
    assert any("below 10" in e for e in errs(visit_min=5))
    assert any("words" in e for e in errs(description="word " * 61))
    assert any("empty" in e for e in errs(description=""))
    assert any("checked" in e for e in errs(provenance={"status": "confirmed"}))
    assert any("municipality" in e for e in errs(municipality="Porto"))
    with pytest.raises(Exception):
        from_dict({k: v for k, v in BASE.items() if k != "name"})


def test_validator_max_walk_and_rejected():
    assert any("max_walk_m" in e for e in errs(max_walk_m=2500))
    assert errs(max_walk_m=2000) == []
    assert any("status_reason" in e for e in errs(provenance={"status": "rejected", "sources": []}))
    assert errs(provenance={"status": "rejected", "sources": [], "status_reason": "no public transport in the current feeds"}) == []


def test_entry_points_and_validation():
    from places.hours import entry_points
    s = "Mar-Oct Tu-Sa 10:30,11:30,14:30; Nov-Feb Tu-Sa 09:30,10:30; PH off"
    assert [t / 3600 for t in entry_points(s, D(2026, 10, 24))] == [10.5, 11.5, 14.5]
    assert [t / 3600 for t in entry_points(s, D(2027, 1, 12))] == [9.5, 10.5]
    assert entry_points(s, D(2026, 10, 26)) == []                       # Monday
    assert entry_points(s, D(2026, 12, 8), {D(2026, 12, 8)}) == []      # PH off
    assert any("entry_times" in e for e in errs(entry_times="10:00-12:00"))
    assert any("entry_times need" in e for e in errs(hours="", entry_times="10:30"))
    assert errs(entry_times="Tu-Su 10:30,11:30") == []


# ---- reference points of long places (CurrentChanges.md P) ------------------------------------------------------------
import copy as _copy
import dataclasses as _dc
from pathlib import Path as _Path

from places.config import load_config as _load_config
from places.model import dumps as _dumps, read_place as _read_place
from places.validate import check_place as _check_place

_DATA = _Path(__file__).resolve().parents[1] / "data" / "places"
_CFG = _load_config(str(_Path(__file__).resolve().parents[1] / "config"))


def _with_points(*pts):
    p = _read_place(_DATA / "praia-costa-nova.toml")
    return _dc.replace(p, points=[{"name": n, "lat": la, "lon": lo, "source": s} for n, la, lo, s in pts])


GOOD = ("south end", 40.6109, -8.7504, "OSM way/1 (ODbL); chosen by Claude, confirm")


def test_points_round_trip_and_render_after_provenance(tmp_path):
    p = _with_points(GOOD, ("boardwalk", 40.6150, -8.7520, "OSM way/1; confirm"))
    text = _dumps(p)
    assert text.index("[provenance]") < text.index("[[points]]") and text.count("[[points]]") == 2
    f = tmp_path / "praia-costa-nova.toml"
    f.write_text(text, encoding="utf-8")
    back = _read_place(f)
    assert back.points == p.points and _dumps(back) == text
    assert back.all_points() == [("main", p.lat, p.lon), ("south end", 40.6109, -8.7504), ("boardwalk", 40.6150, -8.7520)]


def test_a_record_without_points_renders_as_before():
    p = _read_place(_DATA / "se-de-aveiro.toml")
    assert p.points == [] and "[[points]]" not in _dumps(p) and p.all_points() == [("main", p.lat, p.lon)]
    assert _dumps(p) == (_DATA / "se-de-aveiro.toml").read_text(encoding="utf-8")


def _errs(*pts):
    return [m for m in _check_place(_with_points(*pts), _CFG) if "point" in m]


def test_valid_points_pass():
    assert _errs(GOOD) == []


def test_point_rules_are_enforced():
    many = [(f"p{i}", 40.620 + i * 0.003, -8.753, "s") for i in range(7)]
    assert any("7 extra points" in m for m in _errs(*many))
    assert any("outside the area box" in m for m in _errs(("far", 45.0, -8.75, "s")))
    assert any("m from the main point (max 3000)" in m for m in _errs(("away", 40.650, -8.753, "s")))
    assert any("m apart (min 150)" in m for m in _errs(("near", 40.62010, -8.75298, "s")))           # on top of the main point
    assert any("m apart (min 150)" in m for m in _errs(GOOD, ("close", 40.6110, -8.7505, "s")))
    assert any("has no name" in m for m in _errs((" ", 40.6109, -8.7504, "s")))
    assert any("has no source" in m for m in _errs(("south end", 40.6109, -8.7504, "")))
