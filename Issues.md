# Issues — Aveiro GTFS feed

Open issues with the Aveiro bus and ferry feed (`data/gtfs/aveiro.zip`, built by `converter/` from `schedules/schedules.csv`). Status on 19 Oct 2026: the strict build passes. It has 828 trips and 319 stops, the validator reports 0 errors and all 86 converter tests and 42 engine tests pass (rechecked 23 Oct 2026). **None of these issues blocks the build.** Item 1 must be resolved before anyone outside the project sees the feed.

| # | Issue | Priority | Blocks |
|---|---|---|---|
| 1 | Launch on static GTFS without operator permission: conditions to meet | High | Public launch |
| 2 | 36 stop positions proposed by Claude are unchecked | Medium | Trust in the feed |
| 3 | Line 3 `Eucalipto A` is unverified | Medium | — |
| 4 | Two accepted fast legs (line 8, line 12); line 18 `p6t1c19` decided | Low | — |
| 5 | ~~Four fused-name aliases are unconfirmed~~ **Resolved 2026-10-19** | ~~Low~~ | — |
| 6 | ~~Name-cluster suggestions are not reviewed~~ **Resolved 2026-10-19** | ~~Low~~ | — |
| 7 | ~~Urban route names are reconstructed~~ **Resolved 2026-10-19** | ~~Low~~ | — |
| 8 | ~~Aveiro municipal holiday (12 May) is unverified~~ **Resolved 2026-10-19** | ~~Low~~ | — |
| 9 | ~~Feed window ends one day late~~ **Resolved 2026-10-19** | ~~Low~~ | — |
| 10 | Service notices must be re-read before every build | Process | — |
| 11 | Two placeholder values in `feed_info` | Low | Publication |
| 12 | ~~Sunday and holiday line 36 trips are missing their Costa Nova arrival~~ **Resolved 2026-10-19** | ~~High~~ | — |

## Open

### 1. Launch on static GTFS without operator permission: conditions to meet
- **Decision (Pedro, 2026-10-22):** The app launches on the static GTFS built from the operators' public timetables, without waiting for written permission. The GTFS files are not shared outside the project.
- **Reasoning:** Timetables are facts, not creative works. The operator *creates* them as part of running its service, and under the EU Court's fixture-list rulings (*British Horseracing Board v William Hill*, C-203/02; *Fixtures Marketing*), created data is unlikely to be protected by the database right. EU policy (Regulation (EU) 2017/1926) favours reuse of static travel data. This is a reasoned view, not legal advice; get a short legal check before any commercial deal.
- **Caveat:** Keeping the GTFS private is **not** the protection here. If a right applied, showing the times in a public app would count as re-use whether or not the file is shared. The protection is the point above.
- **Conditions before public launch:**
  - [ ] **OpenStreetMap credit** in the app and API responses: "© OpenStreetMap contributors" (ODbL produced work).
  - [ ] **ODbL-ready data:** keep OSM-derived positions identifiable (stop positions in `converter/config/stop_coords.csv`, the places' coordinates and reference points, already marked by source, and the map path cache `data/paths/` and the walking overrides `data/paths/walk_overrides.csv`, both built from OpenStreetMap ways by `mapdata/`). Be ready to offer that subset under the ODbL, because public use of a derived database requires it. This applies to the OSM-derived positions, not to the timetables.
  - [ ] **Disclaimers:** times are the operators' published schedules and "indicative" (the Busway booklet says *meramente indicativos e não vinculativos*). Show a "check with the operator" note on return legs and a "data as of <date>" in the app.
  - [ ] **No implied affiliation:** no CP, Busway or Aveirobus logos, and no wording that suggests an official app.
  - [ ] **CP update step:** `cp.zip` ends on 2026-12-12. Load CP's new timetable before then, update the hash in `engine/config/feeds.toml`, and re-run the golden tests.
  - [ ] **Notice routine** (issue 10) run before every build, with the date recorded.
  - [ ] **Map tiles** from a provider whose terms allow the app's traffic. The test page's map uses OpenStreetMap's standard tile server (`[web] tile_url` in `planner/config.toml`), which is fine for local testing at low volume and not a production source.
  - [ ] **Courtesy email** to Busway-CirAveiro and the Câmara Municipal de Aveiro, saying the app uses their published timetables. It is informational; don't wait for replies. Ask the Câmara for the official Aveirobus GTFS at the same time.

### 2. 36 stop positions proposed by Claude are unchecked
- **What:** These are the rows in `converter/config/stop_coords.csv` whose source says "proposed by Claude … confirm": 3 on lines 35/36 and 33 on other lines. Each is a Busway or OpenStreetMap stop record, chosen by name and route order. The 23 rows marked "placed by Pedro" are not part of this issue.
- **Why it matters:** 15 of the 36 are medium confidence. Those were matched by position along the route, not by name. They include the city-centre stops on the line 15 circulars (Loja do Cidadão, Forca - Hospital, Av. Adelino Amaro da Costa, Sra. das Febres, Arco do Comércio, Segurança Social) and Bustos, Palhaça, Cantanhede and Areão.
- **Resolve:** Open `converter/review/stop_candidates_remaining.geojson` (colour-coded) and `stop_candidates_lines_35_36.geojson` in geojson.io or QGIS. Check the medium ones against Street View. Change `source` to "confirmed by Pedro, <date>", or correct the coordinates.
- **One assumption to check:** OpenStreetMap's stop "Forca - Loja do Cidadão" stands on Av. Adelino Amaro da Costa. It was assumed that the 1 October timetable renamed that stop "Av. Adelino Amaro da Costa", and that the new stop pair outside the Loja do Cidadão building is "Loja do Cidadão".

### 3. Line 3 `Eucalipto A` is unverified
- **What:** Aveirobus closed "Travessa do Eucalipto", and line 3 now starts at "Eucalipto A" (`config/service_changes.csv`). In all 33 line 3 trips the old stop's times were carried over to Eucalipto A. The 500 m sanity check could not run, because the closed stop has no coordinates in any source.
- **Resolve:** Confirm Eucalipto A's position, and confirm that the first departures of the 16 trips starting there still hold. Aveirobus's notice or a current line 3 sheet would show both.

### 4. Two accepted fast legs (line 8, line 12); line 18 `p6t1c19` decided
- **Line 18, trip `p6t1c19` (DECIDED 2026-10-19):** `bus.pdf` page 6 prints Hospital Rovisco Pais 19:26 then Figueira da Foz 19:35, which would be 18.3 km in 9 minutes (about 122 km/h; the other three trips take 28-33 minutes). Decision (Pedro): the trip **ends at Hospital Rovisco Pais 19:26 as printed**; the Figueira row is not added and no leg is accepted. The other three line 18 trips to Figueira are unchanged. A 19:35 arrival in Figueira is therefore not in the feed for this trip.
- **Line 12, trip `u12b1c1`:** the sheet gives 2 minutes for Verba B (08:32) → Estação Quintas A (08:34), a distance of 3.43 km (about 103 km/h). The reverse direction takes 10 minutes. You decided to keep both times as printed, and `config/accepted_legs.csv` exempts this one leg from the 90 km/h check.
- **Line 8:** São Bento - Póvoa do Valado A → Igreja Póvoa do Valado is 1,245 m in 1 minute (75 km/h). It's under the limit, but fast for a village road. It's probably rounding on the sheet.
- **Resolve:** Ask Aveirobus for the real running times, or watch one trip. A journey planner will treat both legs as achievable.

### 5. ~~Four fused-name aliases are unconfirmed~~ RESOLVED 2026-10-19
- **Resolution:** all four pages were re-read (bus.pdf pp. 6, 7, 20, 21). In every case the single printed time belongs to the **first** component, so the earlier aliases (which took the terminal's time) were wrong for three of the four. Now each fused name is aliased to its first component and the second component's time is added from the PDF in `config/extra_stop_times.csv` (78 rows). Effect: line 18 Volta arrivals at the Aveiro terminal are 10 minutes later than the feed said, line 18 Ida now reaches Figueira, and 30 line 36 weekend trips to Aveiro now reach the terminal. A page-by-page audit (PDF text times vs CSV rows plus extra rows) now balances on every page of `bus.pdf`; the only unseen rows are the two Sunday rows printed as single characters (12 on page 20, 12 on page 21), which were read from character positions. Tool: `converter/review/extract_missing_rows.py`.
- **Original report:**
- **What:** In four places, two table columns were read as one stop name. Example: "Hospital / Universidade B (Aveiro) Terminal Rodoviário de Aveiro". The builder agent aliased each one to a single stop, based on its own reading of the sheets (`config/stop_aliases.csv`, "FUSED" section). One of them, line 18 "Hospital Rovisco Pais Figueira da Foz (Estação CP)", loses the Figueira terminus time, which is missing from the CSV.
- **Resolve:** Check `schedules/pdfs/bus/bus.pdf` pages 6, 7, 20 and 21. Where a printed time belongs to the second stop, change the alias. The line 18 terminus time could also be added by hand.

### 6. ~~Name-cluster suggestions are not reviewed~~ RESOLVED 2026-10-19
- **Resolution:** six merged as same-stop variants (accent, case, OCR, glued side letter): `Caçadores 10 - sé`, `Colégio D. Jose I`, `Escola Jaime Magalhaes Lima B`, `Estagao de Aveiro`, `ISCAUAA`, `Av. 25 de Abril - Escola Mario Sacramento B`. Two rejected: `FORTE DA BARRA (FERRY)` vs `Forte da Barra - Ferry` (ferry quay vs bus stop) and `Pessegueiro do Vouga - Av. Joaquim Martins` vs `(2)`. Stops 325 to 319. Reversible in `config/stop_aliases.csv`.
- **Original report:**
- **What:** `converter/build/name_clusters.csv` lists OCR variants of the same stop, with a suggested canonical name for each.
- **Why it matters:** An unmerged variant becomes a separate stop at the same place. That's harmless for routing, but it clutters stop lists.
- **Resolve:** Accept or reject each suggestion, and copy the accepted ones into `config/stop_aliases.csv`. Two have already been merged: Urbanizagao → Urbanizacao, and Jose → José Rosas.

### 7. ~~Urban route names are reconstructed~~ RESOLVED 2026-10-19
- **Resolution:** compared with the title bar on page 1 of every sheet (rendered and read). Corrected six names in `config/routes.toml`: lines 1, 2, 3, 6, 11 and 12 (for example line 2 is "Monte Cacia (por JF Cacia) – Aveiro – Aradas", line 12 uses "EB23", line 6 is "Escolas Velhas Oliveirinha – Aveiro"). The others already matched. The "(L15)" suffix on Azul/Verde is kept by decision.
- **Original report:**
- **What:** The Aveirobus `ab-*` names in `converter/config/routes.toml` were reconstructed from garbled OCR (for example "CARREGA EX IAVERO" became "Carregal – Eixo – Aveiro"). They were not read from the PDF titles.
- **Resolve:** Compare each name with the title of its sheet in `schedules/pdfs/urbano/`. This is cosmetic: names don't affect journeys.

### 8. ~~Aveiro municipal holiday (12 May) is unverified~~ RESOLVED 2026-10-19
- **Resolution:** the Câmara's own press note for the 2026 Festas do Município says "Feriado Municipal, dia 12 de maio" (noticiasdeaveiro.pt). `# VERIFY` removed from `feed.toml`. Still an assumption: that Aveirobus and the ferry run holiday timetables on it.
- **Original report:**
- **What:** `converter/config/feed.toml` applies 12 May as a holiday to Aveirobus and the ferry. It's marked `# VERIFY`.
- **Resolve:** Confirm the date on the Câmara's site. It affects one day a year.

### 9. ~~Feed window ends one day late~~ RESOLVED 2026-10-19
- **Resolution:** `end = 2027-09-11` in `config/feed.toml` and the test fixture; rebuilt.
- **Original report:**
- **What:** The feed ends on 2027-09-12, but should end on 2027-09-11, the day before the second Sunday of September, when school-year services restart. The spec has been corrected; `config/feed.toml` and the test fixture still say 2027-09-12.
- **Resolve:** Change `end` in both files and rebuild.

### 10. Service notices must be re-read before every build
- **What:** The PDF sheets lag behind operator notices. The line 3 closure (issue 3) was found only because you pasted the notice.
- **Resolve:** Before each build, read Aveirobus's "Alterações de serviço" and Busway's notices. Record any stop or route change in `converter/config/service_changes.csv`.

### 11. Two placeholder values in `feed_info`
- **What:** `feed_publisher_url` is `https://example.com`, because the validator rejects `.invalid`, and the feed has no contact email (validator warning `missing_feed_contact_email_and_url`).
- **Resolve:** Set real values before any publication (see issue 1).

### 12. ~~Sunday and holiday line 36 trips are missing their Costa Nova arrival~~ RESOLVED 2026-10-19
- **Resolution:** the Sunday Costa Nova row was not lost from the PDF, only split into single characters, so it was recoverable. Read from `bus.pdf` page 20 by character position, under each Farol column (top = 759.2). The converter now has `config/extra_stop_times.csv` (`source,trip,stop_sequence,stop_name,time,evidence`; fails if the CSV already has that stop_sequence). It holds **28 rows**: 12 Sunday Costa Nova times (columns 1-9, 11-13) and 16 Saturday ones. Saturday's fused row turned out to carry the **Farol** time (07:57), not Costa Nova's (08:05), so its alias now points to `Farol - Forte da Barra` and Costa Nova is added separately. Sunday 25 Oct now has 14 Aveiro terminal to Costa Nova buses (first 07:20, arriving 08:05), not one. Regression tests: `test_issue12_*` in `converter/tests/test_build.py`. The parser and CSV were not changed.
- **Page 21 (Costa Nova to Aveiro) had the same defect and is also fixed**: 28 Saturday/Sunday trips lacked their Terminal arrival (see issue 5).
- **Original report:**
- **What:** On `Domingos e Feriados`, 12 of the 15 outbound line 36 trips (Aveiro → Costa Nova, `bus.pdf` page 20) end at `I_orie Farol - Forte da Barra` (aliased to Farol), with no stop time at `Largo Arrais Ançã - Costa Nova`. The text extraction lost the Costa Nova row of the Sunday column. The other 3 are fine:
  - the 20:25 and 23:15 reach Costa Nova, recovered from reversed, letter-spaced text by an alias;
  - the 17:00 genuinely ends at Forte da Barra - Ferry, as it does on Saturdays.

  On Saturdays the Costa Nova row survived, but fused with Farol (issue 5).
- **Found by:** prototyping the reachability engine on 19 Oct 2026. On Sunday 25 Oct the first bus from the Aveiro terminal that reaches Costa Nova after 09:00 is the 20:25. In the feed, every earlier one stops at Farol, 3.4 km short.
- **Why it matters:** Costa Nova is the main beach day trip. On Sundays and holidays the product would say it's unreachable for most of the day.
- **Resolve:** Read the Costa Nova times for the Sunday column on `bus.pdf` page 20, then add them. Either fix the parser (`schedules/pdf_schedules_to_csv.py`) and regenerate the CSV, or add a converter config file for extra stop times. The converter has none today: `time_fixes.csv` can only change an existing time. Check Saturday's fused "Farol … Costa Nova" row at the same time, because it has only one printed time for two stops.

## Decided (for the record)

| Date | Decision | Where |
|---|---|---|
| 2026-10-19 | School calendar 2026/27 from Despacho n.º 8368/2024. School-term trips run until **11 Jun 2027** (years 9/11/12 end on 4 Jun; 1st cycle on 30 Jun) | `config/feed.toml` |
| 2026-10-19 | Line 17 `p5t1c6`, Zibreiros: the PDF prints 17:02, which is a typo; **17:20** used | `config/time_fixes.csv` |
| 2026-10-19 | Line 12 `u12b1c1`: impossible 2-minute leg kept as printed (issue 4) | `config/accepted_legs.csv` |
| 2026-10-19 | "Farol - Praia da Barra" and "Farol - Forte da Barra" are one stop (Busway 10299) | `config/stop_aliases.csv` |
| 2026-10-19 | Centro de Saúde B moved from a wrong match 11.5 km north to the Aveiro health centre stop (Busway 11010) | `config/stop_coords.csv` |
| 2026-10-19 | 23 stop positions placed by Pedro, including the R. Dr. Mário Sacramento A/B conflicts | `config/stop_coords.csv` |
| 2026-10-19 | Issue 12: 28 missing line 36 Costa Nova stop times added from `bus.pdf` p.20; Saturday fused row re-aliased to Farol | `config/extra_stop_times.csv`, `config/stop_aliases.csv` |
| 2026-10-19 | Fused names re-read on bus.pdf pp. 6, 7, 20, 21: first component's time kept, second added (78 rows in total, includes issue 12) | `config/extra_stop_times.csv`, `config/stop_aliases.csv` |
| 2026-10-19 | Line 18 `p6t1c19` ends at Hospital Rovisco Pais 19:26 as printed (the sheet's 19:35 Figueira time would imply ~122 km/h); no Figueira row, no accepted leg (issue 4) | `config/extra_stop_times.csv` |
| 2026-10-19 | Name clusters: 6 merged, 2 rejected; route names corrected from sheet titles; 12 May confirmed; window end 2027-09-11 | `config/stop_aliases.csv`, `config/routes.toml`, `config/feed.toml` |
| 2026-10-19 | Azul and Verde are line 15 (`ab-15-azul`, `ab-15-verde`) | `config/routes.toml` |
| 2026-10-19 | All Aveirobus sheets treated as current, including those dated June 2025 | spec §5.5 |
| 2026-10-22 | Launch on static GTFS built from public timetables, without waiting for operator permission; GTFS files not shared outside the project; launch conditions listed in issue 1 | issue 1 |
