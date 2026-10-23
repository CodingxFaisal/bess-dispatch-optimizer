"""Write backtest and finance outputs to a run directory.

Layout of a run directory (``runs/<name>/``)::

    backtest/daily.csv          one row per strategy and delivery day
    backtest/intervals.csv      committed schedule and prices per interval
    backtest/summary.csv        totals per strategy
    backtest/market_data.csv    validated market inputs (UTC index, clean units)
    backtest/metadata.json      git, config, versions, policy, data provenance
    backtest/finance/...        written by :func:`write_finance`

The UI (``app/``) only reads these files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from gridflex.backtest.engine import BacktestResult, summary_table
from gridflex.config.loader import config_fingerprint
from gridflex.config.models import GridFlexConfig
from gridflex.data.dataset import MarketDataset
from gridflex.finance.investment import investment_metrics
from gridflex.finance.metrics import capture_by_period, period_table, summarise


class FingerprintMismatchError(RuntimeError):
    """The run was produced with a different configuration."""


def write_backtest_run(
    out: Path,
    results: dict[str, BacktestResult],
    meta: dict[str, Any],
    dataset: MarketDataset,
) -> pd.DataFrame:
    out.mkdir(parents=True, exist_ok=True)
    pd.concat([r.daily for r in results.values()], ignore_index=True).to_csv(
        out / "daily.csv", index=False
    )
    pd.concat([r.intervals for r in results.values()], ignore_index=True).to_csv(
        out / "intervals.csv", index=False
    )
    summary = summary_table(results)
    summary.to_csv(out / "summary.csv", index=False)
    frame = dataset.frame.copy()
    frame.index.name = "interval_utc"
    frame.to_csv(out / "market_data.csv")
    (out / "metadata.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )
    return summary


def write_finance(
    run: Path,
    cfg: GridFlexConfig,
    *,
    benchmark: str = "perfect_foresight",
    force: bool = False,
) -> pd.DataFrame:
    """Compute financial tables for a saved backtest; refuse other configs."""
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    if meta["config_fingerprint"] != config_fingerprint(cfg) and not force:
        raise FingerprintMismatchError(
            "backtest was run with a different configuration (fingerprint mismatch)"
        )
    daily = pd.read_csv(run / "daily.csv")
    out = run / "finance"
    out.mkdir(exist_ok=True)
    bench = daily[daily.strategy == benchmark]
    bench_or_none = bench if not bench.empty else None
    rows, years, months, cap_y, cap_m = [], [], [], [], []
    inv = {}
    for name, d in daily.groupby("strategy", sort=False):
        rows.append(summarise(d, cfg.battery, benchmark=bench_or_none).as_dict())
        years.append(period_table(d, cfg.battery, "year").assign(strategy=name))
        months.append(period_table(d, cfg.battery, "month").assign(strategy=name))
        if bench_or_none is not None:
            cap_y.append(capture_by_period(d, bench, "year").assign(strategy=name))
            cap_m.append(capture_by_period(d, bench, "month").assign(strategy=name))
        inv[str(name)] = investment_metrics(
            summarise(d, cfg.battery), cfg.battery, cfg.economics.investment
        ).as_dict()
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "summary.csv", index=False)
    pd.concat(years).to_csv(out / "by_year.csv", index=False)
    pd.concat(months).to_csv(out / "by_month.csv", index=False)
    if cap_y:
        pd.concat(cap_y).to_csv(out / "capture_by_year.csv", index=False)
        pd.concat(cap_m).to_csv(out / "capture_by_month.csv", index=False)
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
    return summary
