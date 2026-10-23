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
uv run ruff check src tests scripts notebooks
uv run ruff format --check src tests scripts notebooks
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

## 4. v0.1 strategies

These are retained only until Stages 3–6 replace them; their results are not to be quoted. See README.

```bash
uv run python scripts/run_all.py
```

Outputs, including `data_provenance_*.json`, are written to `runs/latest/`.

Diagnostics added in Stages 3–4 (they need cached data; add `--offline` to skip downloads):

```bash
uv run python scripts/check_v01_schedules.py --offline
uv run python scripts/compare_optimizers.py --offline
```

- `check_v01_schedules.py`: every v0.1 schedule vs the independent physics checker.
- `compare_optimizers.py`: the Stage 4 HiGHS model vs the v0.1 MILP, day by day, with solve reports in `runs/latest/optimizer_comparison.csv`.

## Troubleshooting

- **Windows: missing modules after install** (for example `No module named 'sklearn.metrics._pairwise_distances_reduction...'` or `pydantic`). The project or virtual-environment path is too long for Windows' 260-character limit, and some package files were not written. Use a shorter path (such as `C:\src\gridflex`) or enable Windows long-path support, then reinstall.
- **`MarketRegimeError`.** The requested days cross 2025-10-01. Split the window at the switch to 15-minute products.
- **`DataValidationError`.** The message lists each failing check. Nothing is filled automatically; investigate the source data, or rerun with `--refresh` if a cached chunk might be outdated.

## 5. Regenerating the test fixture

Only needed if the fixture must change; this downloads from SMARD.

```bash
uv run python scripts/capture_smard_fixtures.py
```
