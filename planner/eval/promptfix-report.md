# Prompt fix: closing measurement (2026-10-28)

This closes `PlannerPromptFix.md` with **one measurement**, Pedro's option 2 of 28 Oct. It is not the full procedure of that brief: there is no hold-out baseline on the old prompt, and no measured revisions.

**What was measured:** the current pack and prompt (the 26 Oct changes: slots with `enter_by`, `closed_between`, `earliest_arrival`, and the `day` block with `meals_required`) on the 50-request set.
- Claude Sonnet 5.5, medium effort, through the Claude Code backend (subscription, not billed per token).
- Command: `python -m planner eval --backend claude-code --model claude-sonnet-5-5 --effort medium --tag promptfix-current`.
- Log: `runs/claude-sonnet-5-5-medium-2026-10-28-promptfix-current.jsonl`.

## Result against the Gate 3 run (23 Oct, old pack and prompt)

| | 23 Oct (old) | 28 Oct (current) |
|---|---|---|
| Valid on the first try (requests with a plan) | 35 / 50 = **70 %** | 41 / 49 = **84 %** (82 % of all 50) |
| Fell back to the code-only plan | 3 (6 %) | 2 (4 %) |
| No plan possible | 0 | 1 (r35) |
| Mean attempts | 1.44 | 1.24 |

**Failed drafts, by error code:**

| Error | 23 Oct | 28 Oct |
|---|---|---|
| `visit_overruns` | 9 | 5 |
| `meal_missing` | 8 | 4 |
| `wait_too_long` | 7 | 6 |
| `meal_outside_window` | 3 | 5 |
| `too_many_stops` | 3 | 1 |
| `unknown_place` | 2 | 0 |
| `schema` | 1 | 1 |
| `too_few_stops` | 1 | 0 |
| `home_too_late` | 1 | 0 |
| `visit_too_short` | 0 | 1 |

- **First-try changes:** 9 requests went from a failed first draft to a valid one (r06, r09, r10, r21, r22, r33, r38, r39, r48). 3 went the other way (r17, r30, r34).
- **The brief's target:** first-try validity of at least 80 % on the 50-request set is **met**. The fallback stays at 10 % or less. No error code rose by more than 3: `meal_outside_window` rose by 2.
- **Narrative check:** 38 of 47 model plans passed (81 %). The 23 Oct log has no narrative field, so there is no comparison.
- **Latency is not compared:** the Claude Code CLI's times do not carry over to the API (see `report.md`).

## Why this is not a clean comparison

Between the two runs the planner's data changed for every request, not only the prompt:
- Walking times now come from OpenStreetMap paths where they are longer (`WalkBarriers.md`, 28 Oct). All 70 eval and hold-out packs changed, by a few minutes of arrival time.
- The Costa Nova places have reference points (27 Oct).
- Long waits are shown as free time (27 Oct).

So the gain from 70 % to 84 % belongs to the **current planner as a whole**, not to the prompt alone. Isolating the prompt would need the old prompt (`promptfix/llm_v0.py`) run on today's data.

The hold-out check from the brief was not done. The 26 Oct hold-out "baseline" (11 of 20 first-try valid) most likely ran on the new prompt, so it is not a baseline.

## r35: no plan, newly

The request is Porto Campanhã, Tue 8 Dec 2026 (a holiday), 09:30 to 22:30, with interests azulejos and religious.
- On 23 Oct the model's third draft was valid.
- On 28 Oct all three drafts failed (`visit_overruns` ×3, `meal_missing` ×3), and the code-only plan found no dinner (`meal_missing`, dinner window 19:00–22:30).

The Action Plan already lists this request ("the one request where no plan is possible"). The longer walking times may now leave too little room for the dinner. Not investigated.

## Tests

The three `test_day_block_agrees_with_the_validator` failures came from a bug in the test, not the planner: it read the meal name from the wrong part of the error message. That is fixed (`tests/test_pl_pack.py`), and the planner suite now passes in full (189).
