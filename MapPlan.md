# MapPlan.md: a map overview of the planned day

This is a build brief for a coding agent working in this repository. Read it fully before starting. Then read `Planner.md` §3.2 and §9, the "Decisions" in `planner/README.md` (including the `Changes.md` and `CurrentChanges.md` entries of 2026-10-27), and `engine/reach/web/static/map.html`, the engine's existing Leaflet test map. Do the work in the order of §7. Record every choice this brief leaves open in `planner/README.md` under "Decisions", dated.

Written 2026-10-27 at Pedro's request: the test page (`python -m planner.web --backend local`, http://127.0.0.1:8300/) should show the planned day on a map.

**Status:**
- **Phase 1 (§1–§9) is built** (`planner/eval/map-report.md`): rides are drawn stop to stop and walks as straight lines.
- **Phase 2 (§10) is built** (map report, "Phase 2" and the 2026-10-28 rebuild): rides and walks follow OSM roads, railways and footpaths. Bus rides take the *shortest* drivable path between consecutive stops.
- **Phase 3 (§11) is the current work.** Pedro's decision: buses should follow their *factual* route where OSM has it mapped. Do only §11. §1–§10 stay as the record of earlier phases, and §11 says which of their parts change.

## 1. Goal

Next to the timeline, a map shows:
- where the day starts and ends;
- each visit, numbered in order;
- each bus, train or ferry ride along its stops;
- each walk;
- free time, with its optional ideas nearby.

Clicking a timeline row highlights it on the map, and the other way round.

The map is a picture of the checked plan. It adds no times, routes or checks: every position comes from the engine's stops or the places' records.

## 2. What the data can and cannot draw (facts, checked 2026-10-27)

| Element | Source of positions | What gets drawn |
|---|---|---|
| Rides | Neither feed has `shapes.txt` (`data/gtfs/cp.zip`, `data/gtfs/aveiro.zip`). The engine has every trip's stop sequence: `engine.feeds[i].trips[trip_id].times`, with stop positions in `feeds[i].stops`. | A line from stop to stop through every stop the trip serves between boarding and alighting. It follows the route roughly, not the road or track. |
| Walks | The engine walks in a straight line with a detour factor (`reach/network.py`, `WalkParams.detour = 1.3`). There is no street path. | A straight dashed line between the two ends. The legend says "walking direction, not the street route". |
| Visits | The place record, or since `CurrentChanges.md` P the reference point the visit used (`Result.visit_points`) | A numbered marker at that point |
| Free-time ideas | The idea's place and the point it was measured to (`freetime.py`) | A small hollow marker, in a layer that can be switched off |
| Origin | `PlanRequest.origin` | A start/end marker |

**What is missing:** the timeline entries do not carry the stop, point or place IDs the positions come from. The scheduler has them in `Result.journeys` (engine legs with `board_stop_id`, `alight_stop_id`, `from_id`, `to_id`) and `Result.visit_points`. §3.1 carries them through.

## 3. Data changes (planner only)

### 3.1 IDs on timeline entries (additive, all backends)

In `planner/planner/models.py`, add these optional fields, all defaulting to `""`. Fill them where the entries are built (`schedule._legs`, the visit and free-time entries, and `freetime.suggest`):

| Model | New fields | Value |
|---|---|---|
| `Ride` | `feed`, `board_stop_id`, `alight_stop_id` | from the engine's `RideLeg` (IDs are feed-prefixed, as the engine has them) |
| `Walk` | `from_id`, `to_id` | from the engine's `WalkLeg`: a stop ID, a place or point ID (`<id>` or `<id>#<k>`), or the origin's ID |
| `Visit` | `point_id` | the point the visit used (`<id>` or `<id>#<k>`) |
| `FreeTime` | `point_id` | the point where the free time is spent |
| free-time idea dicts | `point_id` | the point the idea's walk was measured to |

- These are output-only. The pack, the prompts, the validator and the narrative payload do not change. Check that `narrative.payload` output is byte-identical.
- They also appear in `plan_b.next_journey` entries, which is fine.
- Update Planner.md §3.2.

### 3.2 The map as GeoJSON

**New module `planner/planner/mapview.py`:** `build(plan, engine, lib) -> dict` returns a GeoJSON `FeatureCollection` built from the plan's timeline and request only. It uses no scheduler state, so it also works on a plan read back from JSON. Coordinates are `[lon, lat]`.

**One resolver** turns an ID into a position:
- a feed-prefixed stop ID → `engine` stops;
- `<place>` or `<place>#<k>` → the place's `all_points()`;
- the origin's ID → `PlanRequest.origin`.

An ID it cannot resolve is skipped. Add a warning to the collection's `properties.warnings`, and never invent a position.

**Features** (each with `properties.kind`, and `properties.step` = the timeline index it belongs to, where it has one):

| `kind` | Geometry | Properties |
|---|---|---|
| `origin` | Point | `name`; `role`: `"start and end"` |
| `visit` | Point at its `point_id` | `n` (1, 2, 3… in timeline order), `name`, `start`, `end` (`HH:MM`), `meal`, `entry`, `inserted`, `reason`, `point_name` (the point's label, empty for the main point) |
| `ride` | LineString through the trip's stops from boarding to alighting, inclusive | `mode` (`rail`, `bus`, `ferry`, as the engine gives it), `route_short`, `headsign`, `board_stop`, `board_time`, `alight_stop`, `alight_time`, `stops` (the count drawn) |
| `stop` | Point at each boarding and alighting stop | `name`, `role` (`board` or `alight`), `time` |
| `walk` | LineString of its two ends | `metres`, `depart`, `arrive`, `to` |
| `free_time` | Point at its `point_id` | `from`, `until`, `reason` |
| `idea` | Point at its `point_id` | `name`, `kind`, `walk_m`, `walk_min`, `minutes_there`, `open_until`, and `step` = the free time's index |

- **Ride lines:** find the trip by `trip_id` in `engine.feeds`. Take its stops from `board_stop_id` to `alight_stop_id` in sequence order. If a trip serves a stop twice (loops, such as the line 15 circulars), use the occurrence whose time matches the ride's board or alight time.
- **Waits** get no feature: they happen at the previous feature's position.
- **Collection properties:**
  - `bounds_all`: the bounds of everything;
  - `bounds_destination`: the bounds of the visits and the legs between them, excluding the first ride out and the last ride home. On a Porto day trip that is the Aveiro area; it may equal `bounds_all`.
  - `warnings`.
- **Read the engine, do not change it.** `engine.feeds`, `Feed.trips`, `Trip.times` and `Feed.stops` are read-only here. If what you need is not reachable without changing `engine/`, stop and ask.

### 3.3 Where it is exposed

- **HTTP:** `POST /plan?map=true` adds `"map": <FeatureCollection>` to the response, under `DayPlan.map`, which defaults to `null`. Without the flag the answer is unchanged. The test page always sends it.
- **CLI:** `planner plan … --geojson PATH` writes the collection to a file, to open in geojson.io.
- **Eval:** unchanged (no map in eval logs).
- A `no_plan` result has `map = null`.

## 4. The map on the test page

In `planner/planner/web/static/index.html`:

### 4.1 Library and tiles

- **Leaflet 1.9.4** from cdnjs, the same pinned URLs as `engine/reach/web/static/map.html`.
- **Tiles:**
  - The URL and attribution come from `/meta`, so they can change without editing the page.
  - New `[web] tile_url` and `[web] tile_attribution` in `planner/config.toml`, defaulting to the OSM standard tiles and "© OpenStreetMap contributors". `/meta` returns them.
  - The OSM standard tile server is fine for this local test page at low volume. It is not a production tile source. Add an unchecked item to the `Issues.md` #1 launch conditions: "map tiles from a provider whose terms allow the app's traffic".
- **The page still works without network:** if Leaflet fails to load, the map panel says so and the timeline renders as now.

### 4.2 Layout

- **Desktop (wider than 760 px):** the map sits above the plan in the right column, 360–420 px tall.
- **Narrow screens:** the map stacks above the timeline, full width, about 300 px tall.
- **No page-level horizontal scroll** at 360 px width.
- The map shows only after a plan with a `map` arrives, and the panel is hidden otherwise.
- Keep the page's colour tokens for everything the page draws; the tiles stay as they are.

### 4.3 Drawing

| Feature | Style |
|---|---|
| `origin` | a distinct start/end marker |
| `visit` | a filled circle marker with its number `n`. Food places get a different fill from sights; a meal shows in the popup. |
| `ride` | a solid line, one colour per mode (rail, bus, ferry), with a legend entry for each mode present |
| `stop` | small dots in the ride's colour |
| `walk` | a thin dashed line |
| `free_time` | a marker with a clock-like ring |
| `idea` | a small hollow marker, in its own layer "Ideas nearby (optional)", on by default and switchable |

- **Popups** show the feature's properties in plain words, for example "Bus 36 towards Largo Arrais Ançã – Costa Nova, 11:34 → 12:15, 8 stops" or "2 · Canastra do Fidalgo, lunch 12:19–13:49".
- **A legend** names the modes present and says "Lines join stops in order; walks are straight lines, not the street route."
- **Two view buttons:**
  - **Whole day** fits `bounds_all`.
  - **Destination** fits `bounds_destination`.

  The plan opens on Destination when it differs from Whole day, because the visits are tiny on a Porto–Aveiro overview.
- Show the collection's `warnings`, if any, under the map.

### 4.4 Linking map and timeline

- Each timeline row knows its index (the same `step` as the features).
- **Clicking a row** highlights its features (thicker line, raised marker), opens the visit's or ride's popup, and pans to it without changing the zoom more than needed.
- **Clicking a feature** highlights its row and scrolls it into view.
- Hovering is optional. Keyboard focus on a row does the same as clicking.

## 5. Tests

### 5.1 Python (`planner/tests/test_pl_map.py`, offline, real engine and places)

- On the golden scripted plan (Planner.md §10.2):
  - there is one `visit` feature per visit, numbered 1–3, at the right points: the seafront at its south-end point, 20 m from Canastra;
  - one `ride` per ride, each starting at its boarding stop's position and ending at its alighting stop's, with `stops` equal to the trip's stop count between them;
  - one `walk` per walk;
  - one `origin`;
  - every coordinate is inside the bounding box of all engine stops (`engine.feeds[*].stops`), widened by 0.05°. The places area box (`places/config/places.toml`) is not used: it stops at latitude 40.90 and leaves Porto out;
  - `step` matches the timeline index.
- A free-time plan (the Costa Nova case from the `Changes.md` tests) has a `free_time` feature and one `idea` per suggestion.
- A circular line, line 15 if a plan uses it, or a synthetic trip, draws only the stops between boarding and alighting.
- `/plan` without `map=true` has no map; with it, the map exists. A `no_plan` result has `map: null`.
- The new timeline ID fields are filled for every ride, walk, visit and free time in the golden plan.
- `narrative.payload` is byte-identical before and after §3.1.
- `--geojson` writes a valid FeatureCollection.

### 5.2 The page

- `node --check` on the page's script. VS Code's bundled Node is at `~/.vscode-server/bin/*/node`.
- A Node harness with a minimal DOM and a stub `L` object: render the golden plan's JSON and check that every feature kind is drawn the expected number of times, and that the row ↔ feature mapping covers every row with a feature.

The visual check is Pedro's (§6).

## 6. Samples for Pedro

- Write `planner/eval/map-sample-golden.geojson` (the golden plan) and `planner/eval/map-sample-costa-nova.geojson` (the free-time case) with `--geojson`, for Pedro to open in geojson.io.
- In the report, list what Pedro should look at on the test page: the Destination view, the bus 36 line, the 20 m walk to the seafront, the idea markers, and the row ↔ map highlight.

## 7. Order and acceptance

| Step | Work | Done when |
|---|---|---|
| 1 | §3.1 IDs on the timeline | Filled on the golden plan; the narrative payload is unchanged; all existing tests still pass |
| 2 | §3.2 `mapview.build` | §5.1 tests pass |
| 3 | §3.3 HTTP flag, `DayPlan.map`, CLI `--geojson` | Their §5.1 tests pass |
| 4 | §4 page | §5.2 checks pass; `/meta` serves the tile settings |
| 5 | §6 samples, docs, report | Both samples written; Planner.md §3.2 and §9.2–9.3, the planner README "Run" and "Decisions", and the `Issues.md` #1 item updated |

## 8. Constraints

- **Do not touch:**
  - `engine/` and `converter/` (read `engine.feeds` only), and `places/` (read records and points only);
  - the pack, `SYSTEM_PROMPT`, `NARRATIVE_SYSTEM`, the output schemas, the scheduler's timing, the validator, and the `selfhostedLLM` config.
- **No Claude API calls.** Nothing here needs a model: test with scripted drafts and `use_llm=False`. A live local-model plan through the page is optional, at most one, and the model server is stopped afterwards.
- **No new Python dependencies.** On the page, Leaflet from cdnjs is the only external script.
- **The prompt-fix session's three failing `test_day_block_agrees_with_the_validator` tests** are not yours. Do not add failures: run the planner, engine and places tests separately with `/usr/bin/python3`.
- **The web server stays bound to 127.0.0.1** (`Issues.md` #1).

## 9. Report

Write `planner/eval/map-report.md`:
- what was built;
- the test results;
- the two sample files;
- any IDs that could not be resolved, and why;
- the decisions recorded;
- what Pedro should check on the page.

Then stop.

## 10. Phase 2: draw the roads, railways and paths actually taken

### 10.1 Goal and principle

- **Every ride follows its road, railway or ferry route.** A bus follows the streets between its stops, a train follows the track and a ferry follows its crossing.
- **Every walk follows footpaths and streets.** Highlight the path of each leg, and of the leg the visitor selects (§10.6).
- **The paths are for display only.** The engine's journeys, times and walking distances stay as they are. A drawn path never changes a time, a check or a plan.
- Where the drawn path and the engine disagree a lot, the map says so (§10.6) and the build report lists it (§10.7) for Pedro. **Do not change `engine/` to fix it.**

### 10.2 Facts this design rests on (checked 2026-10-27)

- **No route shapes:**
  - neither feed has `shapes.txt`;
  - CP's feed is the official one and must not be edited;
  - the Aveiro feed's hash is pinned in `engine/config/feeds.toml`.

  So shapes do not go into the GTFS files.
- **The engine walks in straight lines** (haversine × `walk_detour = 1.3`, `engine/config/engine.toml`). It has no street network.
- **What has to be drawn, in the Porto–Aveiro corridor** (latitude 40.40–41.20, longitude −8.82 to −8.40):
  - **CP:** 78 stops and 196 different consecutive stop pairs. Median 2.2 km; the longest is 54 km, a non-stop intercity.
  - **Aveiro feed:** 298 stops and 450 pairs. Median 1.2 km, longest 16 km.
  - **Places:** 125 points on non-rejected records, counting the reference points.
- **Walks the engine can produce:**
  - between a stop and a place point or origin, up to `access_max_limit_m = 2000`;
  - between two stops, up to `transfer_max_m = 400`;
  - between two places, up to `[schedule] walk_between_m = 2000` in `planner/config.toml`.
- **Memory:** the laptop has 7.5 GiB and no swap. With the model server up, about 1.5 GiB is free. The offline build therefore runs **with the model server stopped**. The runtime may add at most about 100 MB to the web server.

### 10.3 Design: a precomputed path cache from OpenStreetMap

**Decision: build the paths once, offline, from OSM data, and store them as a display cache that the planner reads at runtime.**

Rejected alternatives (record them in the README decisions):
- **A self-hosted routing server (OSRM, Valhalla, GraphHopper):** a Portugal graph needs more memory than this laptop has free. It may suit production hardware later.
- **Public routing APIs:** usage limits, a network dependency at plan time, and terms that rule out an app.
- **Operator shapes:** none are published.

Keep the runtime behind a small interface (`paths.ride(...)`, `paths.walk(...)`), so a routing server can replace the cache later without touching `mapview.py`.

**New top-level package `mapdata/`** (offline tools, its own tests):

```
mapdata/
  README.md            how to build, what the cache holds, the OSM date, the ODbL note
  build.py             python -m mapdata.build: downloads OSM data, routes every needed pair, writes the cache
  graph.py             a small graph from OSM ways and A* search (heapq; no new dependencies)
  osm/                 raw Overpass responses, one file per tile and layer (not committed)
  tests/
data/paths/paths.json.gz      the cache (OSM-derived; ODbL, see 10.8)
data/paths/review.geojson     every bus and rail pattern drawn whole, and every flagged path, for Pedro
```

### 10.4 Building the cache (`python -m mapdata.build`)

**1. Download OSM data with Overpass**, in tiles of about 0.1° × 0.1° with a 3 km overlap, over the corridor box in §10.2.
- **Three layers:**
  - **roads** for buses: `highway` of `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `service`, `living_street` and their `_link`s, plus `busway` and `bus_guideway`. Respect `oneway` and `oneway:bus`.
  - **railways:** `railway=rail` and `railway=light_rail`, without `service=yard` or `siding`. Use one corridor-wide query for railways, since there are few of them.
  - **foot:** every `highway` a person may walk (`footway`, `path`, `pedestrian`, `steps`, `cycleway`, `track` and the road classes), excluding `motorway`, `trunk` and their links, and anything with `foot=no` or `access=private` without `foot=yes`. Include `route=ferry` ways for the ferry layer.
- **Download etiquette:** cache every response under `mapdata/osm/` with its date, never download a tile twice in one build, and pause between queries (Overpass fair use).
- **If Overpass cannot be reached, stop and ask.**

**2. Bus and ferry segments:** for each consecutive stop pair of every trip in the corridor (both feeds, mode from the route):
- **Bus:** snap each stop to the nearest road edge within 150 m, then A* on the road graph by length.
- **Rail:** snap each station to the nearest rail edge within 300 m, then A* on the rail graph.
- **Ferry:** use the OSM `route=ferry` way that joins the two stops' areas (the Forte da Barra ↔ São Jacinto crossing). If none is found, draw the straight line and flag it.
- Store each pair once, keyed by direction (`<stop_a>|<stop_b>|<mode>`).

**3. Walks:** for every pair in §10.2's list, snap both ends to the foot graph within 150 m, then A* by length. Walking routes are the same in both directions, so store one direction and reverse it on lookup. Keys:
- stop IDs (feed-prefixed);
- place point IDs (`<place>` or `<place>#<k>`, the same IDs the timeline carries since §3.1);
- the three `ORIGINS` IDs.

**4. Sanity checks.** Flag a path, keep it, and list it in the report when:
- a snap is further than its limit;
- no route is found (store no path, so the map falls back to a straight line);
- a ride path is more than 2.5 times its straight-line length (a likely wrong turn or a missing OSM link);
- a walk path is more than 1.5 times the engine's walking distance (straight line × 1.3). This means the engine underestimates that walk, for example around water or a railway with no crossing. **Do not change the engine**; list these for Pedro.

**5. Write the cache:**
- **Format:** `data/paths/paths.json.gz` is `{"meta": {...}, "ride": {key: polyline}, "walk": {key: polyline}}`.
  - Polylines use the encoded-polyline format at precision 5, which keeps the file small and is decoded only for the legs drawn.
  - `meta` holds the OSM download date, the build date, the box, the feed hashes and place-data date it was built against, the counts, and the flagged keys with their reason.
- **Review file:** write `data/paths/review.geojson`.

**6. Size and memory:**
- Report the file size and the build's peak memory.
- Target under 15 MB for the file and under 2 GB peak for the build. Process tile by tile if needed.
- The build must not run while the model server is up (§10.2).

### 10.5 Using the cache (`planner/planner/paths.py` and `mapview.py`)

- **Loading:** `paths.load(path)` loads the index lazily on the first map request and keeps it for the server's lifetime. It decodes only the polylines a plan needs.
- **Missing cache:** if the cache file is missing, or was built for different feed hashes, the map still works with straight lines (Phase 1) and adds a collection warning saying so.
- **Rides:** for each ride, concatenate the cached paths of its consecutive stop pairs from boarding to alighting, the same stops as in §3.2. A missing pair falls back to the straight segment for that pair only.
- **Walks:** look up the walk by its `from_id` and `to_id`, reversed if needed. Walks between arbitrary coordinates (`--from lat,lon` on the CLI) have no cached path and stay straight.
- **New feature property `path`:** `"osm"` when every part came from the cache, `"partial"` when some segments fell back to straight lines, `"straight"` when none did. Walks also get `path_m`, the drawn path's length.
- **Ideas (`idea` features):** their walk from the free-time point is drawn as a dashed line inside the "Ideas nearby" layer, from the cache when available.

### 10.6 Page changes (`index.html`)

| Feature | Phase 1 | Phase 2 |
|---|---|---|
| Ride | stop-to-stop line | the cached path; same mode colours |
| Walk | straight dashed line | the cached footpath, dashed |
| A part with no cached path | (all straight) | a dotted grey line, so a guess never looks like a real route |
| Selected leg | thicker line | **highlighted path:** the selected leg drawn on top in a stronger stroke with a light outline (casing), the rest of the day dimmed to about 40 % opacity until the selection is cleared (click again, or press Escape) |

- **Legend:** "Buses follow the roads and trains the railway in OpenStreetMap; walks follow footpaths and streets. Dotted grey: no path found, straight line shown. Times come from the timetable and the planner's walking estimate."
- **Walk popups** keep the timeline's distance (the engine's). When `path_m` differs by more than 25 %, add "the street route is about N m".
- **Attribution:** "© OpenStreetMap contributors" already covers the tiles. Add "paths derived from OpenStreetMap" to the map's attribution control.

### 10.7 Tests and report

- **`mapdata/tests`:**
  - A* on a small hand-made graph;
  - snapping limits;
  - polyline encoding round-trips;
  - `oneway` is respected for buses and not for walks;
  - a missing route gives no path and a flag.
- **`planner/tests/test_pl_map.py`, with a small fixture cache, not the real file:**
  - a ride uses the cached pairs in order and falls back per pair;
  - a reversed walk key is found;
  - `path` is `"osm"`, `"partial"` or `"straight"` as expected;
  - no cache means straight lines and one warning;
  - a cache built for other feed hashes is ignored with a warning.
- **With the real cache** (skip with a message if it isn't built): every ride and walk in the golden plan and the Costa Nova case has `path == "osm"`, and the bus 36 line stays within 50 m of the road it runs on. Spot-check 3 points against the OSM ways.
- **Page:** extend the Node harness for the dimming and casing on selection and the dotted fallback style.
- **Report:** append "Phase 2" to `planner/eval/map-report.md`:
  - counts of ride and walk paths, and of flagged paths, by reason;
  - the file size, the build time and the peak memory;
  - **the list of walks the engine likely underestimates** (§10.4 step 4), as a table of pair, engine metres and path metres;
  - the samples regenerated (`map-sample-golden.geojson`, `map-sample-costa-nova.geojson`);
  - what Pedro should check, and where (`data/paths/review.geojson`, the page).

### 10.8 Constraints for Phase 2

- **Do not touch** `engine/`, `converter/`, the GTFS files, `places/data`, the pack, the prompts, the scheduler's timing or the validator.
- **No new Python dependencies.** The build uses `httpx` (installed) for Overpass, plus `heapq`, `json` and `gzip`.
- **Licence:** `data/paths/` is a database derived from OSM. Add it to the ODbL item of the `Issues.md` #1 launch conditions, next to the stop and place positions: keep it identifiable and ready to be offered under the ODbL.
- **Rebuild trigger:** `mapdata/README.md` says when to rebuild: a feed hash changes, place points change, or every 3 months for OSM changes. The runtime warning in §10.5 catches a stale cache.
- **The prompt-fix session's three failing tests** are not yours. Run the planner, engine, places and `mapdata` tests separately with `/usr/bin/python3`.
- **Model server:** stop it before the build (`cd ~/selfhostedLLM && docker compose stop`). Don't restart it unless a live check needs it.

### 10.9 Order and acceptance

| Step | Work | Done when |
|---|---|---|
| 1 | `mapdata/graph.py`, polyline encoding, snapping | `mapdata/tests` pass |
| 2 | `mapdata/build.py`: download, route, write | The cache and review file are written; size and memory within §10.4 step 6; flags listed |
| 3 | `planner/planner/paths.py` and `mapview.py` use the cache | §10.7 planner tests pass, including with the real cache |
| 4 | Page: paths, fallback style, highlighted selection | Node harness passes; `node --check` is clean |
| 5 | Docs and report | `mapdata/README.md`, the planner README decisions, Planner.md §9.3, `Issues.md` #1 and the Phase 2 report done; samples regenerated |

## 11. Phase 3: buses follow their factual route where OSM maps it

### 11.1 Goal and principle

- **Today:** a bus ride is drawn as the shortest drivable path between each pair of consecutive stops (§10.4 step 2). The stops and their order are factual, from the timetable; the streets between them are a guess.
- **Phase 3:** use the operator's route as mapped in OpenStreetMap (a `type=route` relation with `route=bus`) for every stop pair where that mapping is trustworthy. Keep the shortest path everywhere else.
- **Still for display only.** Times, journeys, walking estimates and checks do not change.
- **Rail and the ferry stay as they are.** Tracks leave little choice, so the shortest rail path is effectively the real route, and the ferry already uses its mapped crossing. Walks stay shortest paths.

### 11.2 What OSM has (measured 2026-10-28)

Overpass found 83 `route=bus` or `route=ferry` relations in the places area box, raw tags saved by the check. Matched to the Aveiro feed's lines by operator or network and `ref`:

| Feed line | OSM relations | Feed stops within 40 m of the relation's ways | Notes |
|---|---|---|---|
| Aveirobus 1 | 2 (both directions) | 26 of 26, 100 % | |
| Aveirobus 2 | 2 | 21 of 30, 70 % | Monte Cacia, R. Dr. Mário Sacramento B and Rotunda da Pedra Moura are off it |
| Aveirobus 3 | 2 | 26 of 29, 90 % | predates the Eucalipto A change (`Issues.md` #3) |
| Aveirobus 4 | 2 | 8 of 17, 47 % | |
| Aveirobus 5 | 1 (one direction) | 13 of 23, 57 % | |
| Aveirobus 6 | 2 | 24 of 24, 100 % | |
| Aveirobus 7 | 1 | 1 of 28, 4 % | only a 6-way fragment is mapped |
| Aveirobus 8 | 1 | 9 of 30, 30 % | |
| Aveirobus 10 | 1 | 24 of 26, 92 % | |
| Aveirobus 11 | 3 | 7 of 10, 70 % | the Forca tunnel and terminal stops are off it |
| Aveirobus 12 | 2 | 11 of 11, 100 % | |
| Aveirobus 15 Azul / 15 Verde | 1 each ("Linha Centro: Azul / Verde") | 17 of 21, 81 % / 19 of 23, 83 % | |
| Aveirobus ferry | 2 (`route=ferry`, L13) | 2 of 2 | the ferry keeps §10 behaviour |
| Busway 16 | 2 | 13 of 21, 62 % | |
| Busway 17 | 2 | 27 of 27, 100 % | |
| Busway 32 | 1 | 11 of 11, 100 % | today's shortest paths are only 15 % on it: check this line first |
| Busway 36 (Costa Nova) | 1, old operator (Transdev "5951 Aveiro => Costa Nova") | 8 of 14, 57 % | probably outdated |
| Busway 37 | 1 | 22 of 22, 100 % | |
| Busway 18, 19, 20, 25, 33, 34, 35 | none | none | stay shortest-path |

**How far today's drawing is from the mapped routes:** on the ten best-mapped lines (1, 3, 6, 10, 12, 15 Azul, 15 Verde, 17, 32, 37):
- 65–99 % of the cached path points lie within 30 m of the relation, line by line; about 80 % overall;
- **72 of their 212 stop-pair segments are mostly on other streets** (less than 80 % of points on the relation).

**Matching rules for the build:**
- The tags are inconsistent, and that is expected. AveiroBus uses `ref=L<n>`, and the centre lines have no `ref`, only "Linha Centro: Azul/Verde" in `name`. Busway relations use `operator=BusWay` or `Busway`, or only `network=Busway`.
- Match on operator or network **and** `ref` (or the name for 15 Azul/Verde) as above, and never on `ref` alone. Several other operators reuse numbers: Unir, Rede Expressos 16, CIRA's "BusWay 3/4", Andante.
- Record the line → relation mapping in `mapdata/README.md`.

### 11.3 Design: route relations first, shortest path as the fallback, decided per stop pair

**1. Download (one Overpass query, cached under `mapdata/osm/` like §10.4):**
- the matched relations with their members (`out body`);
- the member ways' geometry (`way(r); out geom`).

The public `overpass-api.de` returned 504 during the check, and `overpass.kumi.systems` answered. Try the main endpoint, then that mirror, with pauses (fair use). If neither answers, stop and ask.

**2. Assemble each relation into one ordered line:**
- Join the member ways in member order, taking roles `""`, `forward` and `backward`. Skip `platform` and `stop` members.
- Flip a way when its end does not meet the previous way's end.
- **A gap:** if two consecutive ways do not share an end node, the relation is split into pieces at the gap. A stop pair is taken only from a single unbroken piece.
- **Roundabouts:** use only the part of a closed way between the entry and exit nodes, in the direction of travel.

**3. For each consecutive stop pair of each trip on a matched line, use the relation when all of these hold:**
- both stops project onto the same unbroken piece within **40 m**;
- the projection of the second stop comes **after** the first along the piece, which is the direction of travel;
- the piece's length between the two projections is **at most 2 ×** the shortest-path length from §10. This catches outdated or wrongly ordered relations.
- When a line has several relations (both directions, variants such as "via Crasto", or weekdays and Sundays), take the one that passes the tests with the shortest length between the two stops.

**Otherwise keep the §10 shortest path** for that pair. A line can be partly factual and partly shortest-path.

**4. Cache format:**
- Store the chosen path in the same `ride` key (`<stop_a>|<stop_b>|bus`).
- Add `meta.ride_source`, mapping each key to `"relation:<osm id>"` or `"shortest"`.
- `meta.relations` records each relation's ID, line, name and OSM `timestamp`, so stale mappings can be judged.

**5. Flags** (in `meta.flagged`, drawn red in `review.geojson`, as in §10):
- `relation-rejected` with the reason (`far stop`, `wrong direction`, `gap`, `too long`) for each pair of a matched line that fell back;
- `relation-differs` where the relation and the shortest path disagree for more than 30 % of their length. These are the pairs where the map visibly changes; Pedro may want to look at a few.

### 11.4 Planner and page changes

- `paths.py` and `mapview.py` read `meta.ride_source`. A ride feature gets `route_source`:
  - `"relation"`: every pair from relations;
  - `"mixed"`;
  - `"shortest"`.

  Keep `path` (`osm`, `partial`, `straight`) as in §10.5.
- **Ride popups:** add one line:
  - `relation`: "Route: as mapped in OpenStreetMap";
  - `mixed`: "Route: partly as mapped in OpenStreetMap, partly the shortest road path";
  - `shortest`: "Route: shortest road path between stops (this line is not mapped in OpenStreetMap)".
- **Legend:** replace "Buses follow the roads" with "Buses follow their route as mapped in OpenStreetMap where available, otherwise the shortest road path between stops."
- **No new line styles.** The difference is in the popup and the legend, so the map stays readable.

### 11.5 Tests

**`mapdata/tests`, with small synthetic relations:**
- way chaining with flipped ways;
- a gap splits a relation into pieces;
- a roundabout is cut between entry and exit;
- direction of travel: a pair in reverse order is rejected;
- the 40 m and 2 × rules each reject correctly;
- when variants compete, the shortest valid one wins;
- `ride_source` is written for every bus key.

**Planner:**
- with a fixture cache, `route_source` is `"relation"`, `"mixed"` or `"shortest"`, and the popup text follows it;
- with the real cache (skip with a message if it isn't built), a line 12 ride is `"relation"`;
- a line 18 ride is `"shortest"`;
- the golden day's bus 36 legs report whichever source the build chose. State it in the report and do not force it.

**Page:** extend the Node harness for the popup line and the legend text.

### 11.6 Report

Append "Phase 3" to `planner/eval/map-report.md`:
- **Per line:** the pairs from relations against shortest path, and the rejections by reason. Use the §11.2 table's lines, and add the relation timestamps.
- **The `relation-differs` count,** with the five largest differences named. These are where the map visibly changed.
- **Line 32:** whether its relation or the shortest path was right, from the geometry (§11.2 flags it).
- **Line 36:** whether the old Transdev relation was used for any pair.
- **Build:** the extra build time and file size.
- **What Pedro should check** on the page and in `review.geojson`.

### 11.7 Constraints and order

- **The same constraints as §10.8:** display only; no changes to `engine/`, `converter/`, the GTFS files, `places/data`, the pack, the prompts, the scheduler or the validator. No new dependencies. Run with the model server stopped.
- **Do not edit OpenStreetMap from this project.** Wrong or missing relations are listed for Pedro, who may fix them in OSM; a rebuild then picks them up.
- **Licence:** the relation geometry is OSM data (ODbL), already covered by the `data/paths/` item in `Issues.md` #1.
- **Rebuild trigger:** add "an OSM route relation for a feed line changed" to the triggers in `mapdata/README.md`. The relation timestamps in `meta.relations` make that checkable.

| Step | Work | Done when |
|---|---|---|
| 1 | Download and assemble relations | §11.5 chaining, gap and roundabout tests pass |
| 2 | Per-pair choice and flags | §11.5 rule tests pass; cache rebuilt (`--no-download` for the road and foot tiles; only the relations are new) |
| 3 | `route_source` in the planner and on the page | Planner and page tests pass |
| 4 | Report and docs | Phase 3 report written; `mapdata/README.md` (line → relation mapping, triggers) and the planner README decisions updated; samples regenerated |
