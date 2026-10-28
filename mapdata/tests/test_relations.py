"""mapdata.relations: assembling OSM route relations and choosing, per stop pair, the relation or the shortest path (MapPlan.md 11)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mapdata.graph import decode, encode, path_length
from mapdata.relations import Piece, assemble, choose, differs, match, match_relations, overlay, project

LAT = 40.0
M = 1 / (111_320 * math.cos(math.radians(LAT)))          # degrees of longitude per metre


def pt(east_m, north_m=0.0):
    return (LAT + north_m / 110_540, -8.0 + east_m * M)


def way(wid, nodes, coords, **tags):
    return wid, {"nodes": nodes, "geometry": [list(c) for c in coords], "tags": tags}


def ways(*ws):
    return {w[0]: w[1] for w in ws}


def members(*refs, role=""):
    return [{"type": "way", "ref": r, "role": role} for r in refs]


# a straight street east, 100 m per way: nodes 1..6 at 0, 100, ... 500 m
STREET = [way(10 + i, [i + 1, i + 2], [pt(100 * i), pt(100 * (i + 1))]) for i in range(5)]


def test_ways_are_chained_and_flipped_when_needed():
    w = ways(*STREET)
    w[12] = {"nodes": [4, 3], "geometry": [list(pt(300)), list(pt(200))], "tags": {}}              # way 12 runs against the others
    pieces = assemble(members(10, 11, 12, 13, 14), w)
    assert len(pieces) == 1 and pieces[0].nodes == [1, 2, 3, 4, 5, 6]
    assert abs(pieces[0].length - 500) < 3
    first_flipped = ways(*STREET)
    first_flipped[10] = {"nodes": [2, 1], "geometry": [list(pt(100)), list(pt(0))], "tags": {}}    # even the first way may be reversed
    assert assemble(members(10, 11, 12), first_flipped)[0].nodes == [1, 2, 3, 4]


def test_platform_and_stop_members_are_skipped():
    w = ways(*STREET)
    ms = members(10, 11) + [{"type": "node", "ref": 99, "role": "stop"}, {"type": "way", "ref": 55, "role": "platform"}] + members(12)
    assert assemble(ms, w)[0].nodes == [1, 2, 3, 4]


def test_a_gap_splits_the_relation_into_pieces():
    w = ways(*STREET)
    pieces = assemble(members(10, 11, 13, 14), w)                      # way 12 is missing: 11 ends at node 3, 13 starts at node 4
    assert [p.nodes for p in pieces] == [[1, 2, 3], [4, 5, 6]]


def test_a_roundabout_is_cut_between_entry_and_exit():
    # a ring of 8 nodes 100..107; the street arrives at node 100 and leaves at node 103 (three steps round, in the way's direction)
    ring = [100 + i for i in range(8)]
    rc = [(LAT + 0.0003 * math.sin(2 * math.pi * i / 8), -8.0 + 0.0003 * math.cos(2 * math.pi * i / 8)) for i in range(8)]
    w = ways(way(1, [1, 100], [pt(-300), rc[0]]), way(2, ring + [100], rc + [rc[0]], junction="roundabout"),
             way(3, [103, 9], [rc[3], pt(300, 200)]))
    piece = assemble(members(1, 2, 3), w)[0]
    assert piece.nodes == [1, 100, 101, 102, 103, 9]


def test_a_roundabout_with_no_exit_is_skipped():
    ring = [100 + i for i in range(6)]
    rc = [(LAT + 0.0002 * i, -8.0) for i in range(6)]
    w = ways(way(1, [1, 100], [pt(-300), rc[0]]), way(2, ring + [100], rc + [rc[0]], junction="roundabout"))
    assert assemble(members(1, 2), w)[0].nodes == [1, 100]


def piece_street():
    return assemble(members(10, 11, 12, 13, 14), ways(*STREET))


def test_projection_gives_the_distance_along_the_piece():
    p = piece_street()[0]
    s, d, point, i, t = project(p, *pt(250, 20))
    assert abs(s - 250) < 3 and abs(d - 20) < 1
    assert project(p, *pt(250, 60)) is None                                # more than 40 m away


def test_direction_of_travel_a_reversed_pair_is_rejected():
    ps = {1: piece_street()}
    fwd = choose(ps, pt(50, 5), pt(350, 5), 300)
    assert fwd.relation == 1 and abs(fwd.length - 300) < 3 and fwd.coords[0] == pt(50, 5) and fwd.coords[-1] == pt(350, 5)
    back = choose(ps, pt(350, 5), pt(50, 5), 300)
    assert back.relation is None and back.reason == "wrong direction"


def test_the_40_m_rule():
    ps = {1: piece_street()}
    c = choose(ps, pt(50, 5), pt(350, 55), 300)                            # the second stop is 55 m off the line
    assert c.relation is None and c.reason == "far stop"
    assert choose(ps, pt(50, 5), pt(350, 39), 300).relation == 1


def test_the_two_times_shortest_rule():
    ps = {1: piece_street()}
    assert choose(ps, pt(50, 5), pt(350, 5), 160).relation == 1           # 300 m against 160 m: within twice
    c = choose(ps, pt(50, 5), pt(350, 5), 140)                             # 300 m against 140 m: more than twice
    assert c.relation is None and c.reason == "too long"
    assert choose(ps, pt(50, 5), pt(350, 5), None).relation == 1          # no shortest path to compare with


def test_stops_on_different_pieces_are_a_gap():
    w = ways(*STREET)
    pieces = assemble(members(10, 11, 13, 14), w)
    c = choose({1: pieces}, pt(50, 5), pt(450, 5), 400)
    assert c.relation is None and c.reason == "gap"


def test_the_shortest_valid_variant_wins():
    short = piece_street()
    detour = assemble([{"type": "way", "ref": 20, "role": ""}], ways(way(20, [1, 8, 9, 6], [pt(0), pt(100, 100), pt(400, 100), pt(500)])))
    assert detour[0].length > 500
    c = choose({1: short, 2: detour}, pt(50, 5), pt(450, 5), 400)
    assert c.relation == 1
    c2 = choose({1: detour, 2: short}, pt(50, 5), pt(450, 5), 400)
    assert c2.relation == 2                                                 # whatever the order, the shorter piece wins
    only = choose({2: detour}, pt(50, 5), pt(450, 5), 500)
    assert only.relation == 2 and only.length > 500                        # alone, the longer variant is still valid (500 m shortest path)


def test_matching_needs_operator_or_network_and_the_ref():
    rule = {"who": {"operator": {"AveiroBus"}, "network": {"AveiroBus"}}, "ref": "L1"}
    assert match({"route": "bus", "operator": "AveiroBus", "ref": "L1"}, rule)
    assert match({"route": "bus", "network": "AveiroBus", "ref": "L1"}, rule)
    assert not match({"route": "bus", "operator": "Unir", "network": "Andante", "ref": "L1"}, rule)      # a ref alone is not enough
    assert not match({"route": "bus", "operator": "AveiroBus", "ref": "L2"}, rule)
    assert not match({"route": "ferry", "operator": "AveiroBus", "ref": "L1"}, rule)
    rels = [{"id": 1, "tags": {"route": "bus", "operator": "AveiroBus", "network": "AveiroBus", "name": "Linha Centro: Azul"}},
            {"id": 2, "tags": {"route": "bus", "operator": "Unir", "network": "Andante", "ref": "1003"}},
            {"id": 3, "tags": {"route": "bus", "operator": "Busway", "ref": "16"}}]
    m = match_relations(rels)
    assert [r["id"] for r in m["av:ab-15-azul"]] == [1] and m["av:ab-15-verde"] == [] and [r["id"] for r in m["av:bw-16"]] == [3]
    assert m["av:bw-18"] == [] if "av:bw-18" in m else True


def test_differs_flags_more_than_thirty_percent_apart():
    a = [pt(0), pt(500)]
    assert not differs(a, [pt(0, 10), pt(500, 10)])
    assert differs(a, [pt(0), pt(0, 100), pt(500, 100), pt(500)])


def test_overlay_writes_a_source_for_every_bus_key():
    w = ways(*STREET)
    w[20] = {"nodes": [1, 8, 9, 6], "geometry": [list(pt(0)), list(pt(100, 150)), list(pt(400, 150)), list(pt(500))], "tags": {}}
    stops = {"s1": pt(50, 5), "s2": pt(350, 5), "s3": pt(450, 5), "s4": pt(50, 400)}
    ride = {"s1|s2|bus": encode([stops["s1"], stops["s2"]]), "s2|s3|bus": encode([stops["s2"], stops["s3"]]),
            "s1|s4|bus": encode([stops["s1"], stops["s4"]]), "s9|s1|rail": encode([stops["s1"], stops["s2"]]), "s3|s2|bus": encode([stops["s3"], stops["s2"]])}
    rel = {"id": 7, "timestamp": "2026-09-01T00:00:00Z", "tags": {"route": "bus", "operator": "Busway", "ref": "17", "name": "17 A - B"},
           "members": members(10, 11, 12, 13, 14)}
    pairs = {"av:bw-17": {("s1", "s2"), ("s2", "s3"), ("s1", "s4"), ("s3", "s2")}, "av:bw-18": {("s1", "s3")}}
    res = overlay(ride, pairs, stops, [rel], w, {"av:bw-17": "17", "av:bw-18": "18"})
    assert set(res["source"]) == {"s1|s2|bus", "s2|s3|bus", "s1|s4|bus", "s3|s2|bus"}                    # every bus key, no rail key
    assert res["source"]["s1|s2|bus"] == "relation:7" and res["source"]["s2|s3|bus"] == "relation:7"
    assert res["source"]["s1|s4|bus"] == "shortest" and res["source"]["s3|s2|bus"] == "shortest"
    assert res["ride"]["s9|s1|rail"] == ride["s9|s1|rail"] and res["ride"]["s1|s4|bus"] == ride["s1|s4|bus"]   # others untouched
    assert path_length(decode(res["ride"]["s1|s2|bus"])) > 295                                                   # the relation's path
    assert "relation-rejected far stop (line 17)" in res["flags"]["s1|s4|bus"][0]
    assert "wrong direction (line 17)" in res["flags"]["s3|s2|bus"][0]
    st = res["stats"]["av:bw-17"]
    assert (st["pairs"], st["relation"], st["far stop"], st["wrong direction"]) == (4, 2, 1, 1) and "av:bw-18" not in res["stats"]
    assert res["relations"]["7"] == {"lines": ["17"], "name": "17 A - B", "timestamp": "2026-09-01T00:00:00Z", "pieces": 1}


def test_a_line_that_goes_out_and_comes_back_is_read_on_the_right_pass():
    """Out along y=0 to 500 m and back along y=30: a stop at 300 m then a stop at 100 m is the return pass, not 'wrong direction'."""
    out = way(30, [1, 2, 3], [pt(0), pt(300), pt(500)])
    w = ways(out, way(34, [3, 7, 4], [pt(500), pt(520, 15), pt(500, 30)]), way(35, [4, 8], [pt(500, 30), pt(300, 30)]), way(36, [8, 9], [pt(300, 30), pt(0, 30)]))
    piece = assemble(members(30, 34, 35, 36), w)
    assert len(piece) == 1 and piece[0].length > 1000
    ps = {1: piece}
    c = choose(ps, pt(300, 15), pt(100, 15), 250)                      # a at 300 m, b at 100 m: only the return pass has b after a
    assert c.relation == 1 and 190 < c.length < 210                    # the 200 m of the return pass
    out_pass = choose(ps, pt(100, 15), pt(300, 15), 250)               # the outward pass: a then b
    assert out_pass.relation == 1 and 190 < out_pass.length < 210
