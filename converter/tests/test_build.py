"""Integration tests on the real CSV (fixture school calendar, --drop-unplaced)."""
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from gtfsconv import build as build_mod, validate
from gtfsconv.fetch import cache_ready
from gtfsconv.stops import MAX_KMH, dist_km, in_bbox
from gtfsconv.write import write_zip

from conftest import BUILD_DATE, CSV, ROOT

D = dt.date


def count(res, line, date, mode=None):
    d = D.fromisoformat(date)
    return sum(1 for t in res.calendar_trips if t.line == line and (mode is None or t.mode == mode) and d in t.dates)


def test_input_facts(real_build):
    r = real_build.report
    assert (r["rows"], r["trips_loaded"], r["raw_stop_names"]) == (8663, 828, 363)
    assert r["csv_sha256"] == build_mod.EXPECTED_SHA, "CSV changed: re-check the spec's measured facts"


def test_calendar_counts(real_build):
    r = real_build
    assert count(r, "36", "2026-10-24") == 33
    assert count(r, "36", "2026-10-25") == 29
    assert count(r, "36", "2026-12-08") == 29
    assert count(r, "35", "2026-10-24") == 0
    assert count(r, "35", "2027-07-17") == 34
    assert count(r, "Ferry", "2026-10-24") == 26 and count(r, "Ferry", "2026-10-26") == 26
    for line in ("Azul", "Verde"):
        assert count(r, line, "2026-10-24") == 11 and count(r, line, "2026-10-26") == 23


def test_holiday_tuesday_runs_no_dias_uteis_trips(real_build):
    d = D(2026, 12, 8)
    for t in real_build.calendar_trips:
        if t.line == "36" and d in t.dates:
            assert t.day_type in ("Domingos e Feriados", "Todos os dias", "TODOS OS DIAS (Julho e Agosto)")
            assert t.day_type != "Dias Úteis"


def test_no_trip_lost_to_zero_service_dates(real_build):
    assert real_build.report["zero_date_trips"] == []


def test_line3_eucalipto_change(real_build):
    r = real_build
    ch = [c for c in r.report["changes_applied"] if c["line"] == "3"]
    assert len(ch) == 1 and len(ch[0]["trips"]) == 33
    line3 = [t for t in r.calendar_trips if t.line == "3"]
    assert all(s.name != "Travessa Eucalipto" for t in line3 for s in t.stops)
    assert sum(1 for t in line3 if t.stops[0].name == "Eucalipto A") == 16


def test_line6_change_not_applicable(real_build):
    e = [e for e in real_build.report["service_changes"] if e["line"] == "6"]
    assert e and e[0]["status"].startswith("not applicable")


def test_ferry_stops_come_from_osm_terminals_and_stay_apart_from_bus_stops(real_build):
    st = {s["name"]: s for s in real_build.stops}
    quay, bus = st["FORTE DA BARRA (FERRY)"], st["Forte da Barra - Ferry"]
    assert real_build.stop_sources["FORTE DA BARRA (FERRY)"] == {"osm-ferry": real_build.stop_sources["FORTE DA BARRA (FERRY)"]["osm-ferry"]}
    assert real_build.stop_sources["S. JACINTO"].keys() == {"osm-ferry"}
    assert 0 < dist_km((quay["lat"], quay["lon"]), (bus["lat"], bus["lon"])) * 1000 <= 150
    assert quay["stop_id"] != bus["stop_id"]


def test_every_stop_in_bbox_and_no_leg_above_90_kmh(real_build):
    xy = {s["stop_id"]: (s["lat"], s["lon"]) for s in real_build.stops}
    kept = {(a[0], a[2], a[3]) for a in real_build.report["accepted_legs"]}  # config/accepted_legs.csv
    for t in real_build.trips:
        for i, g in enumerate(t.gstops):
            assert in_bbox(*xy[g])
            if i and (t.trip, t.stops[i - 1].name, t.stops[i].name) not in kept:
                mins = max((t.stops[i].t - t.stops[i - 1].t) / 60, 1)
                assert dist_km(xy[t.gstops[i - 1]], xy[g]) / (mins / 60) <= MAX_KMH + 1e-9


def test_trips_have_at_least_two_stop_times_and_monotonic_times(real_build):
    for t in real_build.trips:
        assert len(t.stops) >= 2
        assert all(a.t <= b.t for a, b in zip(t.stops, t.stops[1:]))


def test_line17_time_fix_applied(real_build):
    assert [f[0] for f in real_build.report["time_fixes"]] == ["p5t1c6"]
    assert real_build.report["time_drops"] == []
    t = next(t for t in real_build.trips if t.trip == "p5t1c6")
    assert [s.name for s in t.stops][3] == "Zibreiros" and t.stops[3].t == 17 * 3600 + 20 * 60


def test_interim_zip_validates_and_is_byte_identical(real_build, fixture_config, tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    write_zip(a, real_build.tables)
    res2 = build_mod.build(CSV, None, fixture_config, ROOT / "cache", tmp_path / "b", drop_unplaced=True, build_date=BUILD_DATE)
    write_zip(b, res2.tables)
    assert a.read_bytes() == b.read_bytes()
    assert validate.internal_checks(real_build.tables) == []
    v = validate.run_validator(a, tmp_path / "val")
    if not v["ran"]:
        pytest.skip(v["reason"])
    assert v["errors"] == 0


def test_empty_school_calendar_fails_the_build(tmp_path):
    cfg = tmp_path / "cfg"
    shutil.copytree(ROOT / "config", cfg)
    import re
    f = cfg / "feed.toml"
    f.write_text(re.sub(r"\[school\].*", '[school]\nterms = []\n', f.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")
    with pytest.raises(Exception, match=r"\[school\] is empty"):
        build_mod.build(CSV, None, cfg, ROOT / "cache", tmp_path / "b", drop_unplaced=True, build_date=BUILD_DATE)


def test_strict_build_errors_are_only_placement_conflicts(fixture_config, tmp_path):
    res = build_mod.build(CSV, None, fixture_config, ROOT / "cache", tmp_path, drop_unplaced=False, build_date=BUILD_DATE)
    assert res.unplaced == {} and res.report["time_drops"] == [] and res.report["bad_legs"] == []
    # Remaining strict failures must be the 50-300 m conflicts, and nothing else. Empty once resolved.
    assert all("50-300 m apart" in e for e in res.errors)


# ---- M5: only meaningful once a human has supplied the coordinates (spec 7) -------------------

def _need_strict(res):
    if res.unplaced:
        pytest.skip(f"M5 not reachable yet: {len(res.unplaced)} stop names still unplaced (see build/unplaced_stops.csv)")


def test_m5_all_trips_present(real_build):
    _need_strict(real_build)
    whole = [d for d in real_build.report["time_drops"] if d[1].startswith("trip dropped")]
    assert real_build.report["trips_final"] == 828 - len(whole) - len(real_build.report["exclusions"])


def test_m5_golden_journey_terminal_to_ferry(real_build):
    _need_strict(real_build)
    d = D(2026, 10, 24)
    terminal = {s["stop_id"] for s in real_build.stops if s["name"] == "Terminal Rodoviário de Aveiro"}
    forte = {s["stop_id"] for s in real_build.stops if s["name"] == "Forte da Barra - Ferry"}
    quay = {s["stop_id"] for s in real_build.stops if s["name"] == "FORTE DA BARRA (FERRY)"}
    jac = {s["stop_id"] for s in real_build.stops if s["name"] == "S. JACINTO"}
    ferries = [t for t in real_build.trips if t.mode == "ferry" and d in t.dates and t.gstops[0] in quay and t.gstops[-1] in jac]
    ok = False
    for t in real_build.trips:
        if t.line != "36" or d not in t.dates:
            continue
        gs = t.gstops
        if gs[0] in terminal and any(g in forte for g in gs[1:]):
            arr = next(s.t for s, g in zip(t.stops, gs) if g in forte)
            if any(arr + 300 <= f.stops[0].t <= arr + 300 + 1800 for f in ferries):
                ok = True
                break
    assert ok


def test_accepted_operator_leg_keeps_both_stops(real_build):
    t = next(t for t in real_build.trips if t.trip == "u12b1c1")
    names_ = [s.name for s in t.stops]
    assert "Verba B" in names_ and "Estação Quintas A" in names_
    assert sorted(a[0] for a in real_build.report["accepted_legs"]) == ["u12b1c1"]


def test_issue12_line36_outbound_reaches_costa_nova(real_build):
    """Every line 36 outbound trip that reaches Farol continues to Costa Nova on Saturdays and Sundays/holidays."""
    ids = {s["stop_id"]: s["name"] for s in real_build.stops}
    out = [t for t in real_build.trips if t.line == "36" and t.stops[0].name == "Terminal Rodoviário de Aveiro"
           and t.day_type in ("Sábados", "Domingos e Feriados") and t.trip.startswith(("p20t2", "p20t3"))]
    assert len(out) == 32  # 17 Saturday + 15 Sunday columns
    missing = [t.trip for t in out if t.stops[-1].name not in ("Largo Arrais Ançã - Costa Nova", "Forte da Barra - Ferry")]
    assert missing == []
    sun = [t for t in out if t.day_type == "Domingos e Feriados" and t.stops[-1].name == "Largo Arrais Ançã - Costa Nova"]
    assert len(sun) == 14  # all but the 17:00, which genuinely ends at Forte da Barra - Ferry
    d = D(2026, 10, 25)  # a Sunday
    early = [t for t in sun if d in t.dates and t.stops[0].t > 9 * 3600 and t.stops[-1].t - t.stops[0].t < 3600]
    assert len(early) >= 10  # not only the 20:25 bus


def test_issue12_saturday_fused_row_is_farol_then_costa_nova(real_build):
    t = next(t for t in real_build.trips if t.trip == "p20t2c1")
    assert [(s.name, s.t // 60 % 60 + s.t // 3600 * 100) for s in t.stops][-2:] == [("Farol - Forte da Barra", 757), ("Largo Arrais Ançã - Costa Nova", 805)]


def test_pdf_rows_lost_or_fused_are_restored(real_build):
    """bus.pdf pp. 6, 7, 20, 21: second row of every fused name, and the lost Sunday/Saturday rows (Issues 5 and 12)."""
    by = {t.trip: t for t in real_build.trips}
    last = lambda tid: [(s.name, s.t // 3600 * 100 + s.t // 60 % 60) for s in by[tid].stops][-2:]
    assert last("p7t1c5") == [("Hospital / Universidade B (Aveiro)", 830), ("Terminal Rodoviário de Aveiro", 850)]
    assert last("p6t1c3") == [("Hospital Rovisco Pais", 922), ("Figueira da Foz (Estação CP)", 950)]
    assert last("p21t2c8") == [("Caçadores 10 - Sé", 1310), ("Terminal Rodoviário de Aveiro", 1315)]
    assert last("p21t3c1")[-1] == ("Terminal Rodoviário de Aveiro", 745)
    assert last("p20t3c1")[-1] == ("Largo Arrais Ançã - Costa Nova", 805)
    # no fused name survives in the feed
    names_ = {s["name"] for s in real_build.stops}
    assert not [n for n in names_ if n.endswith("Terminal Rodoviário de Aveiro") and n != "Terminal Rodoviário de Aveiro"
                and "Cais" not in n]
    assert real_build.report["extra_stop_times"] and len(real_build.report["extra_stop_times"]) == 78


def test_line36_inbound_weekend_trips_reach_the_terminal(real_build):
    inbound = [t for t in real_build.trips if t.line == "36" and t.trip.startswith(("p21t2", "p21t3"))]
    assert len(inbound) == 30
    ends = {t.trip: t.stops[-1].name for t in inbound}
    assert set(ends.values()) == {"Terminal Rodoviário de Aveiro"}


def test_p6t1c19_ends_at_rovisco_pais_as_printed(real_build):
    t = next(t for t in real_build.trips if t.trip == "p6t1c19")
    assert (t.stops[-1].name, t.stops[-1].t) == ("Hospital Rovisco Pais", 19 * 3600 + 26 * 60)
