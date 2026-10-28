"""Walking-time overrides from real OpenStreetMap paths (WalkBarriers.md).

`data/paths/walk_overrides.csv` is written by `python -m mapdata.overrides` from the path cache. A row says how long the real path
between two ids is (kind `osm`), that OSM is wrong there and the engine's estimate stands (`osm-gap`), or that no reasonable walk
exists (`blocked`). A row applies in both directions. The engine takes the longer of its own estimate and the OSM path, so an
override can only make a walk longer, never shorter. No file means today's behaviour.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

STALE = "walking overrides were built for other feeds; rebuild with python -m mapdata.build"


class Overrides:
    def __init__(self, rows: dict | None = None, state: str = "off", meta: dict | None = None):
        self.rows = rows or {}            # (id_a, id_b) sorted -> (kind, metres or None)
        self.state = state                # ok | off | missing | stale
        self.meta = meta or {}

    def get(self, a: str, b: str):
        if not self.rows:
            return None
        return self.rows.get((a, b) if a <= b else (b, a))

    def health(self) -> dict:
        return {"state": self.state, "rows": len(self.rows), "built": self.meta.get("built", ""),
                "hash": self.meta.get("hash", "")}

    @classmethod
    def load(cls, path, feeds) -> tuple["Overrides", str | None]:
        """-> (Overrides, warning or None). `path` None: switched off. A file built for other feed hashes is ignored whole."""
        if not path:
            return cls(), None
        p = Path(path)
        if not p.exists():
            return cls(state="missing"), None
        raw = p.read_bytes()
        meta, lines = {"hash": hashlib.sha256(raw).hexdigest()[:16]}, []
        for ln in raw.decode("utf-8").splitlines():
            if ln.startswith("#"):
                k, _, v = ln[1:].partition(":")
                meta[k.strip()] = v.strip()
            elif ln.strip():
                lines.append(ln)
        built = dict(x.split("=", 1) for x in meta.get("feeds", "").split() if "=" in x)
        have = {f.id: f.sha256 for f in feeds}
        if not built or any(built.get(k) != v for k, v in have.items()):
            return cls(state="stale", meta=meta), STALE
        rows = {}
        for r in csv.DictReader(lines):
            kind = r["kind"].strip()
            if kind not in ("osm", "osm-gap", "blocked"):
                continue
            a, b = r["from_id"].strip(), r["to_id"].strip()
            try:
                metres = float(r["metres"]) if kind == "osm" else None
            except ValueError:
                continue
            rows[(a, b) if a <= b else (b, a)] = (kind, metres)
        return cls(rows, "ok", meta), None
