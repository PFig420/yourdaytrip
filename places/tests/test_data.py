"""Tests on the real dataset (PlacesLayer.md 10.3, 10.4)."""
import time
from collections import Counter

from conftest import CAMPANHA, PLACES
from places.shortlist import _first_point, point_id
from places.validate import validate


def test_validate_has_no_errors(eng):
    rep = validate(PLACES / "data", PLACES / "config", engine=eng)
    assert rep.errors == []


def test_counts(real_lib):
    kinds = Counter(p.kind for p in real_lib.places.values() if p.status != "rejected")
    sights = kinds["sight"] + kinds["activity"]
    food = kinds["food"] + kinds["drink"]
    assert 50 <= sights <= 70
    assert 40 <= food <= 70     # 65 on 2026-10-23: 60 agent drafts + 5 imported Costa Nova/Torreira places
    # Pedro's target (M5): 30-40 confirmed food and drink
    confirmed = sum(1 for p in real_lib.places.values() if p.is_food and p.status == "confirmed")
    assert 30 <= confirmed <= 40


def test_every_municipality_has_two_sights(real_lib):
    cfg = real_lib.cfg
    per = Counter(p.municipality for p in real_lib.places.values() if not p.is_food and p.status != "rejected")
    for m in cfg.area["municipalities"]:
        assert per[m] >= 2, m


def test_every_record_has_a_source_and_cache(real_lib):
    import glob
    for p in real_lib.places.values():
        assert p.provenance.sources, p.id
        assert glob.glob(str(PLACES / "cache" / "sources" / f"{p.id}-*.txt")) or p.id in NO_CACHE, p.id


# records whose pages could not be fetched or read from this machine (JavaScript or blocked sites)
NO_CACHE = {"igreja-da-misericordia-aveiro", "casa-museu-julio-dinis"}


def test_smoke_shortlist_with_drafts(real_lib):
    sl = real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "21:00", include_drafts=True, limit=50)
    assert sl.sights and sl.food
    by_id = {i.place["id"]: i for i in sl.sights + sl.food}
    wins = {}
    for mw in {p.max_walk_m for p in real_lib.places.values()}:
        pts = [(point_id(i, k), la, lo) for i, p in real_lib.places.items() if p.max_walk_m == mw
               for k, (_, la, lo) in enumerate(p.all_points())]
        kw = {} if mw is None else {"max_walk_m": mw}
        wins.update({w.id: w for w in real_lib.engine.windows(CAMPANHA, "2026-10-24", "08:00", "21:00", pts, **kw).windows})
    for pid, item in by_id.items():
        w, _ = _first_point(real_lib.places[pid], wins)       # the window of the reference point reached first
        for s in item.slots:
            assert w.arrive <= s.start < s.end <= w.leave, pid


def test_only_confirmed_by_default(real_lib):
    sl = real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "21:00")
    # Without include_drafts only confirmed records may appear (Pedro confirmed 71 on 2026-10-22).
    ids = {i.place["id"] for i in sl.sights + sl.food}
    assert ids, "expected some confirmed places to fit this day"
    assert all(real_lib.get(i).status == "confirmed" for i in ids)


def test_performance(real_lib):
    real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "21:00", include_drafts=True)      # warm the date cache
    t = time.perf_counter()
    for _ in range(3):
        real_lib.shortlist(CAMPANHA, "2026-10-24", "08:00", "21:00", include_drafts=True, limit=100)
    assert (time.perf_counter() - t) / 3 < 0.5


def test_rejected_and_beach_access_points(real_lib):
    for pid in ("cais-do-bico", "praia-furadouro"):
        p = real_lib.get(pid)
        assert p.status == "rejected" and p.provenance.status_reason == "no public transport in the current feeds"
    sl = real_lib.shortlist(CAMPANHA, "2026-10-26", "08:00", "21:00", include_drafts=True, limit=100)
    ids = {i.place["id"] for i in sl.sights}
    assert not ids & {"cais-do-bico", "praia-furadouro"}
    assert "praia-sao-jacinto" in ids                      # reachable only through its max_walk_m
    assert real_lib.get("praia-sao-jacinto").max_walk_m == 1500


def test_food_within_600m_of_a_stop(real_lib, eng):
    from places.network import nearest_stop_m
    for p in real_lib.places.values():
        if p.is_food:
            assert nearest_stop_m(eng, p.lat, p.lon) <= 600, p.id
