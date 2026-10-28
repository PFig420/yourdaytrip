"""The compact pack (Execution.md M5): local backend only; the Claude backends keep the full pack byte for byte."""
import copy

import pytest
from conftest import request

import planner
from planner import pack as packmod


def _cfg(pl, profile):
    cfg = copy.deepcopy(pl.cfg)
    cfg["pack"]["profile"] = profile
    return cfg


@pytest.mark.parametrize("backend", ["api", "claude-code"])
def test_claude_backends_keep_the_full_pack(pl, backend):
    p = planner.Planner(pl.engine, pl.lib, _cfg(pl, "compact"), client=object(), backend=backend)
    assert not p.compact_pack
    req = request()
    assert packmod.build(pl.lib, pl.engine, req, p.cfg, compact=p.compact_pack).dumps() == \
        packmod.build(pl.lib, pl.engine, req, pl.cfg).dumps()


def test_local_backend_uses_compact_only_when_asked(pl):
    assert not planner.Planner(pl.engine, pl.lib, _cfg(pl, "full"), backend="local").compact_pack
    assert planner.Planner(pl.engine, pl.lib, _cfg(pl, "compact"), backend="local").compact_pack


def test_compact_pack_limits(pl):
    req = request(origin="aveiro-station", a="09:30", b="22:30")
    full = packmod.build(pl.lib, pl.engine, req, pl.cfg)
    small = packmod.build(pl.lib, pl.engine, req, pl.cfg, compact=True)
    cc = pl.cfg["pack"]["compact"]
    sights = [c for c in small.candidates if c["kind"] in ("sight", "activity")]
    assert len(sights) <= cc["sights"] and len(small.candidates) - len(sights) <= cc["food"]
    assert all(len(c["description"]) <= cc["description_max"] for c in small.candidates)
    assert all(m <= cc["hint_max_min"] for row in small.hints.values() for m in row.values())
    assert small.request == full.request          # the day block and request are not trimmed
    assert len(small.dumps()) < 0.7 * len(full.dumps())


def test_clip_cuts_at_a_word():
    assert packmod.clip("short", 160) == "short"
    out = packmod.clip("one two three, four five", 15)
    assert len(out) <= 15 and out.endswith("…") and out == "one two three…"
