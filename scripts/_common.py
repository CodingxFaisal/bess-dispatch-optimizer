"""Shared helpers for the CLI scripts.

Adds ``src/`` to the import path (so scripts also run without installing the
package) and builds validated datasets through :mod:`gridflex.data`.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from gridflex.config import GridFlexConfig, load_config  # noqa: E402
from gridflex.data import MarketDataset, SmardClient, build_market_dataset  # noqa: E402

CONFIG = REPO_ROOT / "configs" / "default.yaml"
# Fresh outputs go to runs/ (git-ignored). results/ holds committed, labelled
# example outputs only.
RUNS = REPO_ROOT / "runs" / "latest"

# Series used by the v0.1 strategies: price plus the load forecast, the only
# exogenous input public at the day-ahead decision (audit D1).
DEFAULT_SERIES = ("price", "load_forecast")


def load_cfg(path: str | Path = CONFIG) -> GridFlexConfig:
    return load_config(path)


def make_client(cfg: GridFlexConfig, offline: bool | None = None) -> SmardClient:
    cache_dir = Path(cfg.data.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    return SmardClient.from_config(cfg.data, cache_dir=cache_dir, offline=offline)


def load_dataset(
    cfg: GridFlexConfig,
    start: str,
    end: str,
    *,
    series: tuple[str, ...] = DEFAULT_SERIES,
    offline: bool | None = None,
    refresh: bool = False,
) -> MarketDataset:
    """Validated dataset for local delivery days ``[start, end)`` (ISO dates)."""
    return build_market_dataset(
        cfg,
        make_client(cfg, offline=offline),
        dt.date.fromisoformat(start),
        dt.date.fromisoformat(end),
        series=series,
        refresh=refresh,
    )


def write_provenance(dataset: MarketDataset, out_dir: Path, tag: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"data_provenance_{tag}.json"
    path.write_text(json.dumps(dataset.provenance(), indent=2), encoding="utf-8")
    return path
