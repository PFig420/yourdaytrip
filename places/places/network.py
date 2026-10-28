"""Small geometry helpers (reuse the engine's haversine when available)."""
from __future__ import annotations

import math


def distance_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(a)))


def nearest_stop_m(engine, lat, lon, limit=20000):
    """Metres to the nearest stop of either feed (None only beyond `limit`)."""
    near = engine.net._within(lat, lon, limit)
    return min((m for _s, m in near), default=None)
