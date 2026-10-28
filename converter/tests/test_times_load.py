import pytest

from gtfsconv import times
from gtfsconv.load import BuildError, load_trips

HEADER = "source,mode,page,line,route,direction,day_type,season,trip,stop_sequence,stop_name,time,note,valid_from,extraction\n"


def write_csv(path, rows):
    path.write_text("﻿" + HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def mkconfig(tmp_path, fixes=""):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "time_fixes.csv").write_text("source,trip,stop_sequence,old_time,new_time,evidence\n" + fixes, encoding="utf-8")
    (cfg / "exclusions.csv").write_text("source,trip,reason\n", encoding="utf-8")
    return cfg


LINE17 = [f"pdfs/bus/bus.pdf,bus,5,17,AVEIRO,Volta,Dias Úteis,A,p5t1c6,{i},{n},{t},,2026-09-14,text"
          for i, n, t in [(1, "Vale de Cambra (Gare)", "17:00"), (2, "Vale de Cambra (Escola Dairas)", "17:02"),
                          (3, "Dornelas (2)", "17:15"), (4, "Zibreiros", "17:02"), (5, "Paçô (2)", "17:29")]]


def test_midnight_rollover(tmp_path):
    csv_path = tmp_path / "s.csv"
    write_csv(csv_path, [f"s,bus_urban,1,4,X,Circular,Dias Úteis,,t1,{i},S{i},{t},,2025-06-01,ocr"
                         for i, t in enumerate(["23:56", "00:03", "00:09"], 1)])
    trips, _ = load_trips(csv_path, mkconfig(tmp_path))
    times.rollover(trips[0].stops)
    assert [times.fmt(s.t) for s in trips[0].stops] == ["23:56:00", "24:03:00", "24:09:00"]
    times.check_trip(trips[0])


def test_line17_fails_without_fix_and_passes_with_fix(tmp_path):
    csv_path = tmp_path / "s.csv"
    write_csv(csv_path, LINE17)
    trips, _ = load_trips(csv_path, mkconfig(tmp_path))
    times.rollover(trips[0].stops)
    with pytest.raises(BuildError, match="p5t1c6"):
        times.check_trip(trips[0])

    fixed = tmp_path / "fixed"
    fixed.mkdir()
    cfg = mkconfig(fixed, "pdfs/bus/bus.pdf,p5t1c6,4,17:02,17:22,test fixture only\n")
    trips, info = load_trips(csv_path, cfg)
    times.rollover(trips[0].stops)
    times.check_trip(trips[0])
    assert info["fixes"] and trips[0].stops[3].t == 17 * 3600 + 22 * 60


def test_time_fix_must_match_old_time(tmp_path):
    csv_path = tmp_path / "s.csv"
    write_csv(csv_path, LINE17)
    cfg = mkconfig(tmp_path, "pdfs/bus/bus.pdf,p5t1c6,4,17:99,17:22,x\n")
    with pytest.raises(BuildError, match="changed under the fix"):
        load_trips(csv_path, cfg)


def test_real_csv_has_no_non_monotonic_trip_after_config_fixes():
    from conftest import CSV, ROOT
    if not CSV.exists():
        pytest.skip("csv missing")
    trips, _ = load_trips(CSV, ROOT / "config")
    bad = []
    for t in trips:
        times.rollover(t.stops)
        if times.non_monotonic(t.stops):
            bad.append(t.trip)
    assert bad == []
    # and without the fix, p5t1c6 is the only offender
    import shutil, tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(ROOT / "config", d, dirs_exist_ok=True)
        (Path(d) / "time_fixes.csv").write_text("source,trip,stop_sequence,old_time,new_time,evidence\n")
        trips, _ = load_trips(CSV, Path(d))
    bad = []
    for t in trips:
        times.rollover(t.stops)
        if times.non_monotonic(t.stops):
            bad.append(t.trip)
    assert bad == ["p5t1c6"]


def test_extra_stop_times_inserted_and_refused_when_present(tmp_path):
    csv_path = tmp_path / "s.csv"
    write_csv(csv_path, [f"s,bus,1,36,X,Ida,Dias Úteis,A,t1,{i},S{i},{t},,2026-09-14,text"
                         for i, t in [(1, "10:00"), (2, "10:10"), (4, "10:30")]])
    cfg = mkconfig(tmp_path)
    (cfg / "extra_stop_times.csv").write_text(
        "source,trip,stop_sequence,stop_name,time,evidence\ns,t1,3,S3,10:20,pdf p.1\n", encoding="utf-8")
    trips, info = load_trips(csv_path, cfg)
    assert [(s.seq, s.name) for s in trips[0].stops] == [(1, "S1"), (2, "S2"), (3, "S3"), (4, "S4")]
    assert info["extra_stop_times"][0][3] == "10:20"
    (cfg / "extra_stop_times.csv").write_text(
        "source,trip,stop_sequence,stop_name,time,evidence\ns,t1,4,S4,10:20,pdf p.1\n", encoding="utf-8")
    with pytest.raises(BuildError, match="already has stop_sequence"):
        load_trips(csv_path, cfg)
