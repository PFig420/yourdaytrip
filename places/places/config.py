"""Configuration: vocabulary, meal windows, area, holidays."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .holidays import Holidays

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config"


@dataclass
class Config:
    vocab: dict
    meals: dict
    area: dict
    cluster_minutes: int
    stale_days: int
    holidays: Holidays


def _toml(p):
    with open(p, "rb") as f:
        return tomllib.load(f)


def load_config(config_dir=None) -> Config:
    d = Path(config_dir) if config_dir else DEFAULT_CONFIG
    pl = _toml(d / "places.toml")
    return Config(_toml(d / "vocabulary.toml"), pl["meals"], pl["area"], pl.get("walk", {}).get("cluster_minutes", 10),
                  pl.get("freshness", {}).get("stale_days", 180), Holidays.load(d / "holidays.toml"))


def parse_window(s: str) -> tuple[int, int]:
    """'12:00-15:00' -> seconds since midnight; an end before the start runs past midnight."""
    a, b = s.split("-")

    def sec(t):
        h, m = t.split(":")
        return int(h) * 3600 + int(m) * 60

    a, b = sec(a), sec(b)
    return (a, b + 86400) if b <= a else (a, b)
