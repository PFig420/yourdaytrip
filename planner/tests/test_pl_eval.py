"""The Gate 3 harness, offline: a fake client stands in for the API."""
import json

from conftest import FakeClient, ROOT, draft
from planner import eval as ev
from planner.cli import main


def test_request_set_is_fixed_and_valid():
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")
    assert len(reqs) == 50
    rows = [r for _, r in reqs]
    assert sum(r.weather == "rain" for r in rows) == 5
    assert sum(bool(r.text) for r in rows) == 10
    assert {r.pace for r in rows} == {"relaxed", "packed"}
    assert {r.date for r in rows} >= {"2026-10-24", "2026-10-25", "2026-10-27", "2026-10-28", "2026-12-08", "2027-07-17"}
    assert all(r.origin.id == "aveiro-station" for r in rows if r.date == "2027-07-17")


def test_estimate_makes_no_call(pl, capsys):
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")
    e = ev.estimate(pl, reqs, ev.DEFAULT_RUNS)
    assert e["total_usd_upper"] > 0 and pl._client.calls == []


def test_live_run_refused_without_approval(capsys):
    rc = main(["eval", "--limit", "2"])
    assert rc == 3 and "costs real money" in capsys.readouterr().out


def test_run_with_fake_client(pl, tmp_path):
    reqs = ev.load_requests(ROOT / "planner/eval/requests.jsonl")[:3]
    client = FakeClient(["refusal"] * 3)
    s = ev.run(pl, reqs, "claude-sonnet-5-5", "medium", tmp_path, client=client)
    assert s["requests"] == 3 and s["fallback_rate"] == 1.0 and s["gate"]["fallback_le_10"] is False
    log = [json.loads(l) for l in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert len(log) == 3 and all(r["status"] in ("ok", "no_plan") for r in log)
    ev.write_report([s], tmp_path / "report.md", "2026-10-06")
    assert "claude-sonnet-5-5" in (tmp_path / "report.md").read_text()
