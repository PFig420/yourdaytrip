# places/: the places layer

The places layer holds a curated dataset of things to do around Aveiro (sights, activities, food and drink), and turns the reachability engine's time windows into a **shortlist**: places that are open, reachable and worth doing on a given day, with the slot when each can be visited and why it fits.

It sits between the engine and the planner:

```
../engine (reach)  ──windows for N points──►  places  ──shortlist──►  ../planner (picks, orders, schedules)
                                                ▲
                         data/places/*.toml (the curated dataset) + config/
```

Spec: `../PlacesLayer.md`. `README.md` in this folder has the original status and decisions, but its **status and counts are out of date** (see "Dataset status" below). The core package uses only the standard library. The HTTP API needs FastAPI. The shortlist needs the engine (`../engine`) importable.

## Layout

| Path | Purpose |
|---|---|
| `places/` | The package (about 1,500 lines, described below). |
| `data/places/*.toml` | **The dataset**: one TOML file per place, 117 files. This is the product of this layer. |
| `config/vocabulary.toml` | The only allowed values for `kind`, `category`, `tags`, `cuisine`, `weather`, `price`, `booking`, `meals`, `status`. Extend it here, never in a record. |
| `config/places.toml` | Meal windows, the area (municipalities and bounding box), cluster walking minutes (10), staleness threshold (180 days). |
| `config/holidays.toml` | Municipal holidays per municipality (`MM-DD` or `easter+N`). Murtosa is empty on purpose (`# VERIFY`). |
| `cache/osm_places.json` | OpenStreetMap extract (2026-10-20) used by `extract` and the `food-near` fallback. |
| `cache/sources/<id>-<n>.txt` | Cached copies of the web pages each record was written from (first line: URL and fetch date). Used by the copy check. Never served by the API. About 150 files. |
| `build/` | `candidates.csv` (OSM candidates) and `review.csv` (the review sheet). Git-ignored, generated. |
| `review/` | `food_candidates.csv` and `food_candidates/*.toml`: Costa Nova / Barra / Torreira food drafts not in the dataset (seven files, for example Bastos and Assalam). |
| `tests/` | 49 pytest tests. |

## Setup and running

Run from the repo root (`MVP-LiveBus/`), because the CLI resolves paths relative to it and imports `reach` from `../engine`.

```bash
pip install -e "places[web,dev]"           # or: export PYTHONPATH=places:engine
python3 -m pytest places/tests              # 49 pass (about 2 s). Run engine/tests in a separate pytest run: the file names clash
python3 -m places validate                  # errors fail (exit 1); warnings are listed
python3 -m places shortlist --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 \
                            --interests maritime,beach --pace relaxed [--drafts] [--journeys] [--json]
python3 -m places search "azulejos and old houses"
python3 -m places food-near 40.6125 -8.74974 --radius 300      # unverified OSM fallback
python3 -m places review [--stale DAYS]     # writes places/build/review.csv
python3 -m places apply-review places/build/review.csv        # writes changes back into data/places/*.toml
python3 -m places extract                   # re-query OSM (about 4 minutes, network) → cache/ and build/
python3 -m places.web                       # http://127.0.0.1:8200  (local only, Issues.md #1)
```

Python:

```python
import reach, places
lib = places.load("places/data", engine=reach.load("engine/config"))
sl  = lib.shortlist(("origin", lat, lon), "2026-10-24", "08:00", "21:00", interests=["maritime"], pace="relaxed")
lib.search("azulejos"); lib.food_near(lat, lon, 400); lib.get("farol-da-barra")
```

HTTP: `POST /shortlist`, `GET /search?q=`, `GET /places/{id}`, `GET /food-near?lat=&lon=`, `GET /health`.

## Dataset status (checked 23 Oct 2026, counted from the TOML files)

| | Confirmed | Draft | Rejected | Total |
|---|---|---|---|---|
| Sights and activities | 43 | 7 | 2 | 52 |
| Food and drink | 35 | 30 | 0 | 65 |
| **Total** | **78** | **37** | **2** | **117** |

- **The README's status section is stale.** It still says every record is `draft` and counts 52 sights and 60 food places. Pedro reviewed the dataset on 2026-10-22. Since then 78 records are `confirmed`, 37 remain `draft` and the two rejected are Cais do Bico and Furadouro (no public transport).
- **Rule: confirmed ⇔ has opening hours.** All 37 drafts lack hours, and the validator refuses `confirmed` with empty hours. A draft is never used unless `--drafts`/`include_drafts` is set.
- Five Costa Nova / Barra / Torreira food drafts were imported as confirmed on 2026-10-23. The Bastos and Assalam drafts remain in `review/food_candidates/` without hours. Praça do Peixe and Forte da Barra were set to `24/7`, daylight only. The salt pans deliberately were not (private working pans).

### Do not rerun the dataset generators

Records have been written by generator scripts (kept outside the repo) and then **hand-reviewed**. Re-running a generator over `data/places/` **overwrites the reviewed records**. This has happened once, on 2026-10-23, and statuses had to be restored from `build/review.csv`. **Edit the TOML files directly**, or use `review` / `apply-review`. If you need to regenerate anything, write to a scratch directory first and diff.

## A place record (`data/places/<id>.toml`)

The file name must equal `id`. Example fields (see `data/places/farol-da-barra.toml`):

| Field | Meaning |
|---|---|
| `id`, `name`, `kind`, `category`, `tags`, `municipality`, `lat`, `lon`, `osm`, `website` | Identity. `kind` is `sight`, `activity`, `food` or `drink`. Tags and categories come from `vocabulary.toml`. The entrance coordinates are **not surveyed** (OSM centroid, or the street for geocoded addresses). |
| `hours` | Opening hours in an OSM `opening_hours` subset (below). Required for `confirmed`. `24/7` for open-air places. |
| `hours_note` | Human caveats (conflicting sources, holiday exceptions). Shown to users as a note. |
| `entry_times` | Optional fixed entry/departure times (tours, climbs), same syntax with single times, for example `"Apr-Oct We 14:00,14:30; PH off"`. Needs `hours` too. |
| `last_entry_min` | Minutes before closing that entry stops. |
| `daylight_only` | Clip opening to sunrise-sunset at the place. Set on all `24/7` places. |
| `weather` | `indoor`, `outdoor` or `mixed`. Outdoor places are excluded on `weather=rain`. |
| `visit_min`, `visit_typical` | Stay length in minutes (relaxed uses typical, packed uses min). Most are estimates. |
| `max_walk_m` | Optional per-place walk limit (up to 2000), when the access point is far from any stop. |
| `price`, `ticket_eur`, `booking` | `free`/`€`/`€€`/`€€€` (≤5 EUR, 5-12 EUR, more), optional price, `no`/`recommended`/`required`. |
| `description` | In our own words, at most 60 words. A test rejects any 8 consecutive words shared with a cached source page. |
| `priority` | 1 (best) to 3. Feeds the score. |
| `cuisine`, `meals` | Food and drink only: `lunch`, `dinner`, `snack`, `drinks`. |
| `[provenance]` | `sources`, `hours_source`, `checked` (date), `drafted_by` (`agent` or `pedro`), `status` (`draft`/`confirmed`/`rejected`), `estimates` (which fields are guesses), `status_reason` (required when rejected). |

### Opening-hours syntax (`hours.py`)
A subset of OSM's: rules separated by `;`, later rules override earlier ones for the days they name. Examples: `"Tu-Sa 09:30-17:30; Mo off"`, `"Apr-Oct We 14:00-17:15; Nov-Mar We 13:30-16:45; PH off"`, `"24/7"`. Seasons (`Apr-Oct`, with optional days), weekday ranges, times to `24:00` or past midnight (an end past midnight belongs to the opening day, and nothing spills into the next day's query), and `PH` (public holiday, including the place's municipal holiday), `off`. The parser raises `HoursError` for anything else, and the validator reports it.

## How the code works

| Module | Role |
|---|---|
| `model.py` | `Place`/`Provenance` dataclasses, `read_place`/`write_place` (TOML), `RecordError`. |
| `config.py`, `holidays.py` | Config loading, meal windows (`parse_window`: an end before the start runs past midnight), national holidays computed from Easter, plus municipal ones. |
| `hours.py`, `sun.py` | Opening-hours parser and interval expansion for a date. Sunrise and sunset at the place's coordinates (for `daylight_only`). |
| `library.py` | `Library`/`load`: reads all records, builds the BM25 index over non-rejected places, `get`, `search`, `food_near`, `shortlist`. |
| `retrieval.py`, `text.py` | BM25 keyword search over name, description, tags, cuisine, category, municipality. Accent folding, stop words, naive stemming. |
| `shortlist.py` | `build()`: the shortlist algorithm, below. |
| `validate.py` | Record checks (`check_place`) and dataset checks (`validate`). |
| `review.py` | `write_review` (CSV for the human reviewer) and `apply_review` (writes edits back, only after validation passes). |
| `extract.py`, `sources.py` | Overpass/OSM candidate extraction. Fetch and cache a source page's visible text. Both need the network. |
| `cli.py`, `web/` | The command line and the FastAPI app (`create_app`, port 8200). |

### The shortlist (`shortlist.build`)
1. Candidates are `confirmed` places (and `draft` ones with `include_drafts`).
2. **One engine `windows` call** (with journeys) for all places at the default 1,200 m walk limit, plus **one extra call per distinct `max_walk_m`** value used, so a long walk never loosens the rule for other places. The engine gives each place `arrive` / `leave` / status.
3. For every place the engine says is `ok`, `usable_slots` intersects the opening hours with the arrive-leave window:
   - clip to the window, then to daylight (`daylight_only`), then to the place's meal windows (food only);
   - with `entry_times`, one slot per departure that fits (marked `fixed_entries`); otherwise one slot per open interval where a visit of the needed length fits, with a `latest_start` of min(end − visit, closing − last entry).
   - Otherwise it records an **exclusion reason**: `no_window`, `unreachable`, `no_return` (from the engine), `closed`, `daylight`, `meal_time`, `too_short`, `no_departure`, `weather`, `no_hours`, `bad_hours`.
4. **Score** = 3 × matched interests + 2 × normalised BM25 relevance of the free text + (4 − priority) + fit (≤ 0) − 0.5 × rides from the origin. Ties break on shorter travel, then id.
5. **Clusters**: single-link groups of places whose entrances are within 10 minutes' walk (`Engine.walk_seconds`, so barriers apply). Pairs over 900 m apart are skipped before the check.
6. Output: sights/activities and food/drink ranked separately (top `limit`, default 20), the excluded list with reasons, and warnings (draft records, hours older than 180 days, engine coverage). JSON includes an OSM attribution string.

## Validation (`python -m places validate`)

Errors (exit 1): values not in the vocabulary, outside the area box or municipality list, unparsable `hours`/`entry_times`, empty hours on a non-draft, `visit_min` > `visit_typical` or < 10, bad `max_walk_m`, rejected without a reason, priority outside 1-3, description empty/too long/copied from a source, `confirmed` without `checked`, cuisine/meals on a sight. With the engine loaded it also **warns** about places farther than 800 m from the nearest stop (or than their own `max_walk_m`), stale hours, drafts, food places with no meals, and two entrances less than 20 m apart.

## Tests (`tests/`)
`test_units.py` (hours parser, seasons, public holidays, daylight against an almanac, meal windows, last entry, BM25, the copy check, validator rules), `test_golden.py` (shortlists P1-P8, rain, journeys, `max_walk_m` and fixed entries, on fixture data in `tests/fixtures/`), `test_data.py` (runs over the **real dataset**: no validation errors, every municipality has at least two sights, every record has a source and cached page, only confirmed records by default, every food place within 600 m of a stop, a shortlist performance check) and `test_more.py` (holidays, record round trip, search, review sheet and apply, extract CSV, the HTTP API).

Because `test_data.py` reads the real files, a bad edit to a record fails the suite, which is the point. `test_counts` also hard-codes ranges: 50-70 sights, 40-70 food places and **30-40 confirmed food places**. Confirming more food records than that (35 today) will fail the test until the range is updated.

## Known limitations and gotchas
- Santuário de Vagos (1.9 km from a stop) will always show `unreachable`. Praia da Torreira has no way back on weekends in the current feeds (`no_return`).
- Hours are the published weekly pattern. Exceptions (Easter Sunday, Carnival, the Aveiro museums open on 12 May) are only in `hours_note`. Farol da Barra's winter timetable switches on 1 Nov while the clocks change on the last Sunday of October.
- Several records have hours read through search snippets (Fábrica, Igreja Matriz de Ovar, Casa-Museu Júlio Dinis): `hours_note` says which. Re-check them first.
- Facebook/Instagram pages cannot be read from this machine, so food records based on them have an "activity could not be checked" note.
- Undersupplied areas (against the spec quotas): Costa Nova/Barra and Torreira/Murtosa.
- `entry_times` keys on the building's opening hours; boat tours publish no fixed times so they have none.
- The `/map` page from the spec was never built, and there is no LLM here (that is `../planner`).
- Coordinates are © OpenStreetMap contributors (ODbL). API output carries an attribution field, and `Issues.md` #1 lists the licensing conditions before any public launch.
- The package imports `reach` lazily and expects `../engine` on `sys.path` (the CLI adds it). When using the library from another script, put `engine/` on `PYTHONPATH`.
