# Planner.md — LLM day planner and validator build spec

This is the complete brief for building the YourDayTrip day planner: steps 3 and 4 of the pipeline. It is written for a coding agent working alone in this repository. Read it fully before writing code. Where this spec is silent, choose the simplest option that meets the acceptance criteria (§12) and record the choice in `planner/README.md` under "Decisions".

## 1. What the planner does

It turns a request ("from Porto, Saturday 24 Oct, leave after 08:00, back by 21:00, I like museums and seafood") into a **checked day plan**: an ordered timeline of places, walks and rides, with the last safe way home.

```
request ──► places.shortlist (step 2) ──► candidate pack ──► Claude: pick & order (step 3)
                                              │                        │  JSON draft: place ids + minutes, NO times, NO trips
                                              │                        ▼
                                              │                 scheduler + validator (step 4, code)
                                              │                 engine.earliest for every leg, opening hours,
                                              │                 fixed entries, meals, daylight, home by deadline
                                              │                        │
                                              │          errors ◄──────┤ (at most 2 retries, errors fed back)
                                              │                        ▼ valid
                                              │                 Claude: short narrative from the CHECKED timeline
                                              │                        ▼
                                              └────────────────► DayPlan (timeline, last safe return, plan B,
                                                                 narrative, disclaimers)
```

**The key design rule:** Claude **chooses and orders places, and suggests how long to stay. Nothing else.** Its output contains no clock times, no routes and no trip ids. Every time, walk and ride in a plan comes from the reachability engine, through the scheduler. So the LLM has no transit data it could invent or bend. This is stricter than the action plan's first idea (the LLM returning trip ids), and it makes the validator's job exact.

### In scope
- A candidate pack built from the shortlist, including estimated travel times between candidates.
- The planner call to Claude, returning a structured draft.
- The scheduler and validator, with error feedback and at most 2 retries.
- A deterministic fallback plan built by code alone, used when the model fails or refuses.
- The narrative call, plus a check of the narrative against the timeline.
- A `DayPlan` result with disclaimers, as required by `Issues.md` #1.
- A Python API, CLI and HTTP API.
- Offline tests with a fake client, and a live evaluation harness for Gate 3.

### Out of scope
- Real-time delays (LiveBus); bookings and tickets; multi-day trips; accounts and personal history.
- Languages other than English.
- The mobile web app. This layer provides the API it will call.
- Changing engine routing or place data. The planner only reads them.

## 2. Inputs

### 2.1 The engine (`engine/`, package `reach`, `ReachabilityEngine.md`)
Load it with `reach.load("engine/config")`. The planner uses:
- `Engine.earliest(origin, date, leave_after, target, **opts) -> Journey`. Here `leave_after` may be `HH:MM[:SS]` or ISO, and `opts` may include `max_walk_m` (default 1,200, at most 2,000). A `Journey` has `depart`, `arrive`, `legs`, `rides` and `walk_s`. Each `RideLeg` has `route_short`, `route_long`, `trip_id`, `headsign`, `board_stop`, `board_time`, `alight_stop` and `alight_time`, and each `WalkLeg` has `metres`, `seconds`, `depart` and `arrive`. All instants are UTC epoch seconds.
- `Engine.latest(origin, date, target, back_by, **opts) -> Journey`, the latest departure that still gets to the target in time.
- `Engine.windows(origin, date, leave_after, back_by, points, **opts)`, one forward and one reverse scan for many points.
- The `coverage` and `warnings` on results. The CP feed ends on **2026-12-12**; after that date trains are missing and the engine says so.

### 2.2 The places layer (`places/`, package `places`, `PlacesLayer.md`)
Load it with `places.load("places/data", engine=eng, config_dir="places/config")`. It returns a `Library` with `shortlist(origin, date, leave_after, back_by, **kw)`, `get(id)` and `search(text, k)`.
- `Shortlist` has `sights`, `food`, `excluded`, `warnings` and `coverage`.
- Each `Item` has:
  - `place` (a dict of the record);
  - `arrive` and `leave` (the engine window from the origin);
  - `slots` (each with `start`, `end` and `latest_start`);
  - `visit_s`, `score`, `why`, `notes` and `cluster`;
  - `fixed_entries` in `to_json()`.
- Place records carry `hours`, `entry_times`, `last_entry_min`, `daylight_only`, `meals`, `visit_min`, `visit_typical`, `max_walk_m`, `weather`, `price` and `description`.
- Only `confirmed` places are used (the shortlist default). On 23 Oct 2026 that's 78 places.

### 2.3 Claude
- **SDK:** the official `anthropic` Python SDK. Never use raw HTTP or OpenAI-compatible shims.
- **Model:** set in `planner/config.toml`. The default is **`claude-sonnet-5-5`** (Pedro's decision, 23 Oct 2026). Gate 3 (§11) also runs `claude-opus-5-5` as a quality reference. Use exact model ids, with no date suffixes.
- **Thinking:** omit the `thinking` parameter. It's adaptive by default on Claude Sonnet 5.5 (and on Claude Opus 5.5), and `{"type": "disabled"}` returns a 400 on both. Don't use `{"type": "between_tools"}`; the planner makes no tool calls.
- **Effort:** set it explicitly with `output_config={"effort": "medium"}`, because Claude Sonnet 5.5 defaults to `high` and its levels were recalibrated. It's configurable, and Gate 3 tries `low` and `medium`.
- **Structured output:** `client.messages.parse(..., output_format=<Pydantic model>)`, which returns `response.parsed_output`. **Check `response.stop_reason` before reading the output.**
- **Refusal fallback:** opt in with `fallbacks="default"` on the beta surface: `client.beta.messages.parse(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default", ...)`. For Claude Sonnet 5.5, only this `"default"` form is accepted, and only on the Claude API. The header must be exactly `server-side-fallback-2026-07-01` for the `"default"` form. **Verify against the installed SDK that `beta.messages.parse` accepts `fallbacks`, `betas` and `output_config` together.** If any combination is rejected, use `client.beta.messages.create` with `output_config={"format": {"type": "json_schema", ...}, "effort": ...}`, validate the JSON with the same Pydantic model, and record this as a Decision. Don't guess SDK names: check the installed package. If the final `stop_reason` is still `"refusal"`, use the fallback plan (§7).
- **Errors:** catch typed SDK errors from most specific to least (rate limit, then other status errors, then connection errors). Use the SDK's own retries (default 2). On a final failure, use the fallback plan.
- **Prompt caching:** keep the system prompt and the rules byte-stable and first, and use top-level automatic caching (`cache_control={"type": "ephemeral"}`). Log `usage.cache_read_input_tokens` to confirm hits.
- **Credentials:** read from the environment, or from an `ant auth login` profile, through the zero-argument `anthropic.Anthropic()`. Never hard-code or log a key.

### 2.4 Test backend: Claude Code on Pedro's subscription (unbilled)
For testing plan **quality** without per-token API charges, the planner gets a second backend that calls the locally installed Claude Code CLI. Logged in with Pedro's Claude.ai account, those calls count against his subscription's usage limits instead of being billed. **It's for local testing only; the app never runs on it** (§15).

- **Binary:** `claude` on `PATH`, else the VS Code extension's copy (`~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude`, newest version), else the path in `PLANNER_CLAUDE_BIN`. On 23 Oct 2026 the installed version was 2.1.308.
- **One call per planner or narrative request**, as a subprocess in a fresh empty temporary directory, so no project `CLAUDE.md` or settings are picked up:

  ```
  claude -p --model <model> --effort <effort> --system-prompt <the §5.2 system prompt>
         --json-schema <the DayDraft or Narrative JSON schema> --output-format json
         --tools "" --strict-mcp-config --disable-slash-commands --no-session-persistence
  ```

  Send the user message (the candidate pack, or the retry history rendered as text) on **stdin**, not as an argument (it can be long).
- **Billing safety, required:**
  - Run the subprocess with `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` **removed from its environment**, so the CLI can only use the Claude.ai login. If the CLI then reports it isn't logged in, stop and tell Pedro to run `claude` once and log in.
  - **Never pass `--bare`.** It looks like the right minimal mode, but it switches authentication to API keys only, which is exactly the billing this backend avoids.
- **Output:** parse the `--output-format json` result. Read the structured output field for the schema-constrained answer, plus the usage and error fields if present. Its exact shape isn't specified here: run one tiny call, record the actual field names in the README under Decisions, and validate the answer with the same Pydantic model as the API path. A non-zero exit, a missing structured output, or a usage-limit message counts as an API error for §7 (fallback plan), with the message kept in the log.
- **What doesn't carry over from §2.3:** server-side refusal fallbacks, prompt caching control and the API's token accounting. Retries, validation and the fallback plan are unchanged. Cost and latency measured on this backend are **not** representative of the API, and the report must say so.
- **Pacing:** sequential calls only. On a usage-limit message, stop the run cleanly; every finished request is already logged, and a re-run skips them (`--resume`).

## 3. Request and result

### 3.1 `PlanRequest`
| Field | Type | Default | Notes |
|---|---|---|---|
| `origin` | `{id, lat, lon, name}` | — | MVP origins: Porto Campanhã, Porto São Bento, Aveiro station |
| `date` | `YYYY-MM-DD` | — | Local date |
| `leave_after`, `back_by` | `HH:MM` | — | `back_by` must be after `leave_after`. In `"leave"` mode `back_by` is the leave-by time (next row) |
| `deadline` | `home` / `leave` | `home` | `home`: be back at the origin by `back_by`. `leave`: the first vehicle home leaves the destination by `back_by` (the walk to the stop counts before it); the journey may arrive later, up to `[schedule] leave_mode_home_by` (default 23:59). Pedro's decision, 2026-10-27 |
| `interests` | tags from the places vocabulary | `[]` | Validated against `places/config/vocabulary.toml` |
| `text` | string ≤ 200 chars | `""` | Free text, passed to `search` |
| `pace` | `relaxed` / `packed` | `relaxed` | Sets the stop count and visit lengths (§6.2) |
| `weather` | `any` / `rain` | `any` | `rain` drops outdoor places |
| `include_food` | bool | `true` | At least one meal stop if the day covers a meal window |

### 3.2 `DayPlan` (returned; also `to_json()`)
- `request` (echoed), `source` (`llm` or `fallback`), `model` and `attempts`.
- `timeline`, an ordered list of entries. Each is one of:
  - `walk {from, to, metres, depart, arrive}`;
  - `ride {mode, route_short, route_long, headsign, trip_id, board_stop, board_time, alight_stop, alight_time}`;
  - `wait {at, from, until, reason}`;
  - `free_time {at, from, until, reason, suggestions}`: a gap shown to the user as free time, not as waiting: the evening gap before dinner, or a wait of 30 minutes or more straight after a visit (§6.1). `suggestions` holds up to 3 optional ideas on foot, `{place_id, name, kind, walk_m, walk_min, minutes_there, open_until, description}`;
  - `visit {place_id, name, start, end, entry: open|fixed, meal?: lunch|dinner}`.

  Every time is local ISO 8601 with its offset.

  Entries also carry the ids their positions come from, for the map (output only; the pack, prompts, validator and narrative payload do not use them): `ride {feed, board_stop_id, alight_stop_id}`, `walk {from_id, to_id}` (a stop id, a place or point id `<id>` / `<id>#<k>`, or the origin's id), `visit {point_id}`, `free_time {point_id}` and each suggestion's `point_id` (and `point`, the reference point's name, for long places).
- `home_by`: the planned arrival back at the origin.
- `last_safe_return`: from the last place, the latest departure (from `engine.latest`) and its first vehicle. Also the backup, the departure just before it, if one exists.
- `plan_b`: for every change between vehicles of less than 10 minutes, the next journey to the same target if that connection is missed.
- `narrative`: a short summary and one line per stop (§8). It can be `null` if the narrative check fails.
- `disclaimers`: always all three — "Times are the operators' published schedules and indicative; check with the operator before your return journey." / "Data as of `<feed build dates>`." / "Map data © OpenStreetMap contributors."
- `warnings`: coverage gaps, stale place hours, draft data, and accepted fast legs (`Issues.md` #4) the plan uses.
- `usage`: tokens per call (input, output, cache read), latency per call and in total.
- `map`: `null` unless the plan was asked for with `?map=true` (§9.3): a GeoJSON `FeatureCollection` of the day built by `planner/mapview.py` from the timeline and request only. Features, each with `kind` and (where it belongs to a timeline entry) `step`, the timeline index: `origin`, `visit` (numbered `n`), `ride` (the cached road or rail path between the trip's stops from boarding to alighting; stop to stop where there is none), `stop`, `walk` (the cached footpath, else a straight line), `free_time`, `idea` and `idea_walk` (an idea's walk from the free time). A bus `ride` also has `route_source`: `relation` (every stop pair follows the operator's route as mapped in OpenStreetMap), `mixed`, or `shortest` (the shortest road path between stops); empty for trains, the ferry and when the cache has no source. Properties of the collection: `bounds_all`, `bounds_destination` (`[west, south, east, north]`; the visits and the legs between them) and `warnings` (ids that could not be placed; nothing is invented). A `no_plan` result has `map = null`.
- `debug`: `null` unless `plan(..., debug=True)` (testing only, §9.3). Then `{draft, attempt_drafts}`: the accepted draft as `{title, notes, stops: [{place_id, name, visit_minutes, why}]}` (`null` for a fallback plan) and each failed draft with its errors.

## 4. Candidate pack (code, before the model)

1. Call `shortlist` with the request. Keep the **top 12 sights** and the **top 6 food and drink** places by score. Make sure at least 2 clusters are represented when the shortlist offers them, and include every food place with a lunch slot (up to 6).
2. **Travel hints:** for each candidate, run one `engine.windows` call with that candidate as origin and the other candidates as points, at a reference time (`leave_after + 3 h`), with `max_walk_m = 2000`. Hint = `arrive − reference`, in minutes, rounded up. That's 18 scans, under 1 s in total.
   - A place with extra reference points (`PlacesLayer.md` §4.1) is hinted from the point the visitor reaches first (`Item.point`) to every point of the others, keeping the shortest per pair.
   - Hints are **estimates for the model's reasoning only**. The scheduler never uses them.
   - Also include, for each candidate, the minutes from the origin (`item.arrive − leave_after`) and the latest time to leave it (`item.leave`).
3. **Pack format:** compact JSON, with candidates sorted by score. Each candidate includes:
   - `id`, `name`, `kind`, `category`, `tags`, `cluster`, `description`;
   - `slots` as `HH:MM` local times;
   - `fixed_entry_times` (if any), `visit_min`, `visit_typical`, `meals`, `price`, `weather`;
   - `notes` (the place's notes, ≤ 2);
   - `arrive_from_origin` and `leave_by`.

   Add the hint matrix, keyed by id pairs, plus the request fields.
4. Exclude places whose slots are all shorter than their `visit_min`. The shortlist already does this; assert it.

## 5. Step 3: the planner call

### 5.1 Output schema (Pydantic)

```python
class DraftStop(BaseModel):
    place_id: str            # must be a candidate id
    visit_minutes: int       # suggested stay
    why: str                 # ≤ 25 words, for the narrative

class DayDraft(BaseModel):
    title: str               # ≤ 8 words
    stops: list[DraftStop]   # in visiting order, 2–6 items
    notes: str               # ≤ 40 words: what was left out and why
```

Limits that structured outputs may not enforce (lengths, counts) are checked in code. A violation counts as a validation error (§6.3).

### 5.2 Prompt
- **System prompt** (stable, cached): the role (plan one realistic day out by public transport), plus these rules:
  1. Use only candidate ids.
  2. Order stops so travel is short. Use the hints; group stops in the same cluster.
  3. Respect each place's slots and fixed entry times, and its visit_min and visit_typical.
  4. When the day spans 12:00–15:00 and food is included, put a lunch place in that window. When the day ends after 22:00, end with a dinner place in walking distance of the last sight; a gap of up to two hours before dinner is fine, because the scheduler turns it into free time.
  5. Leave time to get home: the last stop must end well before its `leave_by`.
  6. A relaxed pace means 2–4 stops and visit_typical; packed means 3–6 stops and visit_min.
  7. Never output times, routes or trips.
  8. Explain any trade-off in `notes`.

  Keep the prompt short and direct, without capitals or threats, and give the reason behind each rule.
- **User message:** the candidate pack (§4) and the request.
- **Retry message** (when validation fails): a user turn listing each error as `code: plain explanation`, with the numbers involved. For example: `closed: museu-de-aveiro-santa-joana is closed on Mondays`, or `home_too_late: after costa-nova-palheiros ends at 18:40 the first journey home arrives 21:35, after the 21:00 limit`. Then ask for a corrected draft. Keep the earlier turns as they were (append only, never edit history).
- `max_tokens` = 16000; the call is not streamed.

## 6. Step 4: scheduler and validator (code)

### 6.1 Scheduling, deterministic
Start at the origin at `leave_after`. For each stop, in order:
1. **Travel:** `engine.earliest(current_point, date, current_time, place)`, using (a place with extra reference points is tried at each point and the earliest arrival wins; the visit and the way out both use that point, and the walk names it, e.g. "Praia da Costa Nova (south end, dune boardwalk …)"):
   - for the first stop: the place's own `max_walk_m`, or the engine default;
   - from place to place: `max_walk_m = 2000`, so that a 1.3 km stroll beats two buses.
2. **Entry time:**
   - *Fixed entries:* the first `entry_times` value at or after arrival.
   - *Open hours:* the arrival time, or the start of the next opening interval if the place isn't open yet.
   - In both cases, waiting longer than `max_wait_min` (default 45) is an error, except before dinner (next bullet).
   - *Free time before dinner:* when the stop is a meal place visited for dinner (entry in the dinner window), a gap of up to `max_free_time_min` (default 120) is allowed, **if the leg into the dinner place is walk-only** (no rides, at most 2,000 m). The gap appears in the timeline as `free_time`, not `wait`. A longer gap, or one that needs a ride, is still `wait_too_long`.
   - *Filling free time:* if the free time is at least 45 minutes, the scheduler may insert **one** extra visit into it. It picks the highest-scoring always-open (`24/7`) place that:
     - is in the same cluster as the dinner place;
     - isn't already in the plan;
     - can be visited for its `visit_min` with walk-only legs before sunset (if `daylight_only`).

     The inserted visit is marked `inserted: true` with the reason "fills time before dinner". Any time left over stays `free_time`. The model's draft is never otherwise changed.
   - *Long waits become free time (post-pass, after validation):* a `wait` whose reason is "until it is time to leave for the next stop" (straight after a visit, including the leg home) and that lasts at least `[schedule] suggest_free_time_min` (default 30) minutes is shown as `free_time`. Its reason is "free time before the bus home" (or "train home", from the first ride's mode) when the next legs are the journey home, else "free time before the next stop". Transfer waits ("until the next connection"), "until it opens" and "fixed entry time" stay waits. Times, journeys and checks do not change, and the pass cannot make a plan fail.
   - *Ideas nearby:* each free time (and a free time before dinner into which no visit was inserted) gets up to 3 suggestions, shortest walk first (ties by `place_id`), at most 1 food place. A suggestion is a **confirmed** record not already in the plan, with walking only (one way at most `suggest_walk_m` = 800 m, found with `Router.earliest`), whose walk there, stay of at least `visit_min` and walk back to the free time's location all fit before `until`, and which is open for the whole stay (its own hours; `daylight_only` places only before sunset; food by opening hours, not meal windows; tours with fixed entry times are skipped). Interests and pace are ignored; on a `rain` day outdoor places are dropped. A long place is reached at its nearest point by walking time, which the suggestion names in `point`; the free time's own location is the point its last visit used. `minutes_there` is the time left after both walks within the opening hours; `open_until` is the closing time that applies (the sunset for daylight-only places, `null` for places open 24/7 without a daylight limit). The narrative does not mention them.
3. **Visit length:** `visit_minutes` from the draft, clamped to the range [`visit_min`, 2 × `visit_typical`]. A draft below `visit_min` is an error, not silently raised.
4. **Fit:**
   - The entry must be at or before `closing − last_entry_min`.
   - The visit must end at or before the closing time of that interval, except for fixed-entry tours, which may run past closing (as in the places layer).
   - The visit must end at or before sunset for `daylight_only` places.
   - A meal visit must start inside its meal window.
5. **Move on:** the current point becomes the place, and the current time becomes the end of the visit.

Then the **return:** `engine.earliest(last_place, date, end_of_last_visit, origin, max_walk_m=2000)` must arrive at or before `back_by`. In `"leave"` mode the first vehicle home must leave by `back_by` (`left_too_late`) and the return must arrive by `leave_mode_home_by`. Also compute:
- `last_safe_return` with `engine.latest(last_place, date, origin, back_by, max_walk_m=2000)`, plus the departure before it (the backup);
- `plan_b` for each change with less than 10 minutes between vehicles: `engine.earliest` from that leg's boarding stop, starting 1 minute after its departure, to the same target.

In `"leave"` mode `last_safe_return` is the last vehicle home that still leaves by the leave-by time (`engine.latest` only takes arrival deadlines): from the end of the last visit, `engine.earliest` journeys in turn (each next one starts a minute after the previous departure) while the first vehicle leaves by the leave-by time and the journey arrives by `leave_mode_home_by`; the last is the safe return and the one before it the backup. `plan_b.within_deadline` is judged against the leave-by time for legs before the last visit and against `leave_mode_home_by` for the way home.

### 6.2 Plan-level rules
- 2–6 stops; no place twice; every id in the candidate pack.
- Relaxed pace: at most 4 stops. Packed: at most 6.
- In `"leave"` mode "the end of the day" in the meal rules below is the leave-by time, which is the time actually spent at the destination (dinner is not required for a leave-by of 18:00 even if home is later). Each candidate's `leave_by` in the pack is capped at that time, which is optimistic: the walk to the stop is not subtracted, and the validator has the last word.
- When `include_food` is true and the time between arriving and leaving covers 12:00–15:00, at least one stop with `meals ∋ lunch` must start in that window. The same applies to dinner (19:00–22:30) when `back_by` is 22:00 or later.
- **Weather `rain`:** no `outdoor` places.

### 6.3 Error codes (returned to the model on retry, and logged)
| Code | Meaning |
|---|---|
| `unknown_place` | The id is not in the candidate pack |
| `duplicate_place` | The same id appears twice |
| `too_many_stops` / `too_few_stops` | Outside the pace limits |
| `closed` | No opening interval that day after arrival |
| `no_departure` | No fixed entry time fits (the places layer uses the same name) |
| `wait_too_long` | The wait for opening or entry exceeds `max_wait_min`; or, before dinner, the free time exceeds `max_free_time_min` or the leg into the dinner place needs a ride |
| `entry_too_late` | Arrival is after `closing − last_entry_min` |
| `visit_overruns` | The visit ends after closing time or after sunset |
| `visit_too_short` | `visit_minutes` is below `visit_min` |
| `meal_missing` / `meal_outside_window` | §6.2 |
| `no_journey` | The engine finds no journey for a leg |
| `home_too_late` | The return arrives after `back_by` (in `"leave"` mode: after `leave_mode_home_by`) |
| `left_too_late` | `"leave"` mode only: the first vehicle home leaves after the leave-by time (`back_by`) |
| `schema` | The draft violates §5.1 limits |

Each error carries the place id, the times involved (local `HH:MM`) and a one-line explanation. The model fixes its draft from these.

### 6.4 Why this is enough
The draft carries no times or trips, so the only way a plan can be wrong is a scheduling mistake in code. That's covered by the golden tests (§10.2). Every leg in a `DayPlan` is an engine `Journey` leg, and the engine's own property tests already replay journeys against the timetable.

## 7. Retries and the fallback plan
- **At most 3 planner calls:** the first plus 2 retries. A valid schedule ends the loop.
- **A draft cut off at the output limit** (`max_tokens`) counts as an attempt and a `schema` error ("the reply was cut off or was not valid JSON for the draft format") and is retried, on every backend. On the local backend the cut-off text (at most 4,000 characters) is kept in `debug.attempt_drafts[].raw` and is never sent back to the model; the retry's assistant turn is `{}`. The other local errors (`connection`, `timeout`, `http_status_<n>`, `context_overflow`, `no_structured_output`) still go straight to the fallback plan.
- **The fallback plan is used when:**
  - all 3 drafts fail;
  - the final `stop_reason` is `refusal`;
  - an API error survives the SDK's retries;
  - `--no-llm` is set.
- **Fallback algorithm, deterministic, code only:** start at the origin.
  1. Repeatedly add the highest-scoring sight whose next usable slot fits after the current time, scheduling it with §6.1.
  2. Insert the best-scoring lunch place when the current time enters the lunch window.
  3. When a dinner is required (§6.2), end with the best-scoring dinner place reachable from the last sight by a walk-only leg, using free time (§6.1). If none qualifies, try ending the sights earlier at a place in a cluster that has a dinner place.
  4. Stop when the next stop would break the return check or the pace limit.
- The result has `source = "fallback"` and a warning saying it was built without the language model. It goes through the same validator.

## 8. The narrative call
- **Input:** only the **validated** timeline (place names, visit times, ride summaries) and each stop's `why`.
- **Output (Pydantic):** `Narrative { summary: str (≤ 80 words), stop_lines: list[{place_id, line ≤ 25 words}] }`.
- **Narrative check (code):**
  - Every clock time (`\d{1,2}:\d{2}`) in the text must appear in the timeline.
  - Every place name must belong to a stop in the plan.
  - No price, phone number or URL may appear.
  - The word limits hold.

  If the check fails, set `narrative = null` and add a warning; the timeline stands on its own. One call, no retry on the Claude backends.
- **Local backend only:** `[local] narrative_retries = 1`. When the check returns problems, one more call is made with the original user turn, the failed answer as an assistant turn, then a user turn listing the problems with the facts needed to fix them (the summary's word count against 80, each over-long line's count against 25, and for a stop-line mismatch the exact `place_id`s in timeline order). Same system prompt, schema and token limit; the earlier turns are never edited, so the server's prefix cache is reused. The retry is accepted if it passes the same check; otherwise the narrative is dropped with a warning naming the retry's problems. Usage records the calls as `narrative` and `narrative_retry`. The limits are not put in the JSON schema: a grammar-level `maxLength` cuts text mid-sentence.
- Same model and fallback settings as the planner call. Effort `low`.

## 9. Interfaces

### 9.1 Python API (`planner/__init__.py`)

```python
pl = planner.load(engine=reach.load("engine/config"),
                  places=places.load("places/data", engine=..., config_dir="places/config"),
                  config="planner/config.toml", client=None, backend="api")   # backend: "api" | "claude-code"
plan = pl.plan(PlanRequest(...), use_llm=True) -> DayPlan
```

With `backend="api"` and `client=None` the planner uses `anthropic.Anthropic()`. Pass `client` for tests, which use a fake with the same `beta.messages.parse` / `messages.parse` surface. `backend="claude-code"` uses §2.4; its tests replace the subprocess runner with a fake.

### 9.2 CLI

```bash
python -m planner plan --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 \
                       --interests history,maritime --pace relaxed [--text "..."] [--no-llm] [--json] [--debug] [--geojson PATH]
# --back-by HH:MM (be home by) or --leave-by HH:MM (the last visit ends by); exactly one of them is required
python -m planner eval --set planner/eval/requests.jsonl --model claude-sonnet-5-5 [--effort medium] \
                       --backend claude-code|api [--resume] [--approved]   # Gate 3, §11; api needs --approved
```

The human-readable output prints the timeline, the last safe return, plan B and the disclaimers.

### 9.3 HTTP (`planner.web`, port 8300, local only until the launch conditions in `Issues.md` #1 are met)
- `POST /plan`: the body is a `PlanRequest` (including `deadline`); returns a `DayPlan`. `?use_llm=false` gives the code-only plan. `?debug=true` fills `DayPlan.debug` (the accepted draft's title and notes, and the failed drafts); without it `debug` is `null`.
- `POST /plan?map=true` adds `map` (§3.2). Rides follow the roads, railways and ferry routes of OpenStreetMap and walks follow footpaths and streets, from the display cache `data/paths/paths.json.gz` (built offline by `python -m mapdata.build`; see `mapdata/README.md`). Each `ride`, `walk` and `idea_walk` feature has `path` (`osm`, `partial` or `straight`) and `gaps` (coordinate index ranges that are only straight lines), and walks also `path_m`. A missing or stale cache gives straight lines and one warning. The paths are display only: they change no time, check or plan. `GET /meta` also returns `tile_url` and `tile_attribution` (`[web]` in `config.toml`).
- `GET /`: the test page, with a Leaflet map (1.9.4 from cdnjs) above the timeline: numbered visits, one line per ride, dashed straight walks, free time and optional ideas (a switchable layer), "Whole day" and "Destination" views, and row ↔ map highlighting. A switch chooses "Back home by" or "Leave by"; free time shows its ideas ("Ideas nearby (optional)"); a "Model notes (testing)" box shows the accepted draft's notes. The page always sends `debug=true`.
- `GET /health`: model, feed hashes, place count, API reachability (one cheap `models.retrieve` call, cached).

## 10. Tests (all offline; `pytest planner/tests`)
Use a `FakeClient` that returns scripted `DayDraft` and `Narrative` objects, or scripted refusals and errors. **No test calls the real API.**

### 10.1 Unit
- **Schema limits:** 7 stops, or a 30-word `why`, give the error `schema`.
- **Clamping:** `visit_minutes` above 2 × typical is clamped; below `visit_min` is an error.
- **Waiting:** arriving at 09:24 for a 10:00 opening waits 36 minutes (allowed). Arriving at 08:30 at a place opening at 10:00 gives `wait_too_long`.
- **Free time before dinner:** on Sat 24 Oct, back by 22:30:
  - Sights end about 17:30, then a dinner place opening at 19:00 within walking distance: the timeline shows `free_time` and no error.
  - The same gap when the dinner place needs a bus: `wait_too_long`.
  - A gap of 150 minutes: `wait_too_long`.
  - An always-open daylight place in the dinner place's cluster is inserted only if it fits before that day's sunset (§5.2 of `PlacesLayer.md`), and is marked `inserted: true`.
- **Fixed entries:** Farol da Barra on a Saturday gives `no_departure`. On Wednesday 28 Oct (summer schedule, `Apr-Oct We …`) it gives a visit starting at the first listed time at or after arrival.
- **`last_entry_min`:** arriving at Museu Marítimo de Ílhavo after 17:15 gives `entry_too_late`.
- **Monday closing:** Museu de Aveiro on Monday 26 Oct gives `closed`.
- **Meal rule:** a draft without lunch for 08:00–21:00 with food included gives `meal_missing`.
- **Narrative check:** a narrative containing "11:00" when no 11:00 is in the timeline is rejected, and the plan keeps `narrative = null`.
- **Retry flow:** the fake returns an invalid draft, then a valid one, giving `attempts = 2` and `source = llm`. Three invalid drafts give `source = fallback`. A refusal gives `fallback` straight away.
- **Coverage:** a request from Porto on 2026-12-19 carries the CP coverage warning. With no trains, the plan ends up with `no_journey` from Porto, or comes from the fallback path with that warning.
- **Claude Code backend (§2.4)**, with a fake subprocess runner:
  - the command never contains `--bare`;
  - the subprocess environment never contains `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_BASE_URL`, even when they're set in the parent;
  - the user message goes on stdin, and the working directory is an empty temporary directory;
  - a usage-limit message stops an eval run cleanly, and `--resume` skips logged requests;
  - the web server refuses to start with `backend="claude-code"`.

### 10.2 Golden (real engine and places, `use_llm=False` and a scripted draft)
Request: from Porto Campanhã, Sat 2026-10-24, after 08:00, back by 21:00. Scripted draft, in order: `museu-de-aveiro-santa-joana` 75 min, `canastra-do-fidalgo-costa-nova` 90 min (lunch), `costa-nova-palheiros` 75 min. Expected values, computed on 23 Oct 2026 with the current feeds. They were updated on 27 Oct 2026 for the seafront's reference points (`CurrentChanges.md` P): the first point reached is 20 m from Canastra, at the south end of the seafront. Everything up to the end of lunch is unchanged.

| Step | Expected |
|---|---|
| To the museum | CP "Linha de Aveiro" trip `cp:15715_20260907`, 08:10 → 09:07; walk; arrive **09:24**; **wait** until 10:00; visit **10:00–11:15** |
| To Canastra | walk 11:26–11:34; line 36 trip `av:bw-36-p20t2c6` 11:34 → 12:15; walk; arrive **12:19**; lunch **12:19–13:49** |
| To the seafront | **walk only**, 20 m at 13:49 to the point "south end of the seafront, on Avenida José Estêvão"; visit **13:49–15:04** |
| Home | wait 15:04–15:20 (under 30 min, so it stays a wait); walk 248 m to Largo Arrais Ançã; line 36 trip `av:bw-36-p21t2c10` 15:25 → 16:10; CP "Linha de Aveiro" trip `cp:15637_20251214` at 16:23; home **17:20** |
| Last safe return | leave the seafront by **18:40** (line 36 at 18:45, home 20:48); backup: leave by 18:05 (line 36 at 18:10, home 20:20) |

If a feed hash differs from `engine/config/feeds.toml`, skip with a message naming the feed.

### 10.3 Fallback golden
With `use_llm=False` and the same request, the fallback plan validates, has 2–4 stops including a lunch stop, and is identical across two runs.

## 11. Gate 3: live evaluation (`python -m planner eval`)
**Gate 3 has two parts.** The **quality run** uses the Claude Code backend (§2.4): no per-token charges, only Pedro's subscription limits, so it needs no budget approval. The **cost and speed run** uses the API, costs real money, and is a separate, later step that needs Pedro's approval; keep it small (Sonnet 5.5 only, a subset if Pedro prefers). For the API run, print an estimate first: the average tokens from a 3-request dry run, times 50 requests, times the number of models and efforts tried.

- **Request set (`planner/eval/requests.jsonl`):** 50 requests, written once and kept fixed.
  - **Dates:** Sat 24 Oct, Sun 25 Oct (the day the clocks change), Tue 27 Oct, Wed 28 Oct (Farol da Barra), Tue 8 Dec (holiday), and Sat 17 Jul 2027 from Aveiro station (summer; no CP data, so not from Porto).
  - **Origins:** Porto Campanhã, Porto São Bento, Aveiro station.
  - **Variation:** both paces, `weather` set to `rain` in 5 requests, a range of interests, and free text in 10.
- **Measured per request:** the outcome, one of `llm_valid` (a model draft validated), `llm_failed_fallback` (the model failed or refused, and the fallback plan was used) or `no_plan` (no valid plan exists, even from the fallback); valid on the first try; attempts; errors by code; latency per call and in total; tokens (input, output, cache read); cost from the current price table, recorded with its date.
- **Runs (quality run, Claude Code backend):** `claude-sonnet-5-5` (the default) at effort `medium` and `low`, and `claude-opus-5-5` at `medium` as a quality reference. Pause between runs if the subscription's usage limits are near. Each run is logged to `planner/eval/runs/<model>-<effort>-<date>.jsonl`.
- **Gate 3 passes when:**
  - every shown plan is valid (true by construction; assert it);
  - first-try validity is at least 70%;
  - the share of `llm_failed_fallback` is at most 10%. `no_plan` outcomes are reported separately and don't count against the model: they measure the data and the rules, not the LLM;
  - p95 total latency is at most 30 s;
  - Pedro rates at least 8 of 10 sampled plans acceptable on a short rubric: sensible order, not rushed, interesting, meal well placed.

  These thresholds are proposals; Pedro may change them before the run.
- **Report:** `planner/eval/report.md`, a table per model and effort. It shows whether the default (Sonnet 5.5) holds up against the Opus reference.

## 12. Milestones and acceptance criteria

| # | Milestone | Done when |
|---|---|---|
| M1 | Scheduler and validator | §6 implemented; unit tests for every error code; golden §10.2 passes with a scripted draft |
| M2 | Fallback plan | §7 implemented; §10.3 passes |
| M3 | Planner and narrative calls | §5 and §8 with the fake client; retry, refusal and narrative-check tests pass; SDK usage verified against the installed package |
| M4 | Interfaces | CLI and HTTP API; `DayPlan.to_json()`; disclaimers always present |
| M5 | Gate 3 | §2.4 backend built and tested; quality run on the Claude Code backend with its report. **Then stop and ask Pedro** before any API cost-and-speed run |

## 13. Repository layout

```
MVP-LiveBus/
├── Planner.md
└── planner/
    ├── README.md            (run commands, Decisions, Known limitations)
    ├── pyproject.toml       (package planner; deps: anthropic; extras: web = fastapi, uvicorn; dev = pytest, httpx)
    ├── config.toml          (model, effort, max_wait_min, max_free_time_min, candidate counts, max retries)
    ├── planner/
    │   ├── __init__.py      (load, PlanRequest, DayPlan)
    │   ├── pack.py          (§4)
    │   ├── llm.py           (§5, §8: prompts, schemas, calls, fallbacks, usage)
    │   ├── schedule.py      (§6)
    │   ├── fallback.py      (§7)
    │   ├── narrative.py     (§8 check)
    │   ├── cli.py, web/
    │   └── eval.py          (§11)
    ├── eval/requests.jsonl
    └── tests/               (unique test file names: run separately from engine/ and places/)
```

## 14. Known data issues the planner will meet
- **`costa-nova-palheiros` sits at the north end of the seafront,** 1.3 km from Canastra do Fidalgo and from the Costa Nova bus terminus. The place-to-place walking limit of 2,000 m (§6.1) keeps plans sensible. Moving the point to the centre of the seafront is a places-data fix, outside this spec.
- **Accepted fast legs** (`Issues.md` #4) may appear in plans; list them in `warnings`.
- **37 draft places** are excluded until they have opening hours.

## 15. Things not to do
- Don't let the model output times, routes, stops or trip ids, and don't accept them if it does.
- Don't show any plan that hasn't passed the validator, including fallback plans.
- Don't edit earlier conversation turns on retry; append only.
- Don't call the real API in tests, or run an API evaluation without Pedro's approval.
- Don't use the Claude Code backend in the HTTP API, the app or any shared or deployed setup: the web server must refuse to start with `backend="claude-code"`. It's for Pedro's local testing only.
- Don't pass `--bare` to the Claude Code CLI, or let API-key variables reach it (§2.4).
- Don't log API keys. Don't send anything about the user beyond the request fields.
- Don't change engine routing or place data.
- Don't expose the HTTP API publicly until the launch conditions in `Issues.md` #1 are met.
