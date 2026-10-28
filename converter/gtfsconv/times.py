"""Midnight rollover and monotonicity checks (spec 5.3)."""
from __future__ import annotations

from .load import BuildError

DAY = 86400


def rollover(stops) -> None:
    """In place: a time more than 12 h earlier than its predecessor gets +24 h, and so do later stops."""
    offset = 0
    prev = None
    for s in stops:
        t = s.t + offset
        if prev is not None and prev - t > 12 * 3600:
            offset += DAY
            t += DAY
        s.t = t
        prev = t


def non_monotonic(stops) -> list[int]:
    """Indices of stops whose time is earlier than the previous stop's (after rollover)."""
    return [i for i in range(1, len(stops)) if stops[i].t < stops[i - 1].t]


def check_trip(trip) -> None:
    bad = non_monotonic(trip.stops)
    if bad:
        s = trip.stops[bad[0]]
        p = trip.stops[bad[0] - 1]
        raise BuildError(
            f"non-monotonic times in trip {trip.trip} ({trip.source}, page {trip.page}, line {trip.line}): "
            f"{p.raw!r} {fmt(p.t)} -> {s.raw!r} {fmt(s.t)} (stop_sequence {s.seq}). "
            f"Check the PDF and add a row to config/time_fixes.csv")


def fmt(t: int) -> str:
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
