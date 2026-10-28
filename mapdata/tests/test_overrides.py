"""mapdata.overrides: the walking-overrides file and the exceptions sheet (WalkBarriers.md 2, 4)."""
import csv
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mapdata import overrides as ov
from mapdata.graph import encode, path_length, decode

NAMES = {"s1": "Stop 1 (stop)", "s2": "Stop 2 (stop)", "pl": "Place", "pl#1": "Place (north)", "s3": "Stop 3 (stop)", "s4": "Stop 4 (stop)"}


def poly(metres_east):
    """A straight east-west polyline of about this many metres at latitude 40."""
    lon = metres_east / (111_320 * 0.766)
    return encode([(40.0, -8.0), (40.0, -8.0 + lon)])


def cache(tmp_path, flagged=None, walk=None):
    walk = walk if walk is not None else {"s1|s2": poly(200), "pl|s1": poly(1000), "pl#1|s3": poly(300)}
    data = {"meta": {"built": "2026-10-28", "precision": 5, "feeds": {"cp": "a" * 64, "av": "b" * 64}, "places_hash": "ph",
                     "flagged": flagged if flagged is not None else {
                         "pl|s1": ["engine-underestimate 400 m vs 1000 m"], "s1|s2": ["snap-far from 200 m"],
                         "s3|s4": ["no-route"], "s2|s4": ["no-snap"]}},
            "walk": walk, "ride": {}}
    p = tmp_path / "paths.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    return p


def rows(path):
    return [r for r in csv.DictReader(l for l in open(path, encoding="utf-8") if not l.startswith("#"))]


def run(tmp_path, **kw):
    out = ov.run(cache(tmp_path, **kw), tmp_path / "review.csv", tmp_path / "walk_overrides.csv", names=NAMES, quiet=True)
    return out, rows(tmp_path / "walk_overrides.csv"), ov.read_review(tmp_path / "review.csv")


def test_one_row_per_cached_pair_with_the_path_length(tmp_path):
    out, r, _ = run(tmp_path)
    assert out["rows"] == 3 and out["osm"] == 3 and out["osm-gap"] == out["blocked"] == 0
    by = {(x["from_id"], x["to_id"]): x for x in r}
    assert abs(int(by[("s1", "s2")]["metres"]) - 200) < 3 and by[("s1", "s2")]["kind"] == "osm"
    assert abs(int(by[("pl", "s1")]["metres"]) - 1000) < 5
    assert int(by[("pl#1", "s3")]["metres"]) == round(path_length(decode(poly(300))))


def test_pairs_with_no_route_get_no_row(tmp_path):
    out, r, _ = run(tmp_path)
    assert out["no_route_pairs"] == 2
    ids = {(x["from_id"], x["to_id"]) for x in r}
    assert ("s3", "s4") not in ids and ("s2", "s4") not in ids


def test_header_carries_the_hashes_the_engine_checks(tmp_path):
    run(tmp_path)
    head = [l for l in open(tmp_path / "walk_overrides.csv", encoding="utf-8") if l.startswith("#")]
    text = "".join(head)
    assert "# built: 2026-10-28" in text and f"cp={'a' * 64}" in text and f"av={'b' * 64}" in text
    assert "# places_hash: ph" in text and "# review_hash: " in text


def test_output_is_deterministic(tmp_path):
    run(tmp_path)
    first = (tmp_path / "walk_overrides.csv").read_bytes(), (tmp_path / "review.csv").read_bytes()
    run(tmp_path)
    assert (tmp_path / "walk_overrides.csv").read_bytes() == first[0] and (tmp_path / "review.csv").read_bytes() == first[1]


def test_the_sheet_lists_the_underestimates_biggest_first(tmp_path):
    _, _, sheet = run(tmp_path)
    assert list(sheet) == [("pl", "s1")]
    r = sheet[("pl", "s1")]
    assert (r["excess_m"], r["engine_m"], r["path_m"], r["from"], r["to"], r["verdict"]) == ("600", "400", "1000", "Place", "Stop 1 (stop)", "")


def edit_sheet(tmp_path, verdict, note=""):
    p = tmp_path / "review.csv"
    rs = list(csv.DictReader(open(p, encoding="utf-8")))
    rs[0]["verdict"], rs[0]["note"] = verdict, note
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ov.REVIEW_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rs)


def test_verdicts_map_to_row_kinds(tmp_path):
    run(tmp_path)
    edit_sheet(tmp_path, "osm-gap", "OSM has no crossing here")
    out, r, _ = run(tmp_path)
    row = next(x for x in r if x["from_id"] == "pl")
    assert (row["kind"], row["metres"], row["note"]) == ("osm-gap", "", "OSM has no crossing here") and out["osm-gap"] == 1
    edit_sheet(tmp_path, "BLOCKED", "a motorway")
    out, r, _ = run(tmp_path)
    row = next(x for x in r if x["from_id"] == "pl")
    assert (row["kind"], row["metres"]) == ("blocked", "") and out["blocked"] == 1 and out["osm"] == 2


def test_an_unknown_verdict_is_an_error_not_silently_ignored(tmp_path):
    run(tmp_path)
    edit_sheet(tmp_path, "osmgap")
    with pytest.raises(ValueError, match="unknown verdict"):
        run(tmp_path)


def test_verdicts_carry_over_a_rebuild_and_a_gone_flag_keeps_its_note(tmp_path):
    run(tmp_path)
    edit_sheet(tmp_path, "osm-gap", "checked on Street View")
    _, r, sheet = run(tmp_path)                                    # same flags: the verdict stays
    assert sheet[("pl", "s1")]["verdict"] == "osm-gap" and sheet[("pl", "s1")]["note"] == "checked on Street View"
    # a rebuild in which the flag has gone (the path is now within the engine's estimate)
    _, r, sheet = run(tmp_path, flagged={"s3|s4": ["no-route"]})
    gone = sheet[("pl", "s1")]
    assert gone["verdict"] == "osm-gap" and gone["note"] == "flag gone: checked on Street View" and gone["excess_m"] == ""
    assert next(x for x in r if x["from_id"] == "pl")["kind"] == "osm-gap"                     # and it still applies
    _, _, sheet = run(tmp_path, flagged={"s3|s4": ["no-route"]})                                # the prefix is not stacked
    assert sheet[("pl", "s1")]["note"] == "flag gone: checked on Street View"
    # a row without a verdict whose flag has gone is dropped
    run(tmp_path)
    edit_sheet(tmp_path, "")
    _, _, sheet = run(tmp_path, flagged={})
    assert sheet == {}


def test_the_reverse_orientation_is_carried_over_too(tmp_path):
    run(tmp_path)
    p = tmp_path / "review.csv"
    rs = list(csv.DictReader(open(p, encoding="utf-8")))
    rs[0]["from_id"], rs[0]["to_id"], rs[0]["verdict"] = rs[0]["to_id"], rs[0]["from_id"], "blocked"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ov.REVIEW_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rs)
    out, r, sheet = run(tmp_path)
    assert out["blocked"] == 1 and sheet[("pl", "s1")]["verdict"] == "blocked"
