# mapdata: the path cache behind the planner's map (MapPlan.md §10)

The test page's map draws every ride along its road, railway or ferry route and every walk along footpaths and streets. Neither
timetable feed has `shapes.txt`, and the engine walks in straight lines, so the paths are built **offline from OpenStreetMap** and
stored as a display cache. **The cache never changes a time, a check or a plan.**

```bash
cd ~/selfhostedLLM && docker compose stop          # the build needs the laptop's memory; the model server must be down
cd ~/MVP-LiveBus
/usr/bin/python3 -m mapdata.build --plan-only      # what is needed, no network
/usr/bin/python3 -m mapdata.build                  # download (cached under mapdata/osm/), route, write data/paths/
/usr/bin/python3 -m mapdata.build --no-download    # route again from the cached OSM files
/usr/bin/python3 -m pytest mapdata/tests
```

## What it does
1. **Needs.** From the engine's feeds and the places layer: every stop in the Porto-Aveiro corridor (lat 40.40-41.20, lon -8.82 to
   -8.40), every consecutive stop pair of every trip in it (rail, bus, ferry), and every pair the engine can walk: stop-stop within
   400 m, stop-place point, stop-origin, place-place, place-origin within 2,000 m (haversine x 1.3, the engine's rule).
   Place points include the reference points (`<id>#<k>`).
2. **Download** (Overpass, tiles of 0.1 degree with a 3 km overlap, only the tiles near something that is needed, 6 s between
   queries): one `highway` layer per tile (tags kept; the bus graph and the foot graph are both derived from it, which saves a second
   download of the same ways), one railway query (`rail`, `light_rail`, no yard or siding) and one `route=ferry` query for the corridor.
   Raw answers are cached in `mapdata/osm/` (not committed) with their date, and never fetched twice.
3. **Graph.** `graph.py`: nodes and edges from the ways, each edge flagged for buses and/or pedestrians in each direction
   (`oneway`, `oneway:bus`, roundabouts, `access`, `foot`, `bus`, `psv`; motorways and trunks are not walked). A* by length with
   heapq, no turn costs. Stops are snapped to the nearest allowed segment: buses 150 m, trains 300 m, walkers 150 m.
4. **Route** each pair (buses on roads, trains on rails, ferries on the OSM ferry way, walks on the foot graph; one direction stored
   per walk), tile by tile (a pair loads only the tiles it needs).
5. **Write** `data/paths/paths.json.gz` (`{"meta", "ride": {"<a>|<b>|<mode>": polyline}, "walk": {"<a>|<b>": polyline}}`, polylines
   at precision 5) and `data/paths/review.geojson` (every bus and rail pattern drawn whole, and every flagged path).

## Snapping

A point snaps to the nearest segment allowed for its mode within the limit (bus 150 m, rail 300 m, foot 150 m). Segments of a network with at least `MIN_COMPONENT_NODES` (200) nodes win over a nearer isolated piece, such as a station platform or a courtyard footway. Only when no big network is within the limit is the nearest segment of any size used, so a separate island network still routes (decision 2026-10-28: without it, 112 walks to the old Aveiro station had no route).

## Buses on their mapped route (MapPlan.md §11)
Bus paths start as the shortest drivable path between consecutive stops. Where OSM has the operator's route (a `type=route` relation with `route=bus`) and it fits the stop pair, that route is used instead.
- **Download:** one Overpass query for the `route=bus|ferry` relations in the places area (`out meta`, for the OSM timestamps), then the member ways with geometry of the ones that match a feed line; cached in `mapdata/osm/relations.json.gz`. Endpoints are tried in turn (the main one, then `overpass.kumi.systems`).
- **Line to relation** (operator or network AND `ref` or name, never `ref` alone; the code is `relations.LINES`): Aveirobus lines 1-8, 10, 11, 12 = `operator`/`network` AveiroBus with `ref=L<n>`; 15 Azul and 15 Verde = AveiroBus named "Linha Centro: Azul" / "Linha Centro: Verde" (no `ref`); Busway 16, 17, 32, 37 = `operator`/`network` BusWay or Busway with `ref`; No relation: 18, 19, 20, 25, 33, 34, 35, and **36 by decision (Pedro, 2026-10-28)**. They stay shortest-path. For 36, OSM has only the former operator's relation (Transdev "Autocarro 5951: Aveiro => Costa Nova"), and one of its pairs ran 8.2 km past no stop where the road is 5.0 km; add a Busway 36 entry once Busway's own line is mapped. The ferry and trains are unchanged. 27 relations match.
- **Assembly:** member ways with roles `""`, `forward`, `backward` are joined in order, flipped where needed; two consecutive ways that share no node split the relation into pieces; a closed way (roundabout) is cut between entry and exit in the way's direction.
- **Per stop pair** (consecutive stops of every trip of a matched line, in the corridor): the relation is used when both stops lie within 40 m of the same piece (each stop may pass a piece more than once: any pass is tried), the second after the first in the direction of travel, and the piece between them is at most 2 times the shortest path's length; of several valid relations, and of several lines sharing the pair, the shortest piece wins. Otherwise the shortest path stays. A line can be part relation, part shortest path.
- **In the cache:** the chosen path in the same `ride` key; `meta.ride_source` (`relation:<osm id>` or `shortest` per bus key), `meta.relations` (id, lines, name, OSM timestamp, pieces), `meta.relation_stats` (per line: pairs, from relation, rejections by reason, pairs that differ). Flags: `relation-rejected <reason> (line n)` (far stop, gap, wrong direction, too long) and `relation-differs from the shortest path` (more than 30 % of either path's length lies more than 30 m from the other). Both are drawn red in `review.geojson`.
- Wrong or missing relations are for Pedro to fix in OSM (this project never edits OSM); a rebuild picks the change up.

## Flags (kept in `meta.flagged`, listed in the report, drawn red in `review.geojson`)
`snap-far` (a snap beyond its limit but within 3x: kept), `no-snap` / `no-route` (no path stored: the map falls back to a straight
line), `ride-detour` (a ride path more than 2.5x its straight line), `engine-underestimate` (a walk path more than 1.5x the engine's
distance and at least 100 m longer: the engine's straight line x 1.3 is too short there, for example around water or a railway with no crossing. **The engine
is not changed**; these are for Pedro), `no-ferry-way`.

## The planner side
`planner/planner/paths.py` loads the file lazily on the first map request and decodes only the polylines a plan needs. A missing file,
or one built for other feed hashes (`meta.feeds` against the engine's), gives straight lines and one warning in the map. The same
interface (`ride`, `walk`) can later be served by a routing server.

## Walking overrides for the engine (WalkBarriers.md)
`python -m mapdata.overrides` (also the last step of every `mapdata.build`) turns the cache into the engine's walking times:
- **`data/paths/walk_overrides.csv`**: one row per cached walk pair (`from_id,to_id,metres,kind,note`), sorted, so a re-run gives the same bytes. Header lines: the build date, the feed hashes (`cp`, `av`), the places hash and the review sheet's hash. The engine takes the longer of its own estimate (straight line x 1.3) and the OSM path (`ceil(metres / 1.2)`); a row can only make a walk longer. `kind` is `osm` (`metres` = the drawn path's length), `osm-gap` or `blocked` (from the sheet; no `metres`). Pairs with no cached route get no row and keep the engine's estimate (20 today, all around the Barra channel, which is also a barrier); the generator lists them.
- **`data/paths/underestimates-review.csv`**: the walks whose path is at least 1.5 times and 100 m more than the engine's estimate, biggest gap first (`excess_m, engine_m, path_m, from_id, to_id, from, to, verdict, note`), for Pedro's optional review. Fill `verdict` only for exceptions: `osm-gap` (OSM misses a crossing or path; the engine's estimate is about right) or `blocked` (no reasonable walk). Empty means the OSM length applies. The paths are red in `review.geojson`. A typo in `verdict` is an error, not ignored.
- **After editing the sheet**, run `python -m mapdata.overrides`; the engine picks the file up at its next load. A rebuild rewrites the sheet from the new flags and carries verdicts and notes over by pair (either orientation); a reviewed row whose flag has gone is kept with `note` prefixed "flag gone:", an unreviewed one is dropped.
- A file built for other feed hashes is ignored by the engine, with a warning.

## When to rebuild
- a feed hash changes (the runtime ignores a cache built for other feeds, with a warning);
- place points change (`meta.places_hash` tells);
- an OSM route relation for a feed line changed (`meta.relations` holds each relation's OSM timestamp);
- every 3 months, for OpenStreetMap changes (`meta.osm_downloaded`, `meta.built`).

## Licence
`data/paths/` is a database derived from OpenStreetMap (ODbL). Keep it identifiable and ready to be offered under the ODbL
(`Issues.md` #1). Map attribution on the page: "© OpenStreetMap contributors" and "paths derived from OpenStreetMap".
