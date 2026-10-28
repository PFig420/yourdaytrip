"""Deterministic GTFS zip writer (spec 5.7)."""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

FILES = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt",
         "calendar_dates.txt", "feed_info.txt"]


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def write_zip(path: Path, tables: dict[str, tuple[list[str], list[list]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES:
            header, rows = tables[name]
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 3
            z.writestr(zi, _csv_bytes(header, rows), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
