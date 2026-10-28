import csv
import io
import os
import zipfile
from pathlib import Path

import pytest

import reach

ENGINE_DIR = Path(__file__).resolve().parents[1]
CONFIG = str(ENGINE_DIR / "config")

# Feed hashes the golden values were computed for (see README "Golden values").
GOLDEN_HASHES = {
    "cp": "48813f44f4257ca192f8970c9a7b0854fbdceb75e2b2eee09497d9d756ae9f58",
    "av": "205a30af619b2e8cd66201e3721b06f780d29f57f406b8e3d019bf83a747328d",
}

CAMPANHA = ("campanha", 41.14871926, -8.584835348)
BUS_TERMINAL = ("bus-terminal", 40.643800, -8.638550)
COSTA_NOVA = ("costa-nova", 40.612500, -8.749740)
SAO_JACINTO = ("sao-jacinto", 40.660682, -8.729750)
FORTE_FERRY = ("forte-ferry", 40.649904, -8.730027)
AGUEDA = ("agueda", 40.57749168, -8.448231459)


@pytest.fixture(scope="session")
def eng():
    return reach.load(CONFIG)


@pytest.fixture(scope="session")
def eng_plain(tmp_path_factory):
    """The real feeds and barriers with walk_overrides switched off: today's behaviour before WalkBarriers.md."""
    import tomllib
    root = tmp_path_factory.mktemp("plain")
    cfg = root / "engine" / "config"
    cfg.mkdir(parents=True)
    feeds = tomllib.loads((Path(CONFIG) / "feeds.toml").read_text())["feeds"]
    (cfg / "feeds.toml").write_text("\n".join(
        f'[[feeds]]\nid = "{f["id"]}"\npath = "{ENGINE_DIR.parent / f["path"]}"\nsha256 = "{f["sha256"]}"\n' for f in feeds))
    (cfg / "engine.toml").write_text("\n".join(l for l in (Path(CONFIG) / "engine.toml").read_text().splitlines()
                                             if not l.startswith("walk_overrides")))
    (cfg / "walk_barriers.geojson").write_text((Path(CONFIG) / "walk_barriers.geojson").read_text())
    return reach.load(str(cfg))


@pytest.fixture(scope="session")
def golden_eng(eng):
    for f in eng.feeds:
        if f.sha256 != GOLDEN_HASHES[f.id]:
            pytest.skip(f"feed '{f.id}' changed (hash {f.sha256[:12]}); golden values need recomputing")
    return eng


def hhmmss(t):
    return t["local"][11:19]


def make_feed_zip(path, stops, trips, service_dates=("20261024",), route_type=3):
    """stops: {id: (lat, lon)}; trips: {trip_id: [(stop, arr, dep, pickup, dropoff)]} (times 'HH:MM:SS')."""
    def w(rows, header):
        buf = io.StringIO()
        cw = csv.writer(buf)
        cw.writerow(header)
        cw.writerows(rows)
        return buf.getvalue()

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stops.txt", w([(k, k, v[0], v[1]) for k, v in stops.items()],
                                  ["stop_id", "stop_name", "stop_lat", "stop_lon"]))
        z.writestr("routes.txt", w([("R", "R1", "Long R", route_type)],
                                   ["route_id", "route_short_name", "route_long_name", "route_type"]))
        z.writestr("trips.txt", w([("R", "S", t, "H") for t in trips],
                                  ["route_id", "service_id", "trip_id", "trip_headsign"]))
        rows = []
        for t, sts in trips.items():
            for i, (s, a, d, pk, do) in enumerate(sts):
                rows.append((t, a, d, s, i + 1, pk, do))
        z.writestr("stop_times.txt", w(rows, ["trip_id", "arrival_time", "departure_time", "stop_id",
                                                "stop_sequence", "pickup_type", "drop_off_type"]))
        z.writestr("calendar_dates.txt", w([("S", d, 1) for d in service_dates],
                                           ["service_id", "date", "exception_type"]))


def make_engine(tmp_path, feeds: dict):
    """feeds: {feed_id: (stops, trips)} -> loaded Engine on synthetic data (no barriers)."""
    cfg = tmp_path / "engine" / "config"
    cfg.mkdir(parents=True)
    toml = []
    for fid, (stops, trips) in feeds.items():
        p = tmp_path / f"{fid}.zip"
        make_feed_zip(p, stops, trips)
        toml.append(f'[[feeds]]\nid = "{fid}"\npath = "{p}"\nsha256 = "x"\n')
    (cfg / "feeds.toml").write_text("\n".join(toml))
    (cfg / "engine.toml").write_text((Path(CONFIG) / "engine.toml").read_text())
    (cfg / "walk_barriers.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    return reach.load(str(cfg))
