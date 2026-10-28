"""The Claude Code test backend (Planner.md 2.4, 10.1) with a fake subprocess runner. Nothing here starts the CLI."""
import json
import os

import pytest

import planner
from conftest import GOLDEN, ROOT, draft, narrative, request
from planner import claude_code as cc
from planner import eval as ev

IDS = [i for i, _ in GOLDEN]


def envelope(obj=None, usage=True, is_error=False, result="", rc=0):
    d = {"type": "result", "is_error": is_error, "result": result or (obj.model_dump_json() if obj else ""),
         "usage": {"input_tokens": 3, "output_tokens": 50, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 20},
         "total_cost_usd": 0.01, "duration_ms": 1234}
    if obj is not None:
        d["structured_output"] = obj.model_dump(mode="json")
    return rc, json.dumps(d), ""


class FakeRunner:
    """Records every call; answers from a script of (rc, stdout, stderr) tuples or a callable."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, cmd, stdin, env, cwd, timeout=None):
        self.calls.append({"cmd": list(cmd), "stdin": stdin, "env": dict(env), "cwd": cwd,
                           "cwd_files": os.listdir(cwd), "cwd_exists": os.path.isdir(cwd)})
        return self.script.pop(0)


def make_cc(pl, script, effort="medium"):
    r = FakeRunner(script)
    p = planner.Planner(pl.engine, pl.lib, pl.cfg, accepted_legs=pl.accepted_legs, backend="claude-code", runner=r)
    p.router = pl.router
    p.sched.router = pl.router
    return p, r


def test_command_shape(pl, monkeypatch):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    p, r = make_cc(pl, [envelope(draft(*GOLDEN)), envelope(narrative(IDS))])
    plan = p.plan(request())
    assert plan.status == "ok" and plan.source == "llm" and plan.backend == "claude-code"
    cmd = r.calls[0]["cmd"]
    assert "--bare" not in " ".join(r.calls[0]["cmd"]) and all("--bare" not in c["cmd"] for c in r.calls)
    assert cmd[:2] == ["/x/claude", "-p"]
    for flag in ("--model", "--effort", "--system-prompt", "--json-schema", "--output-format", "--strict-mcp-config",
                 "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5" and cmd[cmd.index("--effort") + 1] == "medium"
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["properties"]["stops"]
    narr = json.loads(r.calls[1]["cmd"][r.calls[1]["cmd"].index("--json-schema") + 1])
    assert "stop_lines" in narr["properties"] and r.calls[1]["cmd"][r.calls[1]["cmd"].index("--effort") + 1] == "low"


def test_environment_is_scrubbed(pl, monkeypatch):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(k, "secret-value")
    p, r = make_cc(pl, [envelope(draft(*GOLDEN)), envelope(narrative(IDS))])
    p.plan(request())
    for c in r.calls:
        assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"} & set(c["env"])
        assert "secret-value" not in " ".join(c["cmd"]) and "PATH" in c["env"]


def test_stdin_and_empty_cwd(pl, monkeypatch):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    bad = draft(("no-such-place", 60), ("museu-de-aveiro-santa-joana", 60))
    p, r = make_cc(pl, [envelope(bad), envelope(draft(*GOLDEN)), envelope(narrative(IDS))])
    plan = p.plan(request())
    assert plan.attempts == 2 and plan.source == "llm"
    first, second = r.calls[0], r.calls[1]
    assert "museu-de-aveiro-santa-joana" in first["stdin"] and "Candidate pack" in first["stdin"]
    assert first["stdin"] not in " ".join(first["cmd"])                    # not an argument
    assert first["cwd_exists"] and first["cwd_files"] == []
    assert not os.path.exists(first["cwd"])                                # temporary, removed afterwards
    assert first["cwd"] != second["cwd"]
    # the retry history is rendered as text: the first request, the previous draft, then the errors
    assert first["stdin"] in second["stdin"] and "no-such-place" in second["stdin"]
    assert "unknown_place: no-such-place" in second["stdin"]


def test_usage_recorded(pl, monkeypatch):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    p, r = make_cc(pl, [envelope(draft(*GOLDEN)), envelope(narrative(IDS))])
    plan = p.plan(request())
    c = plan.usage.calls[0]
    assert c["label"] == "draft" and c["cache_read_tokens"] == 1000 and c["output_tokens"] == 50 and c["notional_usd"] == 0.01
    assert plan.narrative


@pytest.mark.parametrize("script,kind", [
    ([envelope(is_error=True, result="Claude AI usage limit reached|1761300000", rc=1)], "usage_limit"),
    ([envelope(is_error=True, result="You've hit your limit · resets 5pm")], "usage_limit"),
    ([(1, "", "Not logged in · Please run /login")], "not_logged_in"),
])
def test_fatal_errors_stop_the_backend(pl, monkeypatch, script, kind):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    p, r = make_cc(pl, script)
    plan = p.plan(request())
    assert p.llm.fatal == kind
    assert len(r.calls) == 1 and plan.source == "fallback"                # no retries against a dead backend


def test_other_failures_fall_back_without_stopping(pl, monkeypatch):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    p, r = make_cc(pl, [(2, "", "segfault")])
    plan = p.plan(request())
    assert plan.source == "fallback" and p.llm.fatal == ""
    p, r = make_cc(pl, [envelope(None, result="I cannot do that")])         # no structured output
    assert p.plan(request()).source == "fallback"
    p, r = make_cc(pl, [(0, "not json at all", "")])
    assert p.plan(request()).source == "fallback"


def test_binary_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.shutil, "which", lambda n: None)
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path)
    for v in ("2.1.9", "2.1.308"):
        d = tmp_path / f".vscode-server/extensions/anthropic.claude-code-{v}-linux-x64/resources/native-binary"
        d.mkdir(parents=True)
        (d / "claude").write_text("x")
    assert "2.1.308" in cc.find_binary()                                   # numeric, not alphabetical
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path / "nowhere")
    monkeypatch.delenv("PLANNER_CLAUDE_BIN", raising=False)
    with pytest.raises(cc.BackendError):
        cc.find_binary()
    f = tmp_path / "mine"
    f.write_text("x")
    monkeypatch.setenv("PLANNER_CLAUDE_BIN", str(f))
    assert cc.find_binary() == str(f)


def test_web_refuses_claude_code(pl, monkeypatch):
    from planner.web import create_app
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    p, _ = make_cc(pl, [])
    with pytest.raises(RuntimeError, match="claude-code"):
        create_app(p)
    with pytest.raises(RuntimeError):
        create_app(backend="claude-code")


# ---- eval: clean stop on a usage limit, and --resume ----------------------------------
def test_eval_stops_cleanly_and_resumes(pl, monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")[:3]
    r1 = FakeRunner([(1, "", "boom")] * 2 + [envelope(is_error=True, result="usage limit reached", rc=1)])
    s = ev.run(pl, reqs, "claude-sonnet-5-5", "medium", tmp_path, backend="claude-code", runner=r1)
    assert s["stopped"] == "usage_limit" and s["done"] == 2 and s["of"] == 3
    log = next(tmp_path.glob("claude-sonnet-5-5-medium-*.jsonl"))
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r["id"] for r in rows] == [reqs[0][0], reqs[1][0]]
    assert all(r["backend"] == "claude-code" and r["cost_usd"] is None for r in rows)
    # resume: only the unfinished request is run
    r2 = FakeRunner([(1, "", "boom")])
    s = ev.run(pl, reqs, "claude-sonnet-5-5", "medium", tmp_path, backend="claude-code", runner=r2, resume=True)
    assert len(r2.calls) == 1 and s["stopped"] == "" and s["done"] == 3
    assert len(log.read_text().splitlines()) == 3


def test_outcome_categories_and_gate(pl, monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "find_binary", lambda: "/x/claude")
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")
    pick = [(i, r) for i, r in reqs if r.date == "2026-10-24" and r.origin.id == "aveiro-station"][:1]
    s = ev.run(pl, pick, "claude-sonnet-5-5", "low", tmp_path, backend="claude-code", runner=FakeRunner([(1, "", "boom")]))
    assert s["outcomes"]["llm_failed_fallback"] + s["outcomes"]["no_plan"] == 1
    ev.write_report([s], tmp_path / "r.md", "2026-10-06")
    text = (tmp_path / "r.md").read_text()
    assert "not representative of the API" in text and "notional" in text
