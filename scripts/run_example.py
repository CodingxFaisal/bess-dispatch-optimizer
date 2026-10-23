"""One-command minimal end-to-end example (offline, a few seconds).

    uv run python scripts/run_example.py [--out runs/example]

Uses only the committed SMARD fixture (tests/fixtures/smard_cache): validated
data, leakage-guarded forecasts, HiGHS dispatch, verified settlement,
financial metrics and a sensitivity sweep. Then view it with

    GRIDFLEX_RUN_DIR=runs/example uv run streamlit run app/streamlit_app.py

The output is a demonstration of the workflow on 9 delivery days, not a
research result (see docs/RESULTS.md for those).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from _common import CONFIG, REPO_ROOT
from gridflex.config import load_config
from gridflex.reporting.example import build_example_run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO_ROOT / "runs" / "example"))
    args = ap.parse_args()
    t0 = time.perf_counter()
    out = build_example_run(
        load_config(CONFIG),
        REPO_ROOT / "tests" / "fixtures" / "smard_cache",
        Path(args.out),
    )
    summary = pd.read_csv(out / "backtest" / "finance" / "summary.csv")
    pd.set_option("display.width", 200)
    print(
        summary[
            [
                "strategy",
                "days",
                "net_operating_revenue_eur",
                "efc_per_day",
                "capture_ratio",
            ]
        ]
        .round(3)
        .to_string(index=False)
    )
    print(f"example run written to {out} in {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
