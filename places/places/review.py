"""The review sheet for Pedro (PlacesLayer.md 4.3): build/review.csv and apply-review."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from .config import load_config
from .model import read_place, write_place
from .validate import check_place, validate

COLUMNS = ["id", "kind", "name", "municipality", "status", "priority", "hours", "hours_source", "visit_min",
           "visit_typical", "description", "status_reason", "warnings"]
EDITABLE = ("status", "priority", "hours", "visit_min", "visit_typical", "description")


def write_review(data_dir, out, engine=None, config_dir=None, stale_days=None, today=None):
    rep = validate(data_dir, config_dir, engine=engine, today=today)
    today = today or dt.date.today()
    n = 0
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for p in sorted(rep.places, key=lambda p: (p.kind in ("food", "drink"), p.municipality, p.id)):
            chk = p.provenance.checked
            if stale_days is not None and chk is not None and (today - chk).days <= stale_days:
                continue
            w.writerow([p.id, p.kind, p.name, p.municipality, p.status, p.priority, p.hours,
                        p.provenance.hours_source, p.visit_min, p.visit_typical, p.description,
                        p.provenance.status_reason, "; ".join(rep.warn_for(p.id))])
            n += 1
    return n, rep


def apply_review(csv_path, data_dir, config_dir=None):
    """Update status, priority, hours, visit_* and description from the sheet. Returns (changed, problems)."""
    cfg = load_config(config_dir)
    changed, problems = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            path = Path(data_dir) / "places" / f"{row['id']}.toml"
            if not path.exists():
                problems.append((row["id"], "no such record"))
                continue
            p = read_place(path)
            before = (p.status, p.priority, p.hours, p.visit_min, p.visit_typical, p.description)
            try:
                p.provenance.status = row["status"].strip()
                p.priority = int(row["priority"])
                p.hours = row["hours"].strip()
                p.visit_min = int(row["visit_min"])
                p.visit_typical = int(row["visit_typical"])
                p.description = row["description"].strip()
            except (ValueError, KeyError) as e:
                problems.append((row["id"], f"bad value: {e}"))
                continue
            if before == (p.status, p.priority, p.hours, p.visit_min, p.visit_typical, p.description):
                continue
            if p.hours != before[2]:
                p.provenance.drafted_by = "pedro"
            errs = check_place(p, cfg, Path(data_dir).parent / "cache")
            if errs:
                problems.append((row["id"], "not applied: " + "; ".join(errs)))
                continue
            write_place(p, Path(data_dir) / "places")
            changed.append(row["id"])
    return changed, problems
