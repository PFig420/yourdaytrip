# schedules/: timetable extraction

This folder turns the published Aveiro public-transport timetables (PDFs) into one flat CSV, `schedules.csv`. That CSV is the **only input** of the GTFS converter (`../converter/`, spec in `../GTFSConverter.md`), which produces `../data/gtfs/aveiro.zip`. Nothing downstream reads the PDFs.

```
pdfs/*  ──►  pdf_schedules_to_csv.py (+ urbano_parser.py)  ──►  schedules.csv  ──►  ../converter  ──►  ../data/gtfs/aveiro.zip
```

Everything here is Python. The extractor needs `pdfplumber` for all PDFs. The urban sheets also need `numpy`, `Pillow` and `rapidocr-onnxruntime`. There is no requirements file or README, so those dependencies are not pinned anywhere. The tree is not a git repository.

## Contents

| Path | What it is |
|---|---|
| `pdf_schedules_to_csv.py` | CLI entry point. Detects each PDF's layout, parses it, writes the CSV. Contains the BusWay bus-booklet and ferry parsers. |
| `urbano_parser.py` | Parser for the AveiroBus urban-circuit sheets (OCR-based). Imported lazily by the CLI. |
| `schedules.csv` | Output: 8,663 rows, UTF-8 **with BOM**, one row per (trip, stop) time. Generated, so don't hand-edit it. |
| `run.log` | stderr of the last full run (19 Oct 2026). Shows per-file row counts and OCR repair counters. |
| `pdfs/bus/bus.pdf` | BusWay / CIRA intercity booklet (lines 16-20, 25, 32-37). Valid from 2026-09-14. |
| `pdfs/ferry/ferry.pdf` | River ferry sheet (Aveiro, Forte da Barra, S. Jacinto). Valid from 2024-05-01. |
| `pdfs/urbano/` | 13 urban-circuit sheets (lines 1-8, 10-12, Azul, Verde), plus `page.html` (a saved copy of https://www.aveirobus.pt/circuito-urbano) and `urls.txt` (the PDF URLs scraped from it). |
| `.ocr_cache/` | Cached full-page OCR results as JSON, keyed by a SHA-1 of the page image's pixels. Makes reruns fast. See "Gotchas". |
| `__pycache__/` | Bytecode, ignore. |

## Running it

The exact command of the last run isn't recorded. From `run.log` and the `source` column, it was equivalent to:

```bash
cd schedules
python3 pdf_schedules_to_csv.py -o schedules.csv \
    pdfs/bus/bus.pdf pdfs/ferry/ferry.pdf \
    https://www.aveirobus.pt/circuito-urbano
```

Inputs can be local paths, direct PDF URLs, or an HTML page URL. For an HTML page, the script scrapes every `href` containing `/horarios/...pdf`, downloads them, and skips the ferry sheet if it appears there, since the ferry is passed separately. Use `--download-dir DIR` to keep downloaded PDFs. The default is a temp dir that is deleted.

**Source labels matter.** Whatever string you pass as the input becomes the `source` column. Bus and ferry rows carry the local paths (`pdfs/bus/bus.pdf`). Urban rows carry the full aveirobus.pt URL. The urban line id is derived from that name (see below), so a file passed under a renamed path would lose its line number. Running the script from `schedules/` is required for the relative `pdfs/...` labels to match what the converter expects.

Console output goes to stderr: one line per file, plus for urban sheets the OCR repair counters per page.

A full run is slow, because every urban page is rendered at 250 dpi and OCR'd, plus several small re-OCR crops. Cached pages skip the main pass.

## Output schema (`schedules.csv`)

One row per (trip, stop) where a time is printed. Columns, in order:

| Column | Meaning |
|---|---|
| `source` | Input path or URL the row came from. |
| `mode` | `bus` (intercity booklet), `bus_urban` (urban circuits), `ferry`. |
| `page` | PDF page. For urban sheets this is always 1, because the parser hardcodes it and each sheet is one page. |
| `line` | Line id as a string: `16`, `36`, `1`..`12`, `Azul`, `Verde`, `Ferry`. |
| `route` | Title text from the sheet. Urban titles come from OCR and are often garbled (for example `CARREGA EX IAVERO`). Treat as cosmetic. |
| `direction` | Bus: `Ida` / `Volta`. Urban: `First stop -> Last stop`, or `Circular` when first and last stop match. Ferry: `Origin -> Destination`. |
| `day_type` | Normalised calendar label, see below. |
| `season` | Bus only: the 1-2 letter "ÉPOCA" column code (`A`, `B`...). Empty for urban and ferry. |
| `trip` | Trip id, unique per file. Bus: `p{page}t{table}c{column}`. Urban: `u{line}b{band}c{column}`. Ferry: `b{block}t{n}`. |
| `stop_sequence` | 1-based row index within the table (not renumbered after skipped blanks, so there can be gaps). |
| `stop_name` | Stop name as printed. |
| `time` | `HH:MM`. Hours can reach 24 or more for after-midnight services, so check before assuming a 0-23 range. |
| `note` | Footnote text applying to this time (`*` marks on bus, coloured highlight cells on urban), or `OCR uncertain - check against PDF`. |
| `valid_from` | ISO date the timetable takes effect. Empty for line 8. See "Known issues". |
| `extraction` | `text` (read from the PDF text layer) or `ocr`. |

Row counts by mode: bus 2,329, ferry 104, urban 6,230 (4,378 of them OCR).

### Day types

Values present in the CSV: `Dias Úteis`, `Fim-de-semana e Feriados`, `Sábados`, `Domingos e Feriados`, `Sábados, Domingos e Feriados`, `TODOS OS DIAS (Julho e Agosto)`, `Todos os dias`, `Dias Úteis (exceto agosto)`, `Domingos`, `Domingos ou 2.ª feira se feriado`, `3ª feiras (DU)`. The converter maps these to GTFS services, so a new label that is not handled there will fail its strict build. Bus labels are cleaned in `clean_day_type`. Urban labels go through `canon_day_type`.

### Notes

Notes carry school-calendar conditions such as "Escolar - período coincidente com o calendário escolar" or "Realiza-se no período entre o 2.º domingo...". Only the text is kept, and the converter interprets it. About 330 rows have a note.

## How each layout is parsed

`parse_pdf()` picks the parser from the first pages' text: "TRANSPORTE FLUVIAL"/"RIVER TRANSPORT" means ferry. "ÉPOCA" in the first three pages means bus booklet. Anything else is treated as an urban sheet.

### Bus booklet (`parse_bus_page`)
Text-layer PDF, so geometry is read directly with pdfplumber.
- Each table is anchored on the word `ÉPOCA`. The season codes to its right define the **trip columns**.
- Day-type bands are the grey rectangles (fill 0.851) just above that row. Their text is re-extracted by cropping the rectangle, and a column's day type is the band that covers its x-centre.
- Stop rows are lines of time cells (`H:MM`, optionally `*`, or `-` for no service) below the anchor. The table ends at a vertical gap of more than 55 pt.
- Stop names are the words left of the first column, assigned to the nearest row.
- Direction is the nearest `Ida`/`Volta` label above. A few pages (line 36) have none, and are numbered Ida then Volta in booklet order per line.
- `valid_from` comes from "Entrada em vigor: D de <mês> de AAAA".

### Ferry (`parse_ferry`)
One page, four two-row blocks (origin row, destination row). Times use `.` as the separator (`7.30`). Day-type headers are the `ÚTEIS` / `SÁBADOS,` bars. `valid_from` comes from "Em vigor a partir de...".

### Urban circuits (`urbano_parser.parse_page`)
Each sheet is a square page with one or more coloured day-type bands, a stop-name column and one time column per trip. About two thirds of the pages are flat images.
1. **Words.** Render at 250 dpi and always run a full-page OCR pass (needed for the title and line badge, which are vector outlines). If the page has a real text layer (more than 300 chars), the table words come from it, otherwise from OCR. Either way, word boxes are in 250-dpi pixel coordinates.
2. **Bands.** `find_bands` seeds on header words (`DIAS`, `FIM`, `DOMINGO`, `SABADO`, `TODOS`) and grows each band horizontally over non-white pixels. The band extent is how side-by-side tables are told apart.
3. **Grid.** Times under a band are clustered into columns (x) and rows (y). The table ends at the first vertical gap greater than 2.2 times the row pitch. The name column is the words left of the band group's left edge.
4. **Repair (OCR pages only).**
   - *Missing cells:* empty grid cells with enough ink are re-OCR'd from upscaled crops (several crop sizes, zooms, and greyscale). Cells with little ink are treated as a dash and skipped.
   - *Order violations:* times down a trip must be non-decreasing, except across midnight (`in_order`). Cells that break this are re-read the same way, and flagged `OCR uncertain` if they can't be fixed.
   - *Digit fixes:* `O`/`l`/`I` become `0`/`1`. Strings the recogniser reads as if rotated 180° (`09:00` read as `00:60`) are un-rotated (`rot180`). Merged boxes (`08:1009:30`) are split. The OCR engine runs with `use_cls=False` on purpose, because the angle classifier causes the 180° misreads.
5. **Notes.** Highlighted time cells get the footnote whose swatch colour is nearest to the cell's background colour (`find_legends`, `note_for`).
6. **Line id** comes from the filename (`...-l7-202506.pdf` is `7`, `..._l_azul_...` is `Azul`). `valid_from` comes from the edition note printed rotated down the right edge, so the page is rotated and OCR'd.

After all rows are collected, `restore_accents` puts back diacritics that OCR dropped, using only words that appear accented in text-layer rows, and only when that spelling is unambiguous.

## Reading `run.log`

For each urban page the log prints `title: N rows, OCR gaps filled=X reread=Y blank=Z`:
- `filled`: cells the OCR missed that were recovered by cropping.
- `reread`: cells re-read because they broke time order.
- `blank`: cells left empty. Most are legitimate dashes (no service at that stop), but a high count can mean lost times. Line 10 (160) and line 4 (109) are the highest.

## Known issues

These are the ones visible from this folder. `../Issues.md` tracks the downstream and data-quality ones, so read it too.
- **Line 8 has no `valid_from`.** The rotated edition note wasn't read, so 759 rows have it empty.
- **Valid dates are inconsistent between sheets.** Dates in the file names (`202506`, `202602`...) and in `valid_from` span 2025-06 to 2026-10, and ferry is 2024-05. Urban lines 1, 3, 4, 5, 7, 10 and 12 are the oldest (2025-06-01).
- **Urban titles and some stop names are noisy OCR** (see `run.log` titles). Names don't affect journeys, but they do show in the UI. `../Issues.md` lists name fixes.
- **OCR accuracy is unverified beyond the built-in checks.** Rows with `extraction=ocr` have only had order and gap checks, not a comparison against the PDFs. Spot-check against the PDF when a time looks wrong.
- **Missing times.** Some times the parser can't capture, for example the Costa Nova Sunday column on `bus.pdf` page 20 (see `../Issues.md`). Fix either in the parser followed by a regenerate, or in the converter's `config/`, never by editing the CSV.
- **No tests** exist for these two scripts, and the parsers contain many layout-specific constants (pixel fractions, thresholds, a 55 pt gap, a grey fill value). A new PDF edition with a slightly different layout is the most likely thing to break.

## Gotchas

- **Don't edit `schedules.csv` by hand.** `../GTFSConverter.md` treats it and the PDFs as read-only inputs. Corrections belong in the parser or in `../converter/config/`.
- **Regenerating the CSV changes `trip` ids, row order and OCR output**, and the converter's config files (alias and time-fix tables) may key on them or on stop names. Rebuild and run the converter's tests after any regeneration, and diff the old and new CSV first.
- **Keep a copy of the working CSV before a rerun.** A full rerun overwrites `schedules.csv` without a backup.
- **`.ocr_cache/` is keyed on pixel content**, so it is safe to reuse across runs, but if the OCR engine or version changes, stale results will silently persist. Delete the folder to force a fresh OCR. Expect a long run afterwards.
- **Encoding:** the CSV is written as `utf-8-sig`. Read it with `encoding="utf-8-sig"` or the first header becomes `﻿source`.
- **Adding a new urban line or edition:** drop the PDF in `pdfs/urbano/` (or let the scraper fetch it), keep the `...-l<N>-YYYYMM.pdf` / `..._l_<name>_YYYYMM.pdf` naming so the line id resolves, rerun, then check `run.log` for the `blank` and `reread` counters and spot-check the OCR rows.
- **Upstream links go stale.** `urls.txt` lists the 13 PDFs found on 19 Oct 2026. The aveirobus.pt site is the source of truth, and its file names change when a new edition is published.
