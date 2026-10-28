# converter/: `gtfsconv`, Aveiro timetables to GTFS

`gtfsconv` turns `../schedules/schedules.csv` (flat timetable rows extracted from PDFs) into one static GTFS feed, `../data/gtfs/aveiro.zip`. The reachability engine (`../engine/`) reads that feed next to CP's train feed.

The CSV has times and stop *names* only. The converter adds what GTFS needs and the PDFs don't have: clean and merged stop names, **coordinates**, **exact service dates** (holidays and the school calendar) and routes, directions and headsigns.

The spec is `../GTFSConverter.md` (section numbers like "spec 5.4" in the code refer to it). `README.md` has the status, the decision log and the known limitations. `../Issues.md` tracks open data problems. This document explains how the code and config fit together.

```
../schedules/schedules.csv ──┐
config/*  (human decisions) ─┤
cache/GTFS_CIRA.zip (Busway stops) ─┤──► gtfsconv build ──► ../data/gtfs/aveiro.zip
cache/overpass.json (OSM stops)  ───┘            │
                                                 ├─► build/report.md, unplaced_stops.*, name_clusters.csv, stops.geojson
                                                 └─► build/validator/ (MobilityData validator, needs Java)
```

Python ≥ 3.11, standard library only (`pytest` for tests). The enclosing git repo has no commits yet, so there is no history to consult.

## Layout

| Path | Purpose |
|---|---|
| `gtfsconv/` | The package, described below. |
| `config/` | Everything a human decides. **This is where fixes go**, never in the CSV or the PDFs. |
| `cache/` | Downloaded coordinate sources: Busway's GTFS (`GTFS_CIRA.zip`) and an Overpass/OSM extract (`overpass.json`). Git-ignored. |
| `build/` | Generated reports and validator output. Git-ignored. |
| `review/` | Review files for humans (stop and alias candidates) and a one-off tool, `extract_missing_rows.py`, that is not part of the package (needs `pdfplumber`). |
| `tests/` | 86 pytest tests, plus `fixtures/school_2026_27_fixture.toml`. |

## Running it

From `converter/`:

```bash
python -m gtfsconv fetch                   # download Busway stops + OSM stops into cache/ (also automatic if cache/ is empty)
python -m gtfsconv build                   # strict: fails on any unresolved issue
python -m gtfsconv build --drop-unplaced   # interim: drops what can't be placed, and reports it
python -m gtfsconv review --drop-unplaced  # review files only, no zip
PYTHONPATH=<dir with pytest> python -m pytest
```

Options: `--csv`, `--out` (default `../data/gtfs/aveiro.zip`), `--config DIR` (a copy of `config/` for experiments), `--cache`, `--build-dir`, `--build-date YYYY-MM-DD` (pins `feed_version`, so builds are reproducible), `--no-validate`, `--validator-jar`. Exit code is 1 on failure.

The validator jar defaults to `../MVP_LiveBus/.cache/gtfs-validator-cli.jar` and needs `java` on the PATH. If either is missing, the validation step is skipped and the report says why. It does not fail the build.

**Current state (23 Oct 2026):** the strict build passes (`build/report.md`: 828 trips, 26 routes, 20 services, 319 stops, 8,741 stop_times, validator 0 errors), and all 86 tests pass (about 12 s). Two statements in the README are out of date: the "Run" section still says strict `build` stops at line 17 `p5t1c6`, and the "Tests" section says 85 tests. The "Status" line at the top is the accurate one.

## Pipeline (`build.py`)

`build.build()` is the whole pipeline, in this order. Each step reports into `Result.report`, which `report.py` renders.

1. **Load** (`load.py`). Read the CSV and group rows into trips keyed by `(source, trip)`. Apply, in this order:
   - `time_fixes.csv`: replaces one time. It **fails if the CSV's current time isn't the fix's `old_time`**, or if a fix matches no row, so a regenerated CSV can't silently invalidate a fix.
   - `extra_stop_times.csv`: inserts stop_times the PDF prints but the CSV lacks. It fails if that `stop_sequence` already exists.
   - `exclusions.csv`: drops whole trips (empty today).
2. **Names** (`names.py`). `clean_raw` fixes OCR artefacts (stray `N ` prefix, run-together words, glued abbreviations, hyphen spacing). `stop_aliases.csv` maps cleaned raw names to canonical ones. Then it computes OCR *clusters* (report only, never applied automatically, to `build/name_clusters.csv`) and flags *fused* names (one stop name containing another, which happens when the PDF prints two stops as one row).
3. **Calendar and route setup.** `calendars.load_calendar` reads `feed.toml`. `apply_service_changes` applies `service_changes.csv` (`replace_stop` renames a closed stop to its replacement on a line, `info` just records a notice). Then `routes.assign_directions` sets `direction_id`.
4. **Times** (`times.py`). A time more than 12 h earlier than the previous stop gets +24 h, and so does every later stop (past-midnight trips). A trip whose times still go backwards fails the build, pointing at `time_fixes.csv`. In `--drop-unplaced` mode the offending stop_time (or trip, if it's the last stop) is dropped and reported instead.
5. **Service dates** (`calendars.py`), before placement so it's known which trips exist. Each trip's dates come from the **AND** of three rules over the feed window: its *day type*, its *season code* and any *notes*. Trips with identical date sets share one `service_id`. Trips with no dates are listed and dropped.
6. **Stop placement** (`stops.py`). See below.
7. **Checks and tables.** Every leg between placed stops must be at most 90 km/h, all stops must be inside the bounding box (lat 40.05..41.00, lon -8.95..-8.20), and `route_id`/`trip_id` must be unique. Then it builds the seven GTFS tables.
8. **Output** (`__main__.py`). `validate.internal_checks` (referential integrity, no decreasing times), `write.write_zip`, then the external validator, then `build/report.md`.

### Calendars in detail
- `feed.toml` holds the window (`2026-10-19` to `2027-09-11`, the day before the second Sunday of September 2027, when school-year services restart) and the school terms. National holidays are **computed** (Easter-based ones too). Carnival is off. Aveirobus also has the municipal holiday 12 May. Busway has none.
- Day types are matched on the accent-folded label: `DAY_TYPES` in `calendars.py`. A label the table doesn't know raises `BuildError`. So does an unknown season code (`SEASONS`: `A`, `C`, `D`, `E`, `EA`, `PE`, `FE`).
- `PE` / `FE` and school notes depend on `[[school.terms]]`. If the terms are empty the build fails.
- Notes are classified by `classify_note` (school, Wed/Fri school, Mon/Tue/Thu school, "2nd Sunday of September to 30 June", "start of school season to 30 June"). Notes containing "ligação" are informational. **An unrecognised note fails the build** with a message telling you to add a rule.
- Output is `calendar_dates.txt` only (all `exception_type` 1), with no `calendar.txt`.
- **`service_id`s** carry meaning: `bw-sab-A` (Busway, Saturdays, season A), `ab-du-escolar-qua-sex` (Aveirobus, weekdays, Wed/Fri school days).

### Stop placement in detail
The CSV has no coordinates, so each stop name is looked up in several sources, in priority tiers:

1. `config/stop_coords.csv` (manual, wins outright)
2. Busway exact, then Busway with the trailing `(Town)` removed, then Busway fuzzy (similarity ≥ 0.88)
3. OSM exact, OSM without the qualifier, then OSM fuzzy

Matching uses accent-folded keys, also with abbreviations expanded (`Av.` = Avenida, `R.` = Rua...), and the `A`/`B` side suffix is stripped for lookup. Fuzzy matches are rejected when the digits differ. Ferry stops use only OSM `amenity=ferry_terminal` nodes.

A name often has several candidates (the same name exists in different towns). `place_trip` picks one candidate per stop with a **Viterbi** pass, minimising total distance along the trip and penalising speeds above 90 km/h, using the first non-empty tier. If that still gives impossible legs, it retries with all tiers merged. A stop that is still impossible against its neighbours is **pruned** (treated as unplaced for that trip, worst offender first). The leg listed in `accepted_legs.csv` is exempt from both pruning and the speed check.

Then `merge_stops` combines placements across trips into GTFS stops:
- Positions within 50 m of each other under one name become one stop (the average, weighted by number of stop_times).
- Positions 50-300 m apart under one name are a **conflict**: strict mode fails and lists them. Resolve them in `stop_coords.csv`. Interim mode snaps the smaller cluster to the larger.
- Positions more than 300 m apart become separate stops, suffixed with the nearest OSM place name.
- `stop_id` is a slug of the canonical name, with `-2` and so on for collisions.

Stop names that cannot be placed are written to `build/unplaced_stops.csv` with the nearest candidates, as a to-do list for `stop_coords.csv`. Strict mode fails if there are any. The interim mode drops the stop (or the trip, if it's the first or last stop). Today nothing is unplaced, so both modes give an identical feed.

### Routes, trips, outputs
- `route_id`: `bw-<line>` (Busway), `ab-<line>` (Aveirobus), `ab-15-azul`, `ab-15-verde`, `ab-ferry`. Every route needs a `long_name` entry in `config/routes.toml`, or the build fails. Ferry is `route_type` 4, everything else 3.
- `trip_id` is `<route_id>-<csv trip id>`. `trip_headsign` is the last stop with its A/B side removed.
- `direction_id`: Busway lines with an `Ida/Volta` route use the route text (`AVEIRO ...` = 0). Others use "does the trip start at the line's most common first stop" (0, else 1). Circular is 0. Trips starting mid-route therefore get 1.
- Files written: `agency, stops, routes, trips, stop_times, calendar_dates, feed_info`. No `shapes`, no `calendar.txt`. `write.py` produces a **byte-identical zip** for the same input (fixed timestamps and ordering), which a test checks.
- `feed_version` = `<build date>-<first 8 chars of the CSV's SHA-256>`, for example `20261019-cb0e4432`.
- `feed_publisher_url` is `https://example.com`, because the validator rejects `.invalid` URLs.

## Configuration (`config/`)

| File | Role |
|---|---|
| `feed.toml` | Feed window, agencies, holiday settings, school terms. |
| `routes.toml` | Clean `long_name` (and optional `desc`, colours) per `route_id`. |
| `stop_aliases.csv` | `raw,canonical`: merge OCR variants and noise names into the real stop. Seeded by an agent, each row still needs human review (about 55 rows plus comments). |
| `stop_coords.csv` | `canonical,lat,lon,source`: manual coordinates, 63 names. Rows marked "confirm" are proposals from Busway/OSM that still need a human check. |
| `time_fixes.csv` | `source,trip,stop_sequence,old_time,new_time,evidence`: one entry, line 17 `p5t1c6` (17:02 to 17:20). |
| `extra_stop_times.csv` | `source,trip,stop_sequence,stop_name,time,evidence`: 78 rows from `bus.pdf` pages 6, 7, 20, 21. Generated by `review/extract_missing_rows.py`. |
| `service_changes.csv` | `line,kind,old_stop,new_stop,from_date,to_date,evidence`: line 3 `Travessa Eucalipto` to `Eucalipto A`, and an info-only notice for line 6. |
| `accepted_legs.csv` | `source,trip,from_stop,to_stop,reason`: the one impossible leg kept (line 12, 3.4 km in 2 minutes as printed). |
| `exclusions.csv` | `source,trip,reason`: trips to drop. Empty. |

CSV configs allow `#` comment lines. **Always fill in `evidence`/`reason`**: these files are the project's audit trail.

## Build outputs (`build/`)

- `report.md`: status, input hashes, counts, every fix/insertion/drop/conflict, accepted validator warnings. **Read it after each build.**
- `unplaced_stops.csv` / `.geojson`: what still needs coordinates (empty now).
- `name_clusters.csv`: suspected OCR duplicates for review.
- `stops.geojson`: every output stop. Open it on a map to check placements (the fuzzy-matched and "confirm" ones are plausible, not verified).
- `validator/`: MobilityData `report.json`/`report.html`. Accepted: 0 errors and about 80 warnings (ferry `missing_bike_allowance`, `missing_feed_contact_email_and_url`, all-caps ferry stop names).

## Tests (`tests/`)

- `test_names.py`, `test_calendars.py`, `test_stops.py`, `test_times_load.py`: unit tests (name clean-up, rollover and monotonicity, holidays, every day type / season / note rule, Viterbi placement, pruning, merging, `time_fixes` guard rails).
- `test_build.py`: integration tests on the **real CSV**. They need `cache/` (they skip otherwise, with a message to run `fetch`) and use `--drop-unplaced` with a **fixture school calendar** (`tests/fixtures/`, slightly different dates from the official ones). Date-count assertions are therefore independent of the real calendar. They also check the line 3 and 6 changes, ferry placement, speed limits, validator 0 errors and the byte-identical rebuild.

## Gotchas

- **Never edit `../schedules/schedules.csv` or the PDFs here.** Fix data in `config/`. If the CSV is regenerated, `time_fixes.csv` and `extra_stop_times.csv` will refuse to apply (by design) when they no longer match, so review them.
- **`EXPECTED_SHA`** in `build.py` is the CSV hash the config was written against (`cb0e4432...`). A different CSV only adds a warning to the report, but treat it as a prompt to re-check the config.
- **Engine golden tests depend on this feed.** `../engine/tests` pin `aveiro.zip`'s hash and skip when it changes. After a deliberate rebuild, update the pins in `../engine/config/feeds.toml` and `../engine/tests/conftest.py`.
- **Rebuilds depend on cached sources.** `cache/` holds the Busway and OSM data used for coordinates. `fetch` overwrites it, which can move stops. The report records both hashes.
- **Known data problems come from upstream** (OCR noise, fused rows, source typos). See `../Issues.md` and `README.md`'s "Known limitations". Aveirobus sheets dated 2025/early 2026 are assumed valid for the whole window, and the source timetables' licence is unverified, so the feed is **not for publication** (`Issues.md` #1).
- **Open confirmations** (README "Waiting on"): the `Eucalipto A` position (the 500 m check couldn't run, because the old stop has no coordinates anywhere), the 63 "confirm" rows in `stop_coords.csv`, and re-reading the operators' service notices before each build.
- **Python environment:** system pip is externally managed on this machine, so pytest was installed to `~/.cache/pytest-lib` and is run with `PYTHONPATH`.
