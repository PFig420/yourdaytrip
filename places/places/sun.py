"""Sunrise and sunset from the NOAA solar position algorithm (standard library only)."""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")
ZENITH = 90.833          # sunrise/sunset incl. refraction and the solar disc radius


def _terms(jc):
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * jc) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    mean_obl = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obl = mean_obl + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(obl)) * math.sin(math.radians(app))))
    y = math.tan(math.radians(obl / 2)) ** 2
    l0r = math.radians(l0)
    eq = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                          - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return decl, eq


def sun_times(d: dt.date, lat: float, lon: float):
    """(sunrise, sunset) in seconds since local midnight of d (Europe/Lisbon), or None if the sun
    does not rise or set that day. lon is positive east."""
    jd0 = d.toordinal() + 1721424.5                      # 0h UTC
    # two passes: evaluate at the estimated event time
    rise = 720 - 4 * lon
    sset = 720 - 4 * lon
    ha = None
    for _ in range(2):
        r_jc = (jd0 + rise / 1440 - 2451545) / 36525
        s_jc = (jd0 + sset / 1440 - 2451545) / 36525
        r_decl, r_eq = _terms(r_jc)
        s_decl, s_eq = _terms(s_jc)

        def hour_angle(decl):
            lr, dr = math.radians(lat), math.radians(decl)
            x = math.cos(math.radians(ZENITH)) / (math.cos(lr) * math.cos(dr)) - math.tan(lr) * math.tan(dr)
            return None if abs(x) > 1 else math.degrees(math.acos(x))

        rh, sh = hour_angle(r_decl), hour_angle(s_decl)
        if rh is None or sh is None:
            return None
        rise = 720 - 4 * (lon + rh) - r_eq
        sset = 720 - 4 * (lon - sh) - s_eq
    base = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
    out = []
    for minutes in (rise, sset):
        t = (base + dt.timedelta(seconds=minutes * 60)).astimezone(TZ)
        out.append(round((t.replace(tzinfo=None) - dt.datetime(d.year, d.month, d.day)).total_seconds()))
    return tuple(out)
