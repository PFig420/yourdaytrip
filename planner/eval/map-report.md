# MapPlan.md report (2026-10-27)

## What was built
- **IDs on the timeline** (additive, output only): `Ride.feed/board_stop_id/alight_stop_id`, `Walk.from_id/to_id`, `Visit.point_id`, `FreeTime.point_id`, and `point_id` on each free-time idea. They come from the engine's legs and the scheduler's chosen point.
- **`planner/planner/mapview.py`**: `build(plan, engine, lib)` returns a GeoJSON `FeatureCollection` (`origin`, numbered `visit`, `ride` through its trip's stops, `stop`, straight `walk`, `free_time`, `idea`) with `bounds_all`, `bounds_destination` and `warnings`. It works from the plan or its JSON.
- **Exposure:** `DayPlan.map`, `POST /plan?map=true`, `/meta` with `tile_url` and `tile_attribution` (`[web]` in `config.toml`), CLI `--geojson PATH`.
- **The test page:** a Leaflet 1.9.4 map above the timeline (below it on narrow screens): numbered visit pins (food in another colour), one coloured line per ride with stop dots, dashed straight walks, a clock pin for free time, a switchable "Ideas nearby (optional)" layer, popups, a legend, "Whole day" / "Destination" buttons (the plan opens on Destination when it differs), warnings under the map, and row ↔ map highlighting (also from the keyboard). Without Leaflet the panel says so and the timeline is as before.
- `Issues.md` #1 has the new unchecked item about map tiles. Planner.md §3.2, §9.2 and §9.3, and the planner README "Run", layout and "Decisions" are updated.

## Test results
- Planner: 173 pass, 3 fail (the same three `test_day_block_agrees_with_the_validator` cases as before). Places 56 pass, engine 42 pass.
- `tests/test_pl_map.py` (16 tests): ids filled on the golden plan; `narrative.payload` identical with ids blanked; golden map (3 numbered visits, the seafront at its south point 20 m from Canastra, 4 rides each starting and ending at its stops with `stops` equal to the trip's count, 6 walks, 1 origin, every coordinate inside the engine-stop box ±0.05°, `step` matching the timeline entry kind); the destination bounds leave Porto out; the Costa Nova plan has one `free_time` and 2 `idea` features; a plan read back from JSON gives the same map; a synthetic circular trip draws only the stops between boarding and alighting (2 stops for the second pass through A, 5 for the whole loop); an unknown id is skipped with a warning; `?map=true` adds `map`, no flag leaves it `null`; `no_plan` has `map: null`; `/meta` tile settings; `--geojson` writes a valid collection.
- The page: `node --check` passes, and `tests/page_harness.js` (minimal DOM, stub Leaflet) shows 5 solid lines, 8 dashed lines, 10 stop dots, pins 1–4 in order (1 food), 1 origin, 1 clock, 2 ideas, every popup filled, every row mapped, both click directions and the keyboard working, and the panel hidden without a map. No live model plan was run (not needed).

## Sample files (for geojson.io)
- `planner/eval/map-sample-golden.geojson`: the golden day (3 visits, 4 rides, 6 walks).
- `planner/eval/map-sample-costa-nova.geojson`: the 31 Oct case (4 visits, 5 rides, 8 walks, a free time with 2 ideas).

## Ids that could not be resolved
None, in both samples and in the tests (`warnings` is empty). A trip missing from the feeds, or an id the resolver does not know, is skipped or drawn stop to stop and listed in `warnings`.

## Decisions (planner README, "The map")
Idea property `place_kind` (since `kind` is the feature kind); visit numbers count inserted visits; `bounds_destination` = timeline steps from the first to the last visit, ideas excluded; a missing trip falls back to a stop-to-stop line with a warning; `invalidateSize` on showing the panel; re-centre only when the selection is out of view, never zooming in.

## What Pedro should look at on the page
1. **Destination view:** the plan opens on the Aveiro area, with Whole day one click away.
2. **The bus 36 line** from Rua Clube dos Galitos to Largo Arrais Ançã (a line through its stops, not the road).
3. **The 20 m walk** from Canastra to the seafront's south point (a tiny dashed segment; the pin is labelled with the point name in its popup).
4. **The idea markers** (hollow rings) in the Costa Nova case, and the "Ideas nearby" switch.
5. **Row ↔ map:** click a timeline row (the map pans only if it must, and opens the popup) and click a pin or line (the row highlights and scrolls into view).
6. Narrow window (about 360 px): the map stacks above the timeline with no sideways scroll, and the pin sizes are comfortable to tap.


# Phase 2: roads, railways and footpaths (MapPlan.md §10, 2026-10-28)

## What was built
- **`mapdata/`** (offline, its own tests): `graph.py` (OSM ways to a graph with per-direction bus and foot flags, A* with heapq, snapping to the nearest allowed segment, polyline encoding), `build.py` (`python -m mapdata.build`: Overpass download cached under `mapdata/osm/`, routing of every needed pair tile by tile, cache and review file), `README.md` (how to build, flags, when to rebuild, the ODbL note).
- **`data/paths/paths.json.gz`** (the cache), `data/paths/review.geojson` (every bus and rail pattern drawn whole, and every flagged path in red), `data/paths/build-summary.json`.
- **`planner/planner/paths.py`** (lazy, read-only; missing or stale cache means straight lines and one warning) and `mapview.py` using it: rides join their cached stop pairs, walks use the cached footpath; `path` / `gaps` / `path_m` on the features; an idea's walk as `idea_walk`.
- **Page:** the cached lines, dotted grey where there is no path, the selected leg on top with a white casing and the rest dimmed to 40 % (click again or Escape clears), the new legend text, "paths derived from OpenStreetMap" in the attribution, "the street route is about N m" in walk popups.

## The numbers
- **Paths stored:** 641 of 646 ride pairs (196 rail, 448 bus, 2 ferry in the corridor; the 5 missing are listed below) and 5,491 of 5,629 walk pairs.
- **Flagged: 388** (`meta.flagged`, red in `review.geojson`): ride-detour 31 (more than 2.5 times the straight line; worst 9.1x, a one-block bus pair), ride no-route 5, walk no-route 138 (81 stop-to-place, 55 place-to-place, 2 place-to-stop: ends the foot graph does not join, for example across the ria or a railway), snap-far 42 (kept), walk engine-underestimate 172.
- **The 5 ride pairs with no path** (drawn straight): Esgueira-Aveiro (both directions, rail), Espinho-Silvalde on the Vouga line (both directions), and Forca (Túnel A) to Terminal Rodoviário (Cais 1) (bus).
- **File size:** 0.22 MB (`paths.json.gz`); `review.geojson` 3.1 MB. **Build time:** about 25 minutes of Overpass downloads (23 tiles, 6 s apart, 23 MB cached) and 145 s of routing. **Peak memory:** 627 MB. The model server was stopped throughout and was not restarted.
- **OSM data date:** 2026-10-28.

## Walks the engine likely underestimates (§10.4 step 4)
172 walk paths are more than 1.5 times, and at least 100 m longer than, the engine's walking distance (haversine x 1.3): 18 are 3 times longer or more and 4 are 500 m longer or more. The 25 with the biggest excess (**the engine is not changed**; Pedro to look at them in `review.geojson`):

| from | to | engine m | path m |
|---|---|---|---|
| Universidade - Crasto | Fama by Luís Lavrador | 1603 | 2438 |
| Bar 7 | Cacia | 160 | 975 |
| Junta de Freguesia de Cacia | Cacia | 193 | 895 |
| Escola nº1 - A | Escola nº1 - B | 44 | 657 |
| Cais da Fonte Nova B | Sé de Aveiro | 830 | 1319 |
| Bacalhau e Afins | Forneria 1870 | 291 | 768 |
| Estação - A (Ovar) | Sabor da Picanha | 675 | 1126 |
| Estação Quintas B | Quintans | 273 | 720 |
| Estação Quintas A | Quintans | 273 | 720 |
| Loja do Cidadão A | Loja do Cidadão B | 21 | 444 |
| Escola nº1 - A | Café Amizade | 266 | 688 |
| Escola nº1 - B | Café Amizade | 228 | 647 |
| Bacalhau e Afins | Cais da Tosca | 263 | 676 |
| Cais da Fonte Nova A | Sé de Aveiro | 722 | 1117 |
| Escola de Eirol B | Eirol | 213 | 594 |
| Av. 25 de Abril (Aveiro) | Museu Marítimo de Ílhavo | 448 | 825 |
| A Fornalha | Forneria 1870 | 461 | 812 |
| Canal Central and the Rossio | Forneria 1870 | 295 | 637 |
| Forneria 1870 | O Mercantel | 395 | 735 |
| Bacalhau e Afins | Fábrica Centro Ciência Viva de Aveiro | 624 | 955 |
| Bacalhau e Afins | Taberna do Rockabilio | 495 | 825 |
| Ciência Viva | Bacalhau e Afins | 527 | 852 |
| R. Clube dos Galitos B | Bacalhau e Afins | 265 | 589 |
| Parque da Cidade A | Igreja da Misericórdia de Aveiro | 593 | 916 |
| Parque da Cidade A | Sé de Aveiro | 496 | 818 |

Most of the rest are walks around the ria, a railway or a dual carriageway. The ones with an engine distance of 21-44 m and a path of 444-657 m are pairs of opposite-side bus stops ("A" and "B") with no crossing near them in OSM.

## Tests
- Planner 183 pass, 3 fail (the same three `test_day_block_agrees_with_the_validator` cases); places 56, engine 42, `mapdata` 10 pass.
- **`mapdata/tests`:** A* on a hand-made grid, snapping limits, polyline round trip (including Google's documented example), one-way respected for buses and not for walkers, a missing route gives no path, tags to flags, a far snap kept and flagged, a snap beyond 3 times the limit gives no path, tile cells and groups.
- **`planner/tests/test_pl_map.py`** (26): with a fixture cache, a ride uses the cached pairs in order and falls back per pair; a reversed walk key is found and reversed; `path` is `osm`/`partial`/`straight`; no cache gives straight lines and one warning; a cache built for other feeds is ignored with a warning; an idea has its own walk. **With the real cache:** every ride, walk and idea walk in the golden day and the Costa Nova day has `path == "osm"` and no warnings, and the bus 36 line has many road vertices and 3 spot-checked points lie within 50 m of an OSM road vertex it runs on.
- **Page:** `node --check` is clean; the Node harness checks the solid and dotted styles, the casing and stronger stroke on the selected leg, everything else dimmed, clearing by a second click and by Escape, and the attribution.

## Samples (regenerated, with the real cache)
`planner/eval/map-sample-golden.geojson` (4 rides, 6 walks, all `osm`) and `planner/eval/map-sample-costa-nova.geojson` (5 rides, 8 walks, 2 idea walks, all `osm`).

## Decisions
In `planner/README.md` ("Phase 2 of the map"): the cache design and the rejected alternatives, one highway download per tile, `narrow_gauge` added to the railway layer (the Vouga line), the 100 m minimum for the underestimate flag, snaps kept up to 3 times the limit, pairs outside the corridor not cached, a zero-length pair not ratio-checked.

## What Pedro should check
1. **On the page** (`python -m planner.web --backend local`): the bus 36 line follows the road from Aveiro to Costa Nova and the train follows the railway; the 20 m walk from Canastra to the seafront; select a ride or walk (white outline, the rest dimmed; click again or Escape to clear); a dotted grey line anywhere means no path was found.
2. **`data/paths/review.geojson`** in geojson.io: the red flagged paths (the walks above, the 31 ride detours, the 5 missing rides) and a quick look at the bus and rail patterns drawn whole (a bus line that cuts across water or doubles back is a wrong turn).
3. **The 5 missing ride pairs and the 138 walks with no route** (OSM gaps or real barriers) if any matter for plans you test.
4. **Rebuild** with `python -m mapdata.build` (model server stopped) when a feed hash changes, place points change, or every 3 months.

## Rebuild, 2026-10-28: snapping skips isolated pieces of the network

**Cause of most no-route walks:** 112 of the 138 walks with no route involved one place, the old Aveiro station (`estacao-ferroviaria-aveiro`), including the 56 m walk to the station stop. Its point snapped to a small piece of footway that the street network does not join. It was not the ria or a railway.

**Fix (`mapdata/graph.py`):** a point now snaps to a segment of a network with at least `MIN_COMPONENT_NODES = 200` nodes when one is within the snap limit. Only when none is does it take the nearest segment of any size, so a separate island network still routes. Test: `test_snapping_prefers_a_big_network_over_a_nearer_isolated_piece`.

**Rebuilt** with `python -m mapdata.build --no-download` (the same OSM download): 141 s, peak 644 MB, 0.23 MB.

| | Before | After |
|---|---|---|
| Walk paths stored | 5,491 of 5,629 | **5,609** of 5,629 |
| Walks with no route | 138 | **20** |
| The old station's walks with no route | 112 | **0** |
| Ride paths stored | 641 of 646 | 641 of 646 (unchanged) |
| Walks flagged as engine-underestimated | 172 | 175 (3 of the newly routed walks) |
| Flagged in total | 388 | 273 |

Only 1 of the walk paths that existed before changed shape, and no ride path changed.

**The 20 walks still without a route** mostly involve the Forte da Barra and Barra waterfront: `forte-da-barra` (6), `barba-azul-praia-da-barra` (5), the Forte da Barra ferry stops (3 each), and `navio-museu-santo-andre` (5). Also `jardim-oudinot` and `cafe-farol-ilhavo` (3 each). They stay straight and dotted on the map.

Tests: mapdata 11 pass; planner 183 pass, with the same 3 old `test_day_block_agrees_with_the_validator` failures.


# Phase 3: buses on their mapped route (MapPlan.md §11, 2026-10-28)

## What was built
- **`mapdata/relations.py`**: matches the 28 OSM route relations to 18 feed lines, assembles each into unbroken pieces, and chooses per stop pair between the relation and the shortest road path (40 m, direction of travel, at most 2 times the shortest). `build.py` downloads the relations (one query for the route relations in the places area, one for the member ways of the matches; cached in `mapdata/osm/relations.json.gz`, main Overpass endpoint then the kumi mirror) and applies them after the road routing. The cache has `meta.ride_source`, `meta.relations` (with OSM timestamps) and `meta.relation_stats`; flags `relation-rejected` and `relation-differs` are in `meta.flagged` and red in `review.geojson`.
- **Planner and page:** `route_source` (`relation`, `mixed`, `shortest`) on bus rides, the popup line and the new legend sentence; no new line styles. Rail, the ferry and walks are as in Phase 2.
- Tests: `mapdata/tests/test_relations.py` (15: chaining with flipped ways, platform and stop members skipped, a gap splits, a roundabout cut between entry and exit, direction of travel, the 40 m and 2 x rules, out-and-back lines, the shortest variant wins, matching needs operator or network and the ref, `differs`, `ride_source` for every bus key) and 3 planner tests plus the updated legend check (fixture cache: relation / mixed / shortest / none; the popup and legend text through the Node harness; with the real cache a line 12 ride is `relation` and a line 18 ride is `shortest`). Planner 186 pass and 3 fail (the same three `test_day_block_agrees_with_the_validator` cases), mapdata 35, engine 54, places 56.

## Per line (pairs = consecutive stop pairs of the line's trips inside the corridor)
| line | pairs | from relation | shortest path | rejected: far stop / gap / wrong direction / too long | relation differs | relation timestamps |
|---|---|---|---|---|---|---|
| 1 | 27 | 26 | 1 | 0 / 0 / 1 / 0 | 16 | 2026-07-26, 2026-09-18 |
| 2 | 31 | 17 | 14 | 12 / 0 / 2 / 0 | 6 | 2026-07-26, 2026-09-18 |
| 3 | 29 | 23 | 6 | 6 / 0 / 0 / 0 | 8 | 2026-10-15 |
| 4 | 17 | 6 | 11 | 10 / 1 / 0 / 0 | 1 | 2026-06-30, 2026-10-15 |
| 5 | 22 | 6 | 16 | 11 / 0 / 5 / 0 | 2 | 2026-07-26 |
| 6 | 24 | 23 | 1 | 0 / 0 / 0 / 1 | 10 | 2026-07-26, 2026-09-18 |
| 7 | 28 | 0 | 28 | 28 / 0 / 0 / 0 | 0 | 2026-06-30 |
| 8 | 31 | 4 | 27 | 24 / 0 / 3 / 0 | 2 | 2026-07-26 |
| 10 | 30 | 14 | 16 | 5 / 0 / 9 / 2 | 3 | 2026-10-15 |
| 11 | 10 | 3 | 7 | 4 / 2 / 1 / 0 | 0 | 2026-07-26, 2026-09-18 |
| 12 | 10 | 6 | 4 | 0 / 1 / 3 / 0 | 0 | 2026-07-26 |
| 15 Azul | 21 | 12 | 9 | 8 / 0 / 1 / 0 | 5 | 2026-10-17 |
| 15 Verde | 23 | 14 | 9 | 8 / 0 / 1 / 0 | 6 | 2026-10-17 |
| 16 | 33 | 11 | 22 | 15 / 7 / 0 / 0 | 2 | 2026-09-10, 2026-10-14 |
| 17 | 19 | 9 | 10 | 0 / 5 / 5 / 0 | 4 | 2026-10-15 |
| 32 | 4 | 1 | 3 | 0 / 2 / 1 / 0 | 1 | 2026-08-30 |
| 37 | 25 | 3 | 22 | 0 / 19 / 3 / 0 | 2 | 2026-10-15 |
| 36 | 23 | 4 | 19 | 15 / 0 / 4 / 0 | 1 | 2026-08-09 |
| **all** | 407 | 182 | 225 | 146 / 37 / 39 / 3 | 69 | |

- **Totals:** 128 of the 447 bus pairs in the cache follow a relation (some pairs are shared by lines, so the per-line table counts them once per line: 407 line-pairs, 182 from a relation). The other bus pairs, including all of lines 18, 19, 20, 25, 33, 34 and 35, keep the shortest path.
- **Why pairs are rejected:** mostly *far stop* (the stop is more than 40 m from the mapped route: lines 7, 8, 2, 4, 5, 16, 36 and 15 Azul/Verde, matching the coverage figures in §11.2) and *gap* on lines 37, 16 and 17 (a stop pair lies on different pieces because a way is missing in the relation). *Wrong direction* is 39 pairs, mostly a mapped relation that runs the other way for that stop pair (lines 10, 17, 5).

## `relation-differs`: 52 stop pairs (69 line-pairs)
Where the map visibly changes. The five largest differences:
| pair | relation | shortest | relation |
|---|---|---|---|
| Junta de Freguesia B -> Farol - Forte da Barra (line 36) | 8,177 m | 4,963 m | 5286195 (Transdev, 2026-08-09) |
| Centro Coordenador de Transportes (Albergaria) -> Hospital / Universidade A (line 17) | 22,002 m | 19,469 m | 18913874 |
| Tanques de Esgueira B -> Zona Industrial 4 B (line 3) | 4,440 m | 2,617 m | 7284607 |
| Hospital / Universidade A -> Terminal Rodoviário de Aveiro (line 32) | 4,715 m | 3,033 m | 18226473 |
| Santa Rosa -> Azenhas Ponte A (line 37) | 9,777 m | 8,252 m | 18226478 |

## Line 32
The corridor box holds only 4 of its stop pairs. Three are rejected (two *gap*s on the long stretch towards Albergaria, one *wrong direction*), and the one pair inside the city, Hospital / Universidade A -> Terminal Rodoviário, takes the relation (4,715 m against 3,033 m for the shortest path). Judged from the geometry, the relation looks right and the shortest path wrong: both stops lie on the mapped route, in order, and the relation's piece is a single unbroken way sequence; the shortest path is 1.6 times shorter, so it must use streets the mapped route does not. I cannot check it on the ground, and a bus may of course use a street OSM does not show; it is the first thing to look at in `review.geojson`.

## Line 36 (the old Transdev relation)
**It is used for 4 of the line's 23 pairs** (15 are rejected as *far stop*, 4 as *wrong direction*): Farol - Forte da Barra -> Largo Arrais Ançã - Costa Nova, Largo St. John's B -> Junta de Freguesia B, Junta de Freguesia B -> Farol - Forte da Barra and R. Clube dos Galitos A -> Largo St. John's B. The relation is Transdev's "5951 Aveiro => Costa Nova", edited 2026-08-09, an operator that no longer runs the line, and one of its pairs is 8,177 m against 4,963 m for the shortest path (1.65 times, inside the 2 x rule). **It may be outdated: Pedro should check these four on the map.** The golden day's bus 36 legs are therefore `mixed` (11:34 Aveiro -> Costa Nova) and `shortest` (15:25 Costa Nova -> Aveiro); the Costa Nova day's are `mixed` (14:05) and `shortest` (18:10); its line 5 bus is `relation`. I did not force any of this.

## Build
- **Extra time:** the relation download took 189 s (once, then cached); the choice itself takes about a second. The routing run is unchanged at about 2 minutes (the recorded `build_s` in this run is wrong because the system clock jumped during it). **File size:** 0.236 MB against 0.222 MB (+14 KB for `ride_source`, `relations`, `relation_stats`); `review.geojson` 3.5 MB. Peak memory 644 MB.
- Samples regenerated: `map-sample-golden.geojson` and `map-sample-costa-nova.geojson`.

## What Pedro should check
1. **On the page:** click a bus ride and read the popup's last line ("as mapped in OpenStreetMap", "partly...", "shortest road path..."). The Costa Nova bus 36 rides are `mixed`/`shortest`; the line 5 ride is `relation`.
2. **Line 36's four relation pairs and line 32's city pair** in `data/paths/review.geojson` (red = flagged). If the Transdev relation is wrong, remove `av:bw-36` from `relations.LINES` and rebuild (`--no-download`) or fix the relation in OSM.
3. **Lines with many rejections** (7, 8, 37, 2, 4, 5): the relation is partial or off the stops in OSM; fixing it in OSM and rebuilding would move them to `relation`. Line 7 has no pair from a relation: only a 6-way fragment is mapped.
4. The 52 `relation-differs` pairs: the five above are the biggest; a quick scan will show whether the relation or the shortest path looks like a real bus route.

## Line 36 relation dropped (2026-10-28, Pedro's decision)

**The check:** the old Transdev relation ("5951 Aveiro => Costa Nova") drew Junta de Freguesia B → Farol – Forte da Barra as 8,177 m against 4,963 m by the shortest road.
- **Timetable:** 14 min with no listed stop between, so 35 km/h on the relation against 21 km/h on the shortest road. Line 36's other relation pairs run at 27–28 km/h, so this does not settle it.
- **Detour:** the relation swings up to 1.4 km off the direct line, about 1 km from the Marinha Velha stops.
- **Stops passed:** none of any line in either feed within 40 m over the 8.2 km.

The relation also belongs to an operator that no longer runs the line.

**The change:** `av:bw-36` was removed from `mapdata/relations.LINES`, with a comment saying why, and the cache rebuilt with `--no-download` (143 s, peak 639 MB).
- Bus pairs: **124 relation, 323 shortest** (were 128 and 319). The Transdev relation is now used for 0 pairs. 3 ride paths changed; walks unchanged.
- The golden day's two bus 36 legs and the Costa Nova day's are now all `shortest`. Line 5 on the Costa Nova day still uses its relation.
- **Samples regenerated:**
  - `map-sample-golden.geojson`: 4 rides, 8 walks;
  - `map-sample-costa-nova.geojson`: 5 rides, 10 walks, 2 ideas with their walks.

  Both have no warnings.
- **Tests:** planner 186 pass, with the same 3 old `test_day_block_agrees_with_the_validator` failures; mapdata 35 and engine 54 pass.

Add line 36 back once Busway's own line 36 is mapped in OSM.
