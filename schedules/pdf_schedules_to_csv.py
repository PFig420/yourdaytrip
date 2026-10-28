#!/usr/bin/env python3
"""Extract public-transport timetables from PDFs into one long-format CSV.

Supports three layouts, auto-detected per file:
  * BusWay / CIRA bus booklets (many pages, tables with a day-type band,
    an "ÉPOCA" season row and one row per stop)
  * AveiroBus river ferry sheet (one page, four 2-row blocks)
  * AveiroBus urban-circuit sheets (one page per line; mostly flat images,
    so these are OCR'd - needs `rapidocr-onnxruntime`, see urbano_parser.py)

An input that is an HTML page (e.g. https://www.aveirobus.pt/circuito-urbano)
is scraped for its /horarios/ PDF links; the ferry sheet is skipped.

Usage:
  python3 pdf_schedules_to_csv.py -o schedules.csv URL_OR_PATH [URL_OR_PATH ...]

Output: one CSV row per (trip, stop) with a time.
"""
import argparse
import csv
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import pdfplumber

FIELDS = [
    "source", "mode", "page", "line", "route", "direction", "day_type",
    "season", "trip", "stop_sequence", "stop_name", "time", "note", "valid_from", "extraction",
]

BUS_TIME = re.compile(r"^(\d{1,2}):(\d{2})(\*?)$")
FERRY_TIME = re.compile(r"^(\d{1,2})\.(\d{2})$")
SEASON_CODE = re.compile(r"^[A-Z]{1,2}$")
HEADER_GREY = 0.851  # fill of the day-type band rectangles


def fetch(src: str, dest_dir: Path) -> Path:
    if not re.match(r"https?://", src):
        return Path(src)
    dest = dest_dir / (src.rstrip("/").rsplit("/", 1)[-1] or "download.pdf")
    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def hhmm(h, m):
    return f"{int(h):02d}:{m}"


def group_lines(words, tol=4):
    """Cluster words into visual lines by vertical centre."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2):
        yc = (w["top"] + w["bottom"]) / 2
        if lines and abs(lines[-1]["y"] - yc) <= tol:
            lines[-1]["words"].append(w)
            n = len(lines[-1]["words"])
            lines[-1]["y"] += (yc - lines[-1]["y"]) / n
        else:
            lines.append({"y": yc, "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
    return lines


def join(words):
    return " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"]))


def join_lines(words):
    """Join words of a multi-line block in reading order."""
    return " ".join(join(ln["words"]) for ln in group_lines(words, tol=4))


def clean_day_type(txt):
    txt = re.sub(r"[\ue000-\uf8ff]", "", txt)  # private-use icon glyphs
    txt = re.sub(r"\s+", " ", txt).strip()
    # drop English duplicates that follow the Portuguese label
    txt = re.sub(r"\s*(Working Days|Saturdays|Sunday and Holidays|All Days \(July and August\)).*$", "", txt)
    txt = txt.replace("Dias-Úteis", "Dias Úteis").replace("Domingo e Feriados", "Domingos e Feriados")
    return txt.strip()


# ----------------------------------------------------------------- bus -----

def parse_bus_page(pg, pageno, source, valid_from):
    words = pg.extract_words(keep_blank_chars=False, use_text_flow=False)
    anchors = sorted((w for w in words if w["text"] == "ÉPOCA"), key=lambda w: w["top"])
    if not anchors:
        return []

    title_lines = group_lines([w for w in words if w["top"] < 70 and w["x0"] < 700])
    title = join(title_lines[0]["words"]) if title_lines else ""
    m = re.match(r"^(\d{1,2})\s+(.*)$", title)
    line_no = m.group(1) if m else ""
    route = re.sub(r"\s+", " ", m.group(2) if m else title).strip()

    dir_words = [w for w in words if w["text"] in ("Ida", "Volta", "olta") and w["x0"] < 500]

    out = []
    for ai, anc in enumerate(anchors):
        y_lo = anchors[ai - 1]["top"] if ai else 0
        y_hi = anchors[ai + 1]["top"] - 60 if ai + 1 < len(anchors) else pg.height

        # --- columns: season codes on the same line as ÉPOCA
        season_row = [w for w in words
                      if abs((w["top"] + w["bottom"]) / 2 - (anc["top"] + anc["bottom"]) / 2) < 4
                      and w["x0"] > anc["x1"] and SEASON_CODE.match(w["text"])]
        season_row.sort(key=lambda w: w["x0"])
        if not season_row:
            continue
        cols = [{"xc": (w["x0"] + w["x1"]) / 2, "season": w["text"]} for w in season_row]
        pitch = min((b["xc"] - a["xc"] for a, b in zip(cols, cols[1:])), default=50)
        left = cols[0]["xc"] - pitch / 2
        right = cols[-1]["xc"] + pitch / 2

        # --- day-type bands: grey rects ending just above the season row
        bands = []
        for r in pg.rects:
            if (abs(r["bottom"] - anc["top"]) < 25 and r["height"] > 10 and r["top"] > y_lo - 5
                    and r.get("non_stroking_color") in (HEADER_GREY, (HEADER_GREY,), [HEADER_GREY])):
                txt = join_lines(pg.crop((r["x0"], r["top"], r["x1"], r["bottom"])).extract_words())
                bands.append((r["x0"], r["x1"], clean_day_type(txt)))
        # drop duplicate rects
        bands = sorted(set(bands))

        def day_type(col):
            for x0, x1, t in bands:
                if x0 - 2 <= col["xc"] <= x1 + 2:
                    return t
            return ""
        for c in cols:
            c["day_type"] = day_type(c)

        # --- stop rows: lines below ÉPOCA that contain time cells
        cells = [w for w in words
                 if w["top"] > anc["bottom"] and w["top"] < y_hi and left <= (w["x0"] + w["x1"]) / 2 <= right
                 and (BUS_TIME.match(w["text"]) or w["text"] == "-")]
        rows = group_lines(cells, tol=5)
        kept = []
        for ln in rows:
            if kept and ln["y"] - kept[-1]["y"] > 55:
                break
            if any(BUS_TIME.match(w["text"]) for w in ln["words"]) or all(w["text"] == "-" for w in ln["words"]):
                kept.append(ln)
        rows = kept
        if not rows:
            continue
        table_bottom = rows[-1]["y"] + 15

        # --- stop names: words left of the columns, assigned to nearest row
        name_words = [w for w in words
                      if w["x1"] <= left + 1 and anc["bottom"] < w["top"] <= table_bottom
                      and w["x0"] > max(0, anc["x0"] - 400)]
        names = {i: [] for i in range(len(rows))}
        for nl in group_lines(name_words, tol=3):
            i = min(range(len(rows)), key=lambda k: abs(rows[k]["y"] - nl["y"]))
            names[i].append((nl["y"], join(nl["words"])))

        # --- footnote ("* text") right under the table
        foot = ""
        win = [w for w in words if table_bottom - 15 < w["top"] < table_bottom + 30]
        star = next((w for w in win if w["text"] == "*"), None)
        if star:
            yc = (star["top"] + star["bottom"]) / 2
            foot = join([w for w in win if w["x0"] > star["x1"] and abs((w["top"] + w["bottom"]) / 2 - yc) <= 8
                         and w["x0"] < star["x1"] + 400])

        # --- direction: closest Ida/Volta label above the table
        above = [w for w in dir_words if w["top"] < anc["top"]]
        direction = max(above, key=lambda w: w["top"])["text"] if above else ""
        direction = "Volta" if direction == "olta" else direction

        for ri, ln in enumerate(rows):
            stop = " ".join(t for _, t in sorted(names[ri])).strip()
            for w in ln["words"]:
                tm = BUS_TIME.match(w["text"])
                if not tm:
                    continue
                xc = (w["x0"] + w["x1"]) / 2
                starred = bool(tm.group(3)) or any(
                    o["text"] == "*" and 0 <= o["x0"] - w["x1"] <= 6
                    and abs((o["top"] + o["bottom"]) / 2 - ln["y"]) < 6 for o in words)
                ci = min(range(len(cols)), key=lambda k: abs(cols[k]["xc"] - xc))
                col = cols[ci]
                out.append({
                    "source": source, "mode": "bus", "page": pageno, "line": line_no,
                    "route": route, "direction": direction, "day_type": col["day_type"],
                    "season": col["season"], "trip": f"p{pageno}t{ai + 1}c{ci + 1}",
                    "stop_sequence": ri + 1, "stop_name": stop,
                    "time": hhmm(tm.group(1), tm.group(2)),
                    "note": (foot or "*") if starred else "", "valid_from": valid_from,
                })
    return out


def parse_bus(pdf, source):
    first = pdf.pages[0].extract_text() or ""
    full = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
    mv = re.search(r"Entrada em vigor:\s*(\d{1,2}) de (\w+) de (\d{4})", full)
    valid_from = ""
    if mv:
        months = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                  "agosto", "setembro", "outubro", "novembro", "dezembro"]
        valid_from = f"{mv.group(3)}-{months.index(mv.group(2).lower()) + 1:02d}-{int(mv.group(1)):02d}"
    rows = []
    for i, pg in enumerate(pdf.pages, 1):
        rows += parse_bus_page(pg, i, source, valid_from)

    # Some pages (e.g. line 36) carry no Ida/Volta label: booklet order is
    # always Ida first, then Volta, so number the unlabelled pages per line.
    seen = {}
    for pageno in sorted({r["page"] for r in rows if not r["direction"]}):
        ln = next(r["line"] for r in rows if r["page"] == pageno)
        k = seen.get(ln, 0)
        seen[ln] = k + 1
        for r in rows:
            if r["page"] == pageno:
                r["direction"] = ("Ida", "Volta")[k] if k < 2 else ""
    return rows


# --------------------------------------------------------------- ferry -----

def parse_ferry(pdf, source):
    pg = pdf.pages[0]
    words = pg.extract_words()
    text = pg.extract_text() or ""
    mv = re.search(r"Em vigor a partir de (\d{1,2}) de (\w+) de (\d{4})", text)
    months = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
              "agosto", "setembro", "outubro", "novembro", "dezembro"]
    valid_from = (f"{mv.group(3)}-{months.index(mv.group(2).lower()) + 1:02d}-{int(mv.group(1)):02d}"
                  if mv else "")

    # day-type headers (grey bars)
    heads = []
    for ln in group_lines([w for w in words if w["text"] in ("ÚTEIS", "SÁBADOS,")], tol=4):
        w = ln["words"][0]
        label = "Dias Úteis" if w["text"] == "ÚTEIS" else "Sábados, Domingos e Feriados"
        heads.append((w["top"], label))
    heads.sort()

    times = [w for w in words if FERRY_TIME.match(w["text"])]
    rows = group_lines(times, tol=4)
    x_left = min(w["x0"] for w in times) - 2
    out = []
    # rows come in consecutive pairs: (origin stop line, destination stop line)
    for bi in range(0, len(rows) - 1, 2):
        a, b = rows[bi], rows[bi + 1]
        day = [lbl for top, lbl in heads if top < a["y"]][-1] if heads else ""
        labels = []
        for r in (a, b):
            nm = [w for w in words if abs((w["top"] + w["bottom"]) / 2 - r["y"]) < 5
                  and w["x1"] <= x_left and w["x0"] > 45]
            labels.append(join(nm))
        n = min(len(a["words"]), len(b["words"]))
        for ti in range(n):
            for seq, (r, lab) in enumerate(((a, labels[0]), (b, labels[1])), 1):
                tm = FERRY_TIME.match(r["words"][ti]["text"])
                out.append({
                    "source": source, "mode": "ferry", "page": 1, "line": "Ferry",
                    "route": "AVEIRO | FORTE DA BARRA (FERRY) | S. JACINTO",
                    "direction": f"{labels[0]} -> {labels[1]}", "day_type": day,
                    "season": "", "trip": f"b{bi // 2 + 1}t{ti + 1}", "stop_sequence": seq,
                    "stop_name": lab, "time": hhmm(tm.group(1), tm.group(2)),
                    "note": "", "valid_from": valid_from,
                })
    return out


# ---------------------------------------------------------------- main -----

def parse_pdf(path: Path, source: str):
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ""
        if "TRANSPORTE FLUVIAL" in first or "RIVER TRANSPORT" in first:
            return parse_ferry(pdf, source)
        if any("ÉPOCA" in (p.extract_text() or "") for p in pdf.pages[:3]):
            return parse_bus(pdf, source)
        return parse_urban(pdf, source)


def is_ferry(path: Path) -> bool:
    with pdfplumber.open(path) as pdf:
        t = pdf.pages[0].extract_text() or ""
    return "TRANSPORTE FLUVIAL" in t or "RIVER TRANSPORT" in t


def parse_urban(pdf, source):
    import urbano_parser
    rows = []
    for pg in pdf.pages:
        got, rep, title = urbano_parser.parse_page(pg, source)
        print(f"  {title!r}: {len(got)} rows, OCR gaps filled={rep['missing_filled']} "
              f"reread={rep.get('reread', 0)} blank={rep['missing_blank']}", file=sys.stderr)
        rows += got
    return rows


def scrape_pdf_links(page_url: str):
    req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    links = re.findall(r'href="([^"]*?/horarios/[^"]*?\.pdf)"', html, flags=re.I)
    seen, out = set(), []
    for l in links:
        l = urllib.parse.urljoin(page_url, l)
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def restore_accents(rows):
    """OCR drops diacritics.  Put them back for words seen accented in text-layer rows."""
    import unicodedata
    plain = lambda w: "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn").lower()
    forms = {}
    for r in rows:
        if r.get("extraction", "text") == "text":
            for w in re.findall(r"[^\W\d_]+", r["stop_name"]):
                if plain(w) != w.lower():
                    forms.setdefault(plain(w), set()).add(w.lower())
    lex = {k: next(iter(v)) for k, v in forms.items() if len(v) == 1}
    def fix(m):
        w = m.group(0)
        new = lex.get(plain(w))
        if not new or w.lower() == new:
            return w
        return new.capitalize() if w[0].isupper() else new
    for r in rows:
        if r.get("extraction") == "ocr":
            r["stop_name"] = re.sub(r"[^\W\d_]+", fix, r["stop_name"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="PDF URLs or local paths")
    ap.add_argument("-o", "--output", default="schedules.csv")
    ap.add_argument("--download-dir", default=None, help="where to keep downloaded PDFs (default: temp dir)")
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory()
    dl = Path(args.download_dir) if args.download_dir else Path(tmp.name)
    dl.mkdir(parents=True, exist_ok=True)

    sources = []
    for src in args.inputs:
        if re.match(r"https?://", src) and not src.lower().split("?")[0].endswith(".pdf"):
            found = scrape_pdf_links(src)
            print(f"{src}: {len(found)} timetable PDFs found", file=sys.stderr)
            sources += [(u, True) for u in found]
        else:
            sources.append((src, False))

    rows = []
    for src, scraped in sources:
        path = fetch(src, dl)
        if scraped and is_ferry(path):
            print(f"{path.name}: ferry sheet, skipped", file=sys.stderr)
            continue
        print(path.name, file=sys.stderr)
        got = parse_pdf(path, src)
        print(f"  -> {len(got)} rows", file=sys.stderr)
        rows += got
    restore_accents(rows)

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS, restval="text")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {len(rows)} rows -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
