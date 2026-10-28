"""Shared fixtures. Real engine and places data; the language model is always a FakeClient."""
import dataclasses
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (ROOT / "engine", ROOT / "places", ROOT / "planner"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import anthropic  # noqa: E402
import httpx  # noqa: E402

import planner  # noqa: E402
from planner.models import (DayDraft, DraftStop, NarrativeOut, ORIGINS, PlanRequest,  # noqa: E402
                            StopLine)


@pytest.fixture(scope="session")
def pl():
    """One planner (engine, places, route cache) shared by the session; `make` gives it a scripted client."""
    return planner.load(config=str(ROOT / "planner/config.toml"), client=FakeClient([]))


@pytest.fixture
def make(pl):
    def _make(script):
        p = planner.Planner(pl.engine, pl.lib, pl.cfg, client=FakeClient(script), accepted_legs=pl.accepted_legs)
        p.router = pl.router
        p.sched.router = pl.router
        return p
    return _make


def request(origin="porto-campanha", date="2026-10-24", a="08:00", b="21:00", **kw):
    return PlanRequest(origin=ORIGINS[origin], date=date, leave_after=a, back_by=b, **kw)


def draft(*stops, title="A day out", notes=""):
    return DayDraft(title=title, notes=notes,
                    stops=[DraftStop(place_id=i, visit_minutes=m, why="chosen for the test") for i, m in stops])


def narrative(plan_ids, summary="A calm day by the ria.", lines=None):
    return NarrativeOut(summary=summary, stop_lines=[StopLine(place_id=i, line=(lines or {}).get(i, "A good stop.")) for i in plan_ids])


GOLDEN = [("museu-de-aveiro-santa-joana", 75), ("canastra-do-fidalgo-costa-nova", 90), ("costa-nova-palheiros", 75)]


class FakeClient:
    """Same surface as anthropic.Anthropic for the calls the planner makes: beta.messages.parse / messages.parse.

    `script` items: a pydantic object (returned as parsed_output), "refusal", "max_tokens", or an exception to raise.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self.messages = SimpleNamespace(parse=self._parse)
        self.models = SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m))

    def _parse(self, **kw):
        self.calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=900, cache_creation_input_tokens=0)
        if item in ("refusal", "max_tokens"):
            return SimpleNamespace(stop_reason=item, parsed_output=None, usage=usage, content=[])
        return SimpleNamespace(stop_reason="end_turn", parsed_output=item, usage=usage, content=[])


def status_error(cls, code):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(code, request=req), body=None)
