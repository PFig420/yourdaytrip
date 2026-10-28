"""Read the timetable CSV, group trips, apply time_fixes and exclusions (spec 5.1)."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


class BuildError(Exception):
    """A rule marked 'fail' in the spec was violated."""


@dataclass
class StopTime:
    seq: int
    raw: str
    name: str
    t: int  # seconds after midnight; may exceed 86400 after rollover


@dataclass
class Trip:
    source: str
    trip: str
    mode: str
    page: str
    line: str
    route: str
    direction: str
    day_type: str
    season: str
    notes: tuple
    valid_from: str
    stops: list = field(default_factory=list)
    direction_id: int = 0

    @property
    def key(self):
        return (self.source, self.trip)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hhmm(s: str) -> int:
    h, m = s.strip().split(":")[:2]
    return int(h) * 3600 + int(m) * 60


def read_csv_config(path: Path) -> list[dict]:
    """Rows of a config CSV; lines starting with # are comments."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#") and ln.strip()]
    return list(csv.DictReader(lines))


def load_trips(csv_path: Path, config_dir: Path):
    """Return (trips, report_info). Trips are in CSV order of first appearance."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    info = {"rows": len(rows), "fixes": [], "exclusions": []}

    fixes = {}
    for r in read_csv_config(config_dir / "time_fixes.csv"):
        fixes[(r["source"], r["trip"], int(r["stop_sequence"]))] = r
    used = set()

    grouped: dict[tuple, Trip] = {}
    for r in rows:
        k = (r["source"], r["trip"])
        tr = grouped.get(k)
        if tr is None:
            tr = grouped[k] = Trip(r["source"], r["trip"], r["mode"], r["page"], r["line"],
                                   r["route"], r["direction"], r["day_type"], r["season"],
                                   (), r["valid_from"])
        if r["note"] and r["note"] not in tr.notes:
            tr.notes = tr.notes + (r["note"],)
        seq = int(r["stop_sequence"])
        time_s = r["time"]
        fx = fixes.get((r["source"], r["trip"], seq))
        if fx:
            if time_s != fx["old_time"]:
                raise BuildError(
                    f"time_fixes.csv: {r['source']} trip {r['trip']} seq {seq} has time {time_s!r}, "
                    f"fix expects {fx['old_time']!r} (the CSV changed under the fix)")
            info["fixes"].append((r["trip"], seq, r["stop_name"], time_s, fx["new_time"], fx.get("evidence", "")))
            time_s = fx["new_time"]
            used.add((r["source"], r["trip"], seq))
        tr.stops.append(StopTime(seq, r["stop_name"], r["stop_name"], hhmm(time_s)))
    for k in fixes:
        if k not in used:
            raise BuildError(f"time_fixes.csv: no CSV row for source={k[0]} trip={k[1]} stop_sequence={k[2]}")

    # Stop times printed in the PDF but missing from the CSV (config/extra_stop_times.csv)
    info["extra_stop_times"] = []
    for r in read_csv_config(config_dir / "extra_stop_times.csv"):
        k = (r["source"], r["trip"])
        tr = grouped.get(k)
        if tr is None:
            raise BuildError(f"extra_stop_times.csv: no such trip {k}")
        seq = int(r["stop_sequence"])
        if any(s.seq == seq for s in tr.stops):
            raise BuildError(f"extra_stop_times.csv: {k[0]} trip {k[1]} already has stop_sequence {seq} "
                             f"(the CSV was fixed or changed; remove this row)")
        tr.stops.append(StopTime(seq, r["stop_name"], r["stop_name"], hhmm(r["time"])))
        info["extra_stop_times"].append((r["trip"], seq, r["stop_name"], r["time"], r.get("evidence", "")))

    for tr in grouped.values():
        tr.stops.sort(key=lambda s: s.seq)

    excl = {(r["source"], r["trip"]): r.get("reason", "") for r in read_csv_config(config_dir / "exclusions.csv")}
    for k, reason in excl.items():
        if k in grouped:
            del grouped[k]
            info["exclusions"].append((k[1], reason))
        else:
            raise BuildError(f"exclusions.csv: no such trip {k}")
    return list(grouped.values()), info
