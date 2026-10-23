"""One-at-a-time sensitivity of backtest results to battery parameters.

    uv run python scripts/run_sensitivity.py [--offline] [--strategies a,b]
                                             [--start YYYY-MM-DD --end YYYY-MM-DD]

Variations come from ``sensitivity`` in configs/default.yaml. Each variant is a
complete rolling backtest over the period. Outputs (git-ignored) go to
``runs/latest/sensitivity/``: ``sensitivity.csv`` and ``metadata.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import pandas as pd

from _common import RUNS, load_cfg, load_dataset
from gridflex.config import DayPeriod
from gridflex.finance import run_sensitivity
from gridflex.reporting import run_metadata


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strategies")
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    cfg = load_cfg()
    if cfg.backtest is None or cfg.sensitivity is None:
        sys.exit("configs/default.yaml needs backtest and sensitivity sections")
    period = cfg.sensitivity.period or cfg.backtest.period
    if args.start or args.end:
        period = DayPeriod(
            start_day=dt.date.fromisoformat(args.start)
            if args.start
            else period.start_day,
            end_day=dt.date.fromisoformat(args.end) if args.end else period.end_day,
        )
    history = cfg.forecasting.history_start_day if cfg.forecasting else period.start_day
    series = ("price", *(cfg.forecasting.exogenous_series if cfg.forecasting else ()))
    t0 = time.perf_counter()
    ds = load_dataset(
        cfg,
        history.isoformat(),
        period.end_day.isoformat(),
        series=series,
        offline=args.offline or None,
    )
    names = args.strategies.split(",") if args.strategies else None

    def log(msg: str) -> None:
        print(f"[{time.perf_counter() - t0:7.1f} s] {msg}", flush=True)

    table = run_sensitivity(cfg, ds, period=period, strategy_names=names, progress=log)
    out = RUNS / "sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "sensitivity.csv", index=False)
    meta = run_metadata(
        cfg,
        period=period.model_dump(mode="json"),
        data_provenance=ds.provenance(),
        runtime_s=round(time.perf_counter() - t0, 1),
    )
    (out / "metadata.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )
    pd.set_option("display.width", 220)
    cols = [
        "parameter",
        "value",
        "is_base",
        "strategy",
        "net_operating_revenue_eur",
        "net_per_mw_year_eur",
        "net_per_mwh_year_eur",
        "efc_per_day",
        "capture_ratio",
    ]
    print(table[cols].round(3).to_string(index=False))
    log(f"outputs -> {out}")


if __name__ == "__main__":
    main()
