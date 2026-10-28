# Walking overrides report (WalkBarriers.md, 2026-10-28)

Tests: engine 54 pass (42 before, 12 new), planner 183 pass and 3 fail (the same three `test_day_block_agrees_with_the_validator` cases as before), places 56, `mapdata` 20 (10 new). The model server was not needed.

## What was built
- **`mapdata/overrides.py`** (`python -m mapdata.overrides`, also the last step of every `mapdata.build`): writes `data/paths/walk_overrides.csv` from the path cache and the sheet, and rewrites `data/paths/underestimates-review.csv` from the cache's flags, carrying verdicts and notes over by pair.
- **Engine** (`engine/reach/overrides.py`, `network.py`, `engine.py`, `journeys.py`, `engine.toml`): the longer of the engine's estimate and `ceil(metres / 1.2)` for every cached pair, in transfer footpaths, access and egress (`Network.near`) and point-to-point walks; `osm-gap` keeps the estimate, `blocked` removes the walk; distance limits still use the straight line; walk legs report the OSM metres when the OSM time won; a stale or missing file is handled as specified (`health()["walk_overrides"]`).
- **Fix in `planner/planner/pack.py`:** the travel hints started from the main place id with a reference point's coordinates, so an override for the wrong point could apply. They now start from the chosen point's own id.

## Rows written (`data/paths/walk_overrides.csv`, cache built 2026-10-28)
| kind | rows |
|---|---|
| `osm` | 5,609 |
| `osm-gap` | 0 |
| `blocked` | 0 |
| no cached route (no row, the engine's estimate stays) | 20 |

The 20 pairs are all around the Barra channel (Forte da Barra, Farol, Praia da Barra, the ferry stops and São Jacinto), where the engine's own barrier already applies. Nobody has reviewed the sheet yet (175 rows, no verdicts), so every row is `osm`.

## Measurements (§6)
- **Cached pairs that get a longer time: 2,549 of 5,609 (45 %).** The extra time is a median of 51 s, p90 163 s, max 701 s; 1,129 pairs are at least a minute longer and 28 at least five minutes longer. The other 3,060 are unchanged (the OSM path is not longer than straight line x 1.3).
- **Packs for the 70 eval and holdout requests: all 70 change, full and compact.** In every one the arrivals, slots and travel hints move, mostly by minutes: the biggest shift of any candidate's `earliest_arrival` in a request is a median of 3 min, p90 4 min, max 5 min, and none is zero. The candidate set itself changes in 16 requests (full pack) and 4 (compact), by one or two food places. This is an intended data change for every backend (the Claude backends get the full pack).
- **Fallback plans (`use_llm=False`) for r01, r03, r06, r26:** the same stops in the same order for all four. r26 is identical. r01's later visits start 4 and 6 minutes later (Canal Central 14:33 -> 14:37, Moliceiro tour 15:21 -> 15:27), r03's third visit 16:07 -> 16:10, r06's last three visits 2-3 minutes later (Museu Arte Nova 13:34 -> 13:36, Museu de Aveiro 14:12 -> 14:15, Ílhavo 16:03 -> 16:06, home 18:05 -> 18:06). In each the cause is the longer walks around Aveiro.

## Golden changes and why
Nothing changes without the file (`test_real_engine_without_the_file_is_the_old_engine` runs the old values on a copy of the engine with `walk_overrides` removed: 208 s and 73 s). With the real file:
- **Engine G3 (Campanhã -> Costa Nova), the walk 208 s -> 218 s.** The pair is `cp:94_38000` (Aveiro station stop) <-> `av:terminal-rodoviario-de-aveiro`: 191 m in a straight line (x 1.3 = 248 m = 208 s) against **261 m** on the OSM footpath (218 s). The arrival, 10:20:00, is unchanged. The same pair changes `test_walk_time_formula` (208 -> 218 s) and `test_latest_replay_and_forward_agreement`'s formula (see below).
- **Engine G2 (bus terminal -> São Jacinto), the walk 73 s -> 76 s.** The pair is `av:forte-da-barra-ferry-2` -> `av:forte-da-barra-ferry` (the two Forte da Barra ferry stops): about 67 m straight (x 1.3 = 73 s) against **91 m** on the OSM footpath (76 s). The arrival, 10:35:00, is unchanged and no walking leg crosses the channel.
- **Properties** (`test_journeys_replay_and_monotone`, `test_latest_replay_and_forward_agreement`, `test_windows_consistency`): the old check was "walk seconds >= ceil(metres x 1.3 / 1.2)". A leg that carries OSM metres takes no detour factor (seconds = ceil(metres / 1.2)), so with the file the check is the weaker "seconds >= ceil(metres / 1.2)"; without it the old check stands. No journey changed for this.
- **Planner golden (`test_pl_golden.py`): no change** to any expected value (the walks involved shift by seconds, not minutes).
- **Planner:** `test_pl_freetime.py`: the Costa Nova beach idea is 478 m on foot, not 345 m (Canastra -> the boardwalk entrance, `praia-costa-nova#1`: 345 m straight, OSM path 478 m); and the Sé de Aveiro idea: Sé <-> Museu de Aveiro is 94 m straight but **373 m** on foot, so 6 + 45 + 6 minutes no longer fit a 30 minute gap (the museum is the first idea again for an hour, at 373 m). `test_pl_leave.py`: the home leg now ends with a 4 m walk from the Porto Campanhã stop to the origin point (see below), so the test looks at the last ride, not the last entry.

## Cacia and Escola nº1
- **Cacia:** Bar 7 -> Cacia station (`cp:94_38075`): the engine's walk was 134 s (a 123 m straight line x 1.3 = 160 m; the review sheet's "160 m"). It is now **975 m = 813 s**, and a journey from Bar 7 that takes the train at Cacia starts with that walk (`test_cacia_walk_from_bar_7_takes_the_975_m_path`: the first leg is a 975 m, >= 813 s walk to `cp:94_38075`).
- **Escola nº1 A <-> B:** the transfer was 44 m of engine metres (about 48 s) and is now **658 m = 549 s**, both directions, and the leg reports 658 m (the review sheet shows 657: the build's route length; the file holds the drawn polyline's length, 1 m more).

## Decisions (ReachabilityEngine.md §5.4, `mapdata/README.md`, `planner/README.md`)
- Override ids come from the point tuple the caller passes (`origin[0]`, `p[0]`); the file's header is `# built:`, `# feeds: id=hash ...`, `# places_hash:`, `# review_hash:`. `engine.toml` setting absent means `state: off`; present but file absent means `missing`; a header without feed hashes counts as stale.
- The file's `metres` is the length of the drawn polyline; the sheet keeps the build's route length (they differ by 0-1 m).
- A typo in `verdict` is an error in the generator, not silently ignored.
- The generator's sheet order breaks ties by pair id, so a re-run is byte-identical (the sheet written by the earlier ad hoc script had the same rows; ties and line endings differ).

## Needs Pedro
1. **Walks between a point and the station stop it sits on now show a few seconds.** The origins coincide with their stations, but the OSM paths between the origin point and the stop are 4 m (Porto Campanhã) and 7 m (Aveiro station), so every plan from them now ends with a "walk 4 m" entry and each journey is 4-7 s longer. This is what "every cached walk, automatically" gives, and is harmless in length, but 71 cached pairs with a straight line under 30 m behave this way. If you would rather not see them, mark those pairs `osm-gap` in the sheet or tell me to skip rows whose engine walk is under a small threshold.
2. **The review sheet is unreviewed:** 175 walks (biggest: Universidade - Crasto -> Fama by Luís Lavrador, +835 m; Bar 7 -> Cacia, +815 m; Junta de Freguesia de Cacia -> Cacia, +702 m; Escola nº1 A <-> B, +613 m) use the OSM length. Mark `osm-gap` or `blocked` only for exceptions, then run `python -m mapdata.overrides`.
3. **Every cached walk is now at least as long as OSM says:** where OSM lacks a crossing the planner becomes more cautious, never riskier (as decided). Look at the red paths in `data/paths/review.geojson` for suspicious detours (138 walks had no route at all and keep the engine's estimate).
