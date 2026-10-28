# data/: static GTFS feeds

This folder holds the **static GTFS feeds** the projects in this repository read. It has no code. Each feed is a pinned file, and the projects that use it check its SHA-256.

```
data/gtfs/
  cp.zip                 CP trains (Comboios de Portugal), as published       → engine
  aveiro.zip             Aveiro buses and ferry, built by converter/          → engine
  tcb-barreiro.zip       Barreiro bus feed (TCB), with an unzipped copy       → MVP_LiveBus
  tcb-barreiro/          the same feed unzipped
```

## The feeds

| File | What | Source | Validity | SHA-256 (checked 23 Oct 2026) |
|---|---|---|---|---|
| `gtfs/cp.zip` | CP train timetables: 454 stops, 186 routes, 1,945 trips. Route types 2 and the extended 109. | `https://publico.cp.pt/gtfs/gtfs.zip`, downloaded 18 Oct 2026 and pinned in the repo | 2025-12-14 to **2026-12-12** | `48813f44…` |
| `gtfs/aveiro.zip` | Aveiro urban and intercity buses plus the ferry: 319 stops, 26 routes, 828 trips, 8,741 stop_times. Calendar by `calendar_dates.txt` only, no shapes. | **Generated** by `../converter` (`python -m gtfsconv build`) from `../schedules/schedules.csv`. `feed_version` `20261019-cb0e4432`, publisher "YourDayTrip (unofficial, internal use)". | 2026-10-19 to 2027-09-11 | `205a30af…` |
| `gtfs/tcb-barreiro.zip` | Transportes Colectivos do Barreiro (Barreiro, south of Lisbon): the static feed for the LiveBus demo. Has shapes, blocks, `layover.txt` and `dead_runs.txt` (operator extras). | The operator's own feed, version "UTC: 06-Oct-2026 08:57", copied 15 Oct 2026 | 2026-10-06 to 2026-12-31 | `99c23ad3…` |

`gtfs/tcb-barreiro/` is the unzipped copy of `tcb-barreiro.zip`. `../MVP_LiveBus/data/gtfs/` has an identical copy (same hash) so that project's Docker build stays self-contained.

## Who uses what

- **`../engine`** (`reach`) loads `cp.zip` and `aveiro.zip` side by side. `engine/config/feeds.toml` pins both hashes. A mismatch is a warning, not an error. `engine/tests/conftest.py` pins the same hashes for the golden tests, which **skip** (naming the feed) if a hash changes.
- **`../planner`** reads the `feed_info.txt` build date for its "data as of" disclaimer, through the engine.
- **`../MVP_LiveBus`** loads only `tcb-barreiro.zip`.
- **`../converter`** writes `aveiro.zip`.

## Changing a feed

1. **Rebuild Aveiro:** `cd converter && python -m gtfsconv build` rewrites `data/gtfs/aveiro.zip`. The output is deterministic for the same input and `--build-date`. Fix data errors in `converter/config/`, not in the zip.
2. **After any change to a zip**, update the hash in `engine/config/feeds.toml` and in `engine/tests/conftest.py` (`GOLDEN_HASHES`), then rerun `python3 -m pytest engine/tests` and look for skips. Recompute the golden values if they moved (`engine/README.md` explains how earlier changes were handled).
3. **CP expires on 2026-12-12.** Before then, download the new timetable, replace `cp.zip`, update its hash and re-run the golden tests (`../Issues.md` #1 lists this as a launch condition). Until then, queries after that date return coverage warnings, and plans from Porto usually fail (`no_plan`).
4. **TCB expires on 2026-12-31.** `MVP_LiveBus` then falls back to a simulated clock of 15 Oct 2026.

## Licensing and cautions

- The Aveiro feed is derived from the operators' public PDF timetables (Busway-CirAveiro and Aveirobus). The decision of 22 Oct 2026 (`../Issues.md` #1) is to launch on it without waiting for permission, **but not to share the GTFS files outside the project**, and the conditions listed there must be met before any public launch (OSM credit, disclaimers, no implied affiliation, a courtesy email, the CP update step).
- Do not publish `aveiro.zip` under the operators' names or submit it to journey planners. `feed_publisher_url` is a placeholder (`https://example.com`).
- The TCB feed's licence is not verified: internal development and demos only.
- CP's feed is used as published. Check CP's terms before any public use.
- Do not edit the zips by hand: the converter's reports and the engine's pinned hashes assume they are untouched.
