from gtfsconv import stops as S
from gtfsconv.stops import Cand, dist_km


def cand(lat, lon, label="x", source="busway-exact"):
    return Cand(lat, lon, source, label)


def test_viterbi_picks_the_junta_next_to_its_neighbours():
    near_a, near_b = cand(40.640, -8.650, "A"), cand(40.645, -8.652, "B")
    junta_near, junta_far = cand(40.642, -8.651, "J-near"), cand(40.822, -8.651, "J-far")  # ~20 km apart
    assert 19 < dist_km((junta_near.lat, junta_near.lon), (junta_far.lat, junta_far.lon)) < 21
    for order in ([junta_far, junta_near], [junta_near, junta_far]):
        chosen, _ = S.viterbi([[near_a], order, [near_b]], [0, 4, 4])
        assert chosen[1].label == "J-near"


def test_place_trip_prunes_a_stop_whose_only_candidate_is_in_another_town():
    names = ["A", "B", "C"]
    tiers = {"A": [[cand(40.640, -8.650, "A")]], "B": [[cand(40.58, -8.45, "B-far")]], "C": [[cand(40.645, -8.652, "C")]], }
    chosen, pruned = S.place_trip(names, [0, 240, 480], lambda n: tiers[n])
    assert chosen[1] is None and pruned == {1} and chosen[0] and chosen[2]


def test_place_trip_falls_back_to_later_tiers():
    names = ["A", "B", "C"]
    tiers = {"A": [[cand(40.640, -8.650)]], "C": [[cand(40.645, -8.652)]],
             "B": [[cand(40.58, -8.45, "far")], [cand(40.642, -8.651, "near", "osm-exact")]]}
    chosen, pruned = S.place_trip(names, [0, 240, 480], lambda n: tiers[n])
    assert chosen[1].label == "near" and not pruned


def test_unplaced_name_is_none():
    chosen, _ = S.place_trip(["A", "Z"], [0, 240], lambda n: [[cand(40.64, -8.65)]] if n == "A" else [])
    assert chosen[1] is None


def test_merge_within_50m_conflict_up_to_300m_and_split_beyond():
    place = [("Aveiro", 40.64, -8.65), ("Ilhavo", 40.55, -8.60)]
    # 20 m apart -> one stop
    stops, lookup, conf = S.merge_stops({"Junta": {(40.6400, -8.6500): 1, (40.64018, -8.6500): 1}}, place)
    assert len(stops) == 1 and not conf
    # 150 m apart -> conflict
    stops, lookup, conf = S.merge_stops({"Junta": {(40.6400, -8.6500): 1, (40.6414, -8.6500): 1}}, place)
    assert conf and conf[0][0] == "Junta"
    # snapping (interim) merges into the larger cluster
    stops, lookup, conf = S.merge_stops({"Junta": {(40.6400, -8.6500): 5, (40.6414, -8.6500): 1}}, place, snap_conflicts=True)
    assert len(stops) == 1 and abs(stops[0]["lat"] - 40.6400) < 0.0005
    # > 300 m apart -> two stops, ids suffixed by the nearest town
    stops, lookup, conf = S.merge_stops({"Junta": {(40.6400, -8.6500): 1, (40.5500, -8.6000): 1}}, place)
    assert {s["stop_id"] for s in stops} == {"junta-aveiro", "junta-ilhavo"} and not conf


def test_stop_id_is_a_stable_slug():
    assert S.slug("Caçadores 10 - Sé") == "cacadores-10-se"


def test_fuzzy_rejects_different_numbers():
    src = S.Source([("Escola n.º 2", 40.9, -8.5)], "osm")
    assert src.fuzzy([S.key("Escola nº1 - A")]) == []
