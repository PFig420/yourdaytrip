# GTFSConverter.md — Aveiro bus + ferry timetables to GTFS

This is the complete brief for converting the Aveiro bus and ferry timetables in `schedules/schedules.csv` into one valid static GTFS feed. It is written for a coding agent working alone in this repository. Read it fully before writing code. Where this spec is silent, choose the simplest option that meets the acceptance criteria (§11) and record the choice in `converter/README.md` under "Decisions".

## 1. What the converter does

```
schedules/schedules.csv ─┐
  (8,663 rows, from PDFs) │
                          ├─► clean ─► place stops ─► build calendars ─► write GTFS ─► validate
Busway open GTFS ─────────┤    names    (coordinates)    (dates per trip)   aveiro.zip    MobilityData
  (stop coordinates only) │                                                              validator
OpenStreetMap bus stops ──┤
config/*.csv, *.toml ─────┘  (hand-maintained fixes, aliases, coordinates, calendars)
```

Output: `data/gtfs/aveiro.zip`, one feed with two agencies (Busway-CirAveiro and Aveirobus), 35 routes (bus + one ferry), every trip in the CSV, real coordinates for every stop, and exact service dates.

The feed is consumed by the YourDayTrip reachability engine, alongside CP's separate train feed. It is **not** for publication (§12).

### In scope
- Read the CSV as-is; never edit it. All corrections live in `config/`.
- Clean OCR noise in stop and route names.
- Give every stop real coordinates, from the Busway GTFS, OpenStreetMap or a hand-maintained file.
- Turn day types, season codes and notes into explicit service dates.
- Write a deterministic GTFS zip and validate it with zero errors.
- Write a build report listing every fix, every unplaced stop and every assumption.

### Out of scope
- Re-parsing the PDFs. `schedules/pdf_schedules_to_csv.py` already did that; the CSV is the input.
- Merging CP trains into this feed. CP stays a separate feed.
- `shapes.txt`, fares, `transfers.txt`, real-time.
- Geocoding stops from street addresses or town centroids. A stop is placed from a real bus-stop record or by a human, never guessed.

## 2. Inputs

### 2.1 The timetable CSV

| Item | Value |
|---|---|
| File | `schedules/schedules.csv` (UTF-8 with BOM) |
| SHA-256 | `cb0e4432daf109914c401868eb3e49236e92e4b36ec282f83f0033c21a0e2130` |
| Produced by | `schedules/pdf_schedules_to_csv.py` and `schedules/urbano_parser.py`, from the PDFs in `schedules/pdfs/` |
| Columns | `source, mode, page, line, route, direction, day_type, season, trip, stop_sequence, stop_name, time, note, valid_from, extraction` |
| Grain | One row per (trip, stop) with a printed time |

A trip is identified by `(source, trip)`. Record the CSV's SHA-256 in the build report; if it changes, the build still runs but the report says so.

### 2.2 Facts measured from the CSV (18–19 Oct 2026)

Write tests that assert the key ones (§10).

- **Size:** 8,663 rows, 828 trips, 363 distinct stop-name strings. No trip has a single stop, no duplicate `(source, trip, stop_sequence)`, and every trip has exactly one `day_type` and one `season`.
- **Times:** all `HH:MM`, never blank, maximum hour 23. Trips running past midnight are written as `00:xx` (§5.3).
- **Modes and sources:**

  | `mode` | Source | Lines | Trips | `valid_from` | Extraction |
  |---|---|---|---|---|---|
  | `bus` | `pdfs/bus/bus.pdf` — Busway-CirAveiro booklet, "Entrada em vigor: 14 de setembro de 2026" | 16, 17, 18, 19, 20, 25, 32, 33, 34, 35, 36, 37 | 354 | 2026-09-14 | text |
  | `bus_urban` | 13 Aveirobus sheets in `pdfs/urbano/` | 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, Azul, Verde | 422 | 2025-06-01 (lines 1–7, 10, 12), 2026-09-13 (11), 2026-10-01 (Azul, Verde), blank (8). The files for lines 2, 6 and 8 are named `…202602`, so the sheet date read for 2 and 6 may be stale | mostly OCR |
  | `ferry` | `pdfs/ferry/ferry.pdf` — Aveirobus river ferry, "Em vigor a partir de 1 de maio de 2024", 1st edition March 2025 | Ferry | 52 | 2024-05-01 | text |

- **Busway trips by line** (trips in the CSV): 16: 42 · 17: 12 · 18: 72 · 19: 32 · 20: 12 · 25: 5 · 32: 4 · 33: 22 · 34: 7 · 35: 34 · 36: 105 · 37: 7.
- **Busway `route` values** lost their arrow in extraction: `AVEIRO  COSTA NOVA` (two spaces) and `COSTA NOVA  AVEIRO` are the two directions of line 36. Lines 32 and 34 have one `route` value with `<->` covering both directions.
- **Ferry:** 26 crossings on `Dias Úteis` and 26 on `Sábados, Domingos e Feriados`, each trip two stops (`S. JACINTO`, `FORTE DA BARRA (FERRY)`), always 15 minutes.
- **Stop names:** 188 names end in a side suffix `A` or `B` (opposite sides of the road; keep them as separate stops). Five names carry a stray OCR prefix `N ` (`N Av. Dr. Lourenço Peixinho - CTT B`, `N Eixo - Praca J. Magalhães Lima B`, `N Escolas de Nariz`, `N Fonte da Vessada`, `N Lavadouros de Sarrazola B`). OCR variants of one stop exist, for example `Lourengo`/`Lourenço`, `Qulntas`/`Quintas`, `Satide`/`Saúde`, `Eix0`/`Eixo`, `Av.25deAbril -EscolaJoseEstevaoB`.
- **Two stops fused into one name:** `Caçadores 10 - Sé Terminal Rodoviário de Aveiro` (line 36) is `Caçadores 10 - Sé` followed by `Terminal Rodoviário de Aveiro`. `names.py` must flag any name that contains another known stop name as a suffix; the fix (split into two stop_times, or alias to one) is a human decision recorded in `config/`.
- **Route names of urban lines are OCR-garbled** (`CARREGA EX IAVERO`, `VERBANARIZAVEIRO`). Clean names come from `config/routes.toml` (§6.1), not from the CSV.
- **One known time error:** line 17, trip `p5t1c6` (`pdfs/bus/bus.pdf`, page 5): `Dornelas (2)` 17:15 → `Zibreiros` **17:02** → `Paçô (2)` 17:29. Probably 17:22; must be confirmed against the PDF (§10).

### 2.3 Day types, season codes and notes

**Day types** (`day_type`), with trip-row counts:

| `day_type` | Mode | Rows |
|---|---|---|
| `Dias Úteis` | bus, bus_urban, ferry | 1,411 / 4,344 / 52 |
| `Dias Úteis (exceto agosto)` | bus_urban | 180 |
| `3ª feiras (DU)` | bus | 12 |
| `Sábados` | bus | 405 |
| `Domingos` | bus | 18 |
| `Domingos e Feriados` | bus | 259 |
| `Fim-de-semana e Feriados` | bus_urban | 1,598 |
| `Sábados, Domingos e Feriados` | ferry | 52 |
| `Domingos ou 2.ª feira se feriado` | bus_urban (line 11) | 108 |
| `Todos os dias` | bus | 20 |
| `TODOS OS DIAS (Julho e Agosto)` | bus | 204 |

**Season codes** (`season`, Busway only). Legend from page 22 of `bus.pdf`:

| Code | Meaning | Rows |
|---|---|---|
| `A` | Anual — all year | 1,787 |
| `C` | September to June | 10 |
| `D` | July and August only | 204 |
| `E` | June to September only | 0 (in legend, not used) |
| `EA` | Except August | 11 |
| `FE` | Período Não Escolar — school holidays only | 32 |
| `PE` | Período Escolar — school term only | 285 |
| (blank) | Aveirobus and ferry: no season restriction | 6,334 |

**Notes** (`note`), all from Aveirobus sheets except where stated:

| Note (as in the CSV) | Rows | Effect |
|---|---|---|
| `Realiza-se no período entre o 2.º domingo de setembro e até 30 de junho` | 108 | Date range: 2nd Sunday of September to 30 June |
| `Escolar -periodo coincidente com o calendario escolar em vigor` (and 2 spelling variants, 84 + 53 + 16 rows) | 153 | School term only |
| `Escolar 4.a e 6.a feira - realiza-se ...` | 18 | School term, Wednesdays and Fridays only |
| `Escolar 2.a, 3.a e 5.a feira - realiza-se ...` | 18 | School term, Mondays, Tuesdays and Thursdays only |
| `j Realiza-se apenas do inicio da epoca escolar ate 30 de junho` | 8 | First day of the school year to 30 June |
| `Estes hararios tem ligacao com as servicos efetuados pela linha 5`, `Ligação à linha 16`, `Ligação à linha 33 (Angeja)`, `Ligação à linha 70` | 27 | Informational only, no calendar effect |

Match notes by normalised keywords (accent-folded, lower-case: `escolar`, `4.a e 6.a`, `2.a, 3.a e 5.a`, `2.º domingo de setembro`, `inicio da epoca escolar`, `ligac`), not by exact string. An unrecognised non-empty note fails the build.

### 2.4 Coordinate sources

| Source | Use | Notes |
|---|---|---|
| Busway-CirAveiro open GTFS `https://gtfs.itbusway.eu/GTFS_CIRA.zip` | `stops.txt` only (2,424 stops with coordinates) | Its timetable is stale (`feed_version` 20241122); never read its trips. Download once into `converter/cache/` and record the SHA-256 |
| OpenStreetMap via Overpass (`https://overpass-api.de/api/interpreter`) | `highway=bus_stop` and `public_transport=platform` nodes with a `name`, plus ferry terminals (`amenity=ferry_terminal`) | Send a `User-Agent` header or Overpass answers 406. Cache the JSON in `converter/cache/`. Bounding box in §5.4 |
| `config/stop_coords.csv` | Hand-placed stops; always wins | Filled by a human from the review files (§8) |

Measured on 18 Oct 2026, after accent-folding and punctuation stripping, matched against Busway's stop names: Busway stops 111 of 171 exact + 12 fuzzy (≥ 0.88), 48 unmatched; Aveirobus stops 46 exact + 36 fuzzy, 119 unmatched; ferry 1 of 2. Expect roughly 170 stops to need OpenStreetMap or a human.

## 3. Technology choices

- Python ≥ 3.11, **standard library only** (`csv`, `zipfile`, `tomllib`, `difflib`, `unicodedata`, `json`, `urllib`, `hashlib`, `datetime`). Same reason as LiveBus: nothing to install.
- `pytest` for tests.
- Config in TOML (read with `tomllib`) and CSV.
- MobilityData `gtfs-validator` for validation: the jar is at `tools/gtfs-validator-cli.jar` (since 2026-10-28; not committed) and needs Java.

## 4. Repository layout to create

```
MVP-LiveBus/
├── GTFSConverter.md            (this file)
├── schedules/                  (input; do not modify)
├── data/gtfs/aveiro.zip        (output)
└── converter/
    ├── README.md               (how to run, Decisions, Known limitations)
    ├── pyproject.toml          (package gtfsconv, dev extra: pytest)
    ├── config/
    │   ├── feed.toml           (feed window, agencies, holidays, school calendar)
    │   ├── routes.toml         (clean route names, colours, route_type)
    │   ├── stop_aliases.csv    (raw name → canonical name)
    │   ├── stop_coords.csv     (canonical name, lat, lon, source note)
    │   ├── time_fixes.csv      (source, trip, stop_sequence, old_time, new_time, evidence)
    │   ├── exclusions.csv      (source, trip, reason) — trips deliberately left out
    │   └── service_changes.csv (operator notices: closed stops, diversions, §5.2a)
    ├── cache/                  (downloaded Busway GTFS, Overpass JSON; git-ignored)
    ├── build/                  (report.md, review files; git-ignored)
    ├── gtfsconv/
    │   ├── __main__.py         (CLI, §9)
    │   ├── load.py             (read CSV, group trips, apply time_fixes and exclusions)
    │   ├── names.py            (normalisation, aliases, canonical names)
    │   ├── times.py            (midnight rollover, monotonicity checks)
    │   ├── stops.py            (candidate lookup and per-trip placement, §5.4)
    │   ├── calendars.py        (holidays, school calendar, service dates, §5.5)
    │   ├── routes.py           (agencies, routes, trips, directions, headsigns)
    │   ├── write.py            (deterministic zip writer)
    │   ├── validate.py         (internal checks + run the MobilityData jar)
    │   └── report.py           (build/report.md)
    └── tests/
```

## 5. Pipeline

Each stage is a pure function over in-memory data. The build fails loudly (non-zero exit, message naming the trip or stop) on any rule marked **fail**.

### 5.1 Load
- Read the CSV with `encoding="utf-8-sig"`. Group rows into trips by `(source, trip)`, ordered by `int(stop_sequence)`.
- Apply `config/time_fixes.csv`: a fix applies only if the current value equals `old_time`; otherwise **fail** (the CSV changed under the fix).
- Drop trips listed in `config/exclusions.csv`; list them in the report.

### 5.2 Names
1. **Raw clean-up:** strip; collapse whitespace; remove a leading `N ` when followed by an upper-case letter; insert spaces in run-together abbreviations (`Av.25deAbril` → `Av. 25 de Abril`, `-Escola` → `- Escola`).
2. **Aliases:** apply `config/stop_aliases.csv` (raw → canonical), exact match on the cleaned string.
3. **Variant clustering (report only):** within each line, group names whose normalised keys (accent-folded, lower-case, alphanumerics only) are ≥ 0.92 similar by `difflib.SequenceMatcher` and differ only by OCR-typical substitutions (`g`↔`ç`, `l`↔`i`, `0`↔`o`, `ti`↔`ú`, missing spaces). Write each cluster to `build/name_clusters.csv` with a suggested canonical form (the variant that appears in Busway's or OSM's names, else the most frequent). **Do not apply suggestions automatically**; a human copies accepted ones into `stop_aliases.csv`. Seed `stop_aliases.csv` with the five `N ` names and the four variants listed in §2.2.
4. The side suffix (` A`, ` B`, `- A`, `- B`) is part of the name and is kept. It is ignored only when comparing against a coordinate source that lacks it, and then the side is chosen by direction (§5.4).

### 5.2a Operator service changes

The PDF sheets lag behind the operator's notices. Aveirobus publishes "Alterações de serviço" on its site; changes that alter a stop or route go in `config/service_changes.csv` (`line,kind,old_stop,new_stop,from_date,to_date,evidence`) and are applied after name clean-up. `to_date` empty = permanent.

- **`replace_stop`:** every stop_time at `old_stop` on that line moves to `new_stop`, keeping the printed time; the report lists each affected trip as "time carried over from the closed stop". If `new_stop` is already the next or previous stop on the trip, the old stop_time is deleted instead.
- **`info`:** recorded in the report only (temporary diversions with no stop change).
- A change whose date range doesn't overlap the feed window is reported as "not applicable" and skipped.

Seed it with the notices read on 19 Oct 2026:

| line | kind | old_stop | new_stop | from | to | evidence |
|---|---|---|---|---|---|---|
| 3 | replace_stop | Travessa Eucalipto | Eucalipto A | (unknown, already in force) | | Aveirobus "Alterações de serviço": stop "Travessa do Eucalipto" deactivated; line 3 now starts at "Eucalipto A" |
| 6 | info | | | 2026-09-10 | 2026-09-30 | Works on Rua de São Brás; "previsivelmente" to end of September |

Measured: all 33 line 3 trips call at `Travessa Eucalipto` and 16 start there. `Eucalipto A` does not appear on line 3 in the CSV; line 19 has `Eucalipto B` and `Eucalipto (Aveiro)`. Place `Eucalipto A` from the Busway GTFS or OSM like any other stop; if it's more than 500 m from the old stop, **fail** and ask a human (the carried-over times would be wrong).

### 5.3 Times
- Convert `HH:MM` to seconds. Within a trip, when a time is earlier than the previous stop's by more than 12 hours, add 24 h to it and every later stop (the line 4 and line 10 late-night trips that cross midnight). Write GTFS times as `HH:MM:SS`, allowing hours ≥ 24.
- After rollover, times must be non-decreasing along a trip; otherwise **fail** and name the trip. (Line 17 `p5t1c6` fails until a `time_fixes.csv` row resolves it.)
- `arrival_time = departure_time` = the printed time; `timepoint = 1`.

### 5.4 Stops and coordinates

Bounding box for all sources and checks: latitude 40.05 to 41.00, longitude −8.95 to −8.20 (the network reaches Figueira da Foz in the south and Vale de Cambra and Oliveira de Azeméis in the north).

**Candidates.** For each canonical name, gather candidate coordinates, in priority order:
1. `config/stop_coords.csv` — if present, the only candidate.
2. Busway GTFS stops whose normalised name equals the normalised canonical name (with and without the side suffix).
3. Busway GTFS stops with similarity ≥ 0.88.
4. OSM bus stops / platforms / ferry terminals, exact then ≥ 0.88.

**Placement is per trip, not per name.** Names repeat across towns (`Junta de Freguesia - A` exists in several parishes). For each trip, choose one candidate per stop so that the path is plausible:
- Use dynamic programming (Viterbi) over the trip's stop sequence, minimising the sum of straight-line distances between consecutive chosen candidates, with a penalty when a leg implies a speed above 90 km/h for its printed minutes (minimum 1 min).
- A name with one candidate anchors the path.
- A name with no candidate is **unplaced** for that trip.

**Merging into GTFS stops.** One GTFS stop per (canonical name, coordinate cluster): placements of the same canonical name within 50 m of each other become one stop, at their mean position. The same name more than 300 m apart becomes separate stops with ids suffixed by the town (from the nearest OSM `place=*` name, or `-2`, `-3`). Between 50 and 300 m: **fail** and list it for a human.

**stop_id:** a stable slug of the canonical name (accent-folded, lower-case, `-` separated), plus the suffix above. Busway and Aveirobus stops with the same canonical name and position are one stop: both networks' buses use it.

**Ferry stops:** place `FORTE DA BARRA (FERRY)` and `S. JACINTO` from OSM ferry terminals or `stop_coords.csv`, not from the bus stops `Forte da Barra - Ferry` and `Cais de S. Jacinto (ferry)`. If a quay is within 150 m of the bus stop, keep them as separate stops; the YourDayTrip engine adds the walking link.

**Unplaced stops.** Default: **fail**, after writing the review files (§8). With `--drop-unplaced`, an unplaced intermediate stop is removed from that trip (the other times are untouched) and a trip whose first or last stop is unplaced is dropped. Every removal is listed in the report. This flag exists to get a usable interim feed while a human finishes `stop_coords.csv`.

**Sanity checks on the final placement (fail):** every stop inside the bounding box; no consecutive pair on any trip implies more than 90 km/h; no zero-minute leg longer than 1.5 km.

### 5.5 Calendars

Emit **`calendar_dates.txt` only** (every service listed date by date, `exception_type = 1`) and no `calendar.txt`. This is valid GTFS, and exact for rules like "Sunday, or Monday if Monday is a holiday" that `calendar.txt` cannot express.

**Feed window** from `config/feed.toml`: default `start = 2026-10-19`, `end = 2027-09-11` (the day before Sunday 2027-09-12, the second Sunday of September, when school-year services restart). Service dates are generated only inside the window.

**Holidays** (`feed.toml`, `[holidays]`):
- National, computed: 1 Jan, Good Friday, Easter Sunday, 25 Apr, 1 May, Corpus Christi (Easter + 60), 10 Jun, 15 Aug, 5 Oct, 1 Nov, 1 Dec, 8 Dec, 25 Dec. Inside the default window that gives: 2026-11-01, 2026-12-01, 2026-12-08, 2026-12-25, 2027-01-01, 2027-03-26, 2027-03-28, 2027-04-25, 2027-05-01, 2027-05-27, 2027-06-10, 2027-08-15. Test this list.
- Municipal, per agency, from config: Aveirobus uses Aveiro's municipal holiday (12 May — **verify** before relying on it). Busway: none by default (its lines cross several municipalities); record as a Decision.
- Carnival (Tuesday before Ash Wednesday) is not a national holiday: config flag `carnival_is_holiday = false`.

**School calendar** (`feed.toml`, `[school]`): the 2026/27 term periods (start of year, Christmas, Carnival and Easter breaks, end of classes). These must come from the official Despacho on the 2026/2027 school calendar (DGEstE). The agent must **not** invent them: if any trip uses `PE`, `FE` or a school note and `[school]` is empty, **fail** with a message saying exactly which dates are needed. Make `[school]` a list of `{start, end}` term periods; non-term weekdays in the window are school holidays.

**Rules.** A trip runs on date *d* when all of these hold:

| Component | Rule |
|---|---|
| `Dias Úteis` | Mon–Fri and not a holiday |
| `Dias Úteis (exceto agosto)` | as above, and not in August |
| `3ª feiras (DU)` | Tuesday and not a holiday |
| `Sábados` | Saturday and not a holiday |
| `Domingos` | Sunday |
| `Domingos e Feriados` | Sunday, or a holiday |
| `Fim-de-semana e Feriados`, `Sábados, Domingos e Feriados` | Saturday, Sunday, or a holiday |
| `Domingos ou 2.ª feira se feriado` | Sunday, unless the next day (Monday) is a holiday, in which case that Monday instead |
| `Todos os dias` | every day |
| `TODOS OS DIAS (Julho e Agosto)` | every day from 1 Jul to 31 Aug |
| season `A` / blank | no restriction |
| season `C` | September to June |
| season `D` | July and August |
| season `E` | June to September |
| season `EA` | not August |
| season `PE` | a school-term day |
| season `FE` | not a school-term day |
| note "school term" | a school-term day |
| note "4.a e 6.a feira" | school-term day, Wednesday or Friday |
| note "2.a, 3.a e 5.a feira" | school-term day, Monday, Tuesday or Thursday |
| note "2.º domingo de setembro ... 30 de junho" | from the second Sunday of September to 30 June |
| note "inicio da epoca escolar ate 30 de junho" | from the first school-term day to 30 June |

A trip with zero dates in the window is kept out of `trips.txt` and listed in the report (for example, nothing should be lost this way; if something is, it is a rule bug).

**service_id:** one per distinct date set, named readably from its components, for example `bw-sab-A`, `ab-du-escolar-qua-sex`, `ab-dom-ou-seg-feriado`. Trips with identical date sets share a service.

**Validity of the Aveirobus sheets.** Lines 1–7, 10 and 12 read as June 2025 (the files for 2 and 6 are named February 2026), but aveirobus.pt listed all of them as current when scraped. Treat every sheet as valid for the whole window and record this as an assumption in the report. Line 8 has no date; same treatment.

### 5.6 Agencies, routes, trips

**`agency.txt`:**

| agency_id | agency_name | agency_url | agency_timezone | agency_lang |
|---|---|---|---|---|
| `busway` | Busway-CirAveiro | `http://busway-cira.pt` | Europe/Lisbon | pt |
| `aveirobus` | Aveirobus | `https://www.aveirobus.pt` | Europe/Lisbon | pt |

**`routes.txt`:** one route per line, with ids `bw-<line>` (Busway), `ab-<line>` (Aveirobus; the two city-centre circulars are both line 15: `ab-15-azul`, `ab-15-verde`) and `ab-ferry`. `route_type` 3 for buses, 4 for the ferry. `route_short_name` = the line number, `15 Azul`/`15 Verde`, or `Ferry`. `route_long_name` from `config/routes.toml`. Seed it with these (Busway names from the booklet; urban names reconstructed from the garbled CSV and stop lists, **verify each against the PDF title** and fix in config):

| route_id | long name |
|---|---|
| bw-16 | Aveiro – Oliveira de Azeméis |
| bw-17 | Aveiro – Vale de Cambra |
| bw-18 | Aveiro (Terminal) – Figueira da Foz (Estação CP) |
| bw-19 | Aveiro – Cantanhede |
| bw-20 | Aveiro – Praia de Mira |
| bw-25 | Ovar – São Jacinto |
| bw-32 | Sever do Vouga – Aveiro |
| bw-33 | Aveiro – Ovar (por Válega) |
| bw-34 | Aveiro – Ílhavo |
| bw-35 | Aveiro – Praia da Barra |
| bw-36 | Aveiro – Costa Nova |
| bw-37 | Aveiro – Sever do Vouga |
| ab-1 | Cacia (EB2,3 Rio Novo do Príncipe) – Sarrazola – Aveiro |
| ab-2 | Monte (Cacia) – Aveiro – Aradas |
| ab-3 | Cacia (EB2,3 Rio Novo do Príncipe) – Taboeira – Aveiro |
| ab-4 | Carregal – Eixo – Aveiro |
| ab-5 | Colégio D. José I – Aveiro (via Solposto) |
| ab-6 | Escolas Velhas – Oliveirinha – Aveiro |
| ab-7 | Colégio D. José I – Aveiro (via Quinta do Torto) |
| ab-8 | São Bento – Mamodeiro – Aveiro |
| ab-10 | Verba – Nariz – Aveiro |
| ab-11 | Terminal Rodoviário – Universidade – Crasto |
| ab-12 | Verba – Nariz – EB2,3 Castro Matoso |
| ab-15-azul | Linha Centro Azul (L15) |
| ab-15-verde | Linha Centro Verde (L15) |
| ab-ferry | Forte da Barra – São Jacinto (ferry) |

Lines 32 and 37 both run Sever do Vouga ↔ Aveiro; keep them as separate routes as the operator numbers them. The ferry route gets `route_desc` "Replaced by buses in bad weather".

**`trips.txt`:**
- `trip_id` = `<route_id>-<source trip>`, for example `bw-36-p18t2c4`, `ab-4-u4b1c20`. Stable across builds.
- `direction_id`: Busway — the `route` value starting with `AVEIRO` is 0, the reverse is 1; for `<->` routes and Aveirobus lines, 0 when the trip's first stop is the same as the line's most common first stop of its `direction` group, else 1. Circular lines (`direction = Circular`): all 0.
- `trip_headsign` = the canonical name of the last stop, side suffix removed.
- `service_id` from §5.5. `block_id`, `shape_id` empty.
- Keep the informational notes ("Ligação à linha 16") in the report only; GTFS has no trip comment field.

**`stop_times.txt`:** from §5.3 and §5.4. `stop_sequence` renumbered 1..n after any `--drop-unplaced` removal.

**`feed_info.txt`:** `feed_publisher_name` "YourDayTrip (unofficial, internal use)", `feed_publisher_url` empty-safe placeholder `https://example.invalid`, `feed_lang` pt, `feed_start_date`/`feed_end_date` = the window, `feed_version` = `<build date>-<first 8 hex of the CSV SHA-256>`.

### 5.7 Write
- Write files in a fixed order with fixed row order (sorted by id), `\n` line endings, UTF-8 without BOM.
- Zip with a fixed timestamp (1980-01-01) and fixed compression so two builds from the same inputs are byte-identical.

### 5.8 Validate
1. Internal checks: every referenced id exists; every trip has ≥ 2 stop_times; times non-decreasing; every service has ≥ 1 date; every stop inside the bounding box.
2. MobilityData validator: `java -jar tools/gtfs-validator-cli.jar -i data/gtfs/aveiro.zip -o converter/build/validator`. Zero errors required. List every warning type with its count in the report. Expected warnings to accept: none known in advance; record each with a reason in README "Decisions".

## 6. Config files

### 6.1 Formats

- `stop_aliases.csv`: `raw,canonical`
- `stop_coords.csv`: `canonical,lat,lon,source` — `source` says where the position came from ("placed on OSM map by Pedro, 2026-10-20")
- `time_fixes.csv`: `source,trip,stop_sequence,old_time,new_time,evidence` — `evidence` names the PDF page checked
- `exclusions.csv`: `source,trip,reason`
- `routes.toml`: one table per `route_id` with `long_name`, optional `color`, `text_color`
- `feed.toml`: `[window]`, `[agencies]`, `[holidays]`, `[school]`

### 6.2 Seed content the agent writes

- `stop_aliases.csv`: the five `N ` names and the OCR variants in §2.2.
- `routes.toml`: the table in §5.6.
- `feed.toml`: window, agencies, national-holiday rule, Aveiro 12 May marked `# VERIFY`, empty `[school]` with a comment pointing to the DGEstE despacho.
- `time_fixes.csv`: header only. Do not add the line 17 fix without the PDF evidence.

## 7. What a human must supply

The build cannot finish strictly without these. The agent builds everything else, runs with `--drop-unplaced` to prove the rest works, and lists these in `converter/README.md` under "Waiting on":

1. **School calendar 2026/27** term dates (§5.5).
2. **Line 17 Zibreiros time** from `bus.pdf` page 5 (§2.2).
3. **Coordinates** for stops no source can place, using the review files (§8).
4. **Name clusters**: accept or reject the suggestions in `build/name_clusters.csv`.
5. **Aveiro municipal holiday** confirmed.
6. **Urban route names** confirmed against each PDF's title.
7. **Line 3 at Eucalipto A:** confirm the stop's location and whether the first departure times still hold now that line 3 starts there (§5.2a).
8. **Current service notices:** re-read Aveirobus's "Alterações de serviço" and Busway's notices before each build, and add any stop or route change to `service_changes.csv`.

## 8. Review files (written on every build)

- `build/report.md`: input hash, counts per stage, fixes applied, exclusions, unplaced stops, dropped stops/trips, zero-date trips, assumptions, validator summary.
- `build/unplaced_stops.csv`: canonical name, lines using it, previous and next placed stop on each trip (name + coordinates), best candidates with distances.
- `build/unplaced_stops.geojson`: for each unplaced stop, a point at the midpoint between its neighbours on one trip, with properties naming it and its neighbours — to open in geojson.io or QGIS, drag to the right place, and copy into `stop_coords.csv`.
- `build/name_clusters.csv`: §5.2.
- `build/stops.geojson`: every placed stop, for a visual check.

## 9. Command line

```bash
cd converter
python -m gtfsconv build                    # strict: fails on any unresolved issue
python -m gtfsconv build --drop-unplaced    # interim feed; drops unplaced stops, reports them
python -m gtfsconv fetch                    # (re)download Busway GTFS + Overpass into cache/
python -m gtfsconv review                   # write the review files only, no zip
pytest
```

Defaults: `--csv ../schedules/schedules.csv`, `--out ../data/gtfs/aveiro.zip`, `--config config/`. `build` runs `fetch` automatically when the cache is empty.

## 10. Tests (all must pass with `pytest`)

Unit tests use small fixture CSVs; integration tests run the real build with `--drop-unplaced` and a fixture `[school]` calendar (`tests/fixtures/school_2026_27_fixture.toml`, clearly marked as test data, terms 2026-09-14..2026-12-18, 2027-01-04..2027-02-12, 2027-02-18..2027-03-19, 2027-04-05..2027-06-18).

**Unit**
- Name clean-up: the five `N ` names, `Av.25deAbril -EscolaJoseEstevaoB` → `Av. 25 de Abril - Escola Jose Estevao B`.
- Midnight rollover: a trip `23:56, 00:03, 00:09` becomes `23:56:00, 24:03:00, 24:09:00`.
- Monotonicity: line 17 `p5t1c6` fails without a fix and passes with one.
- Holidays: the twelve dates in §5.5 exactly.
- `Domingos ou 2.ª feira se feriado`: with a holiday on Monday 2027-03-29 (synthetic), the Sunday before is off and the Monday is on.
- Every season code and note rule on a constructed date.
- Viterbi placement: two stops named `Junta de Freguesia - A` 20 km apart; the trip picks the one next to its neighbours.
- Determinism: two builds produce byte-identical zips.

**Integration (real CSV)**
- Input: 8,663 rows, 828 trips, 363 stop-name strings, SHA-256 as in §2.1 (warn, not fail, if the hash differs).
- Line 36 trips running on Saturday 2026-10-24: 33. On Sunday 2026-10-25: 29. On Tuesday 2026-12-08 (holiday): the `Domingos e Feriados` set (29), and no `Dias Úteis` trips.
- Line 35 on 2026-10-24: 0. On Saturday 2027-07-17: 34.
- Ferry: 26 trips on 2026-10-24 and 26 on 2026-10-26.
- Azul and Verde: 11 trips each on 2026-10-24, 23 each on 2026-10-26.
- Line 3: no stop_time at `Travessa Eucalipto`; 16 trips start at `Eucalipto A`; the report lists all 33 trips as changed.
- Line 6 change: reported as "not applicable" (ends before the feed window).
- Golden journey: on 2026-10-24 there is a line 36 trip from `Terminal Rodoviário de Aveiro` reaching the Forte da Barra stop at or before a ferry departure to `S. JACINTO` within 30 minutes (walking time allowed: 5 min).
- No trip lost to zero service dates.
- Every stop inside the bounding box; no leg above 90 km/h.
- MobilityData validator: 0 errors.

## 11. Milestones and acceptance criteria

| # | Milestone | Done when |
|---|---|---|
| M1 | Load, names, times | Unit tests for §5.1–5.3 pass; line 17 fails as expected |
| M2 | Calendars | Holiday and rule tests pass; the line 36 / 35 / ferry / Azul date counts match with the fixture school calendar |
| M3 | Stop placement | Viterbi tests pass; review files written; report shows how many stops are placed from each source |
| M4 | Interim feed | `build --drop-unplaced` writes `aveiro.zip`; validator 0 errors; byte-identical rebuild |
| M5 | Strict feed | After the human inputs in §7: `build` (no flag) passes; all integration tests pass |

Stop after M4 if the §7 inputs are not available, and say so plainly in the README and the final message.

## 12. Things not to do

- Do not edit `schedules/schedules.csv` or the PDFs. Fixes go in `config/`.
- Do not invent coordinates, school dates, holiday dates or route names. Missing facts fail the build with a clear message, or go in "Waiting on".
- Do not read trips or calendars from the Busway open GTFS; only its stops.
- Do not merge CP's feed into this one.
- Do not publish the feed, upload it anywhere, or submit it to Google, Moovit or the NAP. The source timetables belong to Busway and Aveirobus and their licence is unverified: internal development and closed testing only.
- Do not add dependencies beyond the standard library and pytest.
