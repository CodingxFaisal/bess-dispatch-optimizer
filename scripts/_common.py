"""Shared helpers for the CLI scripts.

Adds ``src/`` to the import path (so the scripts run with no ``pip install``
needed) and provides a cached dataset loader.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pandas as pd  # noqa: E402

from bess.config import load_config  # noqa: E402
from bess.data import build_dataset  # noqa: E402

DATA_PROCESSED = REPO_ROOT / "data" / "processed"
DATA_RAW = REPO_ROOT / "data" / "raw"
RESULTS = REPO_ROOT / "results"
CONFIG = REPO_ROOT / "config" / "battery.yaml"


def load_or_build_dataset(start: str, end: str, refresh: bool = False) -> pd.DataFrame:
    """Load the processed dataset from cache, or download+build it from SMARD."""
    cfg = load_config(CONFIG)
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    cache = DATA_PROCESSED / f"dataset_{start}_{end}.csv"

    if cache.exists() and not refresh:
        df = pd.read_csv(cache, parse_dates=["timestamp"], index_col="timestamp")
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(cfg.timezone)
        return df

    df = build_dataset(cfg, start, end, cache_dir=DATA_RAW)
    df.to_csv(cache)
    return df
