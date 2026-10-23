"""Financial metrics from a saved backtest run.

    uv run python scripts/run_finance.py [--run runs/latest/backtest] [--force]

Reads ``daily.csv`` and ``metadata.json`` from the backtest output, checks
that the run was made with the current configuration (config fingerprint;
``--force`` overrides), and writes to ``<run>/finance/``:

* ``summary.csv``: per-strategy totals, annualised and normalised metrics,
  capture ratio.
* ``by_year.csv`` and ``by_month.csv``: period totals with completeness flags.
* ``capture_by_year.csv`` and ``capture_by_month.csv``.
* ``investment.json``: payback and NPV, or the inputs that are missing.

Formulas: ``gridflex.finance.metrics`` and docs/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from _common import RUNS, load_cfg
from gridflex.config import config_fingerprint
from gridflex.finance import (
    capture_by_period,
    investment_metrics,
    period_table,
    summarise,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=str(RUNS / "backtest"))
    ap.add_argument("--benchmark", default="perfect_foresight")
    ap.add_argument(
        "--force", action="store_true", help="ignore a config-fingerprint mismatch"
    )
    args = ap.parse_args()

    run = Path(args.run)
    cfg = load_cfg()
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    if meta["config_fingerprint"] != config_fingerprint(cfg) and not args.force:
        sys.exit(
            "backtest was run with a different configuration "
            "(fingerprint mismatch); re-run it or pass --force"
        )
    daily = pd.read_csv(run / "daily.csv")
    out = run / "finance"
    out.mkdir(exist_ok=True)

    bench = daily[daily.strategy == args.benchmark]
    bench = bench if not bench.empty else None
    rows, years, months, cap_y, cap_m = [], [], [], [], []
    for name, d in daily.groupby("strategy", sort=False):
        s = summarise(d, cfg.battery, benchmark=bench)
        rows.append(s.as_dict())
        years.append(period_table(d, cfg.battery, "year").assign(strategy=name))
        months.append(period_table(d, cfg.battery, "month").assign(strategy=name))
        if bench is not None:
            cap_y.append(capture_by_period(d, bench, "year").assign(strategy=name))
            cap_m.append(capture_by_period(d, bench, "month").assign(strategy=name))
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "summary.csv", index=False)
    pd.concat(years).to_csv(out / "by_year.csv", index=False)
    pd.concat(months).to_csv(out / "by_month.csv", index=False)
    if cap_y:
        pd.concat(cap_y).to_csv(out / "capture_by_year.csv", index=False)
        pd.concat(cap_m).to_csv(out / "capture_by_month.csv", index=False)

    inv = {}
    for name, d in daily.groupby("strategy", sort=False):
        r = investment_metrics(
            summarise(d, cfg.battery), cfg.battery, cfg.economics.investment
        )
        inv[name] = r.as_dict()
    (out / "investment.json").write_text(
        json.dumps(
            {
                "source_run_fingerprint": meta["config_fingerprint"],
                "investment_inputs": cfg.economics.investment.model_dump(),
                "results": inv,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    pd.set_option("display.width", 220)
    cols = [
        "strategy",
        "days",
        "partial_year",
        "net_operating_revenue_eur",
        "annualised_net_eur",
        "net_per_mw_year_eur",
        "net_per_mwh_year_eur",
        "efc_per_day",
        "negative_net_days",
        "capture_ratio",
    ]
    print(summary[cols].round(3).to_string(index=False))
    first = next(iter(inv.values()))
    if first["payback_missing"]:
        print(f"investment metrics not computed; missing: {first['payback_missing']}")
    print(f"outputs -> {out}")


if __name__ == "__main__":
    main()
