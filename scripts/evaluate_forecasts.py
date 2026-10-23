"""Select forecast models on the selection period, then score them once on the hold-out.

    uv run python scripts/evaluate_forecasts.py [--offline]

Protocol and periods come from ``forecasting`` in configs/default.yaml.
Outputs (git-ignored) in ``runs/latest/forecast_evaluation/``:

* ``selection.csv``: every candidate's selection-period metrics.
* ``holdout_metrics.csv``: hold-out metrics of the chosen configurations.
* ``holdout_by_year.csv``: hold-out metrics per calendar year.
* ``dm_tests.csv``: Diebold-Mariano tests on the hold-out.
* ``holdout_forecasts.csv``: every hold-out forecast, with its metadata.
* ``metadata.json``: config fingerprint, versions, data provenance, chosen models.
"""

from __future__ import annotations

import argparse
import itertools
import json
import platform
import sys
import time

import pandas as pd

import gridflex
from _common import RUNS, load_cfg, load_dataset
from gridflex.config import config_fingerprint
from gridflex.forecasting import (
    accuracy,
    build_feature_table,
    dm_test,
    evaluate_holdout,
    select_models,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    fc = cfg.forecasting
    if fc is None:
        sys.exit("configs/default.yaml has no forecasting section")
    out = RUNS / "forecast_evaluation"
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    ds = load_dataset(
        cfg,
        fc.history_start_day.isoformat(),
        fc.holdout_period.end_day.isoformat(),
        series=("price", *fc.exogenous_series),
        offline=args.offline or None,
    )
    exog = [(k, lag) for k in fc.exogenous_series for lag in (0, 1)]
    table = build_feature_table(
        ds.frame, cfg, price_lags_days=fc.price_lags_days, exogenous=exog
    )
    actual = ds.frame["price"]

    def log(msg: str) -> None:
        print(f"[{time.perf_counter() - t_start:7.1f} s] {msg}", flush=True)

    sel = select_models(fc, table, cfg.market, actual, progress=log)
    sel.table.to_csv(out / "selection.csv", index=False)
    log(f"chosen: {sel.chosen}")

    metrics, frames = evaluate_holdout(
        fc, sel.chosen, table, cfg.market, actual, progress=log
    )
    metrics.to_csv(out / "holdout_metrics.csv", index=False)

    bench_label = sel.chosen["seasonal_naive"]
    by_year = []
    for label, frame in frames.items():
        years = pd.to_datetime(frame["delivery_day"]).dt.year
        bench = frames[bench_label]
        for year, part in frame.groupby(years):
            b = bench[pd.to_datetime(bench["delivery_day"]).dt.year == year]
            by_year.append(
                {"label": label, "year": int(year), **accuracy(part, b).as_dict()}
            )
    pd.DataFrame(by_year).to_csv(out / "holdout_by_year.csv", index=False)

    dms = []
    for a, b in itertools.permutations(frames, 2):
        dms.append({"model_a": a, "model_b": b, **dm_test(frames[a], frames[b])})
    pd.DataFrame(dms).to_csv(out / "dm_tests.csv", index=False)

    pd.concat(frames.values(), ignore_index=True).to_csv(
        out / "holdout_forecasts.csv", index=False
    )

    meta = {
        "gridflex_version": gridflex.__version__,
        "python": platform.python_version(),
        "config_fingerprint": config_fingerprint(cfg),
        "forecasting_config": fc.model_dump(mode="json"),
        "chosen": sel.chosen,
        "selection_period": fc.selection_period.model_dump(mode="json"),
        "holdout_period": fc.holdout_period.model_dump(mode="json"),
        "data_provenance": ds.provenance(),
        "runtime_s": round(time.perf_counter() - t_start, 1),
    }
    (out / "metadata.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )

    pd.set_option("display.width", 160)
    print(
        "\n=== Selection period",
        fc.selection_period.start_day,
        "->",
        fc.selection_period.end_day,
        "(exclusive) ===",
    )
    print(
        sel.table[["family", "label", "n_days", "mae", "rmse", "bias", "rmae"]]
        .round(3)
        .to_string(index=False)
    )
    print(
        "\n=== Hold-out",
        fc.holdout_period.start_day,
        "->",
        fc.holdout_period.end_day,
        "(exclusive) ===",
    )
    print(
        metrics[
            ["family", "label", "n_days", "n_intervals", "mae", "rmse", "bias", "rmae"]
        ]
        .round(3)
        .to_string(index=False)
    )
    print("\n=== Hold-out by year ===")
    print(
        pd.DataFrame(by_year)[["label", "year", "n_days", "mae", "rmse", "rmae"]]
        .round(3)
        .to_string(index=False)
    )
    print("\n=== Diebold-Mariano (one-sided, H1: B more accurate than A) ===")
    print(pd.DataFrame(dms).round(4).to_string(index=False))
    log(f"outputs in {out}")


if __name__ == "__main__":
    main()
