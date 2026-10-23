"""Integration: the documented one-command example runs end to end, offline.

Runs ``scripts/run_example.py`` as a user would (separate process, network
access not needed) and checks the whole chain through its outputs: data
validation, forecasts with leakage metadata, verified dispatch, settlement,
finance tables and sensitivity.
"""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests_helpers import REPO_ROOT


@pytest.fixture(scope="module")
def example(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("example") / "run"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_example.py"),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "example run written" in proc.stdout
    return out


def test_all_outputs_present(example: Path) -> None:
    for rel in (
        "backtest/daily.csv",
        "backtest/intervals.csv",
        "backtest/summary.csv",
        "backtest/market_data.csv",
        "backtest/metadata.json",
        "backtest/finance/summary.csv",
        "backtest/finance/by_month.csv",
        "backtest/finance/investment.json",
        "forecast_evaluation/holdout_metrics.csv",
        "forecast_evaluation/holdout_forecasts.csv",
        "sensitivity/sensitivity.csv",
    ):
        assert (example / rel).is_file(), rel


def test_pipeline_invariants(example: Path) -> None:
    daily = pd.read_csv(example / "backtest" / "daily.csv")
    assert (daily["status"] == "ok").all() and len(daily) == 3 * 9
    pf = daily[daily.strategy == "perfect_foresight"].set_index("delivery_day")
    for _, d in daily.groupby("strategy"):
        d = d.set_index("delivery_day")
        assert (
            d.net_operating_revenue_eur <= pf.net_operating_revenue_eur + 1e-6
        ).all()
    assert daily[daily.strategy == "perfect_foresight"].solve_status.eq("optimal").all()
    fin = pd.read_csv(example / "backtest" / "finance" / "summary.csv")
    assert fin.set_index("strategy").loc["perfect_foresight", "capture_ratio"] == 1.0
    fc = pd.read_csv(example / "forecast_evaluation" / "holdout_forecasts.csv")
    origin = pd.to_datetime(fc.origin_utc)
    assert (pd.to_datetime(fc.feature_cutoff_utc) <= origin).all()
    assert (origin < pd.to_datetime(fc.delivery_interval_utc)).all()
    meta = json.loads((example / "backtest" / "metadata.json").read_text())
    assert meta["example"].startswith("offline fixture example")
    assert meta["data_provenance"]["series"]["price"]["n_chunks_downloaded"] == 0
    inv = json.loads((example / "backtest" / "finance" / "investment.json").read_text())
    assert all(r["payback_years"] is None for r in inv["results"].values())
