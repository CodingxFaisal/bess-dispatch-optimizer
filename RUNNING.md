# Running this project

Step-by-step setup and run instructions, including a VS Code walk-through.

## Prerequisites

- Python 3.10 or newer (developed on 3.13)
- No API keys or accounts. All data is downloaded from the public
  [SMARD.de](https://www.smard.de/en) endpoints on first run and cached locally.

## 1. Get the code and install dependencies

```bash
git clone https://github.com/CodingxFaisal/bess-dispatch-optimizer.git
cd bess-dispatch-optimizer
```

Create a virtual environment (recommended) and install:

```bash
python -m venv .venv
```

Activate it:

- Windows (PowerShell): `.\.venv\Scripts\Activate.ps1`
- Windows (cmd): `.\.venv\Scripts\activate.bat`
- macOS / Linux: `source .venv/bin/activate`

Then:

```bash
pip install -r requirements.txt
```

## 2. Open in VS Code

1. `File -> Open Folder...` and select this project folder.
2. Install the Microsoft **Python** extension if prompted.
3. Select the interpreter: `Ctrl+Shift+P` -> "Python: Select Interpreter" ->
   choose the `.venv` you just created.
4. Open a terminal with `` Ctrl+` ``. It starts in the project root.

You can run any script with the ▶ button in the editor, or from the terminal as
shown below.

## 3. Reproduce everything

This downloads the data once (a few minutes the first time, then cached) and
runs all three stages, writing charts and CSVs to `results/`:

```bash
python scripts/run_all.py
```

## 4. Or run one stage at a time

```bash
python scripts/download_data.py         --start 2022-01-01 --end 2025-01-01
python scripts/run_perfect_foresight.py --start 2022-01-01 --end 2025-01-01
python scripts/run_backtest.py          --data-start 2022-01-01 --start 2023-01-01 --end 2025-01-01
python scripts/run_duration_sweep.py    --start 2023-01-01 --end 2025-01-01
```

The backtest is the slow step (it solves one small MILP per day and retrains the
forecast model periodically), typically a few minutes.

## 5. Run the tests

```bash
pytest
```

Expected: **19 passed**. The tests check energy balance, state-of-charge bounds,
the charge/discharge binary, the cycle cap, and a hand-computed revenue case.

## Outputs

Everything lands in `results/`:

- `capture_ratio.png`, `ml_dispatch_week.png`, `revenue_by_year.png`,
  `perfect_revenue_by_year.png`, `duration_sweep.png`, `tier1_dispatch_week.png`
- `backtest_summary.csv` and per-strategy `daily_*.csv`

## Troubleshooting

- **Import errors running a script directly:** run from the project root. The
  scripts add `src/` to the path themselves, so `python scripts/run_backtest.py`
  works without installing the package.
- **First run is slow:** it is downloading ~3 years of hourly data from SMARD.
  Subsequent runs read the local cache in `data/`.
