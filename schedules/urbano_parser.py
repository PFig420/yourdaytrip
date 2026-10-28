"""Parser for the AveiroBus urban-circuit (Linha 1..12, Azul, Verde) timetable sheets.

Each sheet is one square page: a title band, then one or more tables, each
topped by a coloured day-type band ("DIAS ÚTEIS", "FIM-DE-SEMANA E FERIADOS" ...),
a stop-name column on the left and one time column per trip.

Some sheets carry a text layer, most are flat images.  Both are reduced to the
same thing - word boxes in pixel coordinates of a 250 dpi rendering - and then
parsed identically.  Day-type band extents are read from the pixels, since the
band is the only thing that tells side-by-side tables apart.  Cells that the
OCR missed are re-OCR'd individually from an upscaled crop.
"""
import hashlib
import json
import re
import statistics
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

DPI = 250
PX = DPI / 72.0
TIME_RE = re.compile(r"^(\d{1,2})[:.;](\d{2})$")
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]

_ocr_engine = None
CACHE_DIR = Path(__file__).resolve().parent / ".ocr_cache"


def _engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------ word boxes ---

def _word(text, x0, top, x1, bottom):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


_ROT180 = str.maketrans("0125689:", "0125986:")


def rot180(t):
    """The recogniser sometimes reads a time as if the cell were turned upside down:
    '09:00' comes back as '00:60'.  Reversing the string and swapping 6<->9 undoes it."""
    return t[::-1].translate(_ROT180)


def _time_ok(t):
    m = re.fullmatch(r"(\d{1,2})[:.;](\d{2})", t)
    return bool(m) and int(m.group(1)) <= 24 and int(m.group(2)) <= 59


def _fix_digits(t):
    """OCR often reads 0 as O and 1 as l/I inside times; also undo 180-degree misreads."""
    if re.fullmatch(r"[\dOolI]{1,2}[:.;][\dOolI]{2}", t):
        t = t.translate(str.maketrans("OolI", "0011"))
        if not _time_ok(t) and _time_ok(rot180(t)):
            t = rot180(t)
    return t


def split_merged_times(w):
    """'08:1009:30' or '08:10 09:30' in one box -> several boxes."""
    t = w["text"].replace(" ", "")
    found = re.findall(r"\d{1,2}[:.;]\d{2}", t)
    if len(found) >= 2 and "".join(found) == t:
        step = (w["x1"] - w["x0"]) / len(t)
        out, pos = [], 0
        for f in found:
            out.append(_word(f, w["x0"] + pos * step, w["top"], w["x0"] + (pos + len(f)) * step, w["bottom"]))
            pos += len(f)
        return out
    return [w]


def ocr_words(img, x_off=0, y_off=0, scale=1.0, cache=False):
    """OCR an image into word boxes.  `cache` keeps full-page passes on disk (reruns are slow)."""
    cpath = None
    if cache:
        CACHE_DIR.mkdir(exist_ok=True)
        cpath = CACHE_DIR / (hashlib.sha1(img.tobytes()).hexdigest() + ".json")
        if cpath.exists():
            return json.loads(cpath.read_text())
    res, _ = _engine()(np.array(img.convert("RGB")), use_cls=False)  # cls flips some digit strings 180 degrees (09:00 -> 00:60)
    out = []
    for box, txt, _conf in res or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        w = _word(_fix_digits(txt.strip()), x_off + min(xs) / scale, y_off + min(ys) / scale,
                  x_off + max(xs) / scale, y_off + max(ys) / scale)
        out.extend(split_merged_times(w))
    if cpath:
        cpath.write_text(json.dumps(out))
    return out


def page_words(pg):
    """Return (words, image, source, ocr) with words in 250-dpi pixel coordinates.

    `ocr` is a full-page OCR pass.  It is always run: the title and line badge
    are vector outlines even on pages that have a text layer for the table.
    """
    img = pg.to_image(resolution=DPI).original.convert("RGB")
    ocr = ocr_words(img, cache=True)
    if len(pg.chars) > 300:
        words = []
        for w in pg.extract_words(x_tolerance=1.2, y_tolerance=2):
            t = w["text"].replace("▼", "").strip()
            if t:
                words.append(_word(t, w["x0"] * PX, w["top"] * PX, w["x1"] * PX, w["bottom"] * PX))
        return words, img, "text", ocr
    return ocr, img, "ocr", ocr


def ocr_crop(img, x0, y0, x1, y1, zoom=3, gray=False):
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(img.width, int(x1)), min(img.height, int(y1))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return []
    crop = img.crop((x0, y0, x1, y1))
    if gray:  # coloured highlight cells: flatten to high-contrast greyscale
        crop = ImageOps.autocontrast(crop.convert('L'), cutoff=2).convert('RGB')
    crop = crop.resize(((x1 - x0) * zoom, (y1 - y0) * zoom), Image.LANCZOS)
    pad = Image.new("RGB", (crop.width + 80, crop.height + 80), "white")
    pad.paste(crop, (40, 40))
    return ocr_words(pad, x_off=x0 - 40 / zoom, y_off=y0 - 40 / zoom, scale=zoom)


# -------------------------------------------------------------- helpers ---

def yc(w):
    return (w["top"] + w["bottom"]) / 2


def xc(w):
    return (w["x0"] + w["x1"]) / 2


def valid_hhmm(t):
    m = TIME_RE.match(t)
    return bool(m) and int(m.group(1)) <= 24 and int(m.group(2)) <= 59


def is_time(w):
    return valid_hhmm(w["text"])


def minutes(t):
    m = TIME_RE.match(t)
    return int(m.group(1)) * 60 + int(m.group(2))


def in_order(prev, cur):
    """Times down a trip never go back, except across midnight."""
    if prev is None or cur is None:
        return True
    return cur >= prev or (prev >= 20 * 60 and cur <= 4 * 60)


def norm_time(t):
    m = TIME_RE.match(t)
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def cluster_1d(values, gap):
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [statistics.mean(g) for g in groups]


def lines_of(words, tol):
    lines = []
    for w in sorted(words, key=yc):
        if lines and abs(lines[-1]["y"] - yc(w)) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"y": yc(w), "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
        ln["y"] = statistics.mean(yc(w) for w in ln["words"])
    return lines


def join(words):
    return " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"]))


# ----------------------------------------------------------------- bands ---

HEADER_PREFIXES = ("DIAS", "FIM", "DOMINGO", "SABADO", "TODOS")


def is_header_seed(w):
    t = strip_accents(w["text"]).strip("*")
    letters = re.sub(r"[^A-Za-z]", "", t)
    return len(letters) >= 3 and t.upper() == t and t.startswith(HEADER_PREFIXES)


def find_bands(words, arr):
    """Day-type bands: seed on header keywords, grow the segment over non-white pixels."""
    bands = {}
    for w in words:
        if not is_header_seed(w):
            continue
        h = w["bottom"] - w["top"]
        y0, y1 = int(w["top"]), int(w["bottom"])
        region = arr[y0:y1, :, :].min(axis=2) < 225
        mask = region.mean(axis=0) > 0.3
        x = int(xc(w))
        if not mask[x]:
            # seed sits on a white glyph stroke: nudge to the nearest set pixel
            near = np.flatnonzero(mask[max(0, x - 15):x + 15])
            if not len(near):
                continue
            x = max(0, x - 15) + int(near[len(near) // 2])
        lo = hi = x
        gap = 0
        i = x
        while i > 0 and gap < 10:
            i -= 1
            if mask[i]:
                lo, gap = i, 0
            else:
                gap += 1
        gap, i = 0, x
        while i < len(mask) - 1 and gap < 10:
            i += 1
            if mask[i]:
                hi, gap = i, 0
            else:
                gap += 1
        key = (round(lo / 8), round(hi / 8), round(w["top"] / 12))
        b = bands.setdefault(key, {"x0": lo, "x1": hi, "top": w["top"], "bottom": w["bottom"], "words": []})
        b["words"].append(w)
        b["top"], b["bottom"] = min(b["top"], w["top"]), max(b["bottom"], w["bottom"])
    out = []
    for b in bands.values():
        h = b["bottom"] - b["top"]
        inside = [w for w in words if b["x0"] <= xc(w) <= b["x1"] and b["top"] - 0.4 * h <= w["top"] and w["bottom"] <= b["bottom"] + 0.4 * h]
        label = re.sub(r"\s+", " ", join(inside)).strip()
        b["label"] = label
        out.append(b)
    return sorted(out, key=lambda b: (round(b["top"] / 20), b["x0"]))


# ------------------------------------------------------------ highlights ---

def cell_background(img_arr, x0, y0, x1, y1):
    crop = img_arr[int(y0):int(y1), int(x0):int(x1)].reshape(-1, 3)
    if not len(crop):
        return (255, 255, 255)
    return tuple(int(v) for v in np.median(crop, axis=0))


def saturation(rgb):
    mx, mn = max(rgb), min(rgb)
    return 0 if mx == 0 else (mx - mn) / mx


def find_legends(words, arr, table_bottom, page_h):
    """Footnotes under the tables: a small coloured swatch followed by text."""
    cand = [w for w in words if table_bottom < w["top"] < table_bottom + 0.06 * page_h
            and not is_time(w) and w["x0"] < 0.7 * arr.shape[1]]
    legends = []
    for ln in lines_of(cand, tol=10):
        first = ln["words"][0]
        text = join(ln["words"]).strip()
        if len(text) < 12:
            continue
        sw = arr[int(first["top"]):int(first["bottom"]), max(0, int(first["x0"]) - 28):max(1, int(first["x0"]) - 4)]
        colour = tuple(int(v) for v in np.median(sw.reshape(-1, 3), axis=0)) if sw.size else (255, 255, 255)
        legends.append({"text": re.sub(r"^[\W_]+", "", text), "colour": colour, "y": ln["y"]})
    return legends


# ----------------------------------------------------------------- parse ---

def canon_day_type(label):
    """OCR and text-layer spellings of the same band label -> one canonical label."""
    t = re.sub(r"[^A-Z0-9.ª]", "", strip_accents(label).upper())
    extra = " (exceto agosto)" if "EXCETOAGOSTO" in t else ""
    if t.startswith("DIAS"):
        return "Dias Úteis" + extra
    if t.startswith("FIM"):
        return "Fim-de-semana e Feriados" + extra
    if t.startswith("DOMINGOS"):
        return "Domingos ou 2.ª feira se feriado"
    return re.sub(r"\s+", " ", label).strip().capitalize()


def parse_valid_from(text):
    m = re.search(r"[Ee]m vigor a partir de (\d{1,2}) de (\w+) de (\d{4})", text)
    months = [strip_accents(x) for x in MONTHS]
    if m and strip_accents(m.group(2).lower()) in months:
        return f"{m.group(3)}-{months.index(strip_accents(m.group(2).lower())) + 1:02d}-{int(m.group(1)):02d}"
    return ""


def read_valid_from(img):
    """The edition note is printed rotated along the right edge."""
    ws = ocr_words(img.rotate(-90, expand=True), cache=True)
    return parse_valid_from(" ".join(w["text"] for w in ws))


def line_from_source(src):
    """Line id from the sheet's file name: ...-l7-202506.pdf -> '7', ..._l_azul_... -> 'Azul'."""
    m = re.search(r"[-_]l(\d+)[-_]\d{6}", src)
    if m:
        return str(int(m.group(1)))
    m = re.search(r"[-_]l_(\w+?)_\d{6}", src)
    return m.group(1).capitalize() if m else ""


def parse_page(pg, source_label):
    words, img, extraction, ocr_all = page_words(pg)
    arr = np.array(img)
    H, W = arr.shape[:2]

    # ---- title and line number
    title_words = [w for w in ocr_all if 0.105 * H < yc(w) < 0.145 * H and w["x0"] < 0.9 * W and not is_header_seed(w)]
    title = re.sub(r"\s+", " ", join(title_words)).strip()
    if not title:
        # light text on a saturated band is sometimes missed by the full-page pass
        band = img.crop((int(0.08 * W), int(0.105 * H), int(0.75 * W), int(0.145 * H)))
        band = ImageOps.invert(ImageOps.autocontrast(band.convert("L"))).convert("RGB")
        title = re.sub(r"\s+", " ", join(ocr_words(band.resize((band.width * 2, band.height * 2), Image.LANCZOS)))).strip()
    title = re.sub(r"CENTRO(?=[A-Z])", "CENTRO ", title)
    line_no = line_from_source(source_label)
    if not line_no:
        num = [w for w in ocr_all if yc(w) < 0.1 * H and xc(w) > 0.8 * W and re.fullmatch(r"\d{1,2}", w["text"])]
        line_no = str(int(max(num, key=lambda w: w["x1"] - w["x0"])["text"])) if num else ""
    valid_from = read_valid_from(img)

    bands = find_bands(words, arr)
    time_words = [w for w in words if is_time(w)]
    rows_out, report = [], {"missing_filled": 0, "missing_blank": 0}

    blocks = []
    for bi, b in enumerate(bands):
        peers = [p for p in bands if p is not b and p["top"] > b["top"] + 20
                 and min(p["x1"], b["x1"]) - max(p["x0"], b["x0"]) > 0.3 * (b["x1"] - b["x0"])]
        limit = min((p["top"] for p in peers), default=H)
        toks = [w for w in time_words if b["x0"] - 6 <= xc(w) <= b["x1"] + 6 and b["bottom"] < w["top"] < limit]
        if not toks:
            continue
        pitch = pitch_of(toks)
        # where does the table end?  Walk down the union of time rows and name rows
        # (OCR can miss whole runs of times, but the name column stays contiguous).
        left_edge = min(p["x0"] for p in bands if abs(p["top"] - b["top"]) < 25)
        name_ys = [yc(w) for w in words if w["x1"] <= left_edge + 4 and w["x0"] >= left_edge - 0.4 * W
                   and b["bottom"] < w["top"] < limit and not is_time(w)
                   and (len(w["text"]) <= 2 or (w["bottom"] - w["top"]) < 1.5 * (w["x1"] - w["x0"]))]
        ys = sorted(set([yc(w) for w in toks] + name_ys))
        cut = limit
        for a_, b2 in zip(ys, ys[1:]):
            if b2 - a_ > 2.2 * pitch:
                cut = (a_ + b2) / 2
                break
        toks = [w for w in toks if yc(w) < cut]
        if not toks:
            continue
        blocks.append((b, toks, cut))

    if not blocks:
        return [], report, title

    table_bottom = max(max(w["bottom"] for w in toks) for _b, toks, _c in blocks)
    legends = find_legends(words, arr, table_bottom, H)

    # left edge of name column per band-row (same-y bands share it)
    for bi, (b, toks, cut) in enumerate(blocks):
        same_row = [p for p, _t, _c in blocks if abs(p["top"] - b["top"]) < 25]
        left = min(p["x0"] for p in same_row)

        widths = [w["x1"] - w["x0"] for w in toks]
        colx = cluster_1d([xc(w) for w in toks], gap=0.45 * statistics.median(widths))
        row_y = cluster_1d([yc(w) for w in toks], gap=0.45 * pitch_of(toks))
        names_all = [w for w in words if w["x1"] <= left + 4 and w["x0"] >= left - 0.4 * W
                     and b["bottom"] < w["top"] < cut and not is_time(w)
                     and w["text"] not in ("▼", "Z", "z") and not re.fullmatch(r"Z\d|\dZ", w["text"])
                     and (len(w["text"]) <= 2 or (w["bottom"] - w["top"]) < 1.5 * (w["x1"] - w["x0"]))]
        # a table to the right of the name column can lose rows that only exist there: add name rows
        for ln in lines_of(names_all, tol=0.35 * pitch_of(toks)):
            if all(abs(ln["y"] - ry) > 0.45 * pitch_of(toks) for ry in row_y):
                row_y.append(ln["y"])
        row_y.sort()
        # rows in the name column only come from the leftmost table of this band-row
        row_pitch = pitch_of(toks)
        row_h = statistics.median([w["bottom"] - w["top"] for w in toks])
        cell_w = statistics.median(widths)
        col_pitch = statistics.median([b2 - a for a, b2 in zip(colx, colx[1:])] or [row_pitch * 2])

        # names per row
        names = {}
        for ln in lines_of(names_all, tol=0.35 * row_pitch):
            i = min(range(len(row_y)), key=lambda k: abs(row_y[k] - ln["y"]))
            if abs(row_y[i] - ln["y"]) < 0.6 * row_pitch:
                names.setdefault(i, []).append(join(ln["words"]))

        # grid of times
        grid = {}
        for w in toks:
            ri = min(range(len(row_y)), key=lambda k: abs(row_y[k] - yc(w)))
            ci = min(range(len(colx)), key=lambda k: abs(colx[k] - xc(w)))
            grid.setdefault((ri, ci), w)

        # --- repair (OCR pages only)
        uncertain = set()
        if extraction == "ocr":
            def cell_box(ri, ci, fw, fh):
                return (colx[ci] - cell_w * fw, row_y[ri] - row_h * fh, colx[ci] + cell_w * fw, row_y[ri] + row_h * fh)

            def read_cell(ri, ci, accept):
                """Re-OCR one cell with several crop sizes / zooms / greyscale; first accepted read wins."""
                for gray in (False, True):
                    for fw, fh, zoom in ((0.58, 0.8, 3), (0.7, 0.95, 4), (0.5, 0.7, 5), (0.62, 1.1, 3)):
                        for w in ocr_crop(img, *cell_box(ri, ci, fw, fh), zoom=zoom, gray=gray):
                            for txt in (w["text"], rot180(w["text"])):
                                if valid_hhmm(txt) and accept(minutes(txt)):
                                    return dict(w, text=txt)
                return None

            def ink(ri, ci):
                x0, y0, x1, y1 = (int(v) for v in cell_box(ri, ci, 0.58, 0.8))
                g = np.array(img.crop((x0, y0, x1, y1)).convert("L")).astype(int)
                return int((g < np.median(g) - 60).sum()) if g.size else 0

            ink_ref = statistics.median([ink(ri, ci) for (ri, ci) in list(grid)[:60]] or [1])
            # 1. cells the detector missed.  Blank-looking cells (a dash) carry far less ink than a time.
            for ri in range(len(row_y)):
                for ci in range(len(colx)):
                    if (ri, ci) in grid:
                        continue
                    if ink(ri, ci) < 0.3 * ink_ref:
                        report["missing_blank"] += 1
                        continue
                    w = read_cell(ri, ci, lambda m: True)
                    if w:
                        grid[(ri, ci)] = w
                        report["missing_filled"] += 1
                    else:
                        uncertain.add((ri, ci))
                        report["missing_blank"] += 1
            # 2. misreads that break the time order down a trip
            report["reread"] = 0
            for ci in range(len(colx)):
                rs = [ri for ri in range(len(row_y)) if (ri, ci) in grid]
                val = lambda ri: minutes(grid[(ri, ci)]["text"])
                for k, ri in enumerate(rs):
                    prev = val(rs[k - 1]) if k else None
                    nxt = val(rs[k + 1]) if k + 1 < len(rs) else None
                    if in_order(prev, val(ri)) and in_order(val(ri), nxt):
                        continue
                    w = read_cell(ri, ci, lambda m: in_order(prev, m) and in_order(m, nxt))
                    if w:
                        grid[(ri, ci)] = w
                        report["reread"] += 1
                    elif prev is not None and not in_order(prev, val(ri)):
                        uncertain.add((ri, ci))

        # highlight colour -> footnote
        def note_for(ri, ci):
            if (ri, ci) in uncertain:
                return "OCR uncertain - check against PDF"
            w = grid[(ri, ci)]
            bg = cell_background(arr, w["x0"] - 14, w["top"] - 2, w["x0"] - 2, w["bottom"] + 2)
            if saturation(bg) < 0.18 or not legends:
                return ""
            best = min(legends, key=lambda L: sum((a - c) ** 2 for a, c in zip(bg, L["colour"])))
            return best["text"]

        first_name = names.get(0, [""])[0]
        last_name = names.get(len(row_y) - 1, [""])[-1]
        strip_ab = lambda s: re.sub(r"\s+[AB]$", "", s.replace("▼", "").strip())
        direction = "Circular" if strip_ab(first_name) == strip_ab(last_name) else f"{strip_ab(first_name)} -> {strip_ab(last_name)}"

        for ci in range(len(colx)):
            seq = 0
            for ri in range(len(row_y)):
                if (ri, ci) not in grid:
                    continue
                seq += 1
                rows_out.append({
                    "source": source_label, "mode": "bus_urban", "page": 1, "line": line_no,
                    "route": title, "direction": direction,
                    "day_type": canon_day_type(b["label"]),
                    "season": "", "trip": f"u{line_no}b{bi + 1}c{ci + 1}",
                    "stop_sequence": ri + 1, "stop_name": " ".join(names.get(ri, [])).replace("▼", "").strip(),
                    "time": norm_time(grid[(ri, ci)]["text"]), "note": note_for(ri, ci),
                    "valid_from": valid_from, "extraction": extraction,
                })
    return rows_out, report, title


def pitch_of(toks):
    ys = sorted(set(round(yc(w)) for w in toks))
    diffs = [b - a for a, b in zip(ys, ys[1:]) if b - a > 8]
    return statistics.median(diffs) if diffs else 40
