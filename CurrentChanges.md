# CurrentChanges.md: several reference points per place, and retries for cut-off local drafts

This is a build brief for a coding agent working in this repository. Read it fully before starting. Then read `PlacesLayer.md` §4.1 and §4.5, `Planner.md` §4, §6.1 and §7, and the "Decisions" in `planner/README.md` (including the `Changes.md` entries of 2026-10-27). Do the work in the order of §6. Record every choice this brief leaves open, dated: in `places/README.md` for the places layer, and in `planner/README.md` for the planner.

Written 2026-10-27 from Pedro's decisions after the `Changes.md` report (`planner/eval/changes-report.md`).

## 0. Why

1. **Long places are stored as one point, so they never look nearby.**
   - Praia da Costa Nova is stored at its north end, 1,126 m from Canastra do Fidalgo in a straight line. The bus 36 terminus at Largo Arrais Ançã is at the south end. The palheiros seafront is stored 1,311 m away.
   - So in the Costa Nova test case (Sat 31 Oct, 16:24–18:05 free time at Canastra), the free-time ideas found nothing, although the beach is a short walk from the restaurant.
   - The same single point also makes the engine's arrival times at these places later than they really are.
   - **Pedro's decision:** a long place such as a seafront or beach gets several reference points, not one.
2. **A local draft cut off at the output limit goes straight to the fallback plan.**
   - On the local backend, a draft that reaches `[local] max_tokens` (800) raises `BackendError("max_tokens")`. `Planner._llm_loop` treats every backend error as final.
   - On the Claude backends, a cut-off draft comes back with `parsed = None` and is retried as a `schema` error (`llm.py`, `stop_reason == "max_tokens"`).
   - Eval request r01 in the report shows the gap: 1 attempt, then fallback.
   - **Pedro's decision:** retry a cut-off local draft as Claude's are retried, and keep the cut-off text for debugging.

## 1. Change P: reference points for long places

### 1.1 Record format (places layer)

Add an optional array of tables to place records:

```toml
[[points]]
name = "south end, by the Largo Arrais Ançã bus stop"
lat = 40.610900
lon = -8.750400
source = "OSM way/301852964 (ODbL); point chosen by Claude 2026-10-27, confirm"
```

- **The record's own `lat`/`lon` stays.** It is the main point, used wherever one position is needed: sunrise and sunset, the area-box check, maps. A record without `[[points]]` behaves exactly as today.
- **Point fields:** `points` is a list of `{name, lat, lon, source}`.
  - `name` says where the point is, in a few words a visitor understands ("south end", "boardwalk by the lighthouse").
  - `source` names the OSM element (ODbL, `Issues.md` #1) or the page the point comes from, and who chose it.
- **The main point counts as one of the place's points** for every distance and routing rule below. Do not repeat it in `[[points]]`.
- **Code:**
  - `places/places/model.py`: load the points, and render them in `dumps()` after `[provenance]`, so diffs stay readable.
  - A helper `Place.all_points()` returns `[(label, lat, lon), …]` with the main point first. Its label is "main" unless the record says otherwise.
- **Validation (`places/places/validate.py`, PlacesLayer.md §4.5):**
  - at most 6 extra points;
  - each point inside the area box;
  - each point within 3 km of the main point;
  - points at least 150 m apart and 150 m from the main point;
  - a non-empty `name` and `source`.
- **Spec:** update PlacesLayer.md §4.1 and §4.5.

### 1.2 Which places get points, and where they come from

**Candidates** are the confirmed places whose extent is long or spread out:
- **Beaches:** `praia-costa-nova`, `praia-da-barra`, `praia-sao-jacinto`, `praia-torreira`, `praia-vagueira`, `praia-de-mira`.
- **Neighbourhoods:** `costa-nova-palheiros`, `bairro-beira-mar-aveiro`, `canal-central-rossio-aveiro`.
- **Nature:** `bioria-percurso-salreu`, `ecomuseu-marinha-da-troncalhada`, `pateira-de-fermentelos`, `lagoa-de-mira`, `reserva-natural-dunas-sao-jacinto`.
- **Parks:** `parque-infante-d-pedro-aveiro`, `jardim-oudinot`.
- **Walking routes:** `art-nouveau-route-aveiro`, `agueda-street-art`, `ovar-azulejos`.

Skip draft and rejected records (`salinas-aveiro`, `praia-furadouro`, `cais-do-bico`).

**Points for places with an OSM way or relation (13 of them):**
1. Fetch the geometry in **one** Overpass query (`out geom`) for all their OSM ids. The places layer already uses Overpass (PlacesLayer.md). If there is no network, stop and ask.
2. Keep a place single-point if its whole geometry lies within 400 m of its main point. Say so in the report.
3. Otherwise choose points along the geometry: both ends, about every 400 m between them, and the geometry point nearest each bus or ferry stop within 600 m of the place. Get the stops from the engine's stop list (`places.network.nearest_stop_m` shows how the places layer reads it); read the engine, do not change it.
4. Drop points closer than 150 m to another, keep at most 6, and name each one.

**Places with only an OSM node, or no OSM element:** Torreira, Vagueira, Mira, Bioria, the Águeda street art and the Ovar azulejos.
- Add points only where the record's own sources give clear positions, for example the listed façades or murals of a route.
- Otherwise leave them single-point and list them under "Needs Pedro".

**Every new point is a proposal** (the `Issues.md` #2 lesson):
- Its `source` ends in "confirm".
- Write `places/review/reference_points.geojson`, one feature per point and per main point, coloured by place, for Pedro to check in geojson.io.
- Run `python -m places validate`.
- **Do not change any other field of any record.** Edit only through `model.dumps`, never with the generator scripts. Check `git diff --stat places/data` touches only the records you meant to change.

**Costa Nova check:** with its points, Praia da Costa Nova and the palheiros seafront must each have a point within 400 m of Canastra do Fidalgo (40.610345, -8.749324). If the geometry does not give one, stop and ask.

### 1.3 Where the points are used

**Rule for all consumers:** a visit uses one point. You arrive at it and leave from it. The point chosen is the one reached first from where you are. Walking along the place belongs to the visit. Do not route in at one point and out at another (record this as a decision; it is a possible later change).

| Consumer | Today | With points |
|---|---|---|
| Shortlist reachability (`places/places/shortlist.py`, `lib.engine.windows`) | one point per place | Pass every point as its own engine point (`"<id>#<k>"`). The place's window comes from the point with the earliest arrival: that point's `arrive` and `leave`. Keep the chosen point on the `Item` as `point`. |
| Shortlist clusters (`shortlist.py`, around `CLUSTER_SKIP_M`) | distance between main points | shortest distance between any two points of the two places |
| Validation, nearest stop (`validate.py`, which uses `places.network.nearest_stop_m`) | main point | nearest stop to any point |
| Pack travel hints (`planner/planner/pack.py`, `hints`) | main point to main point | from each candidate's chosen point (`Item.point`) to every point of the others, keeping the shortest per pair |
| Scheduler (`planner/planner/schedule.py`, `pt = (pid, place.lat, place.lon)` and the `_fill` candidate point) | main point | Try each point of the place with `Router.earliest` from the current position; use the earliest arrival. Leave from the same point. Timeline names say which point: "Praia da Costa Nova (south end, by the Largo Arrais Ançã bus stop)" for walks to and from it. |
| Free-time ideas (`planner/planner/freetime.py`) | crow-fly and walking distance to the main point | Use the nearest point by walking time. The idea shows that point's name. `walk_m` and `walk_min` are to that point. |
| Sun times, the area box, the place's map position | main point | unchanged |

- **Engine limits:** keep a place's points in one `windows` call where the engine accepts several points. Do not change `engine/`. With 6 extra points on about 15 places, the extra engine cost should be small: measure one shortlist call before and after and put both times in the report.
- **Places without points must give byte-identical results:**
  - golden tests: Planner.md §10.2, and the engine and places golden tests;
  - the pack for any request whose candidates have no points.

  A request whose candidates include a place with points may now have earlier arrivals and different slots, which is the point of the change. Say which eval requests' packs changed, for all backends.

### 1.4 Tests

- **Model:** a record with points loads, round-trips through `dumps` unchanged, and renders points after `[provenance]`. A record without points renders exactly as today.
- **Validation:** too many points, a point outside the box or over 3 km away, two points under 150 m apart, and a missing name or source are each rejected.
- **Shortlist:** a place with a point near the origin gets its arrival from that point, and `Item.point` names it.
- **Scheduler:** a visit to a place with points routes to the point reached first and leaves from it. The timeline names the point.
- **Free-time ideas:** the Costa Nova case (scripted draft of the 31 Oct plan, as in the `Changes.md` tests) now shows at least one of Praia da Costa Nova or the palheiros seafront, if the daylight and minimum-stay rules allow it. If they don't, the test must say which rule excluded it.
- **Byte identity:** the golden plan and a pack with no points are unchanged.

## 2. Change R: retry a cut-off local draft, and keep its text

### 2.1 Behaviour

- **Local backend, cut-off draft:** handle it as the Claude backends do (`Planner.md` §7). Count it as an attempt. Record `["schema"]` in `attempt_errors` with the message "the reply was cut off or was not valid JSON for the draft format". Retry with the usual retry message. After 3 failed attempts, use the fallback plan.
- **Leave these unchanged:** the other backend errors stay final and go straight to the fallback plan, as today. That covers `connection` (fatal), `timeout`, `http_status_<n>`, `context_overflow` and `no_structured_output`.
- **Retry turns** (as for Claude, `core.py`):
  - the assistant turn is `"{}"` when there is no parsed draft;
  - the user turn lists the `schema` error.

  Do not send the cut-off text back to the model: it may be long, and repeating it invites the same runaway.

### 2.2 Keeping the cut-off text

- `planner/planner/local.py`: when `finish_reason == "length"`, attach the generated text to the `BackendError` (for example `e.partial`), at most 4,000 characters.
- `planner/planner/llm.py`, `_parse_backend`:
  - for `e.kind == "max_tokens"`, return a `CallResult` with `stop_reason = "max_tokens"`, `parsed = None`, no `error`, and the partial text kept on the result (for example `raw`);
  - still record `backend_error` in usage.
- `planner/planner/core.py`: that attempt's entry in `debug.attempt_drafts` is `{"draft": null, "raw": <partial text>, "errors": [schema error]}`. It appears only with `debug=True`, as now.
- **Test page:** a failed attempt with `raw` shows "Cut off at the output limit" and the first 600 characters in a scrollable `<pre>`, with the full text under a `<details>`.
- **Docs:** Planner.md §7 and the planner README decisions (the local backend's error mapping).

### 2.3 Tests (mocked server, `tests/test_pl_local.py`)

- A `finish_reason=length` draft, then a valid one: a model plan with `attempts == 2`, `attempt_errors == [["schema"]]`, and with `debug=True` the partial text in `debug.attempt_drafts[0].raw`.
- Three cut-off drafts give the fallback plan with three `schema` entries.
- The retry's request carries `"{}"` as the assistant turn and none of the cut-off text.
- `timeout` and `http_status_500` still go straight to the fallback plan (existing tests stay green).
- The Claude backends are unchanged: existing backend tests stay green.

## 3. Constraints

- **No Claude API calls**, and never pass `--approved`. Live checks use the local backend only.
- **Do not touch:**
  - `engine/` and `converter/`;
  - any field of `places/data` records other than the new `[[points]]`;
  - `SYSTEM_PROMPT`, `NARRATIVE_SYSTEM`, the output schemas, the retry count, the validator's error codes, and the `selfhostedLLM` server config.
- **The prompt-fix session may still be active.** The three `test_day_block_agrees_with_the_validator` failures in `tests/test_pl_pack.py` are not yours. Do not add failures: run the planner, engine and places tests separately.
- **Python:** use `/usr/bin/python3`; the `python3` on PATH may be a venv without `anthropic`. Add no dependencies.
- **Memory (laptop, 7.5 GiB, no swap):** one live job at a time. Start the model server with `cd ~/selfhostedLLM && docker compose up -d`, and stop it when done. For more than a few plans, use the watchdog pattern in `~/selfhostedLLM/scripts/run_compact_bench.sh`.

## 4. Live check (local backend, one at a time)

| # | Request | What to record |
|---|---|---|
| 1 | The Costa Nova case: Porto Campanhã, Sat 31 Oct 2026, 09:30–21:00, home mode, architecture + beach + history, relaxed | outcome, attempts, time, the free-time entries and their ideas (expected: a Costa Nova seafront or beach point) |
| 2 | The same request in leave-by mode at 17:30, with the leave-by meaning now in the code (the first vehicle home leaves by X). This replaces the out-of-date row in `changes-report.md`. | outcome, attempts, time, the departure time of the first vehicle home, the last safe return |
| 3 | Eval request r01, the cut-off case | attempts, `attempt_errors`, whether a retry rescued it, the length of the kept text |

Run each through `POST /plan?debug=true` on `python -m planner.web --backend local`, or with `--debug` on the CLI.

## 5. Report

Append a section "CurrentChanges, <date>" to `planner/eval/changes-report.md`:
- **Points:**
  - places given points, with the count each;
  - places left single-point, and why;
  - the review GeoJSON path;
  - shortlist time before and after;
  - eval requests whose packs changed.
- **Retry:** whether r01 was rescued, and what the kept text shows about why the draft ran away (repeated stops, looping text, very long `why` fields).
- **The live-check table,** decisions recorded, and "Needs Pedro".

Then stop.

## 6. Order and acceptance

| Step | Change | Done when |
|---|---|---|
| 1 | R (cut-off retry) | §2.3 tests pass |
| 2 | P, record format and validation | §1.4 model and validation tests pass; `python -m places validate` is clean |
| 3 | P, data | points written for the §1.2 places; the review GeoJSON written; the Costa Nova check met |
| 4 | P, consumers | §1.4 shortlist, scheduler and free-time tests pass; the golden tests are unchanged |
| 5 | Live check and report | §4 and §5 done; the model server stopped |
