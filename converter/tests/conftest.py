import datetime as dt
import shutil
from pathlib import Path

import pytest

from gtfsconv import build as build_mod
from gtfsconv.fetch import cache_ready

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT.parent / "schedules" / "schedules.csv"
FIXTURE_FEED = ROOT / "tests" / "fixtures" / "school_2026_27_fixture.toml"
BUILD_DATE = dt.date(2026, 10, 19)


@pytest.fixture(scope="session")
def fixture_config(tmp_path_factory):
    """config/ with the test-only school calendar swapped in (never the real feed.toml)."""
    d = tmp_path_factory.mktemp("config")
    shutil.copytree(ROOT / "config", d, dirs_exist_ok=True)
    shutil.copy(FIXTURE_FEED, d / "feed.toml")
    return d


@pytest.fixture(scope="session")
def real_build(fixture_config, tmp_path_factory):
    """The real CSV, built with --drop-unplaced and the fixture school calendar."""
    if not CSV.exists():
        pytest.skip("schedules/schedules.csv not present")
    if not cache_ready(ROOT / "cache"):
        pytest.skip("coordinate caches missing; run `python -m gtfsconv fetch`")
    out = tmp_path_factory.mktemp("build")
    res = build_mod.build(CSV, None, fixture_config, ROOT / "cache", out, drop_unplaced=True, build_date=BUILD_DATE)
    return res
