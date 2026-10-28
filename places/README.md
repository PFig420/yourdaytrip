# places: the YourDayTrip places layer

Turns the reachability engine's time windows into a shortlist of things worth doing on a given day: each item is a place,
the slot when it is open and reachable, and why it fits. Spec: `../PlacesLayer.md`. The core (`places/`) uses only the
standard library; the HTTP API needs FastAPI.

**Status (2026-10-20): M1, M2 (drafts), M3 and M4 are done. The work stops at the M2 gate: every record is `draft` until
Pedro reviews `build/review.csv` (M5).**

## Run

From the repo root (`MVP-LiveBus/`):

```bash
pip install -e "places[web,dev]"            # or: export PYTHONPATH=places:engine
python3 -m pytest places/tests              # 49 tests (run engine/tests separately: each has its own conftest)
python3 -m places validate                  # errors fail, warnings listed
python3 -m places review                    # -> places/build/review.csv for Pedro
python3 -m places apply-review places/build/review.csv
python3 -m places shortlist --from 41.14872,-8.58484 --date 2026-10-24 --after 08:00 --back-by 21:00 \
                            --interests maritime,beach --pace relaxed --drafts [--json] [--journeys]
python3 -m places search "azulejos and old houses"
python3 -m places food-near 40.6125 -8.74974 --radius 300     # unverified OSM fallback
python3 -m places extract                   # re-query OSM (slow: about 4 minutes) -> cache/osm_places.json, build/candidates.csv
python3 -m places.web                       # http://127.0.0.1:8200  (local only, Issues.md #1)
```

Python: `lib = places.load("places/data", engine=reach.load("engine/config"))`, then `lib.shortlist(...)`, `lib.search`,
`lib.food_near`, `lib.get`. Only `confirmed` records are used unless `include_drafts=True`.
HTTP: `POST /shortlist`, `GET /search?q=`, `GET /places/{id}`, `GET /food-near?lat=&lon=`, `GET /health`.
The optional `/map` was not built.

## What is in the dataset (all `draft` except two `rejected`)

| | Records | With hours | Without hours |
|---|---|---|---|
| Sights and activities | 52 (50 usable, 2 rejected) | 43 (41 usable) | 9 |
| Food and drink | 60 | 30 | 30 |

Sights per municipality: Aveiro 19, Ílhavo 11, Ovar 4 (+ Furadouro rejected), Águeda 5, Estarreja 3, Mira 3, Murtosa 2 (+ Cais do Bico rejected), Vagos 3.
Source pages are cached in `cache/sources/<id>-<n>.txt` (first line: URL and fetch date). Facebook and Instagram pages
cannot be read from this machine (login wall), so for those the cached text is the search-result summary, and the first
line says so. Where a page could not be read at all the record lists the URL anyway and says so in `hours_note`.

### Food and drink against the §4.3 quotas

Presence rule (relaxed 2026-10-20): an active Facebook or Instagram page, or a municipal or regional tourism listing. I could
not check that a social page is *active*; `hours_note` says "page found by search; current activity could not be checked".
Hours that come only from OpenStreetMap are marked lower confidence. The 600 m rule is enforced by a test; every
place is within 600 m of a stop.

| Area | Quota | Proposed |
|---|---|---|
| Aveiro centre | 14 + 7 bars | 26 restaurants and cafés, 3 pastry shops, 7 bars |
| Costa Nova and Barra | 7 + 1 bar | 3 (few Costa Nova places are within 600 m of a stop); no bar |
| Ílhavo, Gafanha, Vista Alegre | 4 | 4 (Rapotacho, O Braseiro, Santa Brasa, Por Acaso) |
| São Jacinto | 2 | 2 (A Peixaria, O Santos) |
| Torreira and Murtosa | 3 | 1 (O Bico and Taberna D'Alcina are far from any stop) |
| Ovar and Furadouro | 3 + 1 bar | 3 + 1 (all in Ovar town; nothing within 600 m of a stop at Furadouro) |
| Mira, Vagueira, Águeda | 4 + 1 bar | Mira 4 + 1 bar, Vagueira 6, Águeda 1 |

Still short: Costa Nova and Barra (one bar and four places), and Torreira/Murtosa (two). 30 food records have no hours.

## Decisions (where the spec was silent or had to give)

- **Entrance coordinates are not surveyed.** Positions come from OSM (building centroid, or the street for places geocoded
  from an address with Nominatim). Every record lists `lat` and `lon` in `provenance.estimates`. Several are the street, not
  the door; the `hours_note` says so.
- **Open-air places** (beaches, quays, parks, trails, street routes) use `hours = "24/7"` with `daylight_only = true`, as §4.1
  suggests. Their `hours` is listed in `estimates` since no source gives gate times.
- **Beach access points (2026-10-20).** `praia-torreira` is at the beach-front access in Torreira town (toilets and cafés by
  the sea), 270 m from the Torreira - Assembleia Theatro stop (line 25). The sand mapped as "Praia da Torreira" is 3 km south.
  `praia-sao-jacinto` is at the main access (car park, toilets, Café Dunas), about 1.4 km from the ferry-quay stops.
- **Optional `max_walk_m` per place (up to 2000).** The shortlist makes one engine call for the default 1,200 m limit and one
  more for each distinct `max_walk_m` value used, so a long walk never loosens the rule for other places. Used for
  `praia-sao-jacinto` (1500) because its access point is beyond 1,200 m from the quay. I also set it (1500) on
  `reserva-natural-dunas-sao-jacinto`: at 1,200 m the engine finds no way back on weekends. The validator rejects values
  above 2000 and warns when a place is farther than its own limit.
- **Rejected records.** `cais-do-bico` (5.7 km from any stop) and `praia-furadouro` (4.2 km) are `status = "rejected"` with
  `provenance.status_reason = "no public transport in the current feeds"`. Rejected records need a reason, are never used by
  the shortlist or search, and are not counted in the dataset totals.
- **Fixed entries (`entry_times`).** For places visitable only at set times (guided tours, scheduled climbs, departures) a record
  may carry `entry_times`, in the opening-hours syntax with single times instead of ranges, for example
  `"Mar-Oct Tu-Sa 10:30,11:30,14:30; Nov-Feb Tu-Sa 09:30,10:30; PH off"`. `hours` stays the building's opening hours and
  is required. The shortlist then returns one slot per departure (start = the entry time, end = start + the visit length)
  instead of one open interval, and marks the item `fixed_entries: true` with the `why` line "fixed entries at ...". A departure
  counts when the entry time is inside opening hours (and before closing minus `last_entry_min`) and the visit ends before
  `leave`; it may run past closing. If none fits the exclusion reason is `no_departure`. The validator rejects an
  `entry_times` that does not parse or has no `hours`. Set on Casa-Museu Egas Moniz, Farol da Barra and the Vista Alegre chapel.
  Operator-run boat tours publish no fixed times, so they are not set.
- **Area box widened:** `lon_min` is −8.82 instead of −8.80, because Praia de Mira and its museum are at about −8.80.
- **Price scale for sights:** `€` up to 5 EUR, `€€` 5 to 12 EUR, `€€€` above. Most prices are estimates (listed in `estimates`);
  only the Aveiro museums, the Fábrica, the Carmelitas church, and Casa-Museu Egas Moniz' guided slots have sourced values.
- **Municipal holidays** (`config/holidays.toml`) accept `MM-DD` or `easter+N`. Ílhavo is Easter Monday, Vagos and Águeda
  are Easter + 50 (the Monday after Pentecost). Murtosa is empty with `# VERIFY`.
- **Hours semantics (§5.1):** a later rule overrides earlier ones; `PH` rules take over on public holidays, including
  municipal ones for that place's municipality. An end past midnight belongs to the opening day, and nothing spills into
  the next day's query.
- **Slots:** `usable_slots` returns the clipped intervals where the visit fits, plus `latest_start` (closing − last entry,
  and closing or the engine's `leave` − visit length, whichever is earlier). Meal windows are merged, so lunch and snack
  can join up.
- **Exclusion reasons:** `no_window`, `unreachable`, `no_return` (from the engine), `closed`, `daylight`, `meal_time`,
  `too_short`, `weather`, `no_hours`, `bad_hours`.
- **Ranking and clusters (§5.5-5.6):** the shortlist always asks the engine for journeys (to count rides) and drops them from
  the output unless `include_journeys=True`. BM25 relevance is normalised over the places that fit. Clusters are single-link
  over the places that fit, using `Engine.walk_seconds` (10 minutes); candidates 900 m apart are skipped before the barrier check.
- **Staleness** is measured against the query date, not today. Never-checked records (no hours) are always listed by
  `review --stale`.
- **Descriptions** are in my own words; facts come from the cached pages. The copy check (no 8 shared words) passes for all records.
- **Hours from search snippets.** Three records use hours I read through a search snippet because the page could not be
  fetched: Fábrica (JavaScript page), Igreja Matriz de Ovar and Casa-Museu Júlio Dinis (cm-ovar.pt does not resolve from this
  machine). Each says so in `hours_note`. Please re-check them first.

### Reference points of long places (CurrentChanges.md P, 2026-10-27)
- **Format and rules:** optional `[[points]]` (`name`, `lat`, `lon`, `source`) after `[provenance]`; the record's own `lat`/`lon` stays the main point and counts as one of the points. At most 6 extra points, each inside the area box, within 3 km of the main point, at least 150 m from each other and from it, with a name and a source (`validate.check_points`, PlacesLayer.md §4.1 and §4.5). `Place.all_points()` lists them, main first. All 117 records re-render byte for byte with `model.dumps`, so the two edited records changed only by appended `[[points]]` blocks (checked with `diff`).
- **Where they are used:** the shortlist asks the engine for every point (`<id>` for the main one, `<id>#<k>` for the others) in the same `windows` call and keeps the earliest-arriving point's window (the main point wins a tie); `Item.point` and the item's JSON `point` name it. Clusters use the closest pair of points (up to `CLUSTER_SKIP_M`). `validate` warns about the stop distance to the nearest of a place's points (the warnings fell from 17 to 15). Sun times, the area box and maps still use the main point. A visit uses **one** point: you arrive at the point reached first and leave from it; routing in at one point and out at another is a possible later change.
- **Which places got points: two.** Overpass was queried once for the OSM ways and relations of the candidate records (`out geom`, cached in `places/cache/osm_geometry.json`). Only `praia-costa-nova` (way/301852964, geometry out to 1,346 m from its main point) is longer than 400 m. For `costa-nova-palheiros` the record's way is only 74 m long and 1,238 m from Canastra, so by Pedro's choice ("use more OSM") a second query (`osm_costa_nova.json`, `osm_costa_nova_paths.json`) supplied the seafront road (Avenida José Estêvão, ways 54570759 and 109898722) and the dune boardwalk (way/152223321).
  - `praia-costa-nova`: 6 points: the landward end of the dune boardwalk (345 m from Canastra) and five along the beach way (about every 350-700 m, south to north end).
  - `costa-nova-palheiros`: 4 points along the seafront road: its south end (20 m from Canastra), by the Largo Arrais Ançã bus stop, and two further north.
  - The beach is 470 m from Canastra at its nearest point (the sea side of the dunes), so its point within 400 m is the boardwalk entrance, which §4.1 allows ("beach access"). Both Costa Nova checks are met: 345 m and 20 m.
- **Left single-point.** Whole geometry within 400 m of the main point: `praia-da-barra` (229 m), `bairro-beira-mar-aveiro`, `canal-central-rossio-aveiro`, `ecomuseu-marinha-da-troncalhada`, `pateira-de-fermentelos` (289 m), `lagoa-de-mira`, `parque-infante-d-pedro-aveiro`, `jardim-oudinot` (362 m). The OSM element is a small fragment of a much larger place (5-7 m): `praia-sao-jacinto`, `reserva-natural-dunas-sao-jacinto`. No OSM way, or only a node, and no positions in the record's own sources: `praia-torreira`, `praia-vagueira`, `praia-de-mira`, `bioria-percurso-salreu`, `art-nouveau-route-aveiro`, `agueda-street-art`, `ovar-azulejos`. The brief counted 13 candidates with an OSM way or relation; the records have 12.
- **The new points were confirmed by Pedro on 2026-10-27** (their `source` now ends "confirmed by Pedro 2026-10-27"); they were proposals until then. `places/review/reference_points.geojson` has them and the main points for Pedro to look at in geojson.io.

## Known limitations

- Santuário de Vagos (1.9 km from a stop) will always show as `unreachable`. Other places need a long walk (see `validate`
  warnings above 800 m; the engine default is 1,200 m).
- Praia da Torreira is reachable on weekdays only in the current feeds: on Saturdays and Sundays there is no way back
  (`no_return`).
- Hours are the published weekly pattern. Easter Sunday and Carnival Tuesday closures are in `hours_note` only. The Aveiro
  museums are open on 12 May even when it is a Monday; the rule says Mondays closed.
- Farol da Barra: the winter timetable switches on 1 November, but clocks change on the last Sunday of October.
- Umbrella Sky (Águeda), the Costa Nova fish market, the salt pans, the Forte da Barra and the Misericórdia church have no
  published hours, so they stay `draft` and are excluded as `no_hours`.
- Conflicts between sources are recorded in `hours_note` (Museu Marítimo Sundays, Museu Vista Alegre Mondays, Igreja das
  Carmelitas entry fee, the Troncalhada ecomuseum).
- No `/map` page, and no LLM planner (next layer).

## Records for Pedro to look at first

Everything except the two rejected records is `draft`. The review sheet has one row per record with its warnings. The ones that most need a human check:
the 9 sights and 30 food places with empty `hours`; the three read through snippets above; Museu Etnográfico da Praia de
Mira (hours from a municipal page of the Covid period); and the Egas Moniz house (guided visits only).

## Layout

```
places/
  config/     vocabulary.toml, holidays.toml, places.toml (meal windows, area box, thresholds)
  data/places/*.toml   the dataset (one file per place)
  cache/      osm_places.json (OSM, 2026-10-20), sources/<id>-<n>.txt (cached pages, never served by the API)
  build/      candidates.csv, review.csv (git-ignored)
  places/     model, validate, hours, sun, holidays, retrieval, shortlist, library, extract, review, cli, web/
  tests/      unit, golden (fixtures in tests/fixtures/places), data, more
```

## Licences and attribution

Coordinates, OSM ids and the `food-near` fallback list are © OpenStreetMap contributors (ODbL); API responses that include
them carry an attribution field. Descriptions are original text. Cached source pages are for checking only. Nominatim
(OpenStreetMap) was used to geocode 12 addresses, one request per second.
