"""Gate 3: live evaluation harness (Planner.md 11). Costs real money: a live run needs --approved."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import random
import tomllib
from pathlib import Path

from .models import ORIGINS, Origin, PlanRequest
from . import pack as packmod

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS = [("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "low"), ("claude-opus-5-5", "medium")]
OUTPUT_TOKENS_GUESS = 1500        # draft ~300 tokens of JSON plus adaptive thinking, plus the narrative; a guess to be replaced by the dry run
CHARS_PER_TOKEN = 3.5


def load_requests(path) -> list[tuple[str, PlanRequest]]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            out.append((r["id"], PlanRequest(origin=Origin(**ORIGINS[r["origin"]]), date=r["date"], leave_after=r["leave_after"],
                                             back_by=r["back_by"], interests=r["interests"], text=r["text"], pace=r["pace"],
                                             weather=r["weather"], include_food=r["include_food"])))
    return out


def prices() -> dict:
    with open(Path(__file__).resolve().parents[1] / "eval/prices.toml", "rb") as f:
        return tomllib.load(f)


def cost(usage: dict, model: str, table: dict) -> float | None:
    p = table["models"].get(model)
    if not p:
        return None
    plain_in = usage["input_tokens"] - usage["cache_read_tokens"]
    return (max(plain_in, 0) * p["input"] + usage["cache_read_tokens"] * p["cache_read"] + usage["output_tokens"] * p["output"]) / 1e6


def estimate(pl, reqs, models_efforts, sample=3) -> dict:
    """Offline estimate: builds the real packs, counts characters. Makes no API call."""
    from .llm import SYSTEM_PROMPT, first_message
    sizes = []
    for _, req in reqs[:sample]:
        pk = packmod.build(pl.lib, pl.engine, req, pl.cfg)
        sizes.append(len(SYSTEM_PROMPT) + len(first_message(pk.dumps())["content"]))
    avg_in = int(sum(sizes) / len(sizes) / CHARS_PER_TOKEN)
    table = prices()
    n = len(reqs)
    rows, total = [], 0.0
    for model, effort in models_efforts:
        # one planner call (+ ~0.3 retries on average: unknown, assume 1.3 calls) and one narrative call per request
        calls = n * 1.3
        c = cost({"input_tokens": int(avg_in * calls + 800 * n), "cache_read_tokens": 0,
                  "output_tokens": int(OUTPUT_TOKENS_GUESS * n)}, model, table)
        rows.append({"model": model, "effort": effort, "requests": n, "avg_input_tokens_per_call": avg_in,
                     "est_usd_upper": None if c is None else round(c, 2)})
        total += c or 0
    return {"prices_as_of": table["as_of"], "rows": rows, "total_usd_upper": round(total, 2),
            "note": "Upper bound: no prompt-cache hits assumed; output tokens are a guess until a real dry run."}


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, math.ceil(0.95 * len(xs)) - 1)] if xs else None


def outcome(plan) -> str:
    if plan.status != "ok":
        return "no_plan"
    return "llm_valid" if plan.source == "llm" else "llm_failed_fallback"


def _log_path(out_dir, model, effort, backend, resume, tag=""):
    suffix = {"claude-code": "", "api": "-api", "local": "-local"}[backend]
    if tag:
        suffix += f"-{tag}"       # tagged logs (prompt-fix measurements) never collide with the Gate logs
    if resume:
        old = sorted(Path(out_dir).glob(f"{model}-{effort}-????-??-??{suffix}.jsonl"))
        if old:
            return old[-1]
    return Path(out_dir) / f"{model}-{effort}-{dt.date.today().isoformat()}{suffix}.jsonl"


def run(pl_base, reqs, model, effort, out_dir, client=None, backend="api", runner=None, resume=False, tag="") -> dict:
    import planner as pkg
    cfg = copy.deepcopy(pl_base.cfg)
    cfg["model"]["name"], cfg["model"]["effort"] = model, effort
    if backend == "local":
        cfg.setdefault("local", {})["model_label"] = model
    pl = pkg.Planner(pl_base.engine, pl_base.lib, cfg, client=client, accepted_legs=pl_base.accepted_legs,
                     backend=backend, runner=runner)
    table = prices()
    path = _log_path(out_dir, model, effort, backend, resume, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if resume and path.exists():
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = {r["id"] for r in rows}
    stopped = ""
    with open(path, "a" if resume else "w", encoding="utf-8") as f:
        for rid, req in reqs:
            if rid in done:
                continue
            plan = pl.plan(req, use_llm=True, debug=True)
            if pl.llm.fatal:               # usage limit or not logged in: stop cleanly, this request is not logged
                stopped = pl.llm.fatal
                break
            assert plan.status != "ok" or plan.timeline, "an ok plan must have a timeline"
            codes = {}
            for att in plan.attempt_errors:
                for c in att:
                    codes[c] = codes.get(c, 0) + 1
            u = plan.usage
            dcalls = [c for c in u.calls if c.get("label") == "draft"]
            notional = sum(c.get("notional_usd") or 0 for c in u.calls)
            row = {"id": rid, "backend": backend, "model": model, "effort": effort, "outcome": outcome(plan),
                   "status": plan.status, "source": plan.source, "attempts": plan.attempts,
                   "first_try_valid": plan.source == "llm" and plan.attempts == 1,
                   "fallback_used": plan.source == "fallback", "errors_by_code": codes,
                   "calls": u.calls, "total_latency_s": u.latency_s, "input_tokens": u.input_tokens,
                   "output_tokens": u.output_tokens, "cache_read_tokens": u.cache_read_tokens,
                   "narrative_ok": plan.narrative is not None if plan.source == "llm" else None,
                   "call_latency_s": [c.get("latency_s") for c in u.calls],
                   "prompt_tps": [c.get("prompt_tps") for c in u.calls if c.get("prompt_tps")],
                   "gen_tps": [c.get("gen_tps") for c in u.calls if c.get("gen_tps")],
                   "cost_usd": cost(u.model_dump(), model, table) if backend == "api" else None,
                   "notional_usd": notional if backend == "claude-code" else None, "prices_as_of": table["as_of"],
                   "backend_errors": [c["backend_error"] for c in u.calls if c.get("backend_error")],
                   "plan": plan.to_json()}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            rows.append(row)
    s = summarise(rows, model, effort, str(path), backend)
    s["stopped"] = stopped
    s["done"], s["of"] = len(rows), len(reqs)
    return s


def summarise(rows, model, effort, path="", backend="api") -> dict:
    n = len(rows)
    by = {k: sum(r["outcome"] == k for r in rows) for k in ("llm_valid", "llm_failed_fallback", "no_plan")}
    judged = n - by["no_plan"]            # no_plan measures the data and the rules, not the model
    lat = [r["total_latency_s"] for r in rows]
    llm_rows = [r for r in rows if r["outcome"] == "llm_valid"]
    tps = lambda k: sorted(x for r in rows for x in r.get(k, []))
    med = lambda xs: xs[len(xs) // 2] if xs else None
    s = {"backend": backend, "model": model, "effort": effort, "requests": n, "outcomes": by, "plans_shown": n - by["no_plan"],
         "first_try_valid": (sum(r["first_try_valid"] for r in rows) / judged) if judged else 0,
         "first_try_valid_all": (sum(r["first_try_valid"] for r in rows) / n) if n else 0,
         "fallback_rate": (by["llm_failed_fallback"] / judged) if judged else 0,
         "p95_latency_s": p95(lat), "mean_latency_s": (sum(lat) / n) if n else 0,
         "mean_attempts": (sum(r["attempts"] for r in rows) / n) if n else 0,
         "input_tokens": sum(r["input_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
         "cache_read_tokens": sum(r["cache_read_tokens"] for r in rows),
         "cost_usd": sum(r["cost_usd"] or 0 for r in rows), "notional_usd": sum(r.get("notional_usd") or 0 for r in rows),
         "narrative_pass_rate": (sum(bool(r.get("narrative_ok")) for r in llm_rows) / len(llm_rows)) if llm_rows else 0,
         "median_latency_s": med(sorted(lat)), "median_prompt_tps": med(tps("prompt_tps")), "median_gen_tps": med(tps("gen_tps")),
         "log": path}
    codes = {}
    for r in rows:
        for c, k in r["errors_by_code"].items():
            codes[c] = codes.get(c, 0) + k
    s["errors_by_code"] = dict(sorted(codes.items(), key=lambda kv: -kv[1]))
    if backend == "local":
        # Execution.md 8, Gate L (laptop viability); "no OOM and no swap" is checked by hand from the server and `free`
        s["gate_l"] = {"llm_valid_ge_80_of_judged": (by["llm_valid"] / judged if judged else 0) >= 0.80,
                       "fallback_le_20": s["fallback_rate"] <= 0.20,
                       "narrative_ge_60": s["narrative_pass_rate"] >= 0.60,
                       "median_latency_le_6min": (s["median_latency_s"] or 0) <= 360}
    s["gate"] = {"first_try_ge_70": s["first_try_valid"] >= 0.70, "fallback_le_10": s["fallback_rate"] <= 0.10,
                 "p95_le_30s": (s["p95_latency_s"] or 0) <= 30.0}
    return s


def write_report(summaries, path, prices_as_of):
    cc = any(s["backend"] == "claude-code" for s in summaries)
    lines = ["# Gate 3 report", "",
             "Backend: " + ("Claude Code CLI on Pedro's subscription (quality run, no per-token charges)." if cc else "Claude API."), ""]
    if cc:
        lines += ["**Latency, tokens and cost on this backend are not representative of the API.** Each call starts a CLI "
                  "process, adds Claude Code's own overhead and tool scaffolding, and has no prompt-cache control or server-side "
                  "refusal fallback. Only the quality columns (outcomes, first-try validity, errors) carry over. "
                  "`notional` is the CLI's own list-price figure and was not billed.", ""]
    lines += [f"Thresholds: first-try validity >= 70%, `llm_failed_fallback` <= 10% (of requests that have a valid plan at all), "
              "p95 total latency <= 30 s (API run only), and Pedro rates at least 8 of 10 sampled plans acceptable "
              "(see `sample-for-review.md`).", "",
              "| model | effort | requests | llm_valid | llm_failed_fallback | no_plan | first-try valid | fallback share | mean attempts | p95 latency | tokens in / out | notional or cost |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        o = s["outcomes"]
        money = f"notional ${s['notional_usd']:.2f}" if s["backend"] == "claude-code" else f"${s['cost_usd']:.2f}"
        lines.append(f"| {s['model']} | {s['effort']} | {s['requests']} | {o['llm_valid']} | {o['llm_failed_fallback']} | {o['no_plan']} | "
                     f"{s['first_try_valid']:.0%} | {s['fallback_rate']:.0%} | {s['mean_attempts']:.2f} | "
                     f"{s['p95_latency_s']:.1f} s | {s['input_tokens']} / {s['output_tokens']} | {money} |")
    lines += ["", "Errors by code (failed drafts):", ""]
    for s in summaries:
        lines.append(f"- {s['model']} {s['effort']}: {s['errors_by_code'] or 'none'}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sample(log_path, out_path, k=10, seed=1):
    """Ten plans for Pedro's rubric: sensible order, not rushed, interesting, meal well placed."""
    from .models import DayPlan
    from .render import render
    rows = [json.loads(l) for l in open(log_path, encoding="utf-8")]
    rows = [r for r in rows if r["status"] == "ok"]
    pick = random.Random(seed).sample(rows, min(k, len(rows)))
    parts = ["# Plans for rating", "", "Rubric per plan: sensible order / not rushed / interesting / meal well placed. Acceptable? [ ] yes [ ] no", ""]
    for r in pick:
        plan = DayPlan.model_validate(r["plan"])
        parts += [f"## {r['id']}", "", "```", render(plan), "```", "", "Sensible order [ ]  Not rushed [ ]  Interesting [ ]  Meal well placed [ ]  Acceptable: [ ] yes [ ] no", ""]
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")


def main(a) -> int:
    import planner as pkg
    base = pkg.load(config=a.config, backend=a.backend)
    sp = Path(a.set)
    reqs = load_requests(sp if sp.exists() else ROOT / a.set)
    if a.limit:
        reqs = reqs[: a.limit]
    if a.backend == "local":
        label = a.model or base.cfg["local"]["model_label"]
        runs = [(label, "na")]                    # the local model has no effort setting
    else:
        runs = [(a.model, a.effort or "medium")] if a.model else DEFAULT_RUNS
    if a.backend == "local":
        print(f"Local run: {len(reqs)} requests, sequential, no spend (self-hosted server at {base.cfg['local']['base_url']}).")
        if a.estimate:
            return 0
    elif a.backend == "api":
        est = estimate(base, reqs, runs)
        print(json.dumps(est, indent=2))
        if a.estimate:
            return 0
        if not a.approved:
            print("\nThis run costs real money. Nothing was sent. Re-run with --approved once Pedro has approved the budget above.")
            return 3
    else:
        if a.estimate:
            print("claude-code backend: no per-token charges (subscription limits only); nothing to estimate.")
            return 0
        print(f"Quality run on the Claude Code CLI: {len(reqs)} requests x {len(runs)} runs, sequential. "
              "Not billed per token; counts against the subscription limits.")
    out_dir = ROOT / "planner/eval/runs"
    sums = []
    for model, effort in runs:
        s = run(base, reqs, model, effort, out_dir, backend=a.backend, resume=a.resume, tag=a.tag)
        print(json.dumps({k: v for k, v in s.items() if k != "errors_by_code"}, indent=2), flush=True)
        sums.append(s)
        if s["stopped"]:
            print(f"\nStopped: {s['stopped']}. {s['done']} of {s['of']} requests are logged; re-run with --resume to continue.")
            return 4
    if a.backend == "local":
        return 0                 # reports for the local backend are written by write_local_report (selfhostedLLM/reports)
    if a.tag:        # a tagged run (prompt-fix measurement) never overwrites the Gate 3 report or the rating sample
        return 0
    name = "report.md" if a.backend == "claude-code" else "report-api.md"
    write_report(sums, ROOT / "planner/eval" / name, prices()["as_of"])
    write_sample(sums[0]["log"], ROOT / "planner/eval/sample-for-review.md")
    return 0
