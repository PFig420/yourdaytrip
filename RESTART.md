# RESTART.md: YourDayTrip is parked (2026-10-28)

YourDayTrip is **parked**, not abandoned. The technology works; the business is untested. This note says where everything is, what goes stale while it is parked, and what to do first on a restart.

## 1. Why it is parked

- **Built and proven:** a car-free day planner for Aveiro and visitors from Porto. Every bus, train and ferry leg comes from a timetable and is checked in code. Claude Sonnet 5.5 makes a valid plan on the first try **84 %** of the time (50 test requests, 28 Oct 2026).
- **Not tested:**
  - **Demand.** No real person has used a plan, and the hand-made-plan demand test was never run.
  - **Who pays.** No buyer conversation was held with the Câmara, CIRA or the hostels.
- **The honest reading:** the technology was de-risked, the business barely at all. On a restart, start with people, not code (§6).
- **Also considered: the Netherlands.** A first look found plenty of sites with places and ideas but no real planner that builds specific, checked trips, and Gemini's plans were generic. That makes the gap plausible there too. Still open: who would pay, and whether the engine copes with a national feed (§6).

## 2. State at parking

| Part | State | Where to read more |
|---|---|---|
| Aveiro bus and ferry GTFS | Built from the operators' PDF timetables. 828 trips, 319 stops; valid to 2027-09-11. | `GTFSConverter.md`, `Issues.md` |
| Engine | Reachability over CP and Aveiro feeds. Walks use the longer of the straight-line estimate and the real OpenStreetMap path. | `ReachabilityEngine.md`, `engine/walk-overrides-report.md` |
| Places | 117 records, 78 confirmed. The two Costa Nova places have reference points. | `PlacesLayer.md`, `places/README.md` |
| Planner | Claude picks places; code schedules and checks every leg. Free time with nearby ideas, "Back home by" / "Leave by". 189 tests pass. | `Planner.md`, `planner/README.md`, `planner/eval/promptfix-report.md` |
| Test page and map | `python -m planner.web` at 127.0.0.1:8300: timeline and map along OSM roads, rails and footpaths. Buses follow their mapped OSM route where one exists. | `MapPlan.md`, `planner/eval/map-report.md` |
| Self-hosted model | Qwen3-4B on the laptop **failed** its evaluation: 39 % valid plans against Sonnet's 96 %. Production would use Claude, or a larger GPU-hosted model (M6, not started). | `~/selfhostedLLM/Execution.md`, `~/selfhostedLLM/reports/eval-qwen3-4b-instruct-2507-q4_k_m-2026-10-28.md` |
| Plan and decisions | The YourDayTrip Action Plan doc (claude.ai), updated 28 Oct 2026 | https://claude.ai/artifact/QdhKjHrXqTZZhJ3BWx6F2w |

**Git:** private repository `git@github.com:PFig420/yourdaytrip.git`, branch `main`. It holds **code and specs only**.

## 3. What is NOT in git (back these up before parking)

This laptop holds the only copy of everything below. Copy it somewhere safe off the machine, for example a private cloud folder or an external drive.

| Path | Size | What it is | If lost |
|---|---|---|---|
| `data/gtfs/` (`cp.zip`, `aveiro.zip`) | 0.4 MB | The two feeds the engine loads | CP: re-download (§4). Aveiro: rebuild with `converter/`, which needs the two rows below. |
| `schedules/pdfs/` | 75 MB | The operators' PDF timetables (Busway, Aveirobus, ferry) | **Hard to replace.** Operators replace their PDFs without notice. |
| `schedules/schedules.csv` (+ `.ocr_cache/`) | 2 MB | The timetables extracted from the PDFs, with many hand fixes (`Issues.md`) | Re-extraction is days of work |
| `data/paths/` | 4 MB | The OSM path cache, walking overrides and the flagged-walks review sheet | Rebuild: `python -m mapdata.build` (about 25 min of downloads plus 2 min). Any verdicts in the review sheet would be lost. |
| `mapdata/osm/` | 23 MB | Raw OSM downloads | Re-downloaded by the build |
| `places/cache/` | 2 MB | Fetched source pages and OSM extracts for the places layer | Mostly re-fetchable |
| `tools/gtfs-validator-cli.jar` | 39 MB | MobilityData GTFS validator, used by the converter | Re-download from MobilityData's `gtfs-validator` releases |
| `local/` | 164 MB | The Tempo Real / LiveBus project, its spec and the Barreiro feed | **Not in any repository.** |
| `~/selfhostedLLM/` | 2.4 GB (2.3 GB is the model file) | The self-hosted model setup, benchmarks and the M4 report | **Not in any repository.** The model file can be re-downloaded (`scripts/fetch_model.py`); the rest cannot. |

## 4. What goes stale while parked

| Date | What | On restart |
|---|---|---|
| **2026-12-12** | CP's train data expires. After it, Porto day trips fail (`no_plan`). | Download `https://publico.cp.pt/gtfs/gtfs.zip` into `data/gtfs/cp.zip`, update its hash in `engine/config/feeds.toml` and `engine/tests/conftest.py`, then rerun the engine and planner tests. A new hash makes the walking overrides stale until `python -m mapdata.build` is rerun. |
| 2026-12-31 | The Barreiro feed in `local/` (Tempo Real) expires | Only matters for Tempo Real |
| about 2027-01 | The OSM path cache is 3 months old (`mapdata/README.md` rebuild trigger) | `python -m mapdata.build` with the model server stopped |
| about 2027-04-18 | Place opening hours pass `stale_days = 180` (checked 20–23 Oct 2026), and plans start warning | Re-check hours before any user sees a plan |
| any time | The Aveiro PDF timetables change (school terms, summer line 35, notices; `Issues.md` #10) | Re-read the operators' notices and update `converter/config/` |
| 2027-09-11 | The Aveiro feed's own window ends | Rebuild with the new school year's timetables |

## 5. How to run it again

From the repo root (`~/MVP-LiveBus`), with `/usr/bin/python3`. A virtualenv's `python3` may lack `anthropic`.

```
# tests (run each suite separately)
/usr/bin/python3 -m pytest planner/tests       # 189
(cd engine && /usr/bin/python3 -m pytest)      # 54
(cd places && /usr/bin/python3 -m pytest)      # 56
(cd converter && /usr/bin/python3 -m pytest)   # 86
(cd mapdata && /usr/bin/python3 -m pytest)     # 35

# test page (code-only plans need nothing else)
PYTHONPATH=planner:places:engine /usr/bin/python3 -m planner.web --backend local    # http://127.0.0.1:8300/

# model plans: the local model (weak, see §2) or Claude
cd ~/selfhostedLLM && docker compose up -d                                           # for --backend local
PYTHONPATH=planner:places:engine /usr/bin/python3 -m planner plan ... --backend claude-code   # subscription, testing only
```

**Machine limits:** the laptop has 7.5 GiB and no swap. Run one heavy job at a time, and stop the model server when you're not using it.

## 6. Restart checklist (in this order)

1. **Restore the data** from §3 and run the tests (§5).
2. **Refresh what expired** (§4), CP first.
3. **Before any new code, test demand:**
   - **Plans:** write 20–30 plans by hand for real car-free visitors, for example through hostels near Porto São Bento or Erasmus students in Aveiro. `places shortlist` produces the reachable places with times.
   - **Follow-up:** ask the next day whether they went.
   - **The stop rule:** if fewer than 30 % act on the plans, stop.
4. **Hold one buyer conversation** with a date: the Câmara de Aveiro, ideally pitched together with Tempo Real, or CIRA. Ask for a letter of intent or a paid pilot, not feedback.
5. **Decide by a fixed date,** about six weeks after the restart. With no demand signal and no buyer interest, keep YourDayTrip only as a demo for Tempo Real.

**If restarting in the Netherlands instead:**
- Talk to 2–3 possible buyers first: a regional tourism organisation (spreading visitors away from Amsterdam by public transport), an NS or transport partnerships contact, or a hotel group.
- Then load the national GTFS (OVapi / NDOV) into the engine once. It was built for two small feeds, and a pure-Python scan of a national feed may be too slow or too big for this laptop.
- Bikes (OV-fiets) are part of a Dutch car-free day and are not modelled.
- Commit only if a buyer is interested and the engine copes.

## 7. Open items left as they were

- **M6:** stay on Claude (recommended) or price a GPU-hosted larger model. Pricing needs the plan volume, budget and licence choice.
- **Gate 1:** hand-check 20 journeys against printed timetables. Never done.
- **The sample plans:** rate the 10 in `planner/eval/sample-for-review.md`.
- **The paid API run** (about US$2–3) for real cost and latency.
- **Launch conditions** in `Issues.md` #1: OSM credit, disclaimers, courtesy emails, a map tile provider whose terms allow the app's traffic, and the ODbL items.
- **Smaller items:**
  - Planner.md §2.5, the local backend's section, is missing;
  - r35 (Porto, 8 Dec holiday) now finds no plan;
  - reference points for the other long places;
  - partial bus routes in OSM;
  - verdicts in the flagged-walks sheet.
