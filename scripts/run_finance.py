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
from gridflex.reporting.export import FingerprintMismatchError, write_finance


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
    try:
        summary = write_finance(run, cfg, benchmark=args.benchmark, force=args.force)
    except FingerprintMismatchError as exc:
        sys.exit(f"{exc}; re-run the backtest or pass --force")
    out = run / "finance"
    inv = json.loads((out / "investment.json").read_text(encoding="utf-8"))["results"]

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
