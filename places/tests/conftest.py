import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "places"))

import reach  # noqa: E402
import places  # noqa: E402

PLACES = ROOT / "places"
FIXTURES = PLACES / "tests" / "fixtures"
CAMPANHA = ("campanha", 41.14871926, -8.584835348)


@pytest.fixture(scope="session")
def eng():
    return reach.load(str(ROOT / "engine" / "config"))


@pytest.fixture(scope="session")
def fixture_lib(eng):
    return places.load(str(FIXTURES), engine=eng, config_dir=str(PLACES / "config"))


@pytest.fixture(scope="session")
def real_lib(eng):
    return places.load(str(PLACES / "data"), engine=eng, config_dir=str(PLACES / "config"))
