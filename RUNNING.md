# Running GridFlex

## Prerequisites

- Python 3.12. `uv` installs it if it is missing.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.4 or newer.
- No API keys. SMARD data is public; the first run downloads it into `data/cache/`.

## 1. Install

```bash
git clone https://github.com/CodingxFaisal/bess-dispatch-optimizer.git
cd bess-dispatch-optimizer
uv sync
```

`uv sync` creates `.venv/` with the exact versions from `uv.lock`. Without uv:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt -e .
```

On macOS/Linux use `.venv/bin/python` instead of `.venv/Scripts/python`.

## 2. Verify

```bash
uv run pytest
uv run ruff check src tests scripts notebooks app
uv run ruff format --check src tests scripts notebooks app
uv run mypy
```

The default test run is fully offline: it uses the committed SMARD fixture. To also check the live SMARD contract (needs internet):

```bash
uv run pytest -m network
```

## 3. Data

```bash
uv run python scripts/download_data.py --start 2022-01-01 --end 2025-01-01
```

The script prints one validation report per series. Any price gap, duplicate or out-of-range value aborts the run; nothing is filled.

Flags:

- `--refresh` re-downloads everything, ignoring the cache policy.
- `--offline` uses only `data/cache/`.

Windows must stay within one market time-unit regime: before 2025-10-01 (hourly) or from 2025-10-01 onward (15-minute).

## 4. Forecast evaluation

```bash
uv run python scripts/download_data.py --start 2021-01-01 --end 2025-10-01
```

```bash
uv run python scripts/evaluate_forecasts.py --offline
```

Selects models on 2023 and scores them once on 2024-01-01 → 2025-09-30. This takes about 6 minutes. Outputs go to `runs/latest/forecast_evaluation/`; results are summarised in `docs/RESULTS.md` §1.

## 5. Backtest

```bash
uv run python scripts/run_backtest.py --offline
```

Runs the strategies in `backtest:` (configs/default.yaml) over the configured period; this takes about 9 minutes for 5 strategies × 639 days. Options:

- `--start` / `--end` to change the period;
- `--strategies a,b` to run a subset;
- `--out` to choose the output directory.

Outputs go to `runs/latest/backtest/`: `daily.csv`, `intervals.csv`, `summary.csv` and `metadata.json`. Results are summarised in `docs/RESULTS.md` §2.

## Troubleshooting

- **Windows: missing modules after install** (for example `No module named 'sklearn.metrics._pairwise_distances_reduction...'` or `pydantic`). The project or virtual-environment path is too long for Windows' 260-character limit, and some package files were not written. Use a shorter path (such as `C:\src\gridflex`) or enable Windows long-path support, then reinstall.
- **`MarketRegimeError`.** The requested days cross 2025-10-01. Split the window at the switch to 15-minute products.
- **`DataValidationError`.** The message lists each failing check. Nothing is filled automatically; investigate the source data, or rerun with `--refresh` if a cached chunk might be outdated.

## 6. Financial metrics and sensitivity

```bash
uv run python scripts/run_finance.py
```

```bash
uv run python scripts/run_sensitivity.py --offline
```

- `run_finance.py` reads `runs/latest/backtest/` and writes `finance/`, which holds the summary, year and month tables, capture tables and `investment.json`. It stops if the backtest's config fingerprint differs from the current config (`--force` overrides).
- Investment metrics stay "not computed" until the `economics.investment` inputs are set in the config.
- `run_sensitivity.py` writes `runs/latest/sensitivity/sensitivity.csv`.

## 7. User interface

```bash
uv run streamlit run app/streamlit_app.py
```

- A read-only viewer of `runs/latest/`. Set another run directory in the sidebar or with the `GRIDFLEX_RUN_DIR` environment variable.
- Pages whose outputs are missing say which script produces them.
- Downloads: CSV tables, the run configuration (YAML) and the run metadata (JSON).

## 8. Regenerating the pip requirement files

After any dependency change, regenerate both files. `tests/test_packaging.py` fails if they fall out of sync.

```bash
uv export --no-hashes --no-dev --no-emit-project --format requirements-txt -o requirements.txt
```

```bash
uv export --no-hashes --no-emit-project --format requirements-txt -o requirements-dev.txt
```

## 9. Regenerating the test fixture

Only needed if the fixture must change; this downloads from SMARD.

```bash
uv run python scripts/capture_smard_fixtures.py
```
