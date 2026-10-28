"""mapdata.build helpers: tags -> flags, routing a pair with its flags, tile cells, the ferry/rail pieces that need no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mapdata.build import BOX, cell_of, cells_near, group_cells, route_pair, way_flags
from mapdata.graph import BUS, FOOT, Graph


def test_tags_give_the_right_flags():
    both = BUS | FOOT
    assert way_flags({"highway": "residential"}) == (both, both)
    assert way_flags({"highway": "residential", "oneway": "yes"}) == (both, FOOT)                 # buses one way, pedestrians both
    assert way_flags({"highway": "residential", "oneway": "-1"}) == (FOOT, both)
    assert way_flags({"highway": "residential", "oneway": "yes", "oneway:bus": "no"}) == (both, both)   # a contraflow bus lane
    assert way_flags({"highway": "tertiary", "junction": "roundabout"}) == (both, FOOT)
    assert way_flags({"highway": "motorway"})[0] & FOOT == 0 and way_flags({"highway": "trunk_link"})[1] & FOOT == 0   # no walking on them
    assert way_flags({"highway": "footway"}) == (FOOT, FOOT)                                       # no buses on footpaths
    assert way_flags({"highway": "steps"}) == (FOOT, FOOT) and way_flags({"highway": "pedestrian"}) == (FOOT, FOOT)
    assert way_flags({"highway": "busway"}) == (BUS, BUS)
    assert way_flags({"highway": "residential", "foot": "no"}) == (BUS, BUS)
    assert way_flags({"highway": "residential", "access": "private"}) == (0, 0)
    assert way_flags({"highway": "residential", "access": "private", "foot": "yes"}) == (FOOT, FOOT)
    assert way_flags({"highway": "service", "service": "driveway"}) == (FOOT, FOOT)
    assert way_flags({"highway": "residential", "bus": "no"})[0] & BUS == 0
    assert way_flags({"highway": "proposed"}) == (0, 0)


def _g():
    g = Graph()
    g.add_way([1, 2, 3], [(40.0, -8.0), (40.0, -7.999), (40.0, -7.998)], BUS | FOOT, BUS | FOOT)
    return g


def test_route_pair_keeps_a_far_snap_and_flags_it():
    g = _g()
    path, m, fl = route_pair(g, BUS, (40.0006, -8.0), (40.0, -7.998), 150)       # 67 m from the road: within the limit
    assert path and fl == [] and path[0] == (40.0006, -8.0) and path[-1] == (40.0, -7.998) and m > 150
    path, m, fl = route_pair(g, BUS, (40.002, -8.0), (40.0, -7.998), 150)        # 222 m: beyond the limit, within 3 x: kept, flagged
    assert path and len(fl) == 1 and fl[0].startswith("snap-far from 22")
    path, m, fl = route_pair(g, BUS, (40.01, -8.0), (40.0, -7.998), 150)         # 1.1 km: no path
    assert path is None and fl == ["no-snap"]


def test_route_pair_flags_a_missing_route_and_stores_no_path():
    g = _g()
    g.add_way([10, 11], [(40.01, -8.0), (40.01, -7.999)], BUS, BUS)              # an island
    path, m, fl = route_pair(g, BUS, (40.0, -8.0), (40.01, -7.9995), 150)
    assert path is None and fl == ["no-route"]


def test_cells_and_groups():
    assert cell_of(BOX[0] + 0.05, BOX[2] + 0.05) == (0, 0) and cell_of(BOX[0] + 0.15, BOX[2] + 0.25) == (1, 2)
    near = cells_near(BOX[0] + 0.099, BOX[2] + 0.05, 1500)
    assert (0, 0) in near and (1, 0) in near                                       # 1.5 km north of the line reaches the next row
    g = group_cells(BOX[0] + 0.05, BOX[2] + 0.05, BOX[0] + 0.05, BOX[2] + 0.35)    # a 28 km pair spans several columns
    assert {c for _, c in g} >= {0, 1, 2, 3}
