# GridFlex

Battery energy storage (BESS) dispatch optimisation and day-ahead market research for the German–Luxembourg (DE-LU) bidding zone.

GridFlex turns price forecasts into physically feasible battery schedules and evaluates them against realised market prices. The goal is that every reported number can be traced to source data, asset configuration, model settings and a stated methodology.

> **Status: all nine rebuild stages complete (v0.2.0.dev0).** The repository was rebuilt from v0.1.0 (`bess-dispatch-optimizer`) following an audit, [docs/AUDIT.md](docs/AUDIT.md). CI is configured but has not yet run on GitHub (the branches are not pushed); see [docs/LIMITATIONS.md](docs/LIMITATIONS.md) §6.
>
> **The v0.1.0 headline results are withdrawn.** The "84.5 % foresight capture" of the gradient-boosting strategy relied on PV and wind forecasts that are only published after the day-ahead bidding deadline. Without them the same model captured 70.8 % in an audit rerun, which is below a seasonal-naive forecast. The old outputs are kept, labelled, in [results/legacy_v0.1.0/](results/legacy_v0.1.0/README.md). Results from the rebuilt, validated pipeline are in [docs/RESULTS.md](docs/RESULTS.md).
>
> Historical backtest revenue is not a forecast of future revenue.

---

## Research questions

1. How much does a forecast-driven battery strategy earn under realistic constraints?
2. How much value is lost relative to a perfect-foresight upper benchmark?
3. How do power, energy capacity, efficiency, degradation and cycling limits affect results?
4. Does uncertainty-aware optimisation improve realised performance?
5. Which assumptions materially change the financial conclusions?

## Stage plan

| Stage | Scope | State |
|---|---|---|
| 1 | Audit and implementation plan | done ([AUDIT](docs/AUDIT.md)) |
| 2 | Configuration, data contracts, validation | done ([DATA_DICTIONARY](docs/DATA_DICTIONARY.md)) |
| 3 | Battery physics and hand-verifiable tests | done ([MATHEMATICAL_MODEL](docs/MATHEMATICAL_MODEL.md) §2) |
| 4 | Optimisation model (PuLP + HiGHS) and solver diagnostics | done ([MATHEMATICAL_MODEL](docs/MATHEMATICAL_MODEL.md) §3) |
| 5 | Forecast baselines and leakage tests | done ([BACKTEST_METHODOLOGY](docs/BACKTEST_METHODOLOGY.md) §1, [RESULTS](docs/RESULTS.md) §1) |
| 6 | Rolling backtest and settlement | done ([BACKTEST_METHODOLOGY](docs/BACKTEST_METHODOLOGY.md) §2, [RESULTS](docs/RESULTS.md) §2) |
| 7 | Financial metrics and sensitivity analysis | done ([RESULTS](docs/RESULTS.md) §3) |
| 8 | Streamlit UI and exports | done ([VALIDATION](docs/VALIDATION.md)) |
| 9 | Integration tests, clean-environment reproduction, documentation | done ([VALIDATION](docs/VALIDATION.md), [ARCHITECTURE](docs/ARCHITECTURE.md), [LIMITATIONS](docs/LIMITATIONS.md)) |

## Key results (hold-out 2024-01-01 → 2025-09-30, 639 days)

Each figure comes from a recorded run; the linked section gives the command, code version and full table.

- **Forecast accuracy** ([RESULTS §1.2](docs/RESULTS.md)): GBR MAE 22.18 EUR/MWh (rMAE 0.682), LEAR-style Lasso 22.44 (0.690), persistence 27.07, seasonal naive 32.55. GBR and Lasso are statistically indistinguishable (DM p = 0.30).
- **Dispatch** ([RESULTS §2.1](docs/RESULTS.md)), 10 MW / 20 MWh, net operating revenue over 639 days:
  - perfect foresight 942,903 EUR (benchmark, not deployable);
  - Lasso + MILP 790,226 EUR (83.8 % of the benchmark);
  - GBR + MILP 781,601 EUR (82.9 %);
  - persistence + MILP 712,570 EUR (75.6 %);
  - persistence + two-window heuristic 407,114 EUR (43.2 %).
- **Normalised** ([RESULTS §3.2](docs/RESULTS.md)): GBR + MILP 44,645 EUR/MW-yr on a 365-day basis; perfect foresight 53,859 EUR/MW-yr.
- **Sensitivity** ([RESULTS §3.3](docs/RESULTS.md)): duration and degradation cost dominate. The degradation cost is an unsourced placeholder, so absolute figures should not be relied on.

These are net operating revenue, not profit, and do not predict future revenue ([LIMITATIONS](docs/LIMITATIONS.md)). The from-scratch clean-environment reproduction is recorded in [VALIDATION](docs/VALIDATION.md) (Stage 9).

## What exists now

- **Validated configuration** (`gridflex.config`, [configs/default.yaml](configs/default.yaml)):
  - pydantic models with units in the field names;
  - one-way efficiencies (or a round-trip figure split once, never applied twice);
  - explicit conventions for degradation, cycle counting and terminal energy;
  - dated market rules: 60-minute day-ahead MTU until 2025-09-30 and 15-minute from 2025-10-01, plus harmonised price limits;
  - the SMARD series catalogue with publication-time rules.
- **SMARD data layer** (`gridflex.data`):
  - timeouts, bounded retries and request pacing;
  - a raw cache that keeps payloads byte-for-byte with provenance sidecars, under a documented invalidation policy;
  - an offline mode;
  - conversion from SMARD's "MWh per interval" to mean MW;
  - UTC identity with Europe/Berlin calendar handling (23 h and 25 h days);
  - validation that reports gaps, nulls, duplicates, off-grid timestamps, resolution mismatches and out-of-range values, and **never fills or invents data**.
- **Battery physics** (`gridflex.battery`):
  - the state equation with per-interval durations (handles 23/25 h days and 15-minute intervals);
  - AC/DC throughput accounting with an energy-balance identity;
  - equivalent full cycles on a stated basis (default: energy leaving the cells over the usable window);
  - degradation cost on DC or AC discharge;
  - cyclic, fixed or explicitly valued free terminal policies;
  - availability derating;
  - an independent feasibility checker that verifies any schedule against all physical limits.
- **Dispatch optimisation** (`gridflex.optimization`):
  - a price-taking day-ahead MILP built in PuLP and solved with HiGHS;
  - per-interval durations, asymmetric power, availability profiles, cycle caps, all three terminal policies, and binary charge/discharge exclusivity;
  - a `SolveReport` with status, termination reason, objective, dual bound, MIP gap, runtime and solver version;
  - no schedule is returned unless optimality is proven *and* the independent physics check and objective recomputation both pass.
- **Price forecasting** (`gridflex.forecasting`):
  - persistence, seasonal-naive, LEAR-style Lasso and gradient-boosting models;
  - forecasts are issued at the 12:00 gate closure, with every input's publication time tracked and leakage guards that raise;
  - forecast records carry their origin, cutoffs, model version and config;
  - models are selected on 2023 and evaluated once on a hold-out (2024 to Sep 2025). Results: [RESULTS §1](docs/RESULTS.md).
- **Rolling backtest and settlement** (`gridflex.backtest`, `gridflex.settlement`):
  - the strategy is day-ahead only, with a full-day commitment made at D−1 12:00;
  - it compares perfect foresight, forecast-driven MILP strategies and a two-window heuristic under identical assumptions;
  - failed or invalid days are flagged and never settled as optimised;
  - the charge level is carried between days physically;
  - every run records its metadata (git commit, config fingerprint, package versions).

  Results: [RESULTS §2](docs/RESULTS.md).
- **Financial metrics and sensitivity** (`gridflex.finance`):
  - annualised (365-day basis) and normalised revenue, period tables flagged when incomplete, and a forecast capture ratio with a precise definition;
  - payback and NPV only when every input is configured;
  - one-at-a-time sensitivity to power, duration, efficiency, degradation cost and cycle cap.

  Results: [RESULTS §3](docs/RESULTS.md).
- **Streamlit UI** (`app/`): a read-only viewer of saved runs with six pages: overview, market data, forecast evaluation, dispatch, financial analysis and experiment comparison.
  - Plotly charts state their units, time range and timezone.
  - CSV, configuration (YAML) and run-metadata (JSON) downloads are available.
  - The UI contains no modelling code (enforced by a test).
- **Information-availability registry** (`gridflex.data.availability`): it knows what was public at the 12:00 gate closure and raises `InformationLeakageError` otherwise.

## Quick start: one-command example

```bash
uv sync
```

```bash
uv run python scripts/run_example.py
```

This runs the whole pipeline offline in a few seconds on the committed SMARD fixture (9 delivery days around the March 2024 clock change): validated data, leakage-guarded forecasts, HiGHS dispatch with independent verification, settlement, financial metrics and a sensitivity sweep. It writes `runs/example/`, which you can open in the viewer:

```bash
GRIDFLEX_RUN_DIR=runs/example uv run streamlit run app/streamlit_app.py
```

The example demonstrates the workflow; its numbers are not research results. The same command is exercised by the integration test (`tests/test_integration.py`).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Both solvers install with the Python dependencies, with no system install: HiGHS (MIT licence, via `highspy`), the default, and CBC (EPL-2.0, bundled with PuLP), available as `optimization.solver: cbc`.

```bash
uv sync
uv run pytest
```

Exact dependency versions are pinned in `uv.lock`. Pip users can install from the exported `requirements.txt` (runtime) or `requirements-dev.txt` (runtime plus tests and lint).

Other checks:

```bash
uv run ruff check src tests scripts notebooks app
uv run mypy
uv run pytest -m network
```

The last command runs live contract checks against smard.de and is opt-in.

## Running the pipeline

```bash
uv run python scripts/download_data.py --start 2021-01-01 --end 2025-10-01
```

```bash
uv run python scripts/evaluate_forecasts.py --offline
```

```bash
uv run python scripts/run_backtest.py --offline
```

```bash
uv run python scripts/run_finance.py
```

```bash
uv run python scripts/run_sensitivity.py --offline
```

1. The first command downloads and validates the SMARD data. Raw payloads are cached under `data/cache/`, and every run records its provenance.
2. The second selects forecast models on 2023 and scores them once on the hold-out, 2024 to Sep 2025.
3. The third backtests the configured strategies over the hold-out.
4. The fourth computes financial metrics from that backtest; it refuses runs made with a different config.
5. The fifth runs the one-at-a-time sensitivity analysis (8–15 minutes).

Then open the viewer:

```bash
uv run streamlit run app/streamlit_app.py
```

It reads `runs/latest/` by default; change this in the sidebar or with `GRIDFLEX_RUN_DIR`.

Outputs go to `runs/latest/` (git-ignored), each with `metadata.json`. See [RUNNING.md](RUNNING.md) for details.

## Documentation

| Document | Contents |
|---|---|
| [RUNNING.md](RUNNING.md) | Installation, full historical runs, troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, packages, correctness boundaries, technology |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | Series, units, timestamps, publication times, validation |
| [docs/MATHEMATICAL_MODEL.md](docs/MATHEMATICAL_MODEL.md) | Battery physics and the dispatch MILP |
| [docs/BACKTEST_METHODOLOGY.md](docs/BACKTEST_METHODOLOGY.md) | Forecast protocol, rolling backtest, settlement |
| [docs/RESULTS.md](docs/RESULTS.md) | Forecast accuracy, dispatch backtest, finance, sensitivity |
| [docs/VALIDATION.md](docs/VALIDATION.md) | Test results and clean-environment checks per stage |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What is not modelled and how it affects results |
| [docs/AUDIT.md](docs/AUDIT.md) | v0.1 audit, defect register, resolution, definition of done |

## Data and attribution

Market data: **Bundesnetzagentur | SMARD.de**, licensed **CC BY 4.0**. The series used are the DE-LU day-ahead price (4169) and the day-ahead forecasts of load (411), PV (125), onshore wind (123) and offshore wind (3791). Units, timestamps, coverage, publication times and limitations are documented in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

Only the load forecast is published before the day-ahead gate closure. PV and wind day-ahead forecasts appear around 18:00 on D−1 (ENTSO-E art. 14.1(d)) and are therefore not used as bidding inputs.

The test fixture in `tests/fixtures/smard_cache/` is an unmodified SMARD extract under the same licence.

## Methodological references

No code is copied from these projects:

- [PyPSA](https://github.com/PyPSA/PyPSA) (MIT): storage state-of-charge formulation and per-snapshot weighting as a modelling reference.
- [epftoolbox](https://github.com/jeslago/epftoolbox) (Apache-2.0): LEAR benchmark and forecast-evaluation methodology (Lago et al., 2021, *Applied Energy* 293, 116983) as a reference for Stage 5.

## Repository layout

```
configs/default.yaml        all physical, market, data and economic assumptions
src/gridflex/config/        pydantic models, loader, config fingerprint
src/gridflex/data/          SMARD client, raw cache, cleaning, validation, availability, datasets
src/gridflex/battery/       physics: state equation, throughput/EFC, degradation, feasibility checker
src/gridflex/optimization/  Stage 4 MILP (PuLP + HiGHS), solve reports, verification
src/gridflex/forecasting/   Stage 5 forecasters, feature table with publication times, evaluation
src/gridflex/settlement/    settlement of committed schedules (realised and expected)
src/gridflex/backtest/      rolling day-ahead engine, strategies, two-window heuristic
src/gridflex/reporting/     run metadata, run export/loading, Plotly figures, offline example
src/gridflex/finance/       financial metrics, investment metrics, sensitivity
scripts/                    CLI entry points; capture_smard_fixtures.py
app/                        Streamlit viewer (streamlit_app.py + app_pages/), read-only
tests/                      unit, property, offline fixture and integration tests
docs/                       AUDIT, ARCHITECTURE, DATA_DICTIONARY, MATHEMATICAL_MODEL,
                            BACKTEST_METHODOLOGY, RESULTS, VALIDATION, LIMITATIONS
results/legacy_v0.1.0/      superseded v0.1 outputs, kept for traceability
```

## Licence

MIT, see [LICENSE](LICENSE). Data remains subject to its own licence (CC BY 4.0, SMARD.de).

Main dependencies and their licences: PuLP (MIT), HiGHS / `highspy` (MIT), CBC (EPL-2.0, bundled with PuLP), pandas, NumPy, SciPy and scikit-learn (BSD-3), pydantic and PyYAML (MIT), Streamlit (Apache-2.0), Plotly (MIT). Exact versions are in `uv.lock`.

## Author

**Mohammad Faisal**, M.Sc. Power Engineering (Renewable Energy). GitHub: [CodingxFaisal](https://github.com/CodingxFaisal).
