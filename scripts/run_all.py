"""Reproduce every result and chart in the README from scratch.

    python scripts/run_all.py

Downloads the data (once, cached), then runs Tier 1 (perfect foresight),
Tier 2 (forecast-driven backtest) and Tier 3 (duration sweep). Takes a few
minutes on a laptop; the network is only hit on the first run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PY = sys.executable

STEPS = [
    ["download_data.py", "--start", "2022-01-01", "--end", "2025-01-01"],
    ["run_perfect_foresight.py", "--start", "2022-01-01", "--end", "2025-01-01"],
    ["run_backtest.py", "--data-start", "2022-01-01",
     "--start", "2023-01-01", "--end", "2025-01-01", "--retrain-every", "30"],
    ["run_duration_sweep.py", "--start", "2023-01-01", "--end", "2025-01-01"],
]


def main() -> None:
    for step in STEPS:
        print(f"\n{'=' * 70}\n$ python scripts/{' '.join(step)}\n{'=' * 70}")
        subprocess.run([PY, str(SCRIPTS / step[0]), *step[1:]], check=True)


if __name__ == "__main__":
    main()
