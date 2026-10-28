import math
import random

import pytest

from conftest import CAMPANHA, COSTA_NOVA

SAT = "2026-10-24"


def replay(eng, j, cn, t_min=None, buf=120):
    """Check a journey against the real connections, change rules and barriers."""
    net, tt = eng.net, eng.tt
    prev = None
    for i, leg in enumerate(j.legs):
        if leg.type == "ride":
            tpl = tt.tpl[leg.trip_id]
            hops = tpl[3]
            ok = False
            for (tid, sd) in {(t, d) for t, d in cn.trips if t == leg.trip_id}:
                from reach.calendar import to_instant
                base = to_instant(sd, 0)
                # find board hop and alight hop on this trip instance
                b = [h for h in hops if net.ids[h[2]] == leg.board_stop_id and base + h[0] == leg.board_time]
                a = [h for h in hops if net.ids[h[3]] == leg.alight_stop_id and base + h[1] == leg.alight_time]
                if b and a and b[0][6] <= a[0][6] and all(h[4] for h in b[:1]) and a[0][5]:
                    ok = True
            assert ok, f"ride not found in feed: {leg}"
            if prev is not None:
                # change rules: alight + walking + buffer <= board
                gap = leg.board_time - prev["t"] - prev["walk"]
                assert gap >= buf, f"change too tight ({gap}s)"
            prev = {"t": leg.alight_time, "walk": 0}
        else:
            if eng.overrides.state == "ok":       # a leg may carry OSM path metres, which take no detour factor (WalkBarriers.md)
                assert leg.seconds >= math.ceil(leg.metres / 1.2) - 2
            else:
                assert leg.seconds >= math.ceil(leg.metres * 1.3 / 1.2) - 2   # metres are rounded in the leg
            if leg.from_id in net.index and leg.to_id in net.index:
                a, b = net.index[leg.from_id], net.index[leg.to_id]
                assert not net.barriers.blocks(net.lat[a], net.lon[a], net.lat[b], net.lon[b])
            if prev is not None:
                prev["walk"] = leg.seconds
    return True


@pytest.fixture(scope="module")
def pairs(eng):
    rnd = random.Random(20261019)
    ids = [i for i in eng.net.ids]
    out = []
    while len(out) < 50:
        a, b = rnd.sample(ids, 2)
        out.append((a, b))
    return out


def pt(eng, sid):
    i = eng.net.index[sid]
    return (sid, eng.net.lat[i], eng.net.lon[i])


def test_journeys_replay_and_monotone(eng, pairs):
    found = 0
    for a, b in pairs:
        A, B = pt(eng, a), pt(eng, b)
        j = eng.earliest(A, SAT, "08:00", B)
        if j is None:
            continue
        found += 1
        cn = eng.tt.window(__import__("datetime").date(2026, 10, 24))
        replay(eng, j, cn)
        from reach.calendar import local_instant
        import datetime as dt
        d = dt.date(2026, 10, 24)
        assert j.arrive >= local_instant(d, "08:00")
        j2 = eng.earliest(A, SAT, "08:01", B)
        assert j2 is None or j2.arrive >= j.arrive
    assert found >= 10


def test_latest_replay_and_forward_agreement(eng, pairs):
    import datetime as dt
    from reach.calendar import local_instant
    d = dt.date(2026, 10, 24)
    cn = eng.tt.window(d)
    back = local_instant(d, "21:00")
    checked = 0
    for a, b in pairs:
        A, B = pt(eng, a), pt(eng, b)
        j = eng.latest(A, SAT, B, "21:00")
        if j is None:
            continue
        replay(eng, j, cn)
        assert j.arrive <= back
        fwd = eng.earliest(A, SAT, iso_after(j.depart), B)
        assert fwd is not None and fwd.arrive <= back
        checked += 1
    assert checked >= 10


def iso_after(instant):
    from reach.calendar import iso_local
    return iso_local(instant)[:19]


def test_windows_consistency(eng, pairs):
    import datetime as dt
    from reach.calendar import local_instant
    d = dt.date(2026, 10, 24)
    cn = eng.tt.window(d)
    back = local_instant(d, "21:00")
    pts = [pt(eng, b) for _a, b in pairs[:30]]
    for origin_id in (CAMPANHA, pt(eng, pairs[0][0])):
        w = eng.windows(origin_id, SAT, "08:00", "21:00", pts, include_journeys=True)
        for x in w.windows:
            if x.outbound:
                assert x.outbound.arrive == x.arrive
                replay(eng, x.outbound, cn)
            if x.return_:
                assert x.return_.depart == x.leave
                assert x.return_.arrive <= back
                replay(eng, x.return_, cn)
            if x.arrive is not None and x.leave is not None:
                assert x.available_s == x.leave - x.arrive
                assert x.status in ("ok", "no_window")
            if x.arrive is None:
                assert x.status == "unreachable"
            elif x.leave is None:
                assert x.status == "no_return"


def test_windows_match_point_queries(eng):
    w = eng.windows(CAMPANHA, SAT, "08:00", "21:00", [COSTA_NOVA]).windows[0]
    j = eng.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA)
    k = eng.latest(COSTA_NOVA, SAT, CAMPANHA, "21:00")
    assert w.arrive == j.arrive
    assert w.leave == k.depart


def test_exclude_filters(eng):
    j = eng.latest(("bt", 40.643800, -8.638550), SAT, CAMPANHA, "21:00", exclude_routes=["AP"])
    assert all(l.route_short != "AP" for l in j.legs if l.type == "ride")
    j = eng.earliest(CAMPANHA, SAT, "08:00", COSTA_NOVA, exclude_route_types=[2])
    assert j is None        # without trains there is no way from Porto
