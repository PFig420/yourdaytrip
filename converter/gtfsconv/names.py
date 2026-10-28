"""Stop-name clean-up, aliases and canonical names (spec 5.2)."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


def fold(s: str) -> str:
    """Accent-fold and lower-case."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def key(s: str) -> str:
    """Normalised comparison key: accent-folded, lower-case, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", fold(s))


_ABBREV = {"av": "avenida", "r": "rua", "sra": "senhora", "sr": "senhor", "esc": "escola", "dr": "doutor",
           "dra": "doutora", "pc": "praca", "pca": "praca", "est": "estacao", "eng": "engenheiro"}


def ekey(s: str) -> str:
    """Like key(), after expanding common abbreviations (Av. -> Avenida, R. -> Rua, ...)."""
    words = re.findall(r"[a-z0-9]+", fold(s))
    return "".join(_ABBREV.get(w, w) for w in words)


def strip_paren(s: str) -> str:
    """Drop a trailing parenthetical qualifier such as '(Aveiro)' (not '(2)')."""
    return re.sub(r"\s*\((?!\d+\))[^)]*\)\s*$", "", s).strip()


_SIDE_RE = re.compile(r"\s*-?\s*\b[AB]$")


def split_side(name: str) -> tuple[str, str]:
    """Return (name without side suffix, side) where side is 'A', 'B' or ''."""
    m = re.search(r"^(.*?)\s*-?\s*\b([AB])$", name)
    if m and m.group(1):
        return m.group(1).rstrip(" -"), m.group(2)
    return name, ""


def strip_side(name: str) -> str:
    return split_side(name)[0]


def clean_raw(name: str) -> str:
    """Raw clean-up: whitespace, stray OCR prefix, run-together abbreviations."""
    s = name.strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^N (?=[A-ZÀ-Ý])", "", s)
    s = re.sub(r"^N(?=[A-ZÀ-Ý][a-zà-ÿ])", "", s)  # "NVerbaB", "NAv.25de..."
    # run-together words: lower->Upper, digit->lower ("25deAbril"), digit->Capitalised
    s = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", s)
    s = re.sub(r"(?<=\d)(?=[a-zà-ÿ]{2})", " ", s)
    s = re.sub(r"(?<=\d)(?=[A-ZÀ-Ý][a-zà-ÿ])", " ", s)
    # abbreviation dots glued to the next word: "Av.Dr." / "Dr.Lourenço" / "Av.25"
    s = re.sub(r"(?<=[A-Za-zÀ-ÿ]\.)(?=[A-ZÀ-Ý][a-zà-ÿ]|\d)", " ", s)
    # hyphen used as separator: "X -Y", "X- Y", "Abril-Escola" -> "X - Y"
    s = re.sub(r"\s+-(?=\S)|(?<=\S)-\s+|(?<=[a-zà-ÿ]{3})-(?=[A-ZÀ-Ý])", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_aliases(path: Path) -> dict[str, str]:
    from .load import read_csv_config
    return {r["raw"].strip(): r["canonical"].strip() for r in read_csv_config(path) if (r.get("raw") or "").strip()}


def canonical(raw: str, aliases: dict[str, str]) -> str:
    cleaned = clean_raw(raw)
    # aliases are matched on the cleaned string, and (for convenience) on the raw one
    return aliases.get(cleaned) or aliases.get(raw.strip()) or cleaned


_OCR_SUBS = [("g", "c"), ("l", "i"), ("0", "o"), ("ti", "u"), ("1", "i")]


def _ocr_equivalent(a: str, b: str) -> bool:
    """True when two keys differ only by OCR-typical substitutions."""
    if a == b:
        return True

    def norm(s: str) -> str:
        s = s.replace("ti", "u")
        return s.translate(str.maketrans({"g": "c", "l": "i", "0": "o", "1": "i"}))

    return norm(a) == norm(b)


def clusters(names_by_line: dict[str, Counter]) -> list[dict]:
    """OCR variant clusters per line (report only; never applied automatically)."""
    out = []
    for line, counter in sorted(names_by_line.items()):
        items = sorted(counter)
        keys = {n: key(n) for n in items}
        parent = {n: n for n in items}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(items):
            for b in items[i + 1:]:
                ka, kb = keys[a], keys[b]
                if ka == kb or abs(len(ka) - len(kb)) > 3:
                    if ka != kb:
                        continue
                if ka != kb:
                    if SequenceMatcher(None, ka, kb).ratio() < 0.92:
                        continue
                    if not _ocr_equivalent(ka, kb) and strip_side(a) != strip_side(b) \
                            and SequenceMatcher(None, ka, kb).ratio() < 0.97:
                        continue
                    # never merge the two sides of a road
                    if split_side(a)[1] != split_side(b)[1]:
                        continue
                parent[find(a)] = find(b)
        groups = defaultdict(list)
        for n in items:
            groups[find(n)].append(n)
        for members in groups.values():
            if len(members) > 1:
                out.append({"line": line, "members": members})
    return out


def find_fused(names: set[str]) -> list[tuple[str, str]]:
    """Names that contain another known stop name as a suffix: (fused, suffix_name)."""
    res = []
    for n in sorted(names):
        for m in names:
            if m != n and len(m) >= 8 and n.endswith(" " + m) and len(n) > len(m) + 1:
                res.append((n, m))
    return res
