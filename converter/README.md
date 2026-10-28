# gtfsconv: Aveiro bus + ferry timetables to GTFS

Implements `GTFSConverter.md`. Reads `../schedules/schedules.csv` (never modified), adds coordinates and exact service dates, and writes one static GTFS feed. Python 3.11+, standard library only; `pytest` for tests.

**Status: M1 to M5 done.** The strict `python -m gtfsconv build` (no flag) passes and writes `data/gtfs/aveiro.zip`: **all 828 trips, 319 stops, 8,741 stop_times, 26 routes, 20 services, validator 0 errors**, real school calendar. `--drop-unplaced` gives the identical feed now that nothing is unplaced. Remaining items are confirmations, not blockers (see "Waiting on").

## Run

```bash
cd converter
python -m gtfsconv fetch                    # Busway stops + Overpass JSON into cache/ (also automatic when cache/ is empty)
python -m gtfsconv build                    # strict: fails on any unresolved issue
python -m gtfsconv build --drop-unplaced    # interim: drops unplaced stops/trips, reports them
python -m gtfsconv review --drop-unplaced   # review files only, no zip
PYTHONPATH=<dir with pytest> python -m pytest
```

Useful options: `--config DIR` (a copy of `config/` for experiments), `--out`, `--build-dir`, `--build-date YYYY-MM-DD` (pins `feed_version`), `--no-validate`, `--validator-jar`. The validator jar defaults to `../MVP_LiveBus/.cache/gtfs-validator-cli.jar` (needs Java).

With the real `config/`, strict `build` and `--drop-unplaced` give the same feed (nothing is unplaced and no time is dropped).

Outputs in `build/` (git-ignored): `report.md`, `unplaced_stops.csv`, `unplaced_stops.geojson`, `name_clusters.csv`, `stops.geojson`, `validator/`.

## Waiting on (spec §7)

1. ~~School calendar 2026/27~~ **Done** (2026-10-19): Despacho n.º 8368/2024, supplied by the user. The last term ends 11 Jun 2027 (user's choice; years 9/11/12 end 4 Jun, 1st cycle 30 Jun).
2. ~~Line 17 `p5t1c6`, `Zibreiros`~~ **Done** (Pedro, 2026-10-19): the PDF prints `17:02`, a source typo; `17:20` is the user's chosen value (`config/time_fixes.csv`). Line 12 `u12b1c1` is decided too (see Decisions).
3. ~~Coordinates~~ **Done** (every stop name is placed). 63 stop names are placed from `config/stop_coords.csv` (Busway/OSM positions proposed by the agent and marked "confirm", plus 23 marked "placed by Pedro"); the rows marked "confirm" still need a human check.
4. ~~Name clusters~~ **Done** (2026-10-19): 6 merged, 2 rejected (Issues.md 6).
5. ~~Aveiro municipal holiday~~ **Confirmed** (Câmara press note, 2026-10-19).
6. ~~Urban route names~~ **Checked** against the sheet titles; six corrected.
7. **Line 3 at `Eucalipto A`.** The change is applied (33 trips; 16 now start there) and `Eucalipto A` is placed from the Busway stops. The 500 m check could not run because the deactivated `Travessa Eucalipto` has no coordinates in any source. Confirm the position and that the first departures still hold.
8. **Service notices:** re-read Aveirobus "Alterações de serviço" and Busway notices before each build.
9. ~~Fused names~~ **Resolved**: all four re-read; second components added via `extra_stop_times.csv` (78 rows). Line 18 `p6t1c19` is decided: it ends at Hospital Rovisco Pais 19:26 as printed (Issues.md 4).

## Strict mode

Passes with the current `config/`: no unplaced stop, no placement conflict, no time error, no leg above 90 km/h except the one accepted in `config/accepted_legs.csv`. The last blockers were resolved by manual positions for `Av. Dr. Lourenço Peixinho - CTT B` and `R. Dr. Mário Sacramento A/B` (Pedro, 2026-10-19).

## Decisions

- **Extra stop times (issue 12, 2026-10-19).** `config/extra_stop_times.csv` inserts stop_times that the PDF prints but the CSV lacks (`source,trip,stop_sequence,stop_name,time,evidence`). The build fails if the row's stop_sequence already exists, so a regenerated CSV cannot silently collide. It holds 78 rows (bus.pdf pages 6, 7, 20, 21), generated and checked by `review/extract_missing_rows.py`. All four fused names now alias to their first component, whose time is the one printed.

Choices the spec left open, or where the code departs from it, with the reason.

- **Line 12 `u12b1c1`: impossible leg kept (Pedro, 2026-10-19).** The sheet prints `Verba B` 08:32 then `Estação Quintas A` 08:34 for 3.43 km (about 103 km/h). Treated as an operator timetable issue: both stops and times are kept as printed. `config/accepted_legs.csv` (`source,trip,from_stop,to_stop,reason`) exempts exactly that leg from pruning and from the 90 km/h check, and `build/report.md` lists it under "Kept impossible legs". Any other fast leg still fails or prunes.

- **Window end.** The spec says `end = 2027-09-12` is "the day before the second Sunday of September 2027", but that Sunday *is* 2027-09-12. Corrected on 2026-10-19: `end = 2027-09-11`.
- **Config comments.** Config CSVs may contain `#` comment lines; they are skipped.
- **Name clean-up** goes slightly beyond the spec's examples: glued abbreviations (`Av.Dr.`, `R.Dr.`), `NVerbaB`, ` -X` hyphen spacing, `EB23CastroMatosoB`. Real hyphenated names (`Albergaria-a-Nova`) and `Z.I.` are kept. Global (cross-line) clusters are reported as well as per-line ones.
- **Seeded aliases** (`config/stop_aliases.csv`, all to be reviewed by a human): the five `N ` names; the OCR family `Lourengo/Lourenco`, `Qulntas`, `Satide/Saide`, `Eix0`, `Cagadores`, `Escolasde`, `R,`; noise names (`I_orie …`, `M … I_R`, reversed "Horários" with spaced letters) mapped to the plain stop seen elsewhere in the CSV. (`Farol - Forte da Barra Largo Arrais Ançã…` was first read as the terminal and later corrected to Farol, see the extra-stop-times decision.) **Fused names** are always the last stop of a trip with one printed time. For `Caçadores 10 - Sé Terminal Rodoviário…`, `Hospital / Universidade B (Aveiro) Terminal Rodoviário…` the time was taken as the terminal's, because the previous stop is already present or the Ida/Volta running times fit. For `Hospital Rovisco Pais Figueira da Foz (Estação CP)` the time fits only the first component (Tocha to the fused stop is 7 to 10 minutes), so it is aliased to `Hospital Rovisco Pais` and the Figueira terminal time is missing from the CSV. `names.find_fused` only flags; it also flags `Igreja de Taboeira A/B`, which are real stops.
- **Candidate tiers.** Spec order kept (manual, Busway exact, Busway fuzzy, OSM exact, OSM fuzzy), with two additions: matching also tries abbreviation-expanded keys (`Av.` = `Avenida`, `R.` = `Rua`, …) and, right after each source's exact tier, the name without a trailing `(Town)` qualifier. Fuzzy matches are rejected when the digits differ (`Escola nº1` is not `Escola n.º 2`). Only the first non-empty tier is used for a trip, unless that gives an impossible speed, in which case all tiers are merged.
- **Pruning.** After placement, a stop whose chosen candidate implies more than 90 km/h against its neighbours (the only candidates were in another town) is treated as **unplaced for that trip**, worst offender first, instead of failing the build with a wrong coordinate. It then follows the normal unplaced rules and is listed in the report as "pruned".
- **Interim mode (`--drop-unplaced`)** additionally (a) drops an intermediate stop_time whose time breaks monotonicity (line 17) or drops the trip if it is the last stop, and (b) snaps 50 to 300 m placement conflicts for one name to the cluster with more stop_times (strict mode fails and lists them). Both are reported. Interim run: three conflicts snapped (`Av. Dr. Lourenço Peixinho - CTT B`, `R. Dr. Mário Sacramento A/B`).
- **Ferry stops** come only from OSM `amenity=ferry_terminal` nodes (AveiroBus operator preferred): `Forte da Barra` and `São Jacinto`. The quay is 70 m from the bus stop `Forte da Barra - Ferry`, so they stay separate stops.
- **stop_id** is a slug of the canonical name; colliding slugs get `-2` (for example `forte-da-barra-ferry` is the quay, `forte-da-barra-ferry-2` the bus stop).
- **direction_id.** Busway `Ida/Volta` routes use the route text (`AVEIRO …` = 0). For `<->` routes and Aveirobus lines: 0 when the trip's first stop equals the most common first stop of the line's first `direction` group, else 1; `Circular` = 0. Trips that start mid-route therefore get 1.
- **service_id** follows the spec's examples: Busway ids carry the season code (`bw-sab-A`), Aveirobus ids carry note slugs (`ab-du-escolar-qua-sex`); identical date sets share one service.
- **`feed_publisher_url`** is `https://example.com`, not `https://example.invalid`: the MobilityData validator rejects the `.invalid` TLD (`invalid_url` error).
- **Accepted validator warnings** (interim feed: 0 errors, 80 warnings):
  - `missing_bike_allowance` (52, ferry trips): the source has no bicycle information and none is invented.
  - `missing_feed_contact_email_and_url` (1): there is no contact for an internal-use feed.
  - `mixed_case_recommended_field` (27): the ferry stop names `FORTE DA BARRA (FERRY)` and `S. JACINTO` are all caps in the source PDF; kept as printed.
- **Test environment.** `pytest` was installed outside the system Python (`pip install --target ~/.cache/pytest-lib pytest`, run with `PYTHONPATH`) because system pip is externally managed.

## Known limitations

- No trip or stop_time is dropped. Coordinates come from stop names; the 63 manual rows marked "confirm" (agent-proposed from Busway/OSM) and the fuzzy matches are plausible, not verified.
- Coordinates come from stop names. Matches on `busway-fuzzy` / `osm-fuzzy` (30 names) and on qualifier-stripped names are plausible, not verified; check `build/stops.geojson`.
- Times are as printed. After-midnight trips are detected only when a time drops by more than 12 hours.
- The CSV rows exist only for printed times, so stop_sequence gaps in the CSV mean stops without a time on that trip.
- Aveirobus sheets dated 2025/02-2026 are assumed valid for the whole window (also stated in the report).
- Not for publication: the source timetables belong to Busway and Aveirobus and their licence is unverified.

## Tests

`pytest` (86 tests, about 12 s): all pass, including the two `test_m5_*` (they run now that nothing is unplaced). Integration tests still use the fixture calendar (`tests/fixtures/`), whose dates differ slightly from the official ones, so the M2 counts are independent of the real calendar. Nothing is skipped when `cache/` is populated; without it the integration tests skip and ask for `python -m gtfsconv fetch`. Unit tests cover names, rollover, monotonicity, holidays, every day type / season code / note rule, Viterbi placement, pruning and merging. Integration tests run the real CSV with the fixture school calendar: input facts, the M2 date counts (line 36: 33/29/29, line 35: 0/34, ferry 26/26, Azul and Verde 11/23), the Line 3 and Line 6 service changes, ferry stop placement, bounding box and speed, validator 0 errors and a byte-identical rebuild.
