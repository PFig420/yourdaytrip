# WalkBarriers.md: base the engine's walking times on real OpenStreetMap paths

This is a build brief for a coding agent working in this repository. Read it fully, then:
- `ReachabilityEngine.md` (the walking model);
- `engine/reach/network.py` and `engine/reach/engine.py`;
- `mapdata/README.md` (the path cache, `MapPlan.md` §10).

Record every choice this brief leaves open, dated, in the engine section of `ReachabilityEngine.md` and in `mapdata/README.md`.

Written 2026-10-28. Revised the same day at Pedro's decision: apply the OSM path lengths to every walk the cache covers, automatically. Pedro's review only marks exceptions.

## 1. The problem and the decision

**The problem:**
- **The engine guesses every walk as a straight line.** It uses haversine × `walk_detour = 1.3`, at 1.2 m/s (`engine/config/engine.toml`). It never loads OSM, so railways, canals and the ria do not stop it. `engine/config/walk_barriers.geojson` has one barrier, the Barra channel.
- **The real paths already exist.** `mapdata` routes the same walks on OSM's footpath and street network, and the path cache (`data/paths/paths.json.gz`, built 2026-10-28) holds a real path for **5,609 of the 5,629** walk pairs the engine can make.
- **The gap is large in places.** 175 of those paths are much longer than the engine's estimate (at least 1.5 times, and at least 100 m more): 107 by 200 m or more, and 4 by 500 m or more. The worst is Bar 7 → Cacia station: 160 m for the engine, 975 m for real.
- **The risk:** a plan gives 2 minutes for a 12-minute walk and misses its train.

**Decision (Pedro):**
- **Each cached walk takes the longer of the two estimates.** For every walk the cache covers, the engine uses the longer of its own estimate and the OSM path. A walk can only get longer, never shorter. If OSM is wrong, for example a missing crossing, the planner becomes more cautious, never riskier.
- **The review lists exceptions only.** Pedro's review (§4) marks the cases where OSM is wrong (`osm-gap`) or where no reasonable walk exists (`blocked`).

**Why not barrier lines alone:** the engine has no street network of its own, so a barrier makes a walk *impossible*, not longer. A barrier along the railway at Cacia would cut the station off from places that really can reach it the long way round.

## 2. The data: `data/paths/walk_overrides.csv`

**Generator:** `python -m mapdata.overrides` writes it from the path cache and the review sheet. `mapdata.build` also runs it at the end of every build.

**Header lines** (comments starting with `#`): the cache build date, the feed hashes (`cp`, `av`) and the places hash the cache was built against, and the review sheet's hash.

**Columns:** `from_id`, `to_id`, `metres`, `kind`, `note`.
- One row per walk pair in the cache. The IDs are the cache's own: feed-prefixed stop IDs, place point IDs (`<place>` or `<place>#<k>`) and origin IDs. A row applies in both directions.
- `kind` is `osm` (`metres` = the OSM path's length), `osm-gap` (no `metres`: keep the engine's estimate) or `blocked` (no `metres`: refuse the walk).
- **Pairs the cache has no route for** (20 today) get no row, so they keep the engine's estimate. They are listed in the generator's output, and Pedro may mark them `blocked`.

**Deterministic:** sorted by key, so a re-run on the same inputs gives the same bytes. It is derived from OSM (ODbL); add it to the `Issues.md` #1 ODbL item next to `data/paths/`.

## 3. Engine change

This brief **allows changes in `engine/`**, limited to this section and its tests. The feeds and their pinned hashes do not change.

**1. Config:** add `walk_overrides = "data/paths/walk_overrides.csv"` to `engine/config/engine.toml`, a path relative to the repo root. A missing file means today's behaviour.

**2. Load once:** `Network` loads the file into a dict keyed by the unordered ID pair.
- **Stale check:** if the header's feed hashes differ from the feeds the engine loaded, **ignore the whole file** and add one engine warning ("walking overrides were built for other feeds; rebuild with python -m mapdata.build"). The stop IDs could have changed.
- IDs the engine does not know are ignored silently. Places may change between builds; stale place rows are harmless.

**3. Where it applies.** The IDs are already at these calls; pass them through where only coordinates are passed today.
- **Access and egress,** point ↔ stop: `Network.near`, called from `engine.py` with the point tuple.
- **Point to point:** `Network.walk_between_points`.
- **Stop to stop transfers:** the footpaths built in `Network.__init__`.

**4. Rules for a pair with a row:**
- `osm`: walking seconds = **max**(the engine's estimate, `ceil(metres / walk_speed_mps)`). There is no detour factor on the OSM length, because it is the route itself. When the OSM time is the larger one, the walk leg's reported metres are the OSM metres; otherwise they stay as today.
- `osm-gap`: the engine's estimate, as today.
- `blocked`: the walk is not offered, as if a barrier crossed it.

**5. Limits:** distance limits (`access_max_m`, `access_max_limit_m`, `transfer_max_m`, `max_walk_m`) still compare against the straight-line distance, as today. An override changes the time, not which pairs are considered.

**6. Engine health:** `engine.health()` reports the overrides file's row count, build date and hash, or that it is missing or stale.

## 4. Pedro's exceptions review (optional, any time)

`data/paths/underestimates-review.csv` lists the 175 flagged walks, biggest gap first, with the columns `excess_m`, `engine_m`, `path_m`, `from_id`, `to_id`, `from`, `to`, `verdict` and `note`. The paths are red in `data/paths/review.geojson`, which opens in geojson.io.

Fill `verdict` only where needed:
- **`osm-gap`:** OSM is missing a crossing or path, and the engine's estimate is about right.
- **`blocked`:** no reasonable walk exists.
- **Empty:** the OSM length applies, which is the default.

**The build does not wait for this review.** After editing the sheet, run `python -m mapdata.overrides`; the engine picks the change up at its next load.

**Carry-over on rebuild:** when `mapdata.build` runs again, it rewrites the review sheet from the new cache's flags and carries existing verdicts and notes over by key. A reviewed row whose flag disappeared keeps its verdict, with `note` prefixed "flag gone:".

## 5. Tests

- **No file:** everything is identical, including the engine and planner golden tests.
- **A longer OSM path:** it lengthens access, point-to-point and transfer walks, in both directions, and the leg reports the OSM metres.
- **A shorter OSM path** changes nothing.
- **`osm-gap`** keeps the estimate; **`blocked`** removes the walk.
- **Stale feed hashes:** the file is ignored, with one warning and health showing "stale".
- **Generator:** one row per cached pair; deterministic output; verdict mapping; no row for no-route pairs; verdict carry-over on a rebuild of the review sheet.
- **With the real cache:**
  - The engine and planner golden tests. Where a golden journey changes, say exactly why, naming the pair and both lengths, and update the expected values only for that reason.
  - **Cacia:** a journey from Bar 7 that takes the train at Cacia now allows at least the 975 m walk.
  - **Escola nº1 A ↔ B:** the 44 m transfer is now about 657 m.

## 6. Measuring the effect (report it)

- **How many of the 5,609 cached pairs get a longer time,** and by how much (median, p90, max).
- **The packs for the 70 eval and holdout requests:** how many change (shortlist arrivals, slots, travel hints), for the full and the compact pack. This is an intended data change for every backend.
- **Plans:** run the planner golden tests and the fallback plans for eval requests r01, r03, r06 and r26 (`use_llm=False`, no model needed). Report any plan that changed and why.

## 7. Constraints

- **Change only:** what §3 lists in `engine/`, plus `mapdata/overrides.py`, the hook in `mapdata/build.py` (the generator and the review-sheet carry-over), `engine.toml`, and the tests.
- **Do not touch:** the feeds, `converter/`, `places/data`, the planner's scheduler and validator, or the prompts.
- **No Claude API calls.** The model server is not needed. Use `/usr/bin/python3`.
- **The prompt-fix session's** three failing `test_day_block_agrees_with_the_validator` tests are not yours. Run the engine, planner, places and mapdata tests separately.

## 8. Report

Write `engine/walk-overrides-report.md`:
- the rows written, by kind;
- the §6 measurements;
- the golden changes and why;
- the Cacia and Escola nº1 checks;
- the decisions recorded.

Then stop.
