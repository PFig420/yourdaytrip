"""The local backend (Planner.md 2.5) against a mocked llama-server (httpx.MockTransport). Nothing here touches the network."""
import copy
import json

import httpx
import pytest

import planner
from conftest import GOLDEN, ROOT, draft, narrative, request
from planner import eval as ev
from planner import local as lc
from planner.claude_code import BackendError
from planner.models import DayDraft

IDS = [i for i, _ in GOLDEN]


def reply(obj=None, content=None, finish="stop", timings=True, status=200):
    text = content if content is not None else obj.model_dump_json()
    d = {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
         "usage": {"prompt_tokens": 12000, "completion_tokens": 300, "total_tokens": 12300}}
    if timings:
        d["timings"] = {"cache_n": 11000, "prompt_n": 1000, "prompt_per_second": 80.5, "predicted_n": 300, "predicted_per_second": 9.25}
    return httpx.Response(status, json=d)


class Server:
    """A MockTransport handler answering from a script of Responses or exceptions; records every request."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, request):
        self.calls.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content or b"{}")})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self):
        return httpx.MockTransport(self)


def make_local(pl, script):
    s = Server(script)
    cfg = copy.deepcopy(pl.cfg)
    cfg["pack"]["profile"] = "full"     # the scripted GOLDEN draft uses places from the full pack; test_pl_compact covers compact
    p = planner.Planner(pl.engine, pl.lib, cfg, accepted_legs=pl.accepted_legs, backend="local", runner=s.transport)
    p.router = pl.router
    p.sched.router = pl.router
    return p, s


def test_valid_draft_and_narrative(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.status == "ok" and plan.source == "llm" and plan.backend == "local" and plan.attempts == 1
    assert plan.model == "qwen3-4b-instruct-2507-q4_k_m" and plan.narrative
    d = s.calls[0]
    assert d["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    b = d["body"]
    assert b["model"] == "local-planner" and b["stream"] is False and b["seed"] == 42 and b["max_tokens"] == pl.cfg["local"]["max_tokens"]
    assert b["messages"][0]["role"] == "system" and "plan one realistic day" in b["messages"][0]["content"]
    assert b["messages"][1]["role"] == "user" and isinstance(b["messages"][1]["content"], str)
    rf = b["response_format"]["json_schema"]
    assert rf["name"] == "DayDraft" and rf["strict"] is True and rf["schema"]["properties"]["stops"]
    assert s.calls[1]["body"]["max_tokens"] == 1024 and s.calls[1]["body"]["response_format"]["json_schema"]["name"] == "NarrativeOut"


def test_usage_recorded(pl):
    p, _ = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    c = p.plan(request()).usage.calls[0]
    assert c["label"] == "draft" and c["effort"] == "n/a" and c["input_tokens"] == 12000 and c["output_tokens"] == 300
    assert c["cache_read_tokens"] == 11000 and c["prompt_tps"] == 80.5 and c["gen_tps"] == 9.25 and c["notional_usd"] is None


def test_usage_without_timings(pl):
    p, _ = make_local(pl, [reply(draft(*GOLDEN), timings=False), reply(narrative(IDS), timings=False)])
    c = p.plan(request()).usage.calls[0]
    assert c["cache_read_tokens"] == 0 and c["prompt_tps"] is None


def test_retry_history_is_plain_strings(pl):
    bad = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))
    p, s = make_local(pl, [reply(bad), reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.attempts == 2 and plan.source == "llm"
    m = s.calls[1]["body"]["messages"]
    assert [x["role"] for x in m] == ["system", "user", "assistant", "user"]
    assert all(isinstance(x["content"], str) for x in m)
    assert "no-such-place" in m[2]["content"] and "unknown_place: no-such-place" in m[3]["content"]
    assert m[1] == s.calls[0]["body"]["messages"][1]                 # the first turns are never edited (prefix cache)


def test_schema_valid_but_rule_breaking_draft_goes_to_validator(pl):
    bad = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))
    p, _ = make_local(pl, [reply(bad)] * 3)
    plan = p.plan(request())
    assert plan.source == "fallback" and plan.attempts == 3 and plan.attempt_errors and "Built without the language model" in " ".join(plan.warnings)


def test_invalid_json_then_recovery(pl):
    p, _ = make_local(pl, [reply(content="not json"), reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.source == "fallback" and p.llm.fatal == ""           # a backend error ends the loop, as with the other backends


def test_connection_refused_is_fatal(pl):
    p, s = make_local(pl, [httpx.ConnectError("refused")])
    plan = p.plan(request())
    assert p.llm.fatal == "connection" and plan.source == "fallback" and len(s.calls) == 1


@pytest.mark.parametrize("script,kind", [
    ([httpx.ReadTimeout("slow")], "timeout"),
    ([httpx.Response(500, text="boom")], "http_status_500"),
    ([httpx.Response(400, json={"error": {"type": "exceed_context_size_error", "message": "request (30000 tokens) exceeds the available context size (20480 tokens)"}})], "context_overflow"),
    ([reply(draft(*GOLDEN), finish="length")], "max_tokens"),
    ([reply(content="{not json")], "no_structured_output"),
    ([reply(content='{"title": 5}')], "no_structured_output"),
])
def test_backend_error_kinds(script, kind):
    b = lc.LocalBackend({}, transport=Server(script).transport)
    with pytest.raises(BackendError) as e:
        b.call(system="s", messages=[{"role": "user", "content": "x"}], schema_cls=DayDraft)
    assert e.value.kind == kind and e.value.fatal is False


def test_non_fatal_errors_fall_back_without_stopping(pl):
    for item in (httpx.ReadTimeout("slow"), httpx.Response(500, text="boom")):
        p, _ = make_local(pl, [item])
        plan = p.plan(request())
        assert plan.source == "fallback" and p.llm.fatal == ""
        assert plan.usage.calls[0]["backend_error"].startswith(("timeout", "http_status_500"))


def test_schema_retry_when_server_rejects_it():
    s = Server([httpx.Response(400, json={"error": {"message": "Unrecognized schema keyword"}}), reply(draft(*GOLDEN))])
    out, _ = lc.LocalBackend({}, transport=s.transport).call(system="s", messages=[{"role": "user", "content": "x"}], schema_cls=DayDraft)
    assert out.title == "A day out" and len(s.calls) == 2
    simple = s.calls[1]["body"]["response_format"]["json_schema"]["schema"]
    assert "title" in simple["properties"] and "title" not in simple      # the property stays, the annotation goes


def test_api_key_header_only_when_set(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
    s = Server([reply(draft(*GOLDEN))])
    lc.LocalBackend({}, transport=s.transport).call(system="s", messages=[{"role": "user", "content": "x"}], schema_cls=DayDraft)
    assert "authorization" not in s.calls[0]["headers"]
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "k-123")
    s = Server([reply(draft(*GOLDEN))])
    lc.LocalBackend({}, transport=s.transport).call(system="s", messages=[{"role": "user", "content": "x"}], schema_cls=DayDraft)
    assert s.calls[0]["headers"]["authorization"] == "Bearer k-123"


def test_health_with_local_backend(pl):
    from fastapi.testclient import TestClient
    from planner.web import create_app
    up = Server([httpx.Response(200, json={"status": "ok"})])
    p, _ = make_local(pl, [])
    p.llm._runner = up.transport
    h = TestClient(create_app(p)).get("/health").json()
    assert h["api_reachable"] is True and h["model"] == "qwen3-4b-instruct-2507-q4_k_m"
    down = Server([httpx.ConnectError("refused")])
    p, _ = make_local(pl, [])
    p.llm._runner = down.transport
    assert TestClient(create_app(p)).get("/health").json()["api_reachable"] is False


def test_eval_local_rows_and_gate_l(pl, tmp_path):
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")
    pick = [(i, r) for i, r in reqs if r.date == "2026-10-24" and r.origin.id == "aveiro-station"][:1]
    s = Server([httpx.ConnectError("refused")])
    out = ev.run(pl, pick, "qwen3-test", "na", tmp_path, backend="local", runner=s.transport)
    assert out["stopped"] == "connection" and out["done"] == 0                  # fatal: stops cleanly, nothing logged
    s = Server([httpx.Response(500, text="boom")])
    out = ev.run(pl, pick, "qwen3-test", "na", tmp_path, backend="local", runner=s.transport)
    row = json.loads(next(tmp_path.glob("qwen3-test-na-*-local.jsonl")).read_text().splitlines()[0])
    assert row["backend"] == "local" and row["cost_usd"] is None and row["notional_usd"] is None
    assert "call_latency_s" in row and "prompt_tps" in row and "gen_tps" in row
    assert "gate_l" in out and out["gate_l"]["fallback_le_20"] in (True, False)


def test_failed_drafts_are_kept_with_names_and_errors(pl):
    bad = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))
    p, _ = make_local(pl, [reply(bad), reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request(), debug=True)
    assert plan.source == "llm" and len(plan.debug["attempt_drafts"]) == 1
    a = plan.debug["attempt_drafts"][0]
    assert [s["place_id"] for s in a["draft"]["stops"]] == ["no-such-place", "museu-de-aveiro-santa-joana"]
    assert a["draft"]["stops"][0]["name"] is None and a["draft"]["stops"][1]["name"] == "Museu de Aveiro / Santa Joana"
    assert [e["code"] for e in a["errors"]] == plan.attempt_errors[0] and "unknown_place" in plan.attempt_errors[0]
    assert all(e["message"] for e in a["errors"])
    assert "attempt_drafts" not in plan.to_json()                    # moved under debug


def test_debug_is_null_by_default(pl):
    p, _ = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    assert p.plan(request()).debug is None
    p, _ = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    assert p.plan(request(), debug=False).to_json()["debug"] is None


def test_debug_carries_the_accepted_draft_notes(pl):
    p, _ = make_local(pl, [reply(draft(*GOLDEN, title="Ria and sea", notes="Left out the beach: not enough time.")), reply(narrative(IDS))])
    plan = p.plan(request(), debug=True)
    d = plan.debug["draft"]
    assert d["title"] == "Ria and sea" and d["notes"] == "Left out the beach: not enough time."
    assert [s["place_id"] for s in d["stops"]] == IDS and d["stops"][0]["name"] and d["stops"][0]["why"]
    assert plan.debug["attempt_drafts"] == []


def test_debug_on_a_fallback_plan_has_no_draft_but_the_failed_ones(pl):
    bad = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))
    p, _ = make_local(pl, [reply(bad)] * 3)
    plan = p.plan(request(), debug=True)
    assert plan.source == "fallback" and plan.debug["draft"] is None and len(plan.debug["attempt_drafts"]) == 3
    p, _ = make_local(pl, [])
    assert p.plan(request(), use_llm=False, debug=True).debug == {"draft": None, "attempt_drafts": []}


def test_visits_carry_their_reason(pl):
    p, _ = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    visits = [e for e in p.plan(request()).timeline if e.type == "visit" and not e.inserted]
    assert visits and all(e.reason for e in visits)
    fb = [e for e in p.plan(request(), use_llm=False).timeline if e.type == "visit" and not e.inserted]
    assert fb and all(e.reason for e in fb)


# ---- one narrative retry, local backend only (Changes.md C) ----------------------------------------------------------
LONG = " ".join(["word"] * 90)


def narrative_calls(s):
    return [c for c in s.calls if c["body"]["response_format"]["json_schema"]["name"] == "NarrativeOut"]


def test_narrative_retry_rescues_a_too_long_summary(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS, summary=LONG)), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.narrative and plan.narrative["summary"] == "A calm day by the ria."
    assert not [w for w in plan.warnings if "narrative" in w.lower()]
    first, retry = narrative_calls(s)
    m1, m2 = first["body"]["messages"], retry["body"]["messages"]
    assert [x["role"] for x in m2] == ["system", "user", "assistant", "user"]
    assert m2[:2] == m1                                            # the original turns are untouched: the prefix cache holds
    assert json.loads(m2[2]["content"])["summary"] == LONG          # the failed answer, as an assistant turn
    assert "summary has 90 words; the limit is 80" in m2[3]["content"]
    assert retry["body"]["max_tokens"] == first["body"]["max_tokens"] == 1024
    assert retry["body"]["response_format"] == first["body"]["response_format"]
    assert [c["label"] for c in plan.usage.calls] == ["draft", "narrative", "narrative_retry"]
    assert plan.usage.input_tokens == 3 * 12000


def test_narrative_retry_names_the_stop_ids_in_timeline_order(pl):
    wrong = narrative(IDS[:2] + ["somewhere-else"], lines={IDS[0]: " ".join(["w"] * 30)})
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(wrong), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.narrative
    msg = narrative_calls(s)[1]["body"]["messages"][3]["content"]
    assert f"line for {IDS[0]} has 30 words; the limit is 25" in msg
    assert "these place_ids, in this order: " + ", ".join(IDS) in msg
    assert "somewhere-else" in msg                                 # and what the first answer had


def test_two_failed_narratives_give_none_and_one_warning(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS, summary=LONG)), reply(narrative(IDS, summary=LONG + " more"))])
    plan = p.plan(request())
    assert plan.narrative is None and plan.status == "ok" and len(narrative_calls(s)) == 2
    warned = [w for w in plan.warnings if "narrative" in w.lower()]
    assert len(warned) == 1 and "dropped" in warned[0] and "summary is longer than 80 words" in warned[0]
    assert [c["label"] for c in plan.usage.calls] == ["draft", "narrative", "narrative_retry"]


def test_a_passing_first_narrative_makes_no_retry(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request())
    assert plan.narrative and len(narrative_calls(s)) == 1 and [c["label"] for c in plan.usage.calls] == ["draft", "narrative"]


def test_the_retry_count_is_a_setting(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS, summary=LONG))])
    p.cfg["local"]["narrative_retries"] = 0
    plan = p.plan(request())
    assert plan.narrative is None and len(narrative_calls(s)) == 1
    assert pl.cfg["local"]["narrative_retries"] == 1               # the shipped default


def test_a_retry_that_fails_to_answer_is_one_warning(pl):
    p, s = make_local(pl, [reply(draft(*GOLDEN)), reply(narrative(IDS, summary=LONG)), httpx.Response(500, text="boom")])
    plan = p.plan(request())
    assert plan.narrative is None and len([w for w in plan.warnings if "narrative" in w.lower()]) == 1


def test_claude_backends_make_no_narrative_retry(pl, monkeypatch):
    from conftest import FakeClient
    from test_pl_backend import FakeRunner, envelope
    cfg = copy.deepcopy(pl.cfg)
    cfg["local"]["narrative_retries"] = 1                          # even with the local setting on
    bad = narrative(IDS, summary=LONG)
    client = FakeClient([draft(*GOLDEN), bad])
    api = planner.Planner(pl.engine, pl.lib, cfg, client=client, accepted_legs=pl.accepted_legs)
    api.router = api.sched.router = pl.router
    plan = api.plan(request())
    assert plan.narrative is None and len(client.calls) == 2 and len([w for w in plan.warnings if "dropped" in w]) == 1
    runner = FakeRunner([envelope(draft(*GOLDEN)), envelope(bad)])
    code = planner.Planner(pl.engine, pl.lib, cfg, accepted_legs=pl.accepted_legs, backend="claude-code", runner=runner)
    code.router = code.sched.router = pl.router
    import planner.claude_code as cc
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    plan = code.plan(request())
    assert plan.narrative is None and len(runner.calls) == 2


# ---- a cut-off draft is retried like Claude's (CurrentChanges.md R) ---------------------------------------------------
def cut_off(text='{"title": "A day", "stops": [{"place_id": "museu-de-aveiro-santa-joana", "visit_minutes": 6'):
    return reply(content=text, finish="length")


def test_a_cut_off_draft_is_retried_and_its_text_kept(pl):
    partial = '{"title": "A day", "stops": [{"place_id": "x", "visit_minutes": 6'
    p, s = make_local(pl, [cut_off(partial), reply(draft(*GOLDEN)), reply(narrative(IDS))])
    plan = p.plan(request(), debug=True)
    assert plan.source == "llm" and plan.attempts == 2 and plan.attempt_errors == [["schema"]]
    a = plan.debug["attempt_drafts"][0]
    assert a["draft"] is None and a["raw"] == partial
    assert a["errors"][0]["code"] == "schema" and "cut off" in a["errors"][0]["message"]
    m = s.calls[1]["body"]["messages"]
    assert [x["role"] for x in m] == ["system", "user", "assistant", "user"]
    assert m[2]["content"] == "{}" and "visit_minutes" not in m[2]["content"] and partial not in m[3]["content"]
    assert "schema:" in m[3]["content"]
    assert plan.usage.calls[0]["backend_error"].startswith("max_tokens")
    p2, _ = make_local(pl, [cut_off(partial), reply(draft(*GOLDEN)), reply(narrative(IDS))])
    assert p2.plan(request()).debug is None


def test_three_cut_off_drafts_give_the_fallback_plan(pl):
    p, s = make_local(pl, [cut_off("a"), cut_off("b"), cut_off("c")])
    plan = p.plan(request(), debug=True)
    assert plan.source == "fallback" and plan.attempts == 3 and plan.attempt_errors == [["schema"]] * 3 and len(s.calls) == 3
    assert [a["raw"] for a in plan.debug["attempt_drafts"]] == ["a", "b", "c"] and p.llm.fatal == ""


def test_the_kept_text_is_at_most_4000_characters(pl):
    p, _ = make_local(pl, [cut_off("x" * 9000)] * 3)
    assert {len(a["raw"]) for a in p.plan(request(), debug=True).debug["attempt_drafts"]} == {4000}


def test_other_backend_errors_still_go_straight_to_the_fallback(pl):
    for item in (httpx.ReadTimeout("slow"), httpx.Response(500, text="boom"), reply(content="{not json")):
        p, s = make_local(pl, [item])
        plan = p.plan(request())
        assert plan.source == "fallback" and plan.attempts == 1 and len(s.calls) == 1 and plan.attempt_errors == []
