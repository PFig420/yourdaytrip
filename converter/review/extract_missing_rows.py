"""One-off tool (needs pdfplumber; NOT part of gtfsconv): regenerate config/extra_stop_times.csv.

For bus.pdf pages 6, 7, 20 and 21 the CSV lost or fused the last row(s) of some tables.
This reads the missing times from the PDF (text rows, or per-column character positions where the
PDF prints a row as single characters) and checks every row against schedules.csv before writing.
Usage: python3 review/extract_missing_rows.py   (from converter/)
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
SRC = "pdfs/bus/bus.pdf"
pdf = pdfplumber.open(ROOT / "schedules" / SRC)
rows = list(csv.DictReader(open(ROOT / "schedules" / "schedules.csv", encoding="utf-8-sig")))
tr = defaultdict(dict)
for x in rows:
    if x["source"] == SRC:
        tr[x["trip"]][int(x["stop_sequence"])] = (x["stop_name"], x["time"], x["day_type"])
out = []
TIME = re.compile(r"\d\d:\d\d")


def section(page, start, end=None):
    lines = pdf.pages[page - 1].extract_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(start))
    j = next((k for k, l in enumerate(lines) if k > i and end and l.startswith(end)), len(lines))
    return lines[i:j]


def textrow(lines, label, n):
    l = next(l for l in lines if l.startswith(label))
    return l[len(label):].split()[-n:]


def char_row(page, label_top, anchor_row_top, n_cols):
    """Row printed as single characters: group by the x of the anchor row's columns."""
    p = pdf.pages[page - 1]
    ws = p.extract_words()
    cols = sorted([w for w in ws if abs(w["top"] - anchor_row_top) < 1.5 and re.fullmatch(r"\d\d:\d\d|-", w["text"]) and w["x0"] > 200],
                  key=lambda w: w["x0"])
    cols = cols[-n_cols:]
    chars = [c for c in p.chars if abs(c["top"] - label_top) < 1.6 and c["x0"] >= cols[0]["x0"] - 15]
    centres = [(w["x0"] + w["x1"]) / 2 for w in cols]
    buckets = [[] for _ in cols]
    for c in chars:
        mid = (c["x0"] + c["x1"]) / 2
        k = min(range(len(cols)), key=lambda i: abs(centres[i] - mid))
        if abs(centres[k] - mid) < 40:
            buckets[k].append(c)
    res = []
    for cs in buckets:
        t = "".join(c["text"] for c in sorted(cs, key=lambda c: c["x0"])).strip().split()
        res.append(t[0] if t else "-")
    return res


def add(trip, seq, name, time, why):
    assert seq not in tr[trip], (trip, seq, tr[trip][seq])
    out.append((trip, seq, name, time, why))


COSTA = "Largo Arrais Ançã - Costa Nova"
TERM = "Terminal Rodoviário de Aveiro"

# ---- page 20: Saturday (fused row carries the Farol time) and Sunday (row lost to character fragments)
sat = section(20, "Sábados", "Domingo")
far, cn = textrow(sat, "Farol - Forte da Barra", 17), textrow(sat, "Largo Arrais Ançã - Costa Nova", 17)
for n, (f, c) in enumerate(zip(far, cn), 1):
    if f == "-":
        continue
    t = tr[f"p20t2c{n}"]
    assert "Largo" in t[9][0] and t[9][1] == f
    add(f"p20t2c{n}", 10, COSTA, c, f"bus.pdf p.20, Sábados, column {n}: Farol {f}, Costa Nova {c} (CSV had one fused row carrying the Farol time)")
sf = char_row(20, 741.95, 741.95, 15)
sc = char_row(20, 759.2, 741.95, 15)
for n, (f, c) in enumerate(zip(sf, sc), 1):
    if f == "-":
        continue
    t = tr[f"p20t3c{n}"]
    assert t[9][1] == f
    if 10 in t:
        assert t[10][1] == c
        continue
    add(f"p20t3c{n}", 10, COSTA, c, f"bus.pdf p.20, Domingo e Feriados, column {n}: row lost by text extraction; read from character positions under the Farol {f} column")

# ---- page 21: Saturday / Sunday outbound-to-Aveiro trips lack the Terminal row
sat = section(21, "Sábados", "Domingo")
first, term = textrow(sat, "Largo Arrais Ançã - Costa Nova", 16), textrow(sat, "Terminal Rodoviário de Aveiro", 16)
for n, (f, c) in enumerate(zip(first, term), 1):
    if c == "-":
        continue
    t = tr[f"p21t2c{n}"]
    assert t and 10 not in t, (n, t)
    assert f == "-" or t[1][1] == f, (n, t.get(1), f)
    add(f"p21t2c{n}", 10, TERM, c, f"bus.pdf p.21, Sábados, column {n}: Terminal row printed {c}; missing from the CSV")
p21 = pdf.pages[20]
top = max(w["top"] for w in p21.extract_words() if w["text"] == "Largo" and w["top"] > 500 and w["top"] < 640)
first = char_row(21, top, top, 14)
term = char_row(21, top + 156.3, top, 14)
for n, (f, c) in enumerate(zip(first, term), 1):
    if c == "-" or not TIME.fullmatch(c):
        continue
    t = tr[f"p21t3c{n}"]
    assert t and (f == "-" or t[1][1] == f), (n, t.get(1), f)
    if 10 in t:
        assert t[10][1] == c
        continue
    add(f"p21t3c{n}", 10, TERM, c, f"bus.pdf p.21, Domingo e Feriados, column {n}: row lost by text extraction; read from character positions")

# ---- page 7: fused "Hospital / Universidade B (Aveiro) Terminal ..." carries the Hospital time
d = section(7, "Dias Úteis", "Sábados")
term = textrow(d, "Terminal Rodoviário de Aveiro", 20)
hosp = textrow(d, "Hospital / Universidade B (Aveiro)", 20)
for n, (h, c) in enumerate(zip(hosp, term), 1):
    if c == "-":
        continue
    t = tr[f"p7t1c{n}"]
    assert "Terminal" in t[9][0] and t[9][1] == h, (n, t[9], h)
    add(f"p7t1c{n}", 10, TERM, c, f"bus.pdf p.7, Dias Úteis, column {n}: Hospital / Universidade B {h}, Terminal {c} (CSV had one fused row carrying the Hospital time)")

# ---- page 6: fused "Hospital Rovisco Pais Figueira da Foz (Estação CP)" carries the Rovisco Pais time
d = section(6, "Dias Úteis", "Sábados")
rov = textrow(d, "Hospital Rovisco Pais", 23)
fig = textrow(d, "Figueira da Foz (Estação CP)", 23)
for n, (r, c) in enumerate(zip(rov, fig), 1):
    if c == "-":
        continue
    if n == 19:  # decision (Pedro, 2026-10-19, Issues.md 4): p6t1c19 ends at Hospital Rovisco Pais 19:26 as printed
        continue
    t = tr[f"p6t1c{n}"]
    assert "Figueira" in t[10][0] and t[10][1] == r, (n, t[10], r)
    add(f"p6t1c{n}", 11, "Figueira da Foz (Estação CP)", c, f"bus.pdf p.6, Dias Úteis, column {n}: Hospital Rovisco Pais {r}, Figueira {c} (CSV had one fused row carrying the Rovisco Pais time)")

target = ROOT / "converter" / "config" / "extra_stop_times.csv"
with open(target, "w", encoding="utf-8", newline="") as fh:
    fh.write("# Stop times that ARE printed in bus.pdf but missing from schedules.csv (Issues.md 12 and 5). Inserted at the given stop_sequence.\n")
    fh.write("# Generated by review/extract_missing_rows.py, which checks each row against the CSV. The build fails if the CSV already has that stop_sequence.\n")
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["source", "trip", "stop_sequence", "stop_name", "time", "evidence"])
    for o in out:
        w.writerow([SRC, *o])
print(len(out), "rows", {p: sum(1 for o in out if o[0].startswith(p)) for p in ("p6", "p7", "p20t2", "p20t3", "p21t2", "p21t3")})
