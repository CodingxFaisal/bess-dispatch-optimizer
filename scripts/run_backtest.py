"""Tier 2 -- realistic, forecast-driven backtest (the differentiator).

Runs the rolling-horizon backtest for every forecaster and reports the headline
number: the *foresight capture ratio* -- how much of the perfect-foresight
revenue ceiling each realistic strategy actually reaches.

    uv run python scripts/run_backtest.py \
        --data-start 2022-01-01 --start 2023-01-01 --end 2025-01-01

``--data-start`` should sit well before ``--start`` so the forecasters have
history to train on / lag against (the ML model needs ~1 year).
"""

from __future__ import annotations

import argparse

import pandas as pd

from _common import RUNS as RESULTS
from _common import legacy_frame, load_cfg, load_dataset, write_provenance
from gridflex.analysis.metrics import revenue_by_year, summarize_backtest, summary_table
from gridflex.analysis.plots import (
    plot_capture_ratio,
    plot_dispatch_week,
    plot_revenue_by_year,
)
from gridflex.backtest import run_backtest
from gridflex.forecast import get_forecaster
from gridflex.optimize.params import DispatchParams

FORECASTERS = ["perfect", "naive", "seasonal_naive", "ml"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data-start",
        default="2022-01-01",
        help="dataset load start (warmup/training history)",
    )
    ap.add_argument("--start", default="2023-01-01", help="backtest start")
    ap.add_argument("--end", default="2025-01-01", help="backtest end (exclusive)")
    ap.add_argument(
        "--retrain-every", type=int, default=30, help="ML retrain cadence in days"
    )
    args = ap.parse_args()

    gf_cfg = load_cfg()
    cfg = DispatchParams.from_config(gf_cfg)
    ds = load_dataset(gf_cfg, args.data_start, args.end)
    write_provenance(ds, RESULTS, "backtest")
    df = legacy_frame(ds)
    RESULTS.mkdir(parents=True, exist_ok=True)

    results = {}
    for name in FORECASTERS:
        kwargs = {"retrain_every_days": args.retrain_every} if name == "ml" else {}
        print(f"running backtest: {name} ...")
        results[name] = run_backtest(
            df,
            cfg,
            get_forecaster(name, **kwargs),
            args.start,
            args.end,
        )

    perfect_net = results["perfect"].net_revenue_eur
    summaries = [
        summarize_backtest(
            results[name],
            cfg,
            perfect_net_eur=perfect_net if name != "perfect" else None,
        )
        for name in FORECASTERS
    ]
    table = summary_table(summaries)
    table.to_csv(RESULTS / "backtest_summary.csv")

    # Per-day economics for auditability.
    for name in FORECASTERS:
        results[name].daily.to_csv(RESULTS / f"daily_{name}.csv")

    # ---- headline print ------------------------------------------------
    print("\n=== Tier 2: foresight capture ===")
    hdr = f"{'strategy':16s}{'EUR/MW-yr':>12s}{'cycles/yr':>11s}{'capture':>10s}"
    print(hdr)
    print("-" * len(hdr))
    for name in FORECASTERS:
        s = next(x for x in summaries if x["forecaster"] == name)
        cap = "" if name == "perfect" else f"{s['capture_ratio'] * 100:8.1f}%"
        print(
            f"{name:16s}{s['revenue_per_mw_year_eur']:12,.0f}"
            f"{s['equivalent_full_cycles_per_year']:11,.0f}{cap:>10s}"
        )

    ml = next(x for x in summaries if x["forecaster"] == "ml")
    naive = next(x for x in summaries if x["forecaster"] == "naive")
    pf_mw_yr = perfect_net / results["perfect"].n_days * 365 / cfg.power_mw
    print(
        f"\nHEADLINE: perfect foresight = EUR {pf_mw_yr:,.0f}/MW-yr. "
        f"Gradient boosting captures {ml['capture_ratio'] * 100:.0f}%; "
        f"naive persistence {naive['capture_ratio'] * 100:.0f}%."
    )

    # ---- charts --------------------------------------------------------
    net_by_fc = {
        n: results[n].net_revenue_eur for n in ["naive", "seasonal_naive", "ml"]
    }
    plot_capture_ratio(net_by_fc, perfect_net, RESULTS / "capture_ratio.png")

    by_year = {n: revenue_by_year(results[n]) for n in FORECASTERS}
    plot_revenue_by_year(
        pd.DataFrame(by_year).to_dict("series"), RESULTS / "revenue_by_year.png"
    )

    sched = results["ml"].schedule
    wk = sched.loc[str(sched.index[int(len(sched) * 0.45)].normalize()) :].iloc[
        : 24 * 7
    ]
    plot_dispatch_week(
        wk,
        RESULTS / "ml_dispatch_week.png",
        title="Forecast-driven (ML) dispatch: bid on forecast, settle on reality",
    )

    print(
        "\nsaved: backtest_summary.csv, capture_ratio.png, revenue_by_year.png, "
        "ml_dispatch_week.png  (in runs/latest/)"
    )


if __name__ == "__main__":
    main()
