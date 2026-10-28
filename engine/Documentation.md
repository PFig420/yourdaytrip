# engine/: the `reach` reachability engine

`reach` answers one question for any list of map points: **"Leaving here after T and back by D, when is the earliest I can be at each point, when must I leave it to get home in time, and how?"** It uses scheduled trains, buses, the ferry and walking.

It knows nothing about restaurants, monuments or opening hours. A later places layer passes in coordinates and reads back time windows. Every point is treated the same way: a place reached by walking from nearby stops.

The design brief is `../ReachabilityEngine.md` (the spec, section numbers like "§5.4" below refer to it). `README.md` in this folder holds run commands, performance numbers, design decisions and the golden-value table. This document explains how the code fits together.

```
../data/gtfs/cp.zip ──┐
../data/gtfs/aveiro.zip ┤  gtfs.py      read feeds, prefix ids with the feed id
config/*.toml/geojson ──┘  network.py   stops, spatial grid, walking, barriers → footpaths
                           connections.py  per-date sorted connection arrays (cached)
                           scan.py       forward / reverse Connection Scan
                           journeys.py   rebuild legs from scan chains
                           engine.py     public API: earliest · latest · windows · stop_windows · coverage
                              │
                  Python API · cli.py (python -m reach) · web/ (FastAPI + /map)
```

The Aveiro feed is built from PDFs by `../schedules/` and `../converter/`. CP's feed is used as published.

## Layout

| Path | Purpose |
|---|---|
| `reach/` | The package. Standard library only (Python ≥ 3.11 for `tomllib` and `zoneinfo`). |
| `reach/web/` | FastAPI app (`create_app`) and the Leaflet map page `static/map.html`. Needs `fastapi` and `uvicorn`. |
| `config/engine.toml` | Walking and cache parameters. |
| `config/feeds.toml` | Feed ids, paths (relative to the repo root) and pinned SHA-256 hashes. |
| `config/walk_barriers.geojson` | Lines walking may not cross. Only the Barra de Aveiro channel is seeded. |
| `tests/` | 42 pytest tests (units, golden values, properties, performance and HTTP). |
| `pyproject.toml` | Package `reach` 0.1.0. Extras: `web` (fastapi, uvicorn), `dev` (pytest, httpx). |
| `reach.egg-info/`, `.pytest_cache/` | Generated, git-ignored. |

The folder is untracked in a git repo that has no commits yet, so there is no history to consult.

## Setup and running

Run everything **from the repo root** (`MVP-LiveBus/`). The default config path is `engine/config` and the feed paths in `feeds.toml` are relative to the repo root.

```bash
pip install -e "engine[web,dev]"      # or: export PYTHONPATH=engine
python3 -m pytest engine/tests         # 42 pass (about 1.4 s; re-run on 23 Oct 2026)
python3 -m reach earliest --from 41.14872,-8.58484 --to 40.6125,-8.74974 --date 2026-10-24 --after 08:00
python3 -m reach latest   --from 40.6125,-8.74974 --to 41.14872,-8.58484 --date 2026-10-24 --back-by 21:00
python3 -m reach windows  --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 --points points.csv
python3 -m reach coverage --date 2026-12-19
python3 -m reach.web                   # http://127.0.0.1:8100/map  (127.0.0.1 only)
```

`points.csv` is `id,lat,lon` (header optional, `#` comments allowed). Global CLI flags (before the subcommand): `--config DIR`, `--json`. Query flags: `--max-walk`, `--buffer`, `--exclude-route-types 2,3`, `--exclude-routes 1,16`, `--strict`; `windows` also takes `--min-stay` and `--journeys`. Exit code is 1 for "no journey", 2 for an error.

The web app reads the config dir from `REACH_CONFIG` (default `engine/config`). Its docs say it is for local use only, pending `Issues.md` #1 (the Aveiro feed must not be exposed publicly before that is resolved).

### Python API

```python
import reach
eng = reach.load("engine/config")                    # loads both feeds, builds footpaths (~0.15 s)
j  = eng.earliest(("a", lat, lon), "2026-10-24", "08:00", ("b", lat, lon))   # Journey | None
j  = eng.latest(origin, "2026-10-24", target, "21:00")
r  = eng.windows(origin, "2026-10-24", "08:00", "21:00", points, include_journeys=True)  # WindowResult
sw = eng.stop_windows(origin, date, "08:00", "21:00")                          # [StopWindow]
c  = eng.coverage("2026-12-19")                       # Coverage
eng.walk_seconds((lat1, lon1), (lat2, lon2))          # seconds or None if a barrier blocks (no distance cap)
eng.health()
```

Points are `(id, lat, lon)` tuples or `{"id", "lat", "lon"}` dicts. Times are `"HH:MM[:SS]"` on the query date, or a full ISO datetime (for example to name a time on the next day). Options, shared by all queries: `max_walk_m`, `transfer_buffer_s`, `min_stay_s`, `exclude_route_types`, `exclude_routes`, `include_journeys`, `strict_coverage`. Unknown option names raise `BadInput`. `exclude_route_types` uses the *mapped* types: rail 2, bus and coach 3, ferry 4. `exclude_routes` matches the route short name.

## HTTP API (`reach/web/__init__.py`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Feeds loaded (counts, date range, hashes) and warnings. |
| `GET /coverage?date=` | Which feeds lack data for a date. |
| `GET /journey?from=lat,lon&to=lat,lon&date=` + exactly one of `after` or `back_by` | Earliest or latest journey. Returns `{journey, coverage, warnings}` and `journey` is null if none. |
| `POST /windows` | JSON body `{origin, points[], date, leave_after, back_by, ...options}`. |
| `GET /stops/windows?from=&date=&after=&back_by=` | Per-stop windows as a GeoJSON FeatureCollection. The map uses it. |
| `GET /map` | The Leaflet page. |

Errors are `{"error": code, "detail": ...}`. `ReachError` subclasses map to HTTP statuses: `BadInput` gives 400 and `CoverageError` (strict mode) gives 422. `map.html` loads Leaflet from cdnjs and OpenStreetMap tiles, so it needs internet access.

## How it works

### 1. Feeds (`gtfs.py`)
`read_feed` reads one zip with `csv` and no pandas. Every stop, route, trip and service id is **prefixed with the feed id** (`cp:94_38000`, `av:terminal-rodoviario-de-aveiro`) to avoid collisions.
- Only stops with `location_type` empty or 0 are kept.
- `route_mode` maps extended route types to a mode (100-199 rail, 200-299 coach, 700-899 bus, 900-999 tram, 1000-1099 ferry). It never rejects a type. CP uses the extended type 109.
- `pickup_type` or `drop_off_type` = 1 turns boarding or alighting off for that stop. Missing means allowed.
- Both calendar styles are read: `calendar.txt` (CP) and `calendar_dates.txt` only (Aveiro). A feed's coverage is the min and max of every date found in either.
- Times can pass 24:00:00 and are kept as seconds since the service-day start.
- The SHA-256 of each file is compared with `feeds.toml` in `Engine.__init__`. A mismatch only adds to `Engine.warnings`.

### 2. Time (`calendar.py`)
All instants are **UTC epoch seconds**. A service day starts at `noon(date) − 12 h` in `Europe/Lisbon`, which is the GTFS definition and keeps clock-change days correct. `active_services(feed, date)` applies the weekday calendar, then `calendar_dates` removals (type 2), then additions (type 1). `iso_local` renders an instant in local time. Result JSON carries each time as `{"local", "utc"}`.

### 3. Network and walking (`network.py`)
- All stops from all feeds go into parallel lists (`ids`, `names`, `lat`, `lon`, `index`) and a 0.01° grid for neighbour lookups.
- Walking is **straight-line distance × 1.3 detour ÷ 1.2 m/s**, rounded up (`WalkParams.seconds`). There is no street graph, which is a deliberate MVP choice.
- **Footpaths** (stop to stop transfers) are precomputed at load for stops within `transfer_max_m` (400 m), unless a barrier blocks them. They are not transitive: a transfer cannot be chained into another transfer.
- **Access/egress** (`Network.near`): stops within `max_walk_m` (default 1200 m, hard cap 2000 m) of a point, barrier-checked, computed per query.
- **Barriers** are line segments from the GeoJSON. A walk is blocked if the straight line between its two ends crosses one.
- The Aveiro feed has near-duplicate stops a few metres apart (OCR name variants). They are deliberately not merged. Footpaths join them.

### 4. Connections (`connections.py`)
`Timetable` turns each trip into **hops** (one per consecutive stop pair: departure, arrival, from, to, boarding allowed, alighting allowed). For a query date `d` it builds the *connection window*: all hops of service dates **d−1, d and d+1**, so past-midnight trips and late deadlines work. They are stored as parallel lists sorted by `(departure, arrival, trip, hop)`. Windows are cached in an LRU keyed by `(date, excluded types, excluded routes)`, with `cache_dates` entries (8). The same trip running on two dates is two separate trip instances.

### 5. Scans (`scan.py`)
Both are Connection Scan Algorithm variants over the sorted arrays. Each scan makes one pass and stops early.

**Forward scan (earliest arrival).** Starts at time `t0`, iterating connections in departure order. A trip is "entered" at the first connection where the traveller can be at its boarding stop in time. Two arrival labels are kept per stop:
- `ao`: arrival on foot from the origin. **No boarding buffer** applies.
- `av`: arrival by vehicle (or by vehicle plus footpath). Boarding a *different* trip needs `av + transfer_buffer_s` (120 s) ≤ departure.

Staying on the same trip needs no buffer, but a different trip id always does, even for the same physical bus (no block/interlining model). Early termination happens when the best known arrival at the target is reached.

**Reverse scan (latest departure).** The mirror image, from a deadline backwards, with `lx` (latest time to be at a stop after alighting, walking on to the target), `bc` (latest boarding when changing, includes footpaths and buffer) and `bd` (latest boarding reached directly from the origin side).

**Rules shared by both:** an access walk is never followed by a footpath (no walk-walk chains). Alight, footpath, egress walk is allowed. Ties are broken by fewer vehicle legs, then less walking, then smallest trip id (`_rank`).

**Nodes** are immutable tuples `(kind, a, b, secs, prev, legs, walk_s, trip_id)` forming linked chains, with kinds `ACCESS`, `RIDE`, `FOOT`, `EGRESS`. The chain is how a journey is reconstructed without storing whole paths.

### 6. Journeys (`journeys.py`)
`JourneyBuilder.items` walks a node chain to ordered walk/ride items. `finish` adds the access or egress walk, merges adjacent walks, drops zero-length walks and assigns times: **rides keep their timetable times, and each walk is timed relative to its neighbouring ride**. Access walks end at boarding. Transfer and egress walks start at alighting. So a `latest` journey's `depart` is exactly the latest departure, and an `earliest` journey's `arrive` is exactly the earliest arrival, but an `earliest` journey's `depart` is *not* `leave_after`. Walk-only journeys are anchored to the query time. `models.py` holds the frozen result dataclasses with `to_json`.

### 7. Engine and windows (`engine.py`)
`Engine._prepare` validates the date and options, runs the coverage check (raising `CoverageError` in strict mode) and fetches the connection window. Then:
- **`earliest` / `latest`**: one scan, pick the best access/egress stop, and compare with a direct walk (within `max_walk_m`, barrier-free). Direct walking counts as a way to arrive and to return. Without that a point beside the origin would be "unreachable".
- **`windows`**: **one forward scan and one reverse scan from the origin**, shared by all N points. Per point: `arrive` = best forward arrival, `leave` = latest departure from the point that still gets the traveller home, `available_s = leave − arrive`. Status is one of `ok`, `no_window` (`available_s < min_stay_s`), `no_return` (reachable but cannot get back) or `unreachable`. `include_journeys=True` also returns the outbound and return legs for each point. Cost is why 200 points take about 0.02 s.
- **`stop_windows`**: the same two scans reported per stop instead of per point (`arrive` = the earlier of the foot and vehicle labels, `leave` = latest boarding there, or walking home).
- **`walk_seconds`**: a helper added for the places layer.

### 8. Coverage (`coverage.py`)
`check` says which feeds have no data for the date. CP's feed ends **2026-12-12**, and Aveiro's runs 2026-10-19 to 2027-09-11. Queries outside a feed's range return normal results (without that feed's services) plus warnings in `Journey.warnings`, `WindowResult.warnings` and the `/journey` response. They don't silently return "no trains". With `strict_coverage` they fail with 422.

## Configuration (`config/engine.toml`)

| Key | Value | Meaning |
|---|---|---|
| `walk_speed_mps` | 1.2 | Walking speed |
| `walk_detour` | 1.3 | Multiplier on straight-line distance |
| `transfer_max_m` | 400 | Longest stop-to-stop footpath |
| `access_max_m` | 1200 | Default point-to-stop walk (per-query `max_walk_m`) |
| `access_max_limit_m` | 2000 | Hard upper bound for `max_walk_m` |
| `transfer_buffer_s` | 120 | Minimum change time between different trips |
| `cache_dates` | 8 | Connection-window LRU size |

## Tests (`tests/`)

- `test_units.py`: time handling (past midnight, both clock changes), both calendar styles, extended route types, walk formula, barriers, transfer buffer, pickup/drop-off, id prefixing, option validation. Several use tiny synthetic feeds built in `tmp_path`.
- `test_golden.py`: G1-G10, with exact expected values computed from the real feeds (bus terminal, Campanhã, Costa Nova, São Jacinto, Águeda). They are **pinned to feed hashes**: if `aveiro.zip` or `cp.zip` changes, they **skip** and name the feed instead of failing. The `GOLDEN_HASHES` in `tests/conftest.py` need updating, and expected values recomputing, after a deliberate feed rebuild. The README table records how each golden value moved from the spec.
- `test_properties.py`: randomised checks that journeys replay consistently, that forward and reverse agree, and that `windows` matches point queries.
- `test_perf_web.py`: load time and memory, scan speed (fail above 2× the README targets) and every HTTP endpoint through `TestClient`.

Note that skipped golden tests report as skips, so a green run after a feed update proves less. Look for skips.

## Gotchas and known limitations

- **Update both hash places when a feed changes**: `config/feeds.toml` (warning only) and `tests/conftest.py` (`GOLDEN_HASHES`, skips). The README text still shows the older `aveiro.zip` hash history.
- **Working directory matters.** Run from the repo root, or pass `--config` and set `REACH_CONFIG`. Paths in `feeds.toml` resolve relative to the parent of the config's parent folder.
- **Walking is crude.** Straight lines, so rivers, rails and motorways are crossable except the single seeded barrier (the Barra channel), which has not been checked on a map. Extend `walk_barriers.geojson` as problems show up.
- **No block continuations.** Staying on a vehicle that changes trip id costs a full 120 s change.
- **Two "accepted fast legs"** (`Issues.md` #4) are treated as achievable.
- **CP data ends 2026-12-12.** Dates beyond it need a new CP file.
- **`windows` needs `back_by` after `leave_after`**, and `latest` searches from 00:00 local on the query date.
- **The `/map` page was never opened in a browser** by the previous developer (the README says so). Its endpoints are covered by tests, but check the page itself with `python -m reach.web` before relying on it.
- **Data quality comes from upstream.** Wrong or missing Aveiro times are fixed in `../converter/` or `../schedules/`, not here. See `../Issues.md`.
- **Real-time delays, fares, accessibility and a street network are out of scope.** Real-time (LiveBus GTFS-RT) is context only, in spec §13.
