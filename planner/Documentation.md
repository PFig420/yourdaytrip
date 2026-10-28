# planner/: the day planner and validator

The planner turns a visitor's request ("from Porto Campanhã on Saturday, 08:00 to 21:00, history and maritime, relaxed") into a **checked day plan**: a timeline of rides, walks, waits and visits with real times, a safe way home, and a plan B for missed connections.

The division of labour is the central design rule:

> **Claude chooses and orders places and suggests stay lengths. Nothing else.** Every time, walk and ride in a plan comes from the reachability engine through the scheduler. The model never outputs a clock time, route or trip id, and nothing is shown that has not passed the validator.

```
request ─► pack (places shortlist + travel hints)
              │
              ▼
        Claude draft  ──► validator/scheduler ──ok──► timeline + safe return + plan B ─► narrative (Claude, checked) ─► DayPlan
        (ids + minutes)        │ errors
              ▲                └─ retry with the errors (up to 2 times)
              └─ still failing / model unavailable ─► deterministic fallback plan ─► same validator
```

Spec: `../Planner.md` (section numbers like "§6.2" in code refer to it). `README.md` here has the status and an extensive decision log. This document explains how the pieces fit together.

## Layout

| Path | Purpose |
|---|---|
| `planner/` | The package (about 2,400 lines). |
| `config.toml` | Model, effort, schedule limits, pack sizes, retries. |
| `eval/` | Quality and cost evaluation: `requests.jsonl` (the fixed 50-request set), `make_requests.py`, `prices.toml`, `runs/` (logs of finished runs). |
| `tests/` | 70 pytest tests, all offline (a fake client stands in for the model). |
| `pyproject.toml` | Depends on `anthropic>=2.2`. Extras: `web` (fastapi, uvicorn), `dev`. |

## Setup and running

From the repo root. `anthropic` is the one extra dependency (`pip install --user anthropic`; system pip is externally managed on this machine). The engine and places packages are imported from their folders.

```bash
export PYTHONPATH=planner:places:engine
python3 -m planner plan --from porto-campanha --date 2026-10-24 --after 08:00 --back-by 21:00 \
        --interests history,maritime --pace relaxed [--text "..."] [--weather rain] [--no-food] [--no-llm] [--json]
python3 -m planner plan ... --backend claude-code     # local Claude Code CLI on the subscription (local testing only)
python3 -m planner eval --backend claude-code [--resume]        # quality run, unbilled
python3 -m planner eval --backend api --estimate                # offline cost estimate, no call
python3 -m planner eval --backend api --approved                # costs money: only after Pedro approves
python3 -m planner.web                       # http://127.0.0.1:8300  POST /plan, GET /health
cd planner && PYTHONPATH=.:../places:../engine python3 -m pytest tests     # 70 pass, about 11 s
```

- `--from` is `porto-campanha`, `porto-sao-bento`, `aveiro-station` or `lat,lon`.
- `--no-llm` needs no credentials and gives the deterministic fallback plan. Otherwise credentials come from the environment or an `ant auth login` profile via `anthropic.Anthropic()`. Nothing is logged or stored.
- `plan` exits 0 for a plan, 2 for `no_plan`.
- Run planner, places and engine tests as **separate pytest runs** (their test file names clash).
- Python: `pl = planner.load(); plan = pl.plan(PlanRequest(...), use_llm=True)` returns a `DayPlan` (`.to_json()`).

## The request and the result (`models.py`)

`PlanRequest`: `origin {id, name, lat, lon}`, `date`, `leave_after`, `back_by` (HH:MM, `back_by` after `leave_after`), `interests` (must be vocabulary tags from `../places/config/vocabulary.toml`), `text` (≤ 200 chars), `pace` (`relaxed`/`packed`), `weather` (`any`/`rain`), `include_food`.

`DayPlan`: `status` (`ok` or `no_plan`), `source` (`llm` or `fallback`), `model`, `backend`, `attempts`, `title`, `timeline` (entries: `walk`, `ride`, `wait`, `visit`, `free_time`), `home_by`, `last_safe_return`, `plan_b`, `narrative`, `warnings`, `errors` (when `no_plan`), `attempt_errors` (error codes per failed draft, used by the eval), `usage` (tokens, calls, latency) and `disclaimers`. **Three disclaimers are always present**: times are indicative (check with the operator before the return), "data as of" (each feed's validity window and build date), and the OpenStreetMap credit.

## How a plan is made (`core.py`)

`Planner.plan()`:

1. **Pack** (`pack.py`, §4). Ask the places library for a shortlist (confirmed places only), keep the top 12 sights (swapping in the best place of another cluster if they all share one) and 6 food places (every place with a lunch slot, up to 6, topped up by score), and add `travel_minutes` hints between candidates from the engine. About 20 kB, roughly 6,000 tokens. Fewer than two candidates gives `no_plan` straight away.
2. **Draft** (`llm.py`, §5). One call to Claude with a fixed system prompt (kept byte-identical for prompt caching) and the pack. The answer is structured JSON: a title, 2-6 stops (`place_id`, `visit_minutes`, a short `why`) and notes. Defaults (`config.toml`): `claude-sonnet-5-5`, effort `medium`, `max_tokens` 16,000, server-side fallback beta on.
3. **Validate** (`validate.py` and `schedule.py`, §5.1/§6.2). Schema limits first, then pace and duplicate rules, then the **scheduler** builds the real timeline with engine journeys. Errors are listed in a message and sent back to the model for a retry, up to `max_retries` = 2 (so at most 3 calls). The model's previous reply is kept as it was, and the error list is appended as a new turn.
4. **Fallback** (`fallback.py`, §7). If there is no valid draft (errors, a refusal, the API unavailable, or `--no-llm`), build the plan deterministically (below). If even that fails: `status = "no_plan"` with the errors and no timeline.
5. **Finish**: compute the safe return and plan B, collect warnings, and (for `llm` plans only) ask for a narrative (`narrative.py`), which is checked and dropped if it fails.

### The scheduler (`schedule.py`)
Given an ordered list of `(place, minutes)` it asks the engine for the **earliest** journey to each stop, places the visit inside the place's slot, and the **latest** journey back, so the day is feasible by construction. Error codes: `schema`, `unknown_place`, `duplicate_place`, `too_many_stops`, `too_few_stops`, `closed`, `no_departure`, `visit_too_short`, `visit_overruns`, `meal_missing`, `meal_outside_window`, `no_journey`, `home_too_late`. Behaviours worth knowing:
- Soft errors (a closed place, a short stay) do not stop the chain, so one retry message can list several problems. A leg with no journey, or arriving after `back_by`, does.
- Meals are required when the day spans lunch (leave ≤ 12:00 and back ≥ 15:00) or dinner (back ≥ 22:00 and leave ≤ 19:00). A meal visit must start inside a meal window of that place.
- Gaps of a minute or more become `wait` entries. Waits over 45 min (`max_wait_min`) are errors, except **free time before dinner** (walk-only arrival, gap ≤ 120 min), which becomes a `free_time` entry and, if ≥ 45 min, gets one always-open place from the same cluster inserted (`inserted: true`).
- Walks between places are limited to 2,000 m. Daylight-only places are clipped to sunrise-sunset and a visit may not overrun sunset.
- `last_safe_return`: the latest departure from the last place that still gets home by `back_by`, with a backup that arrives strictly earlier. `plan_b`: for every change under 10 minutes in any journey, the next journey from the boarding stop one minute after the missed departure.
- `Router` caches engine calls, since retries and the fallback repeat many identical legs.

### The fallback (`fallback.py`)
Greedy by score. Each added stop must schedule cleanly with a valid way home. A meal is brought in when the clock is within 90 minutes of its window. A sight is refused if it would leave less than 45 minutes of an owed window. Stays are `visit_typical` (relaxed) or `visit_min` (packed). If a required meal can't be placed, a second pass stretches stays to 2 × typical. The result goes through the same validator. With this, 49 of the 50 eval requests get a plan; the one `no_plan` is Porto on 8 Dec back by 22:30.

### The narrative (`narrative.py`)
A second, low-effort call writes a short summary (≤ 80 words plus a line per stop). The check rejects any clock time not in the timeline, any other place name, prices, phone numbers and URLs. A rejected narrative is dropped with a warning, and the timeline stands alone.

### Backends (`llm.py`, `claude_code.py`)
- `api` (default): the `anthropic` SDK (2.2.0) beta `messages.parse` surface with structured output, prompt caching and `fallbacks="default"`. `server_fallback = false` in the config removes the beta header.
- `claude-code`: runs the local `claude` CLI as a subprocess (one per call, empty temp dir, API-key env vars removed) on the user's subscription. It is **a local testing tool**: the web server refuses to start on it. The binary is `claude` on PATH, else the newest VS Code extension copy, else `PLANNER_CLAUDE_BIN`. A usage-limit or login message sets `llm.fatal`, so retries and eval runs stop cleanly.

## HTTP (`web/`)
`POST /plan?use_llm=true` (body = `PlanRequest`, answer = `DayPlan` JSON, 422 on bad input) and `GET /health` (model, feed hashes, place counts, whether the API is reachable, checked at most every 5 minutes, errors never echoed). Local only until the launch conditions in `../Issues.md` #1 are met.

## Evaluation (`eval.py`, `eval/`)
A fixed set of 50 requests (origins Porto Campanhã, Porto São Bento, Aveiro station; varied dates, hours, interests, pace and weather) is run per model and effort. Each request ends as `llm_valid`, `llm_failed_fallback` or `no_plan`, with attempts, tokens, latency and cost. Logs go to `eval/runs/<model>-<effort>-<date>[-api].jsonl`.
- **Quality run** (`--backend claude-code`, unbilled): done on 2026-10-23. `medium`: 50 requests, 47 `llm_valid`, 3 `llm_failed_fallback`. `low`: 27 of 50 logged (24 valid, 3 fallback). The run was apparently interrupted, and `--resume` redoes unlogged requests.
- **API cost-and-speed run** (`--backend api`): **not run, waits for Pedro's approval**. It refuses to run without `--approved`. `--estimate` prints an offline upper bound (about US$6.5 for the full 3 × 50). Prices are in `eval/prices.toml` (as of 2026-10-06; cache writes are costed as plain input, a slight underestimate).
- The README mentions `eval/report.md` and `eval/sample-for-review.md`. **Neither file exists in the folder**: they are written by `eval` (`write_report`, `write_sample`), so rerun the report step or check where the summary went before relying on the README.

## Configuration (`config.toml`)

| Key | Default | Meaning |
|---|---|---|
| `[model] name / effort / narrative_effort / max_tokens / server_fallback` | `claude-sonnet-5-5` / `medium` / `low` / 16000 / `true` | The planner call |
| `[schedule] max_wait_min` | 45 | Longest wait before it is an error |
| `walk_between_m` | 2000 | Place-to-place and return walking limit |
| `plan_b_change_min` | 10 | Changes shorter than this get a plan B |
| `max_free_time_min` / `fill_free_time_min` | 120 / 45 | Free time before dinner, and when to fill it |
| `[pack] sights / food / min_clusters / hint_reference_hours / hint_max_min` | 12 / 6 / 2 / 3 / 120 | Pack size and travel-hint rules |
| `[retries] max_retries` | 2 | Calls = 1 + this |
| `[paths] accepted_legs` | `converter/config/accepted_legs.csv` | Fast legs reported as warnings |

## Gotchas and known limitations
- **Only confirmed places are used.** At the time of writing that is 78 of 117 records (`../places/Documentation.md`), so some good places are absent until their hours are confirmed.
- **Evening days from Porto can end in `no_plan`**: the last trains and buses leave too early to fit dinner and the way home. After **2026-12-12** CP trains are missing from the feed, so plans from Porto say so and usually come back `no_plan`.
- **Dependent data.** Plans depend on `../data/gtfs/aveiro.zip` and `cp.zip` through the engine, and on the places dataset. Updating a feed or a place can change plans, and the golden tests (`test_pl_golden.py`) use fixtures that need review when the engine's golden values change.
- `costa-nova-palheiros` sits at the north end of the seafront (spec §14); the 2,000 m limit keeps plans sensible.
- **Narrative check is conservative**: a story mentioning any other place name is dropped.
- A reply cut off at `max_tokens` counts as a `schema` error and is retried. A refusal goes straight to the fallback.
- **Rain**: outdoor places are not in the pack. A draft that names one gets `unknown_place`.
- Install note: `anthropic` was installed with `--user --break-system-packages`. A virtualenv would be tidier before deployment.
