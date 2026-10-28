"""Internal checks + MobilityData validator (spec 5.8)."""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .load import BuildError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JAR = REPO_ROOT / "tools" / "gtfs-validator-cli.jar"     # not committed: copy the MobilityData jar there


def internal_checks(tables: dict) -> list[str]:
    errs = []
    col = lambda f, c: [r[tables[f][0].index(c)] for r in tables[f][1]]
    stops, routes, services = set(col("stops.txt", "stop_id")), set(col("routes.txt", "route_id")), set(col("calendar_dates.txt", "service_id"))
    agencies = set(col("agency.txt", "agency_id"))
    trips = {}
    for r in tables["trips.txt"][1]:
        trips[r[2]] = r
        if r[0] not in routes:
            errs.append(f"trip {r[2]}: unknown route {r[0]}")
        if r[1] not in services:
            errs.append(f"trip {r[2]}: service {r[1]} has no dates")
    for r in tables["routes.txt"][1]:
        if r[1] not in agencies:
            errs.append(f"route {r[0]}: unknown agency {r[1]}")
    per: dict[str, list] = {}
    for r in tables["stop_times.txt"][1]:
        if r[3] not in stops:
            errs.append(f"stop_time {r[0]}#{r[4]}: unknown stop {r[3]}")
        per.setdefault(r[0], []).append(r)
    for tid in trips:
        rows = per.get(tid, [])
        if len(rows) < 2:
            errs.append(f"trip {tid}: fewer than 2 stop_times")
        for a, b in zip(rows, rows[1:]):
            if b[1] < a[2] and len(a[2]) == len(b[1]):
                errs.append(f"trip {tid}: times decrease at stop_sequence {b[4]}")
    return errs


def run_validator(zip_path: Path, out_dir: Path, jar: Path | None = None) -> dict:
    jar = jar or DEFAULT_JAR
    if not jar.exists():
        return {"ran": False, "reason": f"validator jar not found at {jar}"}
    if not shutil.which("java"):
        return {"ran": False, "reason": "java not found on PATH"}
    out_dir.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["java", "-jar", str(jar), "-i", str(zip_path), "-o", str(out_dir)],
                       capture_output=True, text=True)
    rpt = out_dir / "report.json"
    if not rpt.exists():
        return {"ran": False, "reason": f"validator produced no report (exit {p.returncode}): {p.stderr[-400:]}"}
    data = json.loads(rpt.read_text(encoding="utf-8"))
    counts = Counter()
    sev = Counter()
    for n in data.get("notices", []):
        counts[(n["severity"], n["code"])] += n.get("totalNotices", 1)
        sev[n["severity"]] += n.get("totalNotices", 1)
    return {"ran": True, "exit": p.returncode, "errors": sev.get("ERROR", 0), "warnings": sev.get("WARNING", 0),
            "infos": sev.get("INFO", 0), "by_code": sorted(((s, c, n) for (s, c), n in counts.items()))}
