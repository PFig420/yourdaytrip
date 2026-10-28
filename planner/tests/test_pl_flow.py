"""Retry, refusal, error, narrative and coverage behaviour with a scripted client (Planner.md 10.1)."""
import anthropic
import pytest

from conftest import GOLDEN, FakeClient, draft, narrative, request, status_error
from planner import narrative as nar

IDS = [i for i, _ in GOLDEN]
BAD = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))


def test_retry_then_valid(make):
    p = make([BAD, draft(*GOLDEN), narrative(IDS)])
    plan = p.plan(request())
    assert plan.source == "llm" and plan.attempts == 2 and plan.status == "ok"
    c = p._client.calls
    assert len(c) == 3                                           # 2 planner calls + the narrative
    first, second = c[0]["messages"], c[1]["messages"]
    assert second[: len(first)] == first                         # append only: earlier turns untouched
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "unknown_place: no-such-place" in second[-1]["content"]
    assert [x["label"] for x in plan.usage.calls] == ["draft", "draft", "narrative"]
    assert plan.usage.cache_read_tokens == 2700


def test_three_invalid_drafts_fall_back(make):
    p = make([BAD, BAD, BAD])
    plan = p.plan(request())
    assert plan.source == "fallback" and plan.attempts == 3 and plan.status == "ok"
    assert len(p._client.calls) == 3
    assert any("failed the schedule check" in w for w in plan.warnings)
    assert plan.narrative is None


def test_refusal_goes_straight_to_fallback(make):
    p = make(["refusal"])
    plan = p.plan(request())
    assert plan.source == "fallback" and plan.attempts == 1 and plan.status == "ok"
    assert any("declined" in w for w in plan.warnings)


@pytest.mark.parametrize("exc", [
    status_error(anthropic.RateLimitError, 429),
    status_error(anthropic.InternalServerError, 500),
    anthropic.APIConnectionError(request=__import__("httpx2").Request("POST", "https://api.anthropic.com")),
])
def test_api_errors_fall_back(make, exc):
    p = make([exc])
    plan = p.plan(request())
    assert plan.source == "fallback" and plan.status == "ok" and len(p._client.calls) == 1
    assert any("model call failed" in w for w in plan.warnings)


def test_cut_off_reply_is_a_schema_error_and_retried(make):
    p = make(["max_tokens", draft(*GOLDEN), narrative(IDS)])
    plan = p.plan(request())
    assert plan.source == "llm" and plan.attempts == 2
    assert "schema:" in p._client.calls[1]["messages"][-1]["content"]


def test_call_parameters(make):
    p = make([draft(*GOLDEN), narrative(IDS)])
    p.plan(request())
    kw, nk = p._client.calls[0], p._client.calls[1]
    assert kw["model"] == "claude-sonnet-5-5" and kw["max_tokens"] == 16000
    assert kw["output_config"] == {"effort": "medium"} and "thinking" not in kw
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert nk["output_config"] == {"effort": "low"}
    assert kw["system"] != nk["system"]           # the narrative has its own, equally stable, prompt


def test_system_prompt_is_stable(make):
    a, b = make([draft(*GOLDEN), narrative(IDS)]), make([draft(*GOLDEN), narrative(IDS)])
    a.plan(request())
    b.plan(request(date="2026-10-27"))
    assert a._client.calls[0]["system"] == b._client.calls[0]["system"]


# ---- narrative check ---------------------------------------------------------------
def test_narrative_with_invented_time_is_dropped(make):
    p = make([draft(*GOLDEN), narrative(IDS, summary="Start at 11:00 sharp.")])
    plan = p.plan(request())
    assert plan.status == "ok" and plan.narrative is None
    assert any("11:00" in w for w in plan.warnings)


def test_narrative_with_timeline_time_is_kept(make):
    p = make([draft(*GOLDEN), narrative(IDS, summary="Museum at 10:00, then lunch at 12:19.")])
    assert p.plan(request()).narrative["summary"].startswith("Museum at 10:00")


@pytest.mark.parametrize("text,frag", [
    ("Tickets cost €7.", "price"),
    ("Call 234 123 456 to book.", "phone"),
    ("See www.example.pt for more.", "web address"),
    ("Then visit the Museu Marítimo de Ílhavo.", "not a stop"),
])
def test_narrative_rejections(make, text, frag):
    p = make([draft(*GOLDEN), narrative(IDS, summary=text)])
    plan = p.plan(request())
    assert plan.narrative is None and any(frag in w for w in plan.warnings)


def test_narrative_word_limits_and_missing_lines(make):
    p = make([draft(*GOLDEN), narrative(IDS, summary=" ".join(["word"] * 81))])
    assert p.plan(request()).narrative is None
    p = make([draft(*GOLDEN), narrative(IDS[:2])])
    assert p.plan(request()).narrative is None


def test_narrative_failure_keeps_the_plan(make):
    p = make([draft(*GOLDEN), status_error(anthropic.InternalServerError, 500)])
    plan = p.plan(request())
    assert plan.status == "ok" and plan.narrative is None


# ---- request and coverage --------------------------------------------------------------
def test_request_validation():
    with pytest.raises(ValueError):
        request(a="21:00", b="08:00")
    with pytest.raises(ValueError):
        request(text="x" * 201)
    with pytest.raises(ValueError):
        request(date="2026-13-40")


def test_unknown_interest(make):
    with pytest.raises(ValueError, match="unknown interest"):
        make([]).plan(request(interests=["nope"]), use_llm=False)


def test_cp_coverage_warning(make):
    p = make([])
    plan = p.plan(request(date="2026-12-19"), use_llm=False)
    assert any("cp" in w.lower() for w in plan.warnings), plan.warnings
    assert plan.status == "no_plan" or plan.source == "fallback"
    if plan.status == "no_plan":
        assert plan.errors[0]["code"] == "no_journey" and plan.timeline == []
    assert len(plan.disclaimers) == 3


def test_summer_2027_from_aveiro(make):
    p = make([])
    plan = p.plan(request(origin="aveiro-station", date="2027-07-17", a="09:00", b="20:00"), use_llm=False)
    assert plan.status == "ok"


def test_no_plan_has_disclaimers_and_no_timeline(make):
    plan = make([]).plan(request(a="08:00", b="08:20"), use_llm=False)
    assert plan.status == "no_plan" and plan.timeline == [] and len(plan.disclaimers) == 3
