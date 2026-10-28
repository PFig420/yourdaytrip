# reach: public transport reachability engine

Answers "leaving here after T and back by D, when can I first be at each point, when must I leave it, and how?"
from the pinned CP and Aveiro GTFS feeds plus walking. Spec: `../ReachabilityEngine.md`. The core (`reach/`) uses
only the standard library; the HTTP API and map need FastAPI.

## Run

From the repo root (`MVP-LiveBus/`); the config paths are relative to it:

```bash
pip install -e "engine[web,dev]"            # or: export PYTHONPATH=engine
python3 -m pytest engine/tests               # 42 tests
python3 -m reach earliest --from 41.14872,-8.58484 --to 40.6125,-8.74974 --date 2026-10-24 --after 08:00
python3 -m reach latest   --from 40.6125,-8.74974 --to 41.14872,-8.58484 --date 2026-10-24 --back-by 21:00
python3 -m reach windows  --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 --points points.csv
python3 -m reach coverage --date 2026-12-19
python3 -m reach.web                         # http://127.0.0.1:8100/map  (local only, Issues.md #1)
```

`points.csv` is `id,lat,lon` (header optional). Add `--json` before the subcommand for JSON, `--journeys` on `windows`
for outbound and return legs. Python API: `reach.load("engine/config")` then `earliest`, `latest`, `windows`,
`stop_windows`, `coverage` (spec §6.1).

## Performance (this machine, Python 3.14, WSL2)

| Target | Measured |
|---|---|
| Load both feeds and build footpaths < 3 s | 0.15 s |
| Forward + reverse scan, one date < 0.5 s | 0.001 s (early termination), 0.04 s including the first connection build for a date |
| `windows`, 200 points < 1 s | 0.02 s (with journeys 0.02 s) |
| Memory < 300 MB | 63 MB peak RSS |

`tests/test_perf_web.py` fails above 2x these targets.

## Decisions

- **Engine class in `engine.py`**, result dataclasses in `models.py`, journey rebuilding in `journeys.py`; `windows.py`
  from the spec's layout is folded into `engine.py` because it only orchestrates the two scans.
- **Connection window per query date** = service dates D-1, D and D+1 (always, so late deadlines and past-midnight trips
  work), cached by (date, filters). Connections are parallel lists sorted by (dep, arr, trip, hop number).
- **Trip instances**: one trip on two service dates is two vehicles (`Connections.trips`).
- **Two arrival labels per stop** in the forward scan: arrival on foot from the origin (no boarding buffer) and arrival by
  vehicle plus footpath (120 s buffer). The reverse scan mirrors it with `bd` (direct, used for the point's access) and
  `bc` (changing, includes footpaths). Access walks are never followed by a footpath, matching the "no transitive
  closure" rule; alight, footpath, egress walk is allowed in both directions.
- **Direct walking** from origin to a point (within `max_walk_m`, no barrier) counts as a way to arrive and to return.
  The spec is silent; without it a point beside the origin would be "unreachable".
- **Walk timing in journeys**: an access walk is scheduled to end at boarding, transfer and egress walks start at the
  previous alighting. So a `latest` journey's `depart` is exactly the latest departure, and an `earliest` journey's `arrive`
  is exactly the earliest arrival; the `depart` of an `earliest` journey is therefore not `leave_after`.
- **Time inputs**: `HH:MM[:SS]` on the query date, or a full ISO datetime for the next day. `back_by` must be after
  `leave_after` for `windows`. `latest` searches from 00:00 local on the query date.
- **Coverage** is the span of `calendar.txt` start/end dates and `calendar_dates` dates of a feed. For `av` that is
  2026-10-19 to 2027-09-11 (Issues.md #9: the feed window ends a day early on purpose).
- **Stop-level table** (`stop_windows`): `arrive` = earlier of foot and vehicle arrival at the stop, `leave` = latest
  boarding time there (including via a transfer footpath) or walking home. Only stops with at least one value are listed.
- **Hash mismatch** is a warning in `Engine.warnings` and `/health`. `feeds.toml` pins the current `aveiro.zip`.
- Only stops with `location_type` 0 (or empty) are loaded; neither feed has stations.
- `exclude_route_types` takes mapped types (rail 2, bus and coach 3, ferry 4).

## Golden values (important)

`aveiro.zip` was rebuilt twice on 2026-10-19 after the spec was written (Issues.md #12 resolved: Sunday and Saturday Costa
Nova times restored). Its hash is now `205a30af...` (rebuilt again for Issues.md #4-#9: fused names restored, line 18 and line 36 weekend arrivals, `p6t1c19` ending at Rovisco Pais). It was `b3b007b6...` after #12 alone and `88927e86...` in spec section 2.1. **No golden value changed** between `b3b007b6` and `205a30af`: all 8 golden tests pass unchanged. `tests/test_golden.py` therefore
pins the new hash and **skips, naming the feed, if either hash changes**.

| Golden | Spec | Now | Why |
|---|---|---|---|
| G2, G4, G8, G9 | as spec | as spec | unchanged by the rebuild |
| G1 | arrive 09:31 | 09:40 | 09:31 was the Farol time; Costa Nova is 09:40 (trip `bw-36-p20t2c3`, checked in `stop_times`) |
| G3 | 10:12 | 10:20 | same trip family, `bw-36-p20t2c4` |
| G5 | Costa Nova 18:10, S. Jacinto 18:00 | 18:45 both | new return buses/ferry (`bw-36-p21t2c14`, `ab-ferry-b3t11`); Águeda 18:53 and bus terminal 20:11:32 unchanged |
| G6 | 7:58 / 7:25 / 8:18 | 8:25 / 8:10 / 8:18 | follows G3 and G5 |
| G7 | 10:12, 09:45 | 10:20, 09:45 `no_window` | follows G3 |
| G10 | `xfail` (returns 21:05) | plain regression test, arrives 10:20 | Issues.md #12 is resolved |

## Known limitations

- Walking is straight-line with a 1.3 detour factor; only one barrier (the Barra channel) is seeded and it is unchecked on
  a map (`walk_barriers.geojson`). Other water crossings can be walked through.
- Block continuations are not modelled: a different trip id always needs the 120 s change time.
- `cp.zip` ends 2026-12-12; later dates return the coverage warning (or 422 with `strict_coverage`).
- The two accepted fast legs (Issues.md #4) are treated as achievable.
- `/map` has not been opened in a browser in this session (no browser available); its API calls are covered by the
  HTTP tests and the page uses the same endpoints. Check it with `python -m reach.web` before relying on it (milestone M5).
- Tie-breaks follow spec 5.6/5.7 per label, not globally optimal across all journeys of equal arrival.
- `Engine.walk_seconds(a, b)` (added for the places layer): walking seconds between two `(lat, lon)` points with the engine's speed and
  detour factor, or `None` when a barrier blocks the straight line. No distance cap.
