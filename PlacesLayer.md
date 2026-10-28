# PlacesLayer.md — places layer build spec

This is the complete brief for building the YourDayTrip places layer. It is written for a coding agent working alone in this repository. Read it fully before writing code. Where this spec is silent, choose the simplest option that meets the acceptance criteria (§11) and record the choice in `places/README.md` under "Decisions".

## 1. What the places layer does

It turns the reachability engine's time windows into a **shortlist of things worth doing on a given day**. Each item is a place, the time slot when it's open and reachable, and why it fits.

```
places/data/places/*.toml ──► validate ──┐
   (curated: ~60 sights,                  │
    30–40 food and drink)                 ▼
                       engine.windows(origin, date, leave_after, back_by, entrances)   ← ReachabilityEngine
                                          │   one call, all places
                                          ▼
                 opening hours that date ∩ [arrive, leave] ∩ daylight / meal times
                                          │
                                          ▼
                interests (tags + keyword search) · pace · weather · walking clusters
                                          │
                                          ▼
          Shortlist: candidates with usable slots, journeys, reasons ──► (later) LLM planner
```

The layer has two halves of equal weight:
1. **The dataset:** curated place records with real opening hours, visit lengths and descriptions. The agent drafts them from official sources; Pedro confirms them.
2. **The code:** validation, an opening-hours parser, the shortlist and keyword retrieval.

### Decisions already taken (Pedro, 19 Oct 2026)

| Decision | Value |
|---|---|
| Area | The Aveiro region covered by the Aveiro feed (§2.3). Coimbra, Espinho and Porto are out for now |
| Food and drink | 30–40 hand-picked restaurants, cafés and bars, plus an unverified "food near here" fallback from OpenStreetMap |
| Size | About 60 sights and activities, plus the 30–40 food and drink places |
| Language | English only |
| Descriptions | The agent drafts them from online sources, **in its own words**, with sources recorded. Pedro reviews them |

### In scope
- A candidate extraction from OpenStreetMap.
- A place record format, a validator and a review sheet.
- Drafting about 60 sight records and about 50 food and drink candidates, of which Pedro keeps 30–40.
- An opening-hours parser (an OSM subset), seasons, public holidays and daylight.
- The shortlist API, keyword retrieval, CLI and HTTP API.
- Tests: unit tests, golden tests on the engine's known windows, and data tests.

### Out of scope
- The LLM planner: sequencing a day, writing narratives.
- Reviews, star ratings, photos, booking, live occupancy, events calendars.
- Portuguese text.
- Places outside §2.3.
- Changing the engine's routing. One small public helper is added to the engine (§6.4).

## 2. Inputs

### 2.1 The engine
`engine/` (package `reach`, spec `ReachabilityEngine.md`). Load it with `reach.load("engine/config")` from the repo root. The layer uses:
- `Engine.windows(origin, date, leave_after, back_by, points, **opts) -> WindowResult`. `points` are `(id, lat, lon)`. Each `Window` has `id`, `arrive`, `leave`, `available_s` and `status` (`ok`, `no_window`, `unreachable`, `no_return`), and `outbound`/`return_` journeys with `include_journeys=True`. `arrive` and `leave` are UTC epoch seconds.
- `WindowResult.coverage` (missing feeds and warnings), which must be passed through.
- Engine golden values (`ReachabilityEngine.md` §10.2, current feeds) that this layer's tests rely on. From Porto Campanhã (41.14871926, -8.584835348) on Sat 2026-10-24, leaving after 08:00 and back by 21:00:
  - Costa Nova (40.612500, -8.749740): **10:20:00 → 18:45:00**
  - São Jacinto ferry quay (40.660682, -8.729750): **10:35:00 → 18:45:00**
  - Águeda station (40.57749168, -8.448231459): **10:35:00 → 18:53:00**

### 2.2 OpenStreetMap
Query Overpass (`https://overpass-api.de/api/interpreter`, with a `User-Agent` header, retried on 502/504). Cache the response in `places/cache/osm_places.json`, recording the date. Measured on 19 Oct 2026 for the box 40.40–40.90 N, 8.80–8.40 W (named elements only):

| Category | Count | With `opening_hours` | With website | With `wikidata` |
|---|---|---|---|---|
| restaurant | 616 | 14% | 7% | 0% |
| cafe / ice cream | 522 | 10% | 1% | 0% |
| historic | 390 | 0% | 0% | 3% |
| park / reserve / marina | 194 | 8% | 4% | 6% |
| gallery / artwork | 172 | 1% | 1% | 0% |
| bar / pub | 159 | 6% | 2% | 0% |
| attraction | 86 | 2% | 8% | 15% |
| museum | 45 | 40% | 71% | 20% |
| beach | 23 | 0% | 0% | 21% |
| viewpoint | 14 | 0% | 0% | 0% |

OSM is good for **names, positions and candidates**, and poor for **hours**. Hours come from official sources (§4.3). OSM data is ODbL: credit "© OpenStreetMap contributors" in the README and in API responses that include OSM-derived fields.

### 2.3 Area
These municipalities in the Região de Aveiro, all reachable through the Aveiro feed or CP:
- **Aveiro** (city, Esgueira, Cacia, São Jacinto)
- **Ílhavo** (Ílhavo town, Gafanha da Nazaré, Barra, Costa Nova, Vista Alegre)
- **Murtosa** (Torreira, Cais do Bico)
- **Ovar** (town, Furadouro)
- **Estarreja**
- **Vagos** (Vagueira)
- **Mira** (Praia de Mira)
- **Águeda**

Bounding box for validation: latitude 40.40 to 40.90, longitude −8.80 to −8.40.

### 2.4 Public holidays
Reuse the national holiday computation in `converter/gtfsconv/calendars.py`; import it or copy it with attribution in a comment. Municipal holidays go in `places/config/holidays.toml`, one per municipality. **Aveiro's 12 May is confirmed** (Câmara press note, `Issues.md` #8). For the other seven municipalities, the agent looks up each date on the municipality's official site (`cm-<name>.pt`) and records the URL. If a date can't be confirmed, it leaves that municipality empty with a `# VERIFY` comment. **Never guess a date.**

## 3. Technology choices
- Python ≥ 3.11. **The core (`places/`) uses the standard library only**, like the engine.
- Place records are **one TOML file per place** (`tomllib` reads them; write them from a fixed template). Humans edit them, and diffs stay readable.
- HTTP: FastAPI, as the optional extra `web`, on **port 8200**.
- `pytest` for tests.

## 4. The dataset

### 4.1 Record format (`places/data/places/<id>.toml`)

```toml
id = "museu-maritimo-ilhavo"            # slug, unique, equals the file name
name = "Museu Marítimo de Ílhavo"
kind = "sight"                          # sight | activity | food | drink
category = "museum"                     # museum | church | monument | architecture | beach | nature | viewpoint | park
                                        # | neighbourhood | market | activity | restaurant | cafe | pastry | bar
tags = ["maritime", "history", "indoor", "family"]   # from the vocabulary in §4.2
municipality = "Ílhavo"
lat = 40.0                              # the ENTRANCE (or beach access, or boarding point), 5+ decimals
lon = -8.0
osm = "way/123456"                      # optional
website = "https://…"                   # official site, if any

# Opening hours: OSM opening_hours subset (§5.1). Use "24/7" for always-open places.
hours = "Tu-Su 10:00-18:00; Mo off; PH off"
hours_note = "Last entry 30 min before closing."      # optional, shown to users
last_entry_min = 30                     # minutes before closing after which entry is refused; default 0
daylight_only = false                   # true for beaches, viewpoints, nature trails, outdoor streets
weather = "indoor"                      # indoor | outdoor | mixed

visit_min = 45                          # minimum sensible visit, minutes
visit_typical = 90                      # typical visit, minutes
price = "€€"                            # free | € | €€ | €€€ (food: average main course < 12 € / 12–20 € / > 20 €)
ticket_eur = 4.0                        # optional; sights only
booking = "no"                          # no | recommended | required

description = """Two or three plain sentences, at most 60 words, in English, in the drafter's own words."""
priority = 2                            # 1 = must-see, 2 = good, 3 = niche (curator's call)

[provenance]
sources = ["https://…", "https://…"]    # every page used, official first
hours_source = "https://…"              # the page the hours came from
checked = 2026-10-20                    # date the hours and prices were checked
drafted_by = "agent"                    # agent | pedro
status = "draft"                        # draft | confirmed | rejected
estimates = ["visit_min", "visit_typical"]   # fields that are the drafter's estimate, not sourced
```

A long or spread-out place (a beach, a seafront, a walking route) may add **extra reference points**, after `[provenance]`:

```toml
[[points]]
name = "south end, by the Largo Arrais Ançã bus stop"   # where it is, in words a visitor understands
lat = 40.610900
lon = -8.750400
source = "OSM way/301852964 (ODbL); point chosen by Claude 2026-10-27, confirm"   # the OSM element or page, and who chose it
```

The record's own `lat`/`lon` stays the **main point** (sun times, the area-box check, maps, and wherever one position is needed). It counts as one of the place's points for distances and routing, so it is not repeated. A record without `[[points]]` behaves as before. A visit uses **one** point: you arrive at the point reached first and leave from it.

For food and drink, add:

```toml
cuisine = ["seafood", "portuguese"]     # from §4.2
meals = ["lunch", "dinner"]             # lunch | dinner | snack | drinks
```

### 4.2 Tag vocabulary (`places/config/vocabulary.toml`)
These are the only values allowed in `tags` and `cuisine`, so retrieval and filtering stay predictable. Extend the list only through the vocabulary file, never ad hoc.
- **Themes:** `history`, `religious`, `art`, `art-nouveau`, `azulejos`, `architecture`, `maritime`, `salt`, `ria`, `science`, `porcelain`, `local-life`, `street-art`
- **Setting:** `beach`, `nature`, `birdwatching`, `walk`, `viewpoint`, `garden`, `boat`
- **Audience:** `family`, `kids`, `romantic`, `nightlife`, `rainy-day`, `free`
- **Weather:** `indoor`, `outdoor`
- **Cuisine:** `seafood`, `fish`, `eel`, `portuguese`, `regional`, `petiscos`, `vegetarian-friendly`, `pastry`, `ovos-moles`, `coffee`, `wine`, `cocktails`, `beer`

### 4.3 How the agent drafts records
1. **Extract candidates.** Run `python -m places extract` to write `places/build/candidates.csv` from the OSM cache: OSM id, name, category, coordinates, municipality, hours, website, wikidata and cuisine.
2. **Sights: about 60.** Start from the seed list in §4.4 and complete it from the candidates (museums and attractions first). For each one:
   1. Find the **official page**: the place itself, the Câmara Municipal, or the Turismo Centro de Portugal region site. Use Wikipedia for background only.
   2. Fetch each page used. Save its visible text to `places/cache/sources/<id>-<n>.txt`, with the URL and fetch date on the first line.
   3. Fill hours **from the official page**. If OSM disagrees, the official page wins; note it in `hours_note`. If no official hours exist, set `status = "draft"` and `hours = ""`. The validator flags it (§4.5). **Don't invent hours.**
   4. Set the **entrance** coordinates. Use the OSM entrance node if mapped; otherwise use the building centroid moved to the street side. For beaches, use the main public access. For boat tours, use the boarding point.
   5. Estimate `visit_min` and `visit_typical`, and list them in `estimates`.
   6. Write the description **in your own words**, using the cached sources for facts only (§4.6).
3. **Food and drink: propose about 50, Pedro picks 30–40.** Quotas for the proposal:

   | Area | Restaurants and cafés | Bars |
   |---|---|---|
   | Aveiro centre | 14 (include 2–3 ovos moles pastry shops) | 7 |
   | Costa Nova and Barra | 7 | 1 |
   | Ílhavo, Gafanha and Vista Alegre | 4 | — |
   | São Jacinto | 2 | — |
   | Torreira and Murtosa | 3 (eel dishes are a local speciality) | — |
   | Ovar and Furadouro | 3 | 1 |
   | Mira, Vagueira and Águeda | 4 | 1 |

   Choose from OSM candidates with a name and a findable official presence: own website, municipality listing, or regional tourism listing. Each must be **within 600 m of a stop in either feed**; check with `engine.windows` or distance to stops. Every proposal gets `status = "draft"`. Mark the 30–40 the agent recommends with `priority` 1–2, and the rest with 3. Pedro sets the final `status`.
4. **Review sheet.** Run `python -m places review` to write `places/build/review.csv`: one row per record, with id, kind, name, municipality, status, hours, the `hours_source`, visit lengths, description, and any validator warnings. Pedro reviews in a spreadsheet. Changes go back into the TOML files, by hand or with `python -m places apply-review review.csv`, which only updates `status`, `priority`, `hours`, `visit_*` and `description`.

### 4.4 Seed list for sights (candidates; the agent verifies each exists, is open to visitors, and finds its hours)
- **Aveiro:**
  - Museu de Aveiro / Santa Joana (Mosteiro de Jesus)
  - Sé de Aveiro
  - Igreja da Misericórdia
  - Igreja das Carmelitas
  - Museu Arte Nova (Casa Major Pessoa) and the Art Nouveau facades route
  - Museu da Cidade de Aveiro
  - Fábrica Centro Ciência Viva
  - Canal Central and the Rossio
  - Praça do Peixe and the fish market
  - Bairro da Beira-Mar and Capela de São Gonçalinho
  - Old railway station (azulejo panels)
  - Ecomuseu Marinha da Troncalhada (salt pans)
  - A salt-pan visit
  - Moliceiro boat tour on the Canal Central (one record, activity)
  - Parque Infante D. Pedro
  - University of Aveiro campus architecture
- **São Jacinto:** Reserva Natural das Dunas de São Jacinto; Praia de São Jacinto
- **Ílhavo:**
  - Museu Marítimo de Ílhavo
  - Navio-Museu Santo André
  - Museu Vista Alegre
  - Capela de Nossa Senhora da Penha de França (Vista Alegre)
  - Farol da Barra
  - Praia da Barra
  - Costa Nova seafront and the striped *palheiros*
  - Praia da Costa Nova
  - Costa Nova fish market
  - Jardim Oudinot
- **Murtosa:** Cais do Bico; Praia da Torreira
- **Ovar:** Praia do Furadouro; Ovar's azulejo facades; Igreja Matriz de Ovar; Casa-Museu Júlio Dinis; Museu de Ovar
- **Estarreja:** BioRia / Percurso de Salreu; Casa-Museu Egas Moniz (Avanca)
- **Vagos:** Praia da Vagueira; Santuário de Nossa Senhora de Vagos
- **Mira:** Praia de Mira; Lagoa de Mira; Museu Etnográfico da Praia de Mira
- **Águeda:** Umbrella Sky Project (summer only; confirm the 2027 season dates or record it as unknown); Pateira de Fermentelos; Águeda street art

The list has about 50 entries. Complete it to about 60 from the OSM candidates. Drop any entry that is closed to the public, and say why in the README.

### 4.5 Validation (`python -m places validate`, also run by the tests)
**Errors** (the build fails):
- TOML doesn't parse, a required field is missing, or `id` ≠ file name.
- A tag, cuisine or category isn't in the vocabulary.
- `hours` doesn't parse (§5.1). An empty `hours` is allowed only when `status = "draft"`.
- The coordinates fall outside §2.3's box.
- `visit_min > visit_typical`, or `visit_min < 10`.
- The description is over 60 words or empty, or fails the copy check (§4.6).
- `status = "confirmed"` while `hours` is empty or `provenance.checked` is missing.
- Extra points (§4.1): more than 6, outside the area box, more than 3 km from the main point, less than 150 m from each other or from the main point, or without a `name` or `source`.

**Warnings** (listed in the review sheet):
- The place is more than 800 m from the nearest stop of either feed. It's reachable only with a long walk; the engine's `max_walk_m` default is 1,200 m.
- `checked` is more than 180 days old.
- The place is `draft`.
- Two places' entrances are less than 20 m apart.
- A food place has no `meals`.

### 4.6 Descriptions and copying
The user wants descriptions drafted from online sources. **Facts may come from sources; wording may not.** Rules:
- At most 60 words, in English and plain words: what it is, why go, and one practical tip (for example "climb the lighthouse on Wednesdays" — only if the source says so).
- **Copy check, automated:** no run of **8 or more consecutive words** may be shared with any cached source text for that place. Compare after lower-casing and stripping punctuation and accents. A failing description is an error.
- No superlatives without a source ("the oldest", "the best"), no prices or hours in the description (they have their own fields), and no review-site text.
- List every page used in `provenance.sources`.

## 5. Rules

### 5.1 Opening hours
Support this subset of the OSM `opening_hours` syntax; anything else is a validation error:
- Rules separated by `;`, applied left to right. A later rule overrides earlier ones for the days it names.
- **Weekday selectors:** `Mo`–`Su`, ranges `Mo-Fr`, lists `Mo,We,Fr`.
- **Time ranges:** `10:00-18:00`, several separated by commas, `off`, `24/7`. Ends past midnight (`22:00-02:00`) belong to the opening day.
- **`PH`** for public holidays, alone or as `PH off` / `PH 10:00-13:00`. It takes precedence over weekdays.
- **Month and date prefixes for seasons:** `Jun-Sep`, `Oct-May`, `Jul 01-Aug 31`, `Dec 24 off`, `Dec 25 off`. A range may wrap around the year end.

Examples that must parse:
- `Tu-Su 10:00-12:30,14:00-17:30; Mo off; PH off`
- `Jun-Sep Mo-Su 10:00-19:00; Oct-May Tu-Su 10:00-17:00`
- `24/7`
- `Mo-Sa 12:00-15:00,19:00-22:30; Su 12:00-15:00`
- `Fr-Sa 21:00-02:00`

`intervals(hours, date, holidays) -> list[(start, end)]` returns local-time intervals for that date. `last_entry_min` moves the end of each interval earlier for entry purposes.

### 5.2 Daylight
For `daylight_only` places, intersect with **sunrise to sunset** at the place's position on that date. Use the NOAA solar position algorithm (standard library `math` only) and return local times. Test the result against a published almanac value for Aveiro on 2026-10-24 and 2027-06-21, within ±3 minutes, and record the almanac URL in the test.

### 5.3 Meal times (`places/config/places.toml`)
- `lunch = "12:00-15:00"`, `dinner = "19:00-22:30"`, `snack = "15:00-19:00"`, `drinks = "18:00-02:00"`.
- A food or drink place's usable intervals are its opening hours ∩ the meal windows it lists ∩ `[arrive, leave]`.

### 5.4 When a place fits
For each place, after the single `engine.windows` call:
1. Skip it unless the window status is `ok`.
2. Take the **usable intervals:** opening intervals on that date (§5.1) ∩ `[arrive, leave]`, also ∩ daylight (§5.2) and meal windows (§5.3) where they apply.
3. Visit length is `visit_typical` for pace `relaxed` and `visit_min` for pace `packed`.
4. It **fits** if some usable interval is at least the visit length. **Entry** must be before `closing − last_entry_min`, and the visit must end before closing and before `leave`.
5. Weather `rain` excludes `outdoor` places; `mixed` places stay, with a note.
6. **Bars:** the visit must end at or before `leave`. Since `leave` is the last safe departure, an evening bar suggestion never strands the user.

### 5.5 Ranking
`score = 3 × (interest tags matched) + 2 × BM25 relevance (normalised 0–1, only if free text was given) + (4 − priority) + slot_fit − 0.5 × (rides in the outbound journey)`

`slot_fit` = min(1, longest usable interval ÷ visit length) − 1, so it's 0 when the visit fits comfortably. Ties go to the shorter outbound journey, then to `id`. Food and drink are ranked separately from sights, and returned in their own list.

### 5.6 Clusters
Group places whose entrances are within **10 minutes' walk** of each other, using the engine's walking rule, which includes barriers (§6.4). Use single-link clustering. Each candidate carries its `cluster` id, so the planner can chain nearby places without a ride.

### 5.7 Keyword retrieval
- A BM25 index over `name`, `description`, `tags`, `category` and `municipality`. Accent-fold and lower-case. Use a small English stop-word list, plus Portuguese place-name particles (`de`, `da`, `do`, `dos`, `das`).
- `search(text, k) -> [(id, score)]`. It powers free-text interests now, and the LLM planner's retrieval later.

### 5.8 The unverified food fallback
`food_near(lat, lon, radius_m=400)` returns OSM restaurants, cafés and bars that aren't in the curated set, sorted by distance. Each result is clearly marked `"verified": false`, with no hours guarantee. Never mix these into the curated shortlist.

## 6. Interfaces

### 6.1 Python API (`places/__init__.py`)

```python
lib = places.load(data_dir="places/data", engine=reach.load("engine/config"))
lib.shortlist(origin, date, leave_after, back_by, *, interests=(), text="", pace="relaxed",
              weather="any", include_food=True, limit=20, include_journeys=False) -> Shortlist
lib.search(text, k=10) -> list[tuple[str, float]]
lib.food_near(lat, lon, radius_m=400) -> list[dict]
lib.get(place_id) -> Place
```

`Shortlist` contains:
- `origin`, `date`, `leave_after`, `back_by`, and `coverage`, passed through from the engine;
- `sights` and `food` lists. Each item has: the place summary; `arrive` and `leave` (the engine window); `slots` (the usable intervals); `visit_s`; `score`; `why` (matched tags and fit notes); `cluster`; and the journeys if asked for;
- `excluded`: id and reason, for example `closed`, `no_window`, `unreachable`, `daylight`, `too_short` or `weather`. This keeps the planner and the tests transparent;
- `warnings`: stale hours, draft records used, coverage gaps.

Only `confirmed` records are used, unless `include_drafts=True`. Drafts are allowed during development and in the review tool.

### 6.2 CLI

```bash
python -m places extract                     # OSM → build/candidates.csv
python -m places validate                    # errors fail, warnings listed
python -m places review                      # build/review.csv for Pedro
python -m places apply-review build/review.csv
python -m places shortlist --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 \
                           --interests maritime,beach --pace relaxed [--drafts] [--json]
python -m places search "azulejos and old houses"
```

### 6.3 HTTP (`places.web`, port 8200, local only — Issues.md #1)
`POST /shortlist` (the body mirrors §6.1), `GET /search?q=`, `GET /places/{id}`, `GET /food-near?lat=&lon=`, `GET /health`. Optionally, `/map` reuses the engine map's look, adding places coloured by fit for the chosen origin and times.

### 6.4 One engine addition
Add `Engine.walk_seconds(a, b) -> int | None` to `engine/reach/engine.py`, with a test. It's the engine's walking time between two points, or `None` if a barrier blocks the straight line. It's the only change to `engine/`. It keeps the walking rule (speed, detour factor, barriers) in one place.

## 7. Repository layout

```
MVP-LiveBus/
├── PlacesLayer.md            (this file)
└── places/
    ├── README.md             (run commands, Decisions, Known limitations, OSM attribution)
    ├── pyproject.toml        (package places; extras: web = fastapi, uvicorn; dev = pytest, httpx)
    ├── config/               (vocabulary.toml, holidays.toml, places.toml)
    ├── data/places/*.toml    (the dataset, in git)
    ├── cache/                (osm_places.json, sources/*.txt; in git, so drafts can be re-checked)
    ├── build/                (candidates.csv, review.csv; git-ignored)
    ├── places/               (package: model.py, validate.py, hours.py, sun.py, retrieval.py, shortlist.py,
    │                          extract.py, review.py, cli.py, web/)
    └── tests/
```

## 8. Freshness
- Hours change by season. Every record has `checked`; records older than 180 days are warned about. The shortlist adds a warning when it uses one.
- Before a test campaign with real users, run `python -m places review --stale 90` and re-check what it lists.

## 9. Licences and attribution
- OSM-derived fields (coordinates, OSM ids, the fallback list) are ODbL. Credit OpenStreetMap contributors.
- Descriptions are original text (§4.6), owned by the project.
- Cached source text is kept for checking only and never served by the API.
- Wikipedia text (CC BY-SA) must not be copied into descriptions.

## 10. Tests (all must pass with `pytest`)

### 10.1 Unit
- **Hours:** every example in §5.1 on chosen dates. `Fr-Sa 21:00-02:00` on a Saturday gives 21:00–02:00 (next day). `PH off` closes on 2026-12-08. `Jun-Sep …; Oct-May …` picks the right rule on 2026-10-24 and 2027-07-17. An unsupported syntax is rejected.
- **Daylight:** §5.2 almanac values within ±3 min.
- **Meal windows:** a place open 11:00–23:00 with `meals = ["lunch"]` gives 12:00–15:00 inside a long window.
- **`last_entry_min`:** open 10:00–18:00 with last entry 30 min and a visit of 60 min: entry is impossible after 17:00.
- **BM25:** on a fixture corpus of 5 records, a query for a tag word ranks the tagged record first. Accent-folding makes "azulejo" match "Azulejos".
- **Copy check:** a description sharing 8 consecutive words with its cached source fails; one sharing 7 passes.
- **Validator:** one fixture per error type.

### 10.2 Golden (real engine, fixture places)
All tests: origin Porto Campanhã, Sat 2026-10-24, after 08:00, back by 21:00, pace `relaxed`, using fixture places in `tests/fixtures/places/` (not the real dataset).

| # | Fixture place | Expected |
|---|---|---|
| P1 | At Costa Nova; `hours = "Sa 11:00-13:00"`, visit 60 min | fits; slot **11:00–13:00** (the engine window is 10:20–18:45) |
| P2 | At Costa Nova; `hours = "Sa 19:00-23:00"` | excluded `closed` (opens after `leave` 18:45) |
| P3 | At São Jacinto quay; `hours = "Mo-Fr 09:00-17:00"` | excluded `closed` on a Saturday |
| P4 | At Águeda station; `hours = "24/7"`, `daylight_only = true`, visit 90 min | fits; slot starts at **10:35** and ends at sunset (the §5.2 value), which is before `leave` 18:53 |
| P5 | At Costa Nova; `hours = "Sa 18:00-23:00"`, visit 60 min | excluded `too_short`: only 18:00–18:45 is usable |
| P6 | At Costa Nova; food, `meals = ["lunch"]`, `hours = "Mo-Su 12:00-23:00"` | in `food`, slot **12:00–15:00** |
| P7 | 50 m from P1 | same `cluster` as P1 |
| P8 | Same as P1, with `back_by` 12:00 | excluded `no_window` (passed through from the engine) |

### 10.3 Data (real dataset)
- `validate` reports **0 errors**.
- Counts: 50–70 records of kind `sight` or `activity`, and 40–60 of kind `food` or `drink` in draft. Once Pedro has reviewed: 30–40 confirmed food and drink records.
- Every municipality in §2.3 has at least 2 sights.
- Smoke test: the G6 origin and times return a non-empty shortlist with `include_drafts=True`, and every returned slot lies inside its engine window.

### 10.4 Performance
`shortlist` for the whole dataset (about 110 places) in under 0.5 s after the engine is loaded. The engine call alone measured 0.02 s for 200 points.

## 11. Milestones and acceptance criteria

| # | Milestone | Done when |
|---|---|---|
| M1 | Format and tooling | Record format, vocabulary, validator, hours parser, daylight, BM25 and copy check; unit tests pass |
| M2 | Draft dataset | `extract` run; about 60 sight records and about 50 food and drink proposals drafted with cached sources; `validate` 0 errors; `review.csv` written. Stop here for Pedro's review |
| M3 | Shortlist | §5.4–5.6 implemented; `Engine.walk_seconds` added; golden tests P1–P8 pass |
| M4 | Interfaces | CLI, HTTP and the food fallback; performance test passes |
| M5 | Reviewed dataset | After Pedro's review: the confirmed counts in §10.3, and a re-run of `validate` and the smoke test |

## 12. What Pedro must supply
1. **Review of the drafts:** status, priority, visit lengths, hours and descriptions, via `review.csv`. This is the M2 → M5 gate.
2. **The pick of 30–40 food and drink places** from about 50 proposals.
3. **Municipal holidays** the agent couldn't confirm.
4. Any place he wants added or removed from §4.4.

## 13. Things not to do
- Don't invent hours, prices, holiday dates or facts. Leave the field empty, keep the record `draft`, and let the validator flag it.
- Don't copy wording from any source (§4.6). Don't serve cached source text.
- Don't scrape review sites (TripAdvisor, Google reviews, TheFork) for text or ratings.
- Don't put places outside §2.3 in the dataset.
- Don't call an LLM. The planner is the next layer.
- Don't change engine routing; only add `walk_seconds` (§6.4).
- Don't add core dependencies beyond the standard library.
- Don't publish the API (Issues.md #1).

## 14. Later (context only)
- **LLM planner:** takes a `Shortlist`, orders a day using only the slots and journeys provided, and has every plan re-checked against the engine and the hours before it's shown.
- Portuguese descriptions; events (for example Feira de Março, summer festivals); Coimbra and Espinho as destinations.
- Moving the dataset to a database once it outgrows files.
