# PlannerPromptFix.md — raise the planner's first-try validity

This is a task brief for a coding agent working in this repository. Read it fully, and read `Planner.md` §4–§6 first. Record every choice the brief leaves open in `planner/README.md` under "Decisions".

## 1. Goal
Gate 3's quality run (25 Oct 2026, `planner/eval/report.md`) shows **Claude Sonnet 5.5 at medium effort produces a valid draft on the first try 70% of the time**. That's exactly the gate threshold, so there's no margin. **The target is at least 80% on the 50-request set, without getting worse on a fresh hold-out set (§5).**

Only the **candidate pack** and the **planner system prompt** change. The scheduler, validator, error codes, fallback plan, retry count, output schemas and narrative call stay exactly as they are.

## 2. What went wrong (Sonnet 5.5 medium, 50 requests)

| Error | Count | Likely cause in what the model sees |
|---|---|---|
| `visit_overruns` | 9 | Each slot is a bare range such as `"10:00-12:30"`, `"13:30-18:00"`. The model isn't told the **latest entry** that still fits a stay, or that two slots on one day mean **closed in between** (museums close for lunch). A 75-minute visit started at 11:45 overruns 12:30 |
| `meal_missing` (+3 `meal_outside_window`) | 8 | The meal rule exists only as prose (rule 4). The request doesn't say **which meals this day requires** |
| `wait_too_long` | 7 | `arrive_from_origin` is in minutes, not a clock time, so it isn't easy to compare with opening times. Nothing says "don't arrive long before a place opens or before its next fixed entry" |
| `too_many_stops` | 3 (8 at low effort) | The stop limits for the pace are only in prose |
| `unknown_place` | 2 | Ids not copied exactly |

(Opus 5.5 also had 23 `schema` errors, mostly over-long texts. The length limits must stay prominent, but Opus isn't the target.)

## 3. Changes to the candidate pack (`planner/planner/pack.py`)

1. **Slots as objects**, not strings: `{"from": "10:00", "to": "12:30", "enter_by": "11:15"}`. Here `enter_by` is the latest entry that still fits a stay of `visit_typical` (relaxed) or `visit_min` (packed), using `Slot.latest_start` and `last_entry_min`. Leave out any slot shorter than that stay.
2. **`closed_between`** for places with more than one slot on the day, for example `["12:30-13:30"]`.
3. **`earliest_arrival`** as a clock time (`HH:MM`, from `item.arrive`), alongside the existing `arrive_from_origin`.
4. **A `day` block in the request:** `min_stops` and `max_stops` for the pace, and `meals_required`, a list such as `[{"meal": "lunch", "window": "12:00-15:00"}]`. Compute it with the same rule the validator applies (§6.2), so the two never disagree.
5. Keep candidate selection, ordering and the travel hints unchanged.

## 4. Changes to the system prompt (`SYSTEM_PROMPT` in `planner/planner/llm.py`)
Keep the current style: short, direct, a reason for each rule, no capitals or threats. Changes:
- **Rule 1:** copy ids exactly as written in the pack.
- **Rule 3, rewritten:**
  - A stay must fit inside one slot. Enter no later than that slot's `enter_by`.
  - A place with `closed_between` can't be visited across the break, so pick the slot before or after it.
  - Fixed-entry places can only be entered at their listed times.
- **Rule 4, rewritten:** include exactly one stop for each entry in `day.meals_required`. Use a food place whose `meals` include it, ordered so the meal starts inside its window.
- **New rule:** don't plan to arrive much more than 45 minutes before a place opens, or before its next fixed entry. If the day starts early, begin with a place that's already open. The exception is the free time before dinner, which is allowed.
- **Rule 6:** refer to `day.min_stops` and `day.max_stops`.
- **Keep the length limits** (title, why, notes) in the closing paragraph.

## 5. How to measure (Claude Code backend only: unbilled, uses Pedro's subscription)
1. **Before changing anything,** write a hold-out set, `planner/eval/holdout.jsonl`: 20 new requests in the same format, unlike the 50 in their details.
   - **Dates:** in November, plus Tue 1 Dec (a public holiday).
   - **Variety:** origins, both paces, 5 dinner days, 2 rainy days, and interests and free text not used in the 50-request set.
   - Freeze it; never tune on it.
2. **Baseline:** run Sonnet 5.5 at medium effort on the hold-out set with the **current** prompt.
3. **Iterate** on the 50-request set: **at most 3 measured revisions**, to avoid fitting the prompt to these 50 cases.
4. **Final check:** run the final version on both sets.
5. **Pass when:**
   - first-try validity on the 50-request set is at least 80%;
   - first-try validity on the hold-out set is no more than 5 points below its baseline;
   - the model-failed fallback stays at or below 10% on both sets;
   - no single error code rises by more than 3.
6. **Usage budget:** at most about 190 plans (20 baseline + 3 × 50 + 20 final), all Sonnet 5.5 medium. **No Opus runs, and no API calls.** If a usage limit stops a run, resume it with `--resume`.

## 6. Constraints
- **Backends:** the pack and the prompt are shared by the `api`, `claude-code` and `local` backends.
  - `planner/planner/local.py` belongs to the self-hosted LLM work (`~/selfhostedLLM/Execution.md`), which may be in progress in another session: **don't edit `local.py`**.
  - Keep any byte-identity tests that Execution.md requires passing.
  - If the pack change conflicts with the local backend's compact pack, stop and tell Pedro.
- **Tests:** all offline planner tests still pass, including the golden plan (`Planner.md` §10.2). Update tests that assert the old pack format, and add tests for `enter_by`, `closed_between`, `earliest_arrival` and `day.meals_required` (that one must agree with the validator's meal rule).
- **Spec:** update `Planner.md` §4 (pack format) and §5.2 (prompt rules) to match.
- **Don't touch** the scheduler, validator, error codes, fallback, retry count or schemas, the engine, or the place data.

## 7. Report
Write `planner/eval/promptfix-report.md`:
- a before/after table for both sets (first-try validity, fallback share, mean attempts, errors by code);
- the final prompt diff;
- one paragraph on what helped and what didn't.

Then stop.

## Follow-up found 2026-10-27: food slots in the pack are stricter than the validator

Found in the 27 Oct test session (`Changes.md` §0, item 4). Nothing was changed for this: it belongs to this work, which owns the pack format.

**The mismatch.** For a food place, `usable_slots` intersects the opening hours with the meal window, so the pack's slot is the window, and `enter_by` is `window end − stay`. The validator is looser: a meal visit only has to *start* inside its meal window (`meal_outside_window`) and *end* before the place closes (`visit_overruns`).

**Example: Canastra do Fidalgo, lunch, Sat 31 Oct 2026, relaxed.**
- Hours are `Mo-We,Fr-Su 10:00-02:00` and the lunch window is 12:00–15:00. The pack slot is `{"from": "12:00", "to": "15:00", "enter_by": "13:30"}`: a 90-minute lunch must end by 15:00.
- The validator accepted lunch starting at 14:54 and ending at 16:24, because 14:54 is inside the window and 16:24 is before the 02:00 closing. The plan was valid; the pack just never offered that slot.
- So the pack hides valid plans from the model: any meal that starts late in its window is never offered. The same holds for dinner (window 19:00–22:30).

**Two candidate fixes, for this work to choose between:**
1. **Make the pack match the validator.** For food, `to` is the place's closing time and `enter_by` is the end of the meal window (the latest *start* the validator allows). The model then sees what is allowed. `closed_between` and the stay-fits-the-slot rule (prompt rule 3) stay true.
2. **Make the validator match the pack.** Require a meal to *end* by its window's end. This is stricter, so fewer plans pass, and it changes error behaviour (a new reason to fail, or a reuse of `meal_outside_window`). It conflicts with §6 above (don't touch the validator or the error codes), so it needs Pedro's approval.

**It affects the Claude backends too.** The pack and the prompt are shared by `api`, `claude-code` and `local`, so any change to food slots changes all three. The compact pack for the local backend uses the same slot objects. Re-measure both sets (and keep the byte-identity tests for the home-mode pack in `tests/test_pl_compact.py` and `tests/test_pl_leave.py` in step).

## Closed 2026-10-28 (Pedro, option 2)

Closed with one measurement instead of the full procedure in §5: the current pack and prompt on the 50-request set, Sonnet 5.5 medium, Claude Code backend.
- First-try validity is **84 %**, against 70 % on 23 Oct; the fallback is 4 %.
- The comparison is not clean: walking times and place data also changed since 23 Oct.
- No hold-out check and no revisions were made.

The test bug behind the three `test_day_block_agrees_with_the_validator` failures is fixed. The details are in `planner/eval/promptfix-report.md`. The 26 Oct pack and prompt stay as they are.
