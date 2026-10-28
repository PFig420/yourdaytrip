# Changes.md: planner changes from the 27 Oct test session

This is a build brief for a coding agent working in this repository. Read it fully before starting. Then read `Planner.md` §3, §6 and §8, and `planner/README.md` "Decisions". Do the changes in the order of §7. Record every choice this brief leaves open in `planner/README.md` under "Decisions", dated.

Written 2026-10-27 from Pedro's decisions after testing the local model through the test page (`python -m planner.web --backend local`, http://127.0.0.1:8300/).

## 0. Background: the test plan that prompted this

On Sat 31 Oct 2026, from Porto Campanhã, 09:30 to 21:00, the interests were architecture, beach and history (relaxed pace). The local model made a valid plan on its first try. Its weak points:

1. **A 1 h 41 min "wait at Canastra do Fidalgo" (16:24–18:05).** Bus 36 leaves Costa Nova at 16:25 and then 18:10. Lunch ends at 16:24 and the stop is 4 minutes away. The gap is forced by the timetable. The page shows it as a wait at the restaurant, with Praia da Costa Nova about 250 m away.
2. **The model's own notes are lost.** It never said why "beach" was left out, because a successful draft's `notes` are not kept.
3. **The narrative was dropped** ("summary is longer than 80 words"). Two of the last four local narratives failed the check; the other failure was "stop_lines do not match the stops in the plan".
4. **The pack is stricter than the validator for food places.** Canastra's pack slot said `enter_by 13:30`, which assumes a 90-minute lunch must end by 15:00. The validator accepted lunch at 14:54: it only requires a meal to start inside its window and to end before closing (02:00).

## 1. Change A: long waits become free time, with nearby suggestions

**Goal:** a wait of 30 minutes or more at a place the visitor has just visited is shown as free time, with up to 3 nearby things to do. The suggestions are optional ideas. The timeline's times, journeys and checks do not change.

### 1.1 Which waits

- Convert a `Wait` to `FreeTime` when all of these hold:
  - `reason == "until it is time to leave for the next stop"`, so it comes straight after a visit and before walking away. This includes the leg home.
  - It lasts at least `[schedule] suggest_free_time_min` minutes (new, default 30).
- Leave these as waits:
  - transfer waits (`"until the next connection"`);
  - waits before a place opens (`"until it opens"`) and before a fixed entry (`"fixed entry time"`). The validator already limits those to 45 minutes.
- The existing "free time before dinner" entries (Planner.md §6.1) also get suggestions when no visit was inserted into them.
- `FreeTime.reason`: `"free time before the bus home"` (or `"... the train home"`, from the first ride's mode) when the next legs are the journey home. Otherwise `"free time before the next stop"`. `at` stays the place's name.

### 1.2 What to suggest

Add `suggestions: list[dict]` to `FreeTime` (default `[]`). Each suggestion is `{place_id, name, kind, walk_m, walk_min, minutes_there, open_until, description}`. `description` is clipped to 160 characters with `pack.clip`.

**Which places qualify:**
- Confirmed records only; draft hours are not confirmed.
- Not already in the plan.
- **The visitor's interests and pace are ignored.** That is Pedro's decision: it is free time, not part of the planned day.
- Weather is honoured. On a rain day, skip places with `weather == "outdoor"`, the same rule the shortlist applies.
- Reachable on foot only: walking one way of at most `[schedule] suggest_walk_m` metres (new, default 800). Use `Router.earliest` with walk-only journeys, as `_fill` does.
- The round trip fits in the gap: walk there, at least `visit_min` there, then walk back to the free time's location by its `until`. Back to the location, not to the bus stop, so the planned walk to the stop is unchanged.
- Open the whole time it is suggested for:
  - checked with `places.hours.parse` and the place's own rules;
  - `daylight_only` places only before sunset (`places.sun.sun_times`);
  - food places by their opening hours, not by meal windows (a coffee at 16:30 is fine).

**Ranking:** shortest walk first, ties by `place_id`. At most 3 suggestions, of which at most 1 is a food place. `minutes_there` is the time left after both walks. `open_until` is the closing time that applies (`HH:MM`), or `null` for always-open places.

**Where it goes:** a post-pass over a valid schedule, in `Scheduler` or a new `planner/freetime.py`. It runs after validation, for model and fallback plans alike, so it cannot make a plan fail. It does no engine search outside walking.

### 1.3 Display and outputs

- **Test page** (`planner/planner/web/static/index.html`): show free time as its own timeline row. Under it, put the suggestions with their name, walk distance and time, "about N min there", open until, and the description, labelled "Ideas nearby (optional)".
- **CLI renderer** (`render.py`): one line per suggestion under the free-time line.
- **Narrative:** it does not mention suggestions. The narrative payload stays as it is; free time is not in it today.

### 1.4 Tests

- The Costa Nova case (the request in §0, built with a scripted draft of the plan's four stops) produces a `free_time` entry from 16:24 to 18:05 with at least one suggestion. Praia da Costa Nova is expected if it fits the rules.
- A 20-minute wait stays a `wait`, and so does a transfer wait of 40 minutes.
- A rain-day request drops outdoor suggestions.
- A suggestion's walk there + `visit_min` + walk back always fits inside the free time.
- Update the golden tests (Planner.md §10.2) only where a wait now legitimately becomes free time. Say which ones in the report.

## 2. Change B: the chosen draft's notes, for testing only

**Goal:** see what the model said about the plan it chose, especially trade-offs (rule 9 of the prompt). This is debug output, not part of the app's answer.

- Add `debug: dict | None = None` to `DayPlan`. It is filled only when `Planner.plan(req, use_llm=True, debug=True)`; the default is `False`, so the field stays `null`.
- **Contents:**
  - `draft`: the accepted draft as `{title, notes, stops: [{place_id, name, visit_minutes, why}]}`, or `null` for a fallback plan;
  - `attempt_drafts`: move it here from the top level. It was added on 27 Oct, and only the test page reads it.

  `attempt_errors` stays top-level, because the eval uses it.
- **Switches:**
  - HTTP: `POST /plan?debug=true`. The test page always sends it.
  - CLI: `--debug` on `plan`.
  - Eval: always passes `debug=True`, so M4 analysis has the drafts in its logs.
- **Test page:** a "Model notes (testing)" box under the title with the accepted draft's title and notes. The failed-drafts section reads `debug.attempt_drafts`.
- **Tests:**
  - `debug` is `null` by default and on `POST /plan` without the flag.
  - With the flag, a model plan carries its notes, and a fallback plan carries `draft: null` plus its failed drafts.
  - Update `test_failed_drafts_are_kept_with_names_and_errors` in `tests/test_pl_local.py` for the move.

## 3. Change C: one narrative retry, local backend only

**Goal:** fewer dropped narratives on the local backend. Claude's behaviour does not change.

- New `[local] narrative_retries = 1` in `planner/config.toml`. The `api` and `claude-code` backends make no narrative retry, as today. Add a test for that.
- **When `narrative.check` returns problems on the local backend,** make one more `narrate` call:
  - Messages: the original user turn, the failed answer as an assistant turn, then a user turn listing the problems.
  - The problem turn adds the facts needed to fix them:
    - the summary's word count against 80, and each over-long stop line's count against 25;
    - for a stop-line mismatch, the exact `place_id`s required, in timeline order.
  - Keep the original turns unchanged, so the server's prefix cache is reused.
  - Same schema and `narrative_max_tokens`.
- Accept the retry if it passes the same check. Otherwise drop the narrative as today, with the warning naming the retry's problems.
- Usage records both calls, labelled `narrative` and `narrative_retry`.
- **`NARRATIVE_SYSTEM` and the narrative schema do not change**, because Claude uses them too. Do not enforce lengths through the JSON schema: a grammar-level `maxLength` cuts text mid-sentence.
- **Tests (mocked server, as in `tests/test_pl_local.py`):**
  - a too-long summary, then a passing retry, gives a narrative;
  - two failures give no narrative and one warning;
  - a passing first answer makes no retry.

## 4. Change D: flag the food-slot mismatch for the prompt-fix work

**Do not change the pack or the validator here.** `PlannerPromptFix.md` owns the pack format and may still be in progress.

- Append a section to `PlannerPromptFix.md` titled "Follow-up found 2026-10-27: food slots in the pack are stricter than the validator". It should describe the mismatch and give the Canastra example from §0, item 4.
- Name the two candidate fixes, for that work to choose:
  1. for food, `to` is the place's closing time and `enter_by` the meal window's end;
  2. the validator requires a meal to end by its window's end.
- Note that this affects the Claude backends too.

## 5. Change E: "Back home by" or "Leave by"

**Goal:** the visitor chooses one of two deadlines:
- **Back home by** (today's behaviour): be back at the origin by X.
- **Leave by:** leave the last place visited by X. The journey home may arrive later.

**Decision for this brief:** "leave the visiting location" means **the last visit ends by X**. The walk to the stop is part of the journey home. Record this as a decision. If testing shows Pedro meant "depart the destination town's station by X", stop and ask.

### 5.1 Request

- Add `deadline: Literal["home", "leave"] = "home"` to `PlanRequest`. `back_by` keeps holding the deadline time in both modes. Renaming it would touch the eval sets, the pack format, the places API and the golden tests.
- Document in Planner.md §3.1 that in `"leave"` mode `back_by` is the leave-by time.
- The existing check stays: `back_by` must be after `leave_after`.

### 5.2 Rules in "leave" mode

**New setting:** `[schedule] leave_mode_home_by` (default `"23:59"`), the latest arrival home the engine searches for. A journey home must exist.

**Shortlist and pack:**
- Call the shortlist and the engine with `back_by = leave_mode_home_by`.
- Cap each place's `leave` at the leave-by time **before** slots are computed. A visit must end by X, and `enter_by` follows from that.
- This needs one optional argument, `leave_by`, on `places.shortlist.build` (and `Library.shortlist`). It applies `min(w.leave, leave_by)` before `usable_slots`. The default `None` leaves the shortlist unchanged; add a places test.
- This is the only change allowed in `places/`. Do not touch `places/data`.

**Pack:** the request block gets `"deadline": "leave"` **only in leave mode**. In home mode the pack stays byte-identical to today's (add a test). Do not change `SYSTEM_PROMPT`: each candidate's `leave_by` already carries the cap. If drafts keep failing on it, stop and ask.

**Validator:**
- New error code **`left_too_late`**: "the last stop ends HH:MM, after the HH:MM leave-by time". Add it to Planner.md §6.3.
- `home_too_late` applies against `leave_mode_home_by`.
- **Meals:** `required_meals` uses the leave-by time as the end of the day, which is the time actually spent at the destination.

**Last safe return:** the latest journey home that leaves the last place by X. The engine's `latest` works on arrival deadlines only. Either iterate `earliest` from X backwards in 5-minute steps, or add what is needed in the planner (not in `engine/`). Record the choice.

**Plan B:** `within_deadline` is judged against the leave-by time for legs before the last visit, and against `leave_mode_home_by` for the journey home.

**Fallback:** it uses the same scheduler and validator, so it needs no separate rule. Check it with a test.

### 5.3 Interfaces

- **Test page:** a two-way switch above the time field, "Back home by" / "Leave by". The time field's label follows it. The plan header shows "leave by 18:00" or "back by 21:00". "Getting back" shows when the visitor leaves and when they reach home.
- **CLI:** `--leave-by HH:MM` as an alternative to `--back-by`, mutually exclusive, with exactly one required.
- **HTTP:** the `deadline` field in the request body.
- **Eval:** `requests.jsonl` and `holdout.jsonl` stay as they are (home mode by default).

### 5.4 Tests

- Home mode: the pack and the golden plan are unchanged.
- Leave mode:
  - every candidate's `leave_by` is at most X;
  - a draft whose last visit ends after X fails with `left_too_late`;
  - a plan may arrive home after X but not after `leave_mode_home_by`;
  - the CLI and HTTP accept the mode; the fallback respects X.
- `required_meals` in leave mode: dinner is not required when leave-by is 18:00, even if home is later.

## 6. Constraints

- **No Claude API calls**, and never pass `--approved`. Live checks use the local backend only.
- **Do not touch:** `engine/`, `converter/`, `places/data`, `SYSTEM_PROMPT`, `NARRATIVE_SYSTEM`, the output schemas (`DayDraft`, `NarrativeOut`), or the retry count.
  - `places/` code: only the `leave_by` argument in §5.2.
  - The scheduler: only the free-time post-pass (§1) and the leave-mode rules (§5).
- **Claude backends:** for home-mode requests the pack stays byte-identical (`tests/test_pl_compact.py` and the new tests). The free-time conversion does change their timeline output. That is intended; update Planner.md §6.1.
- **The prompt-fix session may still be active.** Its three failing tests in `tests/test_pl_pack.py` (`test_day_block_agrees_with_the_validator`) were already failing on 27 Oct and are not yours to fix. Do not add failures: run `cd planner && python3 -m pytest tests`, then the engine and places tests separately.
- **Python:** the `python3` on PATH may be a venv without `anthropic`; use `/usr/bin/python3`. Do not add dependencies.
- **Memory (laptop, 7.5 GiB, no swap):**
  - Run one live job at a time.
  - Start the model server with `cd ~/selfhostedLLM && docker compose up -d` (its config is final; do not change it).
  - For any run longer than a few plans, use the watchdog pattern in `~/selfhostedLLM/scripts/run_compact_bench.sh`.
  - Stop the servers when done.
- **Spec updates:** Planner.md §3.1 (deadline), §6.1 (free time and suggestions), §6.3 (`left_too_late`), §8 (local narrative retry), §9.2 (CLI flags), §9.3 (the `debug` flag and the test page). Also the planner README "Run" section.

## 7. Order and acceptance

| Step | Change | Done when |
|---|---|---|
| 1 | B (debug notes) | Its tests pass; the test page shows "Model notes (testing)" |
| 2 | A (free time and suggestions) | Its tests pass; the Costa Nova case shows free time with suggestions on the page |
| 3 | E (leave by) | Its tests pass, including byte-identity in home mode |
| 4 | C (narrative retry) | Its tests pass; Claude backends make no retry |
| 5 | D (note) | The section is appended to `PlannerPromptFix.md` |
| 6 | Live check | The report below is written |

**Live check (local backend, one at a time):**
1. The §0 request in home mode.
2. The same request in leave mode, leave by 17:30.
3. Eval requests r01, r03, r06, r26 in home mode.

For each, record the outcome, attempts, total time, free-time entries and their suggestions, and whether the narrative passed (first try, after the retry, or dropped).

## 8. Report

Write `planner/eval/changes-report.md`:
- the live-check table;
- the narrative pass count before and after the retry;
- golden tests that changed, and why;
- every decision recorded in the README;
- anything that needed Pedro.

Then stop.
