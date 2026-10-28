# ReachabilityEngine.md — public transport reachability engine build spec

This is the complete brief for building the YourDayTrip reachability engine. It is written for a coding agent working alone in this repository. Read it fully before writing code. Where this spec is silent, choose the simplest option that meets the acceptance criteria (§11) and record the choice in `engine/README.md` under "Decisions".

## 1. What the engine does

The engine answers one question for any list of points on the map: **"Leaving here after T and back by D, when is the earliest I can get to each point, the latest I can leave it, and how?"** It uses scheduled trains, buses and the ferry, plus walking.

```
data/gtfs/cp.zip ─────┐
data/gtfs/aveiro.zip ─┤
config/*.toml/geojson ┘
        │ load (once)
        ▼
  stops · trips · calendars · footpaths (walking, with barriers)
        │ per date (cached)
        ▼
  sorted connections ──► forward scan  (earliest arrival)  ─┐
                    └──► reverse scan  (latest departure)  ─┴─► windows for N points + journeys
                                                                  │
                                                Python API · CLI · HTTP · map page
```

The engine knows nothing about restaurants, monuments or beaches. The places layer, built later, passes their coordinates in as points and reads back time windows. **Every point is treated the same way: as a place reached by walking from nearby stops.**

### In scope
- Load several static GTFS feeds side by side: CP trains and the Aveiro bus and ferry feed.
- Service calendars in both GTFS styles, trips running past midnight, and the daylight-saving changes.
- Walking: point to stop, stop to stop for transfers, and stop to point. Configured barriers (water) block walking.
- Forward query (earliest arrival), reverse query (latest departure), windows for many points, and point-to-point journeys from any time.
- Journeys returned as legs, each naming its route, trip, stops and times.
- A coverage check that says when a feed has no data for the asked date.
- A Python API, a CLI, an HTTP API and a simple map page.
- Tests with exact expected values computed from the real feeds (§10).

### Out of scope
- Places, opening hours, interests and LLM planning. Those belong to the places layer, which consumes this API.
- Real-time delays (LiveBus's GTFS-RT). §13 is context only.
- Fares, tickets, reservations, accessibility and bikes.
- A street network for walking. Straight-line distance with a detour factor and barriers is the deliberate MVP choice (§5.4).
- Editing or rebuilding the feeds. `converter/` owns the Aveiro feed; CP's feed is used as published.

## 2. Inputs

### 2.1 Feeds

| Feed id | File | SHA-256 | Source | Validity |
|---|---|---|---|---|
| `cp` | `data/gtfs/cp.zip` | `48813f44f4257ca192f8970c9a7b0854fbdceb75e2b2eee09497d9d756ae9f58` | `https://publico.cp.pt/gtfs/gtfs.zip`, downloaded 18 Oct 2026, pinned in the repo | 2025-12-14 to **2026-12-12** |
| `av` | `data/gtfs/aveiro.zip` | `205a30af619b2e8cd66201e3721b06f780d29f57f406b8e3d019bf83a747328d` (rebuilt 19 Oct 2026 after Issues #4-#9 and #12; earlier versions `88927e86…`, `b3b007b6…`) | Built by `converter/` from Busway and Aveirobus timetables (`GTFSConverter.md`) | 2026-10-19 to 2027-09-11 |

Both stay inside the project. The app may launch on them once the conditions in `Issues.md` #1 are met (decided 22 Oct 2026). Read the zips directly. On load, compare each file's SHA-256 with `config/feeds.toml`. A different hash is a **warning**, not an error, because feeds get updated, but the golden tests (§10) then report which feed changed.

### 2.2 Facts measured from the feeds (19 Oct 2026)

Write tests that assert the key ones.

| | `cp` | `av` |
|---|---|---|
| Files | agency, calendar, calendar_dates, routes, stops, stop_times, trips | agency, stops, routes, trips, stop_times, calendar_dates, feed_info |
| Stops / routes / trips / stop_times | 454 / 186 / 1,945 / 30,780 | 319 / 26 / 828 / 8,741 |
| Calendars | `calendar.txt` (1,945 rows, one per trip) + 27,080 `calendar_dates` rows | **`calendar_dates.txt` only** (2,481 rows), no `calendar.txt` |
| `route_type` | `2` (130 routes) and **`109`** (56, extended type "suburban railway") | `3` (25 bus routes), `4` (1 ferry) |
| Latest time | `25:43:00` (trips run past midnight) | `24:09:00` |
| Extra columns | `pickup_type`, `drop_off_type`, `stop_headsign`, `shape_dist_traveled` | `timepoint` |
| Stop ids | numeric-like, e.g. `94_38000` (Aveiro), `94_2006` (Porto Campanhã) | slugs, e.g. `terminal-rodoviario-de-aveiro` |
| Shapes | none | none |
| Timezone | `Europe/Lisbon` | `Europe/Lisbon` |

The code must handle these:
- **Two calendar styles.** A service is active on date *d* if `calendar.txt` makes it active and no `calendar_dates` row type 2 removes it, or if a type 1 row adds it.
- **Extended route types.** Accept 100–1700 extended types and map them to a mode (100–199 → rail, 200–299 → coach, 700–799 → bus, 1000–1099 or 4 → ferry). Never reject a feed for using them.
- **`pickup_type` / `drop_off_type`.** A value of `1` means no boarding or no alighting at that stop. A missing column or an empty value means allowed.
- **Times at or past 24:00:00** belong to the previous service day (§5.2).
- **Id collisions across feeds** are possible. Prefix every stop, trip, route and service id with its feed id: `cp:94_38000`, `av:terminal-rodoviario-de-aveiro`.
- **Duplicate stops a few metres apart in `av`** come from leftover OCR variants (`Issues.md` #6). For example, `Estação de Aveiro` and `Estagao de Aveiro` are both 48 m from CP's Aveiro station. Footpaths (§5.4) join them; do not deduplicate.
- **The `cp` feed ends on 2026-12-12.** CP's December timetable change needs a new file. Queries after that date must say so (§5.9), not silently return "no trains".

### 2.3 Key places used in tests

| Name | Coordinates | Notes |
|---|---|---|
| Porto Campanhã | 41.14871926, -8.584835348 | CP stop `94_2006` |
| Aveiro bus terminal | 40.643800, -8.638550 | `av:terminal-rodoviario-de-aveiro`; 191 m from CP Aveiro (`94_38000`, 40.64342677, -8.640761068) |
| Costa Nova | 40.612500, -8.749740 | `av:largo-arrais-anca-costa-nova`, line 36 terminus |
| São Jacinto ferry quay | 40.660682, -8.729750 | `av:s-jacinto` |
| Forte da Barra ferry quay | 40.649904, -8.730027 | `av:forte-da-barra-ferry`; the line 36 bus stop `av:forte-da-barra-ferry-2` is 73 s' walk away |
| Águeda station | 40.57749168, -8.448231459 | CP `94_42218`, Vouga line |

## 3. Technology choices

- Python ≥ 3.11. **The core (`reach/`) uses the standard library only.** It must stay importable by the places layer without extra installs.
- HTTP API and map page: FastAPI + uvicorn, already used by LiveBus, as the optional extra `web`. Leaflet loaded from a CDN on the page, as LiveBus does.
- `pytest` for tests.
- Config in TOML (`tomllib`) and GeoJSON.
- Write a fresh minimal GTFS reader. LiveBus's `livebus/gtfs` is shaped around one feed and vehicle matching, so don't reuse it. Its `calendar.py` (`service_day_start`, `to_instant`) is the reference for the time rules in §5.2.

## 4. Repository layout to create

```
MVP-LiveBus/
├── ReachabilityEngine.md        (this file)
├── data/gtfs/cp.zip, aveiro.zip (inputs; do not modify)
└── engine/
    ├── README.md                (run commands, Decisions, Known limitations)
    ├── pyproject.toml           (package reach; extras: web = fastapi, uvicorn; dev = pytest, httpx)
    ├── config/
    │   ├── feeds.toml           (feed id, path, sha256)
    │   ├── engine.toml          (walking and transfer defaults, §5.4–5.5)
    │   └── walk_barriers.geojson
    ├── reach/
    │   ├── __init__.py          (public API, §6.1)
    │   ├── gtfs.py              (reader, id prefixing, extended route types)
    │   ├── calendar.py          (active services, service-day instants, DST)
    │   ├── network.py           (stops, spatial grid index, footpaths, barriers)
    │   ├── connections.py       (per-date connection arrays + LRU cache)
    │   ├── scan.py              (forward and reverse Connection Scan)
    │   ├── journeys.py          (leg reconstruction)
    │   ├── windows.py           (multi-point windows, statuses)
    │   ├── coverage.py
    │   ├── cli.py               (python -m reach …)
    │   └── web/                 (FastAPI app + static/map.html)
    └── tests/
```

## 5. Model and rules

### 5.1 Ids
Every id is `"<feed>:<original id>"`. Results show the original names; the prefixed ids appear in each leg so a caller can trace a trip back to its feed.

### 5.2 Time and service days
- **Internally, every time is an absolute instant** (integer UTC epoch seconds). Never mix bare "seconds since midnight" from different days.
- A GTFS time `t` on service date `d` is the instant `noon(d, Europe/Lisbon) − 12 h + t`. This is the GTFS rule, and it is what makes daylight-saving days work. On 2026-10-25 (clocks go back 02:00 → 01:00), `noon − 12 h` is 00:00 UTC, which is 01:00 local in summer time.
- To cover a query on local date *D*, load trips of service dates *D − 1* and *D*, plus *D + 1* when the query's deadline runs past midnight. Keep only connections whose instants fall inside the query's time range.
- **Query inputs are local wall-clock times** (`2026-10-24T08:00`, Europe/Lisbon). Outputs give both the ISO local time with offset and the UTC instant.
- Clock changes inside the feeds' range: **2026-10-25** (last Sunday of October) and **2027-03-28** (last Sunday of March).

### 5.3 Connections
- A connection is one hop of one trip: `(dep_instant, arr_instant, from_stop, to_stop, trip, board_ok, alight_ok)`. `board_ok` comes from the from-stop's `pickup_type`, `alight_ok` from the to-stop's `drop_off_type`.
- For a service date, build the connections of every active trip and sort them by departure instant, then arrival instant. Cache the arrays per (service date, set of feeds) with a small LRU cache; 8 dates is plenty.
- Store connections in parallel arrays or `array` module columns, not one object per connection. CP alone produces about 30,000 hops a day.

### 5.4 Walking
- **Distance:** haversine with R = 6,371,000 m.
- **Walking time:** `ceil(distance_m × 1.3 / 1.2)` seconds. That's a 1.3 detour factor on straight-line distance at 1.2 m/s. These defaults live in `config/engine.toml`, and the golden values in §10 depend on them.
- **Transfer footpaths:** connect every pair of stops (across feeds too) within **400 m**, both ways, as one hop. Don't compute transitive closures.
- **Access and egress:** a point connects to every stop within `max_walk_m`. The default is **1,200 m**; a query may set 0–2,000 m.
- **Barriers:** `config/walk_barriers.geojson` holds LineStrings. A footpath, access or egress edge whose straight segment crosses any barrier segment is removed. Seed the file with the Aveiro harbour channel between Barra/Forte da Barra and São Jacinto. Without it, the engine walks 1.2 km across the water instead of taking the ferry:

  ```json
  {"type":"FeatureCollection","features":[{"type":"Feature",
   "properties":{"name":"Barra de Aveiro channel (Forte da Barra / São Jacinto)","note":"seeded 2026-10-19; check on a map and extend"},
   "geometry":{"type":"LineString","coordinates":[[-8.7600,40.6440],[-8.7400,40.6530],[-8.7300,40.6555],[-8.7150,40.6580],[-8.7000,40.6650]]}}]}
  ```

  GeoJSON order is [lon, lat]. Test that Forte da Barra ferry quay → S. Jacinto crosses it, and Farol → Costa Nova does not.
- **Walking overrides (WalkBarriers.md, 2026-10-28).** `engine.toml` `walk_overrides = "data/paths/walk_overrides.csv"` (relative to the repo root; no file or no setting means everything above, unchanged). The file, written by `python -m mapdata.overrides` from the OSM path cache, has one row per walk pair the cache covers (`from_id,to_id,metres,kind,note`; ids are the feed-prefixed stop ids, place point ids `<place>` / `<place>#<k>` and the origin ids; a row applies both ways) and `#` header lines with the build date and the feed hashes. For a pair with a row:
  - `osm`: walking seconds = **max**(the estimate above, `ceil(metres / walk_speed_mps)`); there is no detour factor on the OSM length, which is the route itself. When the OSM time is the larger, the walk leg's reported metres are the OSM metres. A walk can only get longer.
  - `osm-gap` (Pedro's review: OSM misses a crossing): the estimate above, as before.
  - `blocked`: the walk is not offered, like a barrier.
  It applies to transfer footpaths, access and egress (`Network.near`) and point-to-point walks (`walk_between_points`), wherever both ids are known. Distance limits (`transfer_max_m`, `access_max_m`, `access_max_limit_m`, `max_walk_m`) still compare the straight-line distance, so an override changes a time, not which pairs are considered. `Engine.walk_seconds` (coordinates only) is unchanged. A file whose header feed hashes differ from the loaded feeds is ignored whole, with one engine warning, and `health()["walk_overrides"]` reports `state` (`ok`, `off`, `missing`, `stale`), `rows`, `built` and `hash`. Rows for ids the engine does not know are ignored silently.
- Use a grid index (about 0.01° cells) for "stops within r of a point". No dependency is needed.

### 5.5 Transfers
- **Boarding a vehicle after leaving another vehicle** requires `arrival + walking time + 120 s ≤ departure`. The 120 s buffer is `transfer_buffer_s` in `engine.toml`. It also applies at the same stop, with zero walking.
- **The first boarding of a journey** (after walking from the origin point) has no buffer. **The final alighting** (before walking to the target point) has no buffer.
- Staying on the same trip has no buffer. Different trip ids are different vehicles: block continuations are not modelled.

### 5.6 Forward query: earliest arrival
Use a Connection Scan over the sorted connections starting at the departure instant:
- **Start:** every access stop gets `arrival = t0 + walk`, marked "from origin", so no buffer.
- A connection can be **boarded** if its trip was already boarded, or if `board_ok` and its stop's arrival plus the buffer (if not "from origin") is no later than its departure.
- When boarded and `alight_ok`, improve the to-stop's arrival. Then relax its footpaths.
- Keep pointers (`entered_at` connection per trip; per stop the connection or footpath that set it) so journeys can be rebuilt.
- **Point arrival** = min over egress stops of `arrival(stop) + walk`.
- **Tie-break,** to be deterministic: earliest arrival, then fewer vehicle legs, then less walking, then the lexicographically smallest trip id.

### 5.7 Reverse query: latest departure
Use a Connection Scan backwards over connections, in decreasing departure order, ending at the deadline:
- **Start:** every egress stop of the target gets `egress(stop) = deadline − walk`.
- A connection is **useful** if its trip is already marked useful, or if `alight_ok` and either `arrival ≤ egress(to)` (alighting there, then walking to the target) or `arrival + 120 ≤ board(to)` (changing vehicle there).
- If useful and `board_ok`: `board(from) = max(board(from), departure)`, then for each footpath `board(neighbour) = max(…, departure − walk)`.
- **Point latest departure** = max over access stops of `board(stop) − walk`. The first boarding has no buffer, matching §5.5.
- Keep pointers to rebuild the return journey. Use the mirror of the tie-break in §5.6: latest departure, then fewer legs, then less walking, then the smallest trip id.

### 5.8 Windows
For an origin point, date, `leave_after`, `back_by` and N target points, run **one** forward scan from the origin and **one** reverse scan to the origin (not 2N scans). Then for each point return:

| Field | Meaning |
|---|---|
| `arrive` | earliest arrival at the point (§5.6) |
| `leave` | latest departure from the point that still gets home by `back_by` (§5.7) |
| `available_s` | `leave − arrive` |
| `status` | `ok` (`available_s ≥ min_stay_s`, default 0); `no_window` (both exist but `leave < arrive + min_stay_s`); `unreachable` (no `arrive`); `no_return` (no `leave`) |
| `outbound`, `return` | journeys (§6.2), omitted unless asked for (`include_journeys`) |

Also provide the **stop-level window table**: earliest arrival and latest departure for every stop, from the same two scans. The map page (M5) colours stops with it.

### 5.9 Coverage
- A feed **covers** a date if its calendar can make any service active on it (between the feed's first and last calendar date).
- Every response carries `coverage: {"date": …, "missing_feeds": [...]}`. When a feed is missing, also add a human-readable warning, e.g. "No CP train data after 2026-12-12; trains are not included."
- `strict_coverage=true` turns a missing feed into an error (HTTP 422).

### 5.10 Filters
Optional query filters: `exclude_route_types` (GTFS types after mapping, e.g. `[2]`) and `exclude_routes` (route short names, e.g. `["AP"]` for CP's Alfa Pendular, which needs a reservation). The defaults exclude nothing. The golden tests use the defaults.

### 5.11 Journeys
A journey is a list of legs:
- **Ride:** mode, feed, route short and long name, trip id, headsign, board stop and time, alight stop and time.
- **Walk:** from, to, metres, seconds.

The first and last legs are walks when the point isn't exactly at a stop; omit walks of 0 s. Merge consecutive walks.

## 6. Interfaces

### 6.1 Python API (`reach/__init__.py`)

```python
eng = reach.load(config_dir="engine/config")               # loads feeds, builds footpaths
eng.coverage(date) -> dict
eng.earliest(origin: Point, date, leave_after, target: Point, **opts) -> Journey | None
eng.latest(origin: Point, date, target: Point, back_by, **opts) -> Journey | None      # latest departure from origin
eng.windows(origin: Point, date, leave_after, back_by, points: list[Point], **opts) -> WindowResult
eng.stop_windows(origin, date, leave_after, back_by, **opts) -> list[StopWindow]
```

`Point = (id, lat, lon)`. `opts`: `max_walk_m`, `transfer_buffer_s`, `min_stay_s`, `exclude_route_types`, `exclude_routes`, `include_journeys`, `strict_coverage`. Every result is a frozen dataclass with `to_json()`.

### 6.2 CLI

```bash
python -m reach earliest --from 41.14872,-8.58484 --to 40.6125,-8.74974 --date 2026-10-24 --after 08:00
python -m reach windows  --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 --points points.csv
python -m reach coverage --date 2026-12-19
```

### 6.3 HTTP (`reach.web`, port 8100 so it doesn't clash with LiveBus's 8000)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | | feeds loaded, hashes, stop and trip counts |
| GET | `/coverage?date=` | | §5.9 |
| GET | `/journey?from=lat,lon&to=lat,lon&date=&after=` | opts as query params | earliest journey |
| GET | `/journey?from=…&to=…&date=&back_by=` | | latest-departure journey |
| POST | `/windows` | `{"origin":{"lat":…,"lon":…},"date":"2026-10-24","leave_after":"08:00","back_by":"21:00","points":[{"id":"costa-nova","lat":40.6125,"lon":-8.74974}],"include_journeys":true}` | §5.8 |
| GET | `/stops/windows?from=…&date=&after=&back_by=` | | stop-level table (GeoJSON) |
| GET | `/map` | | page: pick origin, date and times; stops coloured by hours available, click a stop for both journeys |

Errors return JSON `{"error": code, "detail": …}` with HTTP 400 for bad input and 422 for strict coverage.

## 7. Config

- `feeds.toml`: `[[feeds]]` with `id`, `path` (relative to the repo root), `sha256`.
- `engine.toml`: `walk_speed_mps = 1.2`, `walk_detour = 1.3`, `transfer_max_m = 400`, `access_max_m = 1200`, `access_max_limit_m = 2000`, `transfer_buffer_s = 120`, `cache_dates = 8`.
- `walk_barriers.geojson`: §5.4.
- `engine.toml` `walk_overrides`: §5.4 (walking overrides).

## 8. Performance targets
Measure on this machine and report in the README:
- Load both feeds and build footpaths: < 3 s.
- One forward plus one reverse scan for a single date: < 0.5 s. The prototype took 0.07 s with no caching.
- `windows` for 200 points: < 1 s, excluding the first connection build for a date.
- Memory: < 300 MB.

## 9. Known data defects the engine will expose
These belong in `Issues.md`, not in engine code. Never patch data inside the engine.
- ~~Sunday and holiday line 36 to Costa Nova (`Issues.md` #12)~~ resolved in the converter on 19 Oct 2026.
- **Two accepted fast legs (`Issues.md` #4):** line 12 Verba B → Estação Quintas A, and line 8 São Bento → Igreja Póvoa do Valado. The engine treats both as achievable.

## 10. Tests (all must pass with `pytest`)

### 10.1 Unit
- **Time:** GTFS `24:30:00` on service date 2026-10-24 = `2026-10-24T23:30:00Z` = 00:30 local on 2026-10-25. `25:43:00` on 2026-10-24 = `2026-10-25T00:43:00Z` (01:43 local, still summer time). `09:35:00` on 2026-10-25 = `2026-10-25T09:35:00Z` (09:35 local, winter time). One case each around 2027-03-28.
- **Calendar:** a CP trip active through `calendar.txt`, one removed by a type 2 row, one added by a type 1 row; an `av` service from `calendar_dates` only.
- **Boarding rules:** a connection with `pickup_type=1` is never boarded; one with `drop_off_type=1` never improves its stop.
- **Walking:** `ceil(m × 1.3 / 1.2)` (CP Aveiro → `av:terminal-rodoviario-de-aveiro` = 208 s without the walking overrides; 218 s with them, because the OSM footpath is 261 m); the barrier cases in §5.4; an access radius of 0 m works only for a point exactly at a stop.
- **Transfer buffer:** a synthetic two-trip case where the change is 119 s short fails and 120 s succeeds; the first boarding needs no buffer.
- **Id prefixing:** the same raw stop id in two synthetic feeds gives two stops.

### 10.2 Golden journeys (real feeds, default options)
Computed with a prototype of exactly these rules on 19 Oct 2026, and recomputed on the rebuilt `av` feed the same day; the engine and the prototype agree on every value. If a feed hash differs from §2.1, skip with a message naming the feed instead of failing.

| # | Query | Expected |
|---|---|---|
| G1 | Aveiro bus terminal → Costa Nova, Sat 2026-10-24, after 09:00 | arrive **09:40:00**; 1 ride: line 36, trip `av:bw-36-p20t2c3`, board 09:00:00 at Terminal Rodoviário de Aveiro |
| G2 | Aveiro bus terminal → São Jacinto ferry quay, Sat, after 09:00 | arrive **10:35:00**. Line 36 `av:bw-36-p20t2c4` 09:35 → Forte da Barra - Ferry 09:57; walk 73 s (76 s with the walking overrides: the OSM path between the two ferry stops is 91 m) to FORTE DA BARRA (FERRY); ferry `av:ab-ferry-b4t3` 10:20 → S. JACINTO 10:35. **No walking leg may cross the channel barrier** |
| G3 | Porto Campanhã → Costa Nova, Sat, after 08:00 | arrive **10:20:00**. CP route "Linha de Aveiro", Campanhã 08:10 → Aveiro 09:07; walk 208 s to Terminal Rodoviário de Aveiro; line 36 `av:bw-36-p20t2c4` 09:35 → 10:20 |
| G4 | Porto Campanhã → Águeda, Sat, after 08:00 | arrive **10:35:00**. "Linha de Aveiro" 08:10 → Aveiro 09:07, then CP route "R" Aveiro 09:58 → Águeda 10:35 |
| G5 | Latest departure to reach Porto Campanhã by 21:00, Sat | Costa Nova **18:45:00** (line 36 `av:bw-36-p21t2c14`); São Jacinto **18:45:00** (ferry `av:ab-ferry-b3t11`); Águeda **18:53:00** (CP "R"); Aveiro bus terminal **20:11:32** (walk 208 s, CP route "AP" from Aveiro at 20:15) |
| G6 | `windows` from Porto Campanhã, Sat, after 08:00, back by 21:00 | Costa Nova 10:20:00 → 18:45:00 (**8 h 25 min**); São Jacinto 10:35:00 → 18:45:00 (**8 h 10 min**); Águeda 10:35:00 → 18:53:00 (**8 h 18 min**); all `ok` |
| G7 | Same as G6, but back by 12:00 | Costa Nova: `arrive` 10:20:00, `leave` 09:45:00, status **`no_window`** |
| G8 | Coverage | 2026-12-12: `missing_feeds = []`; 2026-12-13: `["cp"]` plus the warning |
| G9 | Sun 2026-10-25 (clocks go back): connections arriving at Porto Campanhã from Saturday's service after midnight | instants `2026-10-24T23:09:30Z`, `2026-10-24T23:30:00Z`, `2026-10-25T00:43:00Z` |
| G10 | Regression for Issues.md #12: Aveiro bus terminal → Costa Nova, Sun 2026-10-25, after 09:00 | arrive **10:20:00** (was 21:05:00 before the converter fix) |

### 10.3 Properties (over 50 random origin and target pairs from the real stops, fixed seed)
- Every returned journey replays: each ride leg matches real connections of one trip, every change respects §5.5, every walk respects §5.4 and the barriers.
- `earliest(a → b, after t).arrive` ≥ t, and asking again with `after = t + 1 min` never arrives earlier.
- For `windows`, the `outbound` journey arrives at `arrive`, and the `return` journey leaves at `leave` and reaches the origin by `back_by`.
- The forward and reverse scans agree: if `leave(p)` exists, then `earliest(p → origin, after leave(p))` arrives ≤ `back_by`.

### 10.4 Performance
A test that times §8's single-scan and 200-point cases and fails above 2× the targets. That leaves headroom for slower machines.

## 11. Milestones and acceptance criteria

| # | Milestone | Done when |
|---|---|---|
| M1 | Feeds, calendars, time | Unit time and calendar tests pass; G8, G9 pass; hashes checked |
| M2 | Network and forward scan | Walking, barrier, buffer and boarding-rule tests pass; G1–G4 pass |
| M3 | Reverse scan and windows | G5–G7 pass; G10 is xfail; property tests pass |
| M4 | API, CLI, HTTP | §6 works end to end; HTTP tests via `httpx`/TestClient; performance test passes |
| M5 | Map page | `/map` shows stops coloured by hours available for a chosen origin, date and times. Clicking a stop shows the outbound and return journeys. Checked in a browser |

## 12. Things not to do
- Don't add place, restaurant or opening-hours logic. Points are coordinates only.
- Don't call an LLM.
- Don't patch, rewrite or merge the feeds, and don't fix data defects inside the engine (§9).
- Don't download feeds at runtime. Feeds are pinned files; updating them is a deliberate, hashed change.
- Don't walk through barriers, and don't add walking beyond `access_max_limit_m`.
- Don't return "no service" for a date a feed doesn't cover without the coverage warning.
- Don't publish the feeds or expose the HTTP API publicly (licences, `Issues.md` #1).
- Don't add core dependencies beyond the standard library.

## 13. Later (context only, do not build now)
- **Real-time:** feed LiveBus's GTFS-RT TripUpdates into the connection instants for today's queries (delays, cancellations), and prefer the **return** leg's real-time status for "last safe return".
- **Places layer:** reads `windows` for each candidate place, then filters by opening hours, visit length and interests. A bar is only suggested if its hours overlap `[arrive, leave]`.
- **New CP timetable:** from 13 Dec 2026, a new `cp.zip` (with a new hash, and new golden values for trains).
- **Street network walking** (OpenStreetMap) replacing the straight-line walking and barriers, if walking times prove too rough in testing.
