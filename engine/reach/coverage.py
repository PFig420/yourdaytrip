"""Feed coverage by date."""
from __future__ import annotations

from .calendar import feed_covers, to_date
from .models import Coverage

LABELS = {"cp": ("CP train", "trains"), "av": ("Aveiro bus and ferry", "buses and ferries")}


def check(feeds, d) -> Coverage:
    d = to_date(d)
    missing, warns = [], []
    for f in feeds:
        if feed_covers(f, d):
            continue
        missing.append(f.id)
        label, what = LABELS.get(f.id, (f"{f.id} feed", "its services"))
        fmt = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
        if f.last_date and d.strftime("%Y%m%d") > f.last_date:
            warns.append(f"No {label} data after {fmt(f.last_date)}; {what} are not included.")
        else:
            warns.append(f"No {label} data before {fmt(f.first_date)}; {what} are not included.")
    return Coverage(d.isoformat(), tuple(missing), tuple(warns))
