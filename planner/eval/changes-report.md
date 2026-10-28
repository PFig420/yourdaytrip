# Changes.md report (2026-10-27)

Tests: planner 146 pass, 3 fail (the three `test_day_block_agrees_with_the_validator` cases that were failing before; not touched). Engine 42 and places 50 pass. 

## Live check (local backend, one request at a time, server stopped afterwards)

| Request | Outcome | Attempts | Time | Narrative | Free-time entries and ideas |
|---|---|---|---|---|---|
| §0, home mode (31 Oct, 09:30–21:00) | model plan | 1 | 222 s | dropped after the retry (retry still "summary longer than 80 words") | 16:24–18:05 at Canastra, "free time before the bus home", **0 ideas** |
| §0, leave by 17:30 | model plan | 1 | 183 s | passed first try | same free time, 0 ideas; last visit ends 16:24, home 19:48 |
| r01 | fallback | 1 (draft hit the 800-token limit, `max_tokens`) | 214 s | none (fallback) | none |
| r03 | fallback | 3 (`meal_missing` ×3) | 207 s | none | 17:49–19:30 at Salpoente, before dinner: O Mercantel (604 m, ~78 min), Sé de Aveiro (795 m, ~56 min) |
| r06 | fallback | 3 (`wait_too_long` ×3, one also `visit_overruns`) | 283 s | none | none |
| r26 | fallback | 3 (`visit_overruns`/`wait_too_long`, `wait_too_long`, `meal_missing`) | 231 s | none | 13:19–14:15 at Canastra: A Praia do Tubarão (78 m, ~53 min) |

The model's notes for the 31 Oct home plan ("Beach visit not included due to timing conflicts…") now show in `debug.draft`; this answers Change B's purpose.

## Narrative pass count
Only two requests reached a narrative (the others were fallbacks): before the retry 1 of 2 passed; after it 1 of 2 (the retry did not rescue the one failure). Far too few to judge the retry; it needs a run on the 50 requests, which I did not do (laptop memory and time).

## Golden tests that changed
None. The golden tests call `Scheduler.schedule`, which still returns waits; the free-time pass runs in `Planner._finish`. In `plan()` output the golden plan's 15:28–16:2x wait before the 16:25 bus would now be free time.

## Decisions (all in `planner/README.md`, "Decisions", Changes of 2026-10-27)
Debug shape and `no_plan` coverage; free-time pass location and reasons; opening-hours rule for ideas (stay cut at closing/sunset); `open_until` for daylight places; tours and food-off handling; leave = last visit ends by X; `left_too_late` checked once on the last visit; leave-mode last safe return (5-minute steps back from X); plan B in leave mode; narrative retry behaviour. A home-mode pack matched the pre-change code byte for byte on all 70 request-set requests, full and compact.

## Needs Pedro
1. **The Costa Nova case has no ideas.** The brief expects Praia da Costa Nova, but in the place data it is 1,126 m on foot from Canastra (not ~250 m) and needs 60 min while only ~48 remain before the 17:32 sunset. The nearby restaurants are closed 15:30–19:00 and the fish market is a draft. Options: fix the beach's coordinates/`visit_min` in `places/data` (not mine to touch), or accept empty ideas there. Raising `suggest_walk_m` does not help.
2. **Leave-by meaning** was changed after your answer: the first vehicle home must leave by X. The live check above was run with the old meaning (last visit ends by X) and was not repeated.
3. **Local draft reliability** (not a regression): r01 ran into the 800-token cap, and 3 of the 4 eval requests fell back. Change D's food-slot mismatch is written into `PlannerPromptFix.md`.
4. The three failing pack tests belong to the prompt-fix session.


# CurrentChanges, 2026-10-27

Tests at the end: planner 157 pass, 3 fail (the same three `test_day_block_agrees_with_the_validator` cases as before); places 56 pass; engine 42 pass. `python -m places validate`: 117 records, 0 errors. The model server is stopped.

## Reference points (P)

- **Places given points: 2.**
  - `praia-costa-nova`: 6 points (the landward end of the dune boardwalk, 345 m from Canastra; five along the beach way).
  - `costa-nova-palheiros`: 4 points along the seafront road (south end 20 m from Canastra).
  - Costa Nova check: met for both (345 m and 20 m). The beach itself is 470 m from Canastra at its nearest, so its point inside 400 m is the boardwalk entrance (the §4.1 "beach access" position).
- **Left single-point, and why:** see `places/README.md` ("Reference points of long places"). Short: 8 places have their whole OSM geometry within 400 m of the main point; 2 (`praia-sao-jacinto`, `reserva-natural-dunas-sao-jacinto`) have an OSM way of 5-7 m that clearly is a fragment of a much larger place; 7 have no OSM way, or only a node, and no positions in their own sources (`praia-torreira`, `praia-vagueira`, `praia-de-mira`, `bioria-percurso-salreu`, `art-nouveau-route-aveiro`, `agueda-street-art`, `ovar-azulejos`).
- **Review file:** `places/review/reference_points.geojson` (14 features: the main and extra points of the two places in two colours, Canastra and the bus stop for reference). Every point's `source` ends in "confirm". Only these two records changed (`diff` against a copy: only appended `[[points]]` blocks).
- **Shortlist time** (one call, Porto Campanhã, three dates): 0.07-0.08 s before, 0.07-0.09 s after.
- **Packs that changed (all backends: the Claude backends get the full pack, local the compact one):** with the data before the change the new code gives the same pack as the old code for all 70 requests, 0 of 140 differ. With the points, 63 full and 60 compact packs change: 73 contain one of the two places (earlier arrivals, other slots, hints), 48 differ only in cluster labels, and 2 (`r12`, `r21`, full) differ only in which candidates share a cluster.
- **The golden plan changed**, because its last stop is the seafront: the Canastra-to-seafront stroll (1.3 km, 13:49-14:13) is now a 20 m walk, the visit 13:49-15:04, home 17:20 (was 18:20), last safe return 18:40 (was 18:25). The `Changes.md` leave-mode tests were updated to match. Everything up to Canastra is unchanged.

## Retry of cut-off local drafts (R)

Tested with the mocked server: a cut-off draft is retried (`attempts == 2`, `attempt_errors == [["schema"]]`), the cut-off text (at most 4,000 characters) is kept in `debug.attempt_drafts[].raw`, the retry sends `{}` as the assistant turn and none of the text, and three cut-offs give the fallback. **r01 was not rescued in the live run: this time its first draft was valid** (the pack changed with the points, so the model wrote a different draft); there was no cut-off text to read, so I cannot say why the earlier draft ran away. The earlier cut-off had `max_tokens` at 800 and no text was kept.

## Live check (local backend, one at a time)

| # | Request | Outcome | Attempts | Time | Detail |
|---|---|---|---|---|---|
| 1 | 31 Oct, home mode | model plan | 1 | 249 s | Museu Arte Nova, Sé, **the seafront (13:01-14:16, walk to its bus-stop point)**, Canastra lunch 14:22-15:52; home 18:20. The model's notes: "Visitor's beach interest met via Costa Nova". No free time in this plan, so no ideas shown (the unit test reproduces the 16:24-18:05 case: ideas are the seafront, 20 m, and the beach, 345 m, both open until 17:32). Narrative passed after its retry. |
| 2 | same, leave by 17:30 | model plan | 1 | 264 s | Same stops. First vehicle home: bus 36 at 16:25 (the validator accepts it, ≤ 17:30); last safe return: leave by 16:20, bus 16:25, home 18:20. Narrative passed after its retry. |
| 3 | r01 | model plan | 1 | 211 s | Not cut off this time. A 45 min free time at Canastra (14:34-15:20) with one idea: the seafront, south point, 20 m, ~45 min there. Narrative passed first try. |

This replaces the leave-by row of the `Changes.md` table (that one used the old meaning).

**Narrative pass count:** 3 plans reached a narrative: 1 of 3 passed on the first call, 3 of 3 after the retry (both rescued narratives had failed the first check; before the retry change the same request failed in the earlier run). Still a tiny sample.

## Decisions (in the READMEs)

`places/README.md`: format, validation, where the points are used, which places and why, the extra OSM query chosen by Pedro, the one-point-per-visit rule. `planner/README.md`: scheduler (`<id>#<k>` ids, display names), pack hints, free-time ideas (`point`), byte identity, golden change, cut-off text mapping.

## Needs Pedro

1. ~~Confirm the 10 new points~~ Confirmed by Pedro, 2026-10-27.
2. **Places left single-point that probably need points**: the two São Jacinto records (their OSM ways are tiny fragments), and the node-only places (Torreira, Vagueira, Mira, Bioria) and routes (Art Nouveau, Águeda, Ovar), which need positions from you or another source.
3. ~~**`Planner.md` §10.2 golden table** still shows the old times~~ Updated 2026-10-27 from the golden test's plan (20 m walk to the seafront, visit 13:49–15:04, home 17:20, last safe return 18:40).
4. r01's original runaway draft: if it comes back, run with `debug=True` to read the kept text.
