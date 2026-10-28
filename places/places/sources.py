"""Fetch a source page and cache its visible text (PlacesLayer.md 4.3 step 2.2)."""
from __future__ import annotations

import datetime as dt
import html
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) YourDayTrip-places/0.1 (research prototype)"
SKIP = {"script", "style", "noscript", "svg", "head"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "td"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def visible_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.out))
    lines = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def fetch_source(cache_dir, place_id: str, url: str, timeout=25) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en,pt;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    text = visible_text(raw) if "<html" in raw[:2000].lower() or "<body" in raw.lower() else raw
    d = Path(cache_dir) / "sources"
    d.mkdir(parents=True, exist_ok=True)
    n = 1
    while (d / f"{place_id}-{n}.txt").exists():
        first = (d / f"{place_id}-{n}.txt").read_text(encoding="utf-8").split("\n", 1)[0]
        if first.startswith(url + " "):
            break
        n += 1
    path = d / f"{place_id}-{n}.txt"
    path.write_text(f"{url} fetched {dt.date.today().isoformat()}\n{text}\n", encoding="utf-8")
    return path
