"""Deterministic end-to-end backtest on the committed SMARD fixture (offline).

Data: Bundesnetzagentur | SMARD.de (CC BY 4.0), hourly 2024-03-25 to
2024-04-07, including the 23 h day on 31 March. Strategies: perfect foresight,
persistence forecast + MILP, persistence forecast + two-window heuristic.

The regression totals were first computed on 2026-10-23 (Stage 6, HiGHS 1.15.1).
They are cross-checked here by invariants that do not depend on them:
determinism, the perfect-foresight upper bound, reconciliation with the
interval export, and cyclic state carry.
"""

import datetime as dt

import numpy as np
import pytest

from gridflex.backtest import run_backtest, summary_table
from gridflex.config import DayPeriod, GridFlexConfig
from gridflex.data import SmardClient, build_market_dataset
from tests_helpers import FIXTURE_CACHE

STRATEGIES = ["perfect_foresight", "forecast_persistence", "heuristic_persistence"]
PERIOD = DayPeriod(start_day=dt.date(2024, 3, 30), end_day=dt.date(2024, 4, 8))
REGRESSION_NET_EUR = {
    "perfect_foresight": 9468.031987332713,
    "forecast_persistence": 5699.940326863294,
    "heuristic_persistence": 3562.4902024088337,
}


@pytest.fixture(scope="module")
def run(gf_cfg: GridFlexConfig):  # type: ignore[no-untyped-def]
    client = SmardClient.from_config(gf_cfg.data, cache_dir=FIXTURE_CACHE, offline=True)
    ds = build_market_dataset(
        gf_cfg,
        client,
        dt.date(2024, 3, 25),
        dt.date(2024, 4, 8),
        series=("price", "load_forecast"),
    )
    return [
        run_backtest(gf_cfg, ds, period=PERIOD, strategy_names=STRATEGIES)
        for _ in range(2)
    ]


def test_regression_totals(run) -> None:  # type: ignore[no-untyped-def]
    results, _ = run[0]
    table = summary_table(results).set_index("strategy")
    for name, value in REGRESSION_NET_EUR.items():
        assert table.loc[name, "net_operating_revenue_eur"] == pytest.approx(
            value, rel=1e-9, abs=1e-6
        )
    assert (table["failed_days"] == 0).all()
    assert table.loc["perfect_foresight", "days"] == 9


def test_deterministic(run) -> None:  # type: ignore[no-untyped-def]
    (a, meta_a), (b, meta_b) = run
    for name in STRATEGIES:
        cols = [c for c in a[name].daily.columns if c != "solve_runtime_s"]
        assert a[name].daily[cols].equals(b[name].daily[cols])
        assert a[name].intervals.equals(b[name].intervals)
    assert meta_a["config_fingerprint"] == meta_b["config_fingerprint"]


def test_invariants(run) -> None:  # type: ignore[no-untyped-def]
    results, meta = run[0]
    pf = results["perfect_foresight"].daily
    assert pf.n_intervals.tolist() == [24, 23, 24, 24, 24, 24, 24, 24, 24]
    for name in STRATEGIES:
        d, iv = results[name].daily, results[name].intervals
        # Perfect foresight is an upper bound day by day (identical start energy).
        assert (
            d.net_operating_revenue_eur.to_numpy()
            <= pf.net_operating_revenue_eur.to_numpy() + 1e-6
        ).all()
        # Cyclic policy: every day ends where it started (10 MWh).
        np.testing.assert_allclose(d.e_end_mwh, 10.0, atol=1e-6)
        # Gross revenue reconciles with the interval export (dt = 1 h).
        gross = (
            (iv.price_realised_eur_per_mwh * (iv.discharge_mw - iv.charge_mw))
            .groupby(iv.delivery_day)
            .sum()
            .to_numpy()
        )
        np.testing.assert_allclose(gross, d.gross_revenue_eur, atol=1e-6)
    assert meta["data_provenance"]["series"]["price"]["license"] == "CC BY 4.0"
