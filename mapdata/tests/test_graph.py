"""mapdata.graph: A*, snapping, one-way rules, polylines."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mapdata.graph import BUS, FOOT, RAIL, Graph, decode, encode, haversine, path_length

# a 3 x 3 street grid, nodes 10*r + c, about 111 m between neighbours
LAT0, LON0, STEP = 40.0, -8.0, 0.001


def grid(oneway_row=None):
    g = Graph()
    pos = lambda n: (LAT0 + (n // 10) * STEP, LON0 + (n % 10) * STEP * 1.3)
    for r in range(3):                                             # rows: west -> east
        ns = [10 * r + c for c in range(3)]
        both = BUS | FOOT
        g.add_way(ns, [pos(n) for n in ns], both, 0 if oneway_row == r else both)   # a one-way bus street: foot still both ways
        if oneway_row == r:
            g.add_way(ns, [pos(n) for n in ns], FOOT, 0)
            g.add_way(ns[::-1], [pos(n) for n in ns[::-1]], FOOT, 0)
    for c in range(3):                                             # columns: south -> north
        ns = [10 * r + c for r in range(3)]
        g.add_way(ns, [pos(n) for n in ns], BUS | FOOT, BUS | FOOT)
    return g, pos


def test_polyline_round_trip():
    pts = [(40.12345, -8.54321), (40.12399, -8.54001), (40.0, -8.0), (41.14872, -8.58484)]
    back = decode(encode(pts))
    assert len(back) == len(pts) and all(abs(a[0] - b[0]) < 1e-5 and abs(a[1] - b[1]) < 1e-5 for a, b in zip(pts, back))
    assert encode([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"      # Google's documented example
    assert decode("") == [] and encode([]) == ""
    rnd = random.Random(1)
    many = [(40 + rnd.random(), -8 + rnd.random()) for _ in range(200)]
    assert all(abs(a[0] - b[0]) < 1e-5 for a, b in zip(many, decode(encode(many))))


def test_astar_finds_the_shortest_path_on_a_grid():
    g, pos = grid()
    a, b = g.snap(*pos(0), BUS, 50), g.snap(*pos(22), BUS, 50)
    path, m = g.route(a, b, BUS)
    assert math.isclose(m, path_length(path), rel_tol=0.02)
    manhattan = haversine(*pos(0), *pos(2)) + haversine(*pos(2), *pos(22))
    assert abs(m - manhattan) < 5                                  # the grid has no shortcuts
    assert path[0] == pos(0) and path[-1] == pos(22)


def test_snapping_respects_the_limit_and_projects_onto_the_segment():
    g, pos = grid()
    la, lo = pos(0)[0], (pos(0)[1] + pos(1)[1]) / 2                # the middle of the street between nodes 0 and 1
    near = g.snap(la + 0.0002, lo, BUS, 150)                       # 22 m north of it
    assert near and 15 < near.dist < 30 and abs(near.point[1] - lo) < 1e-5 and near.point[0] == la
    assert g.snap(la + 0.01, lo + 0.05, BUS, 150) is None          # far from every street
    assert g.snap(la + 0.0002, lo, BUS, 10) is None
    assert g.snap(la, lo, RAIL, 150) is None                       # nothing here is a railway


def test_oneway_is_respected_for_buses_and_not_for_walks():
    g, pos = grid(oneway_row=1)                                    # row 1 runs west -> east for buses
    west, east = pos(10), pos(12)
    ab = (g.snap(*west, BUS, 50), g.snap(*east, BUS, 50))
    fwd, _ = g.route(ab[0], ab[1], BUS)
    back, m_back = g.route(ab[1], ab[0], BUS)
    assert path_length(fwd) < 260                                  # straight along the street
    assert m_back > 400                                            # against the arrow: round the block
    f2, m_f2 = g.route(g.snap(*east, FOOT, 50), g.snap(*west, FOOT, 50), FOOT)
    assert m_f2 < 260                                              # a pedestrian walks straight back
    assert any(abs(p[0] - pos(10)[0]) > 1e-4 for p in back)        # the bus detours through another row


def test_a_missing_route_gives_none():
    g, pos = grid()
    island = Graph()
    island.add_way([100, 101], [(40.5, -8.5), (40.5, -8.499)], BUS, BUS)
    for n, c in island.coords.items():
        g.coords[n] = c
    g.adj.update(island.adj)
    a, b = g.snap(*pos(0), BUS, 50), g.snap(40.5, -8.4995, BUS, 100)
    assert a and b and g.route(a, b, BUS) is None


def test_same_segment_is_direct_and_a_snap_inside_one_edge_works():
    g, pos = grid()
    la, lo = pos(0)
    a = g.snap(la, lo + 0.0002, BUS, 50)
    b = g.snap(la, lo + 0.0008, BUS, 50)
    path, m = g.route(a, b, BUS)
    assert len(path) == 2 and 45 < m < 55          # 0.0006 degrees of longitude, about 51 m


def test_snapping_prefers_a_big_network_over_a_nearer_isolated_piece():
    import mapdata.graph as mg
    g, pos = grid()
    la, lo = pos(0)[0] - 0.0005, pos(0)[1]                        # 55 m south of the grid's south-west corner
    g.add_way([900, 901], [(la - 0.0001, lo - 0.0001), (la - 0.0001, lo + 0.0001)], FOOT, FOOT)   # a lone footway 11 m away
    old = mg.MIN_COMPONENT_NODES
    try:
        mg.MIN_COMPONENT_NODES = 5                                # the 9-node grid counts as big here, the lone footway does not
        s = g.snap(la, lo, FOOT, 150)
        assert s is not None and {s.u, s.v} <= set(range(30)) and 50 < s.dist < 60
        assert g.snap(la, lo, FOOT, 30) is not None and {g.snap(la, lo, FOOT, 30).u} <= {900, 901}   # no big network within 30 m
    finally:
        mg.MIN_COMPONENT_NODES = old
