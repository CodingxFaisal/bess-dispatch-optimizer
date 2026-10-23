# GridFlex

Battery energy storage (BESS) dispatch optimisation and day-ahead market research for the German–Luxembourg (DE-LU) bidding zone.

GridFlex turns price forecasts into physically feasible battery schedules and evaluates them against realised market prices. The goal is that every reported number can be traced to source data, asset configuration, model settings and a stated methodology.

> **Status: under reconstruction (Stage 3 of 9 complete).** This repository is being rebuilt from v0.1.0 (`bess-dispatch-optimizer`) following an audit, [docs/AUDIT.md](docs/AUDIT.md).
>
> **The v0.1.0 headline results are withdrawn.** The "84.5 % foresight capture" of the gradient-boosting strategy relied on PV and wind forecasts that are only published after the day-ahead bidding deadline. Without them the same model captured 70.8 % in an audit rerun, which is below a seasonal-naive forecast. The old outputs are kept, labelled, in [results/legacy_v0.1.0/](results/legacy_v0.1.0/README.md). New results will be published in `docs/RESULTS.md` once the validated pipeline exists (Stages 6–7).
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
| 3 | Battery physics and hand-verifiable tests | **done** ([MATHEMATICAL_MODEL](docs/MATHEMATICAL_MODEL.md) §2, [VALIDATION](docs/VALIDATION.md)) |
| 4 | Optimisation model (PuLP + HiGHS) and solver diagnostics | next |
| 5 | Forecast baselines and leakage tests | |
| 6 | Rolling backtest and settlement | |
| 7 | Financial metrics and sensitivity analysis | |
| 8 | Streamlit UI and exports | |
| 9 | Integration tests, clean-environment reproduction, documentation | |

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
- **Information-availability registry** (`gridflex.data.availability`): it knows what was public at the 12:00 gate closure and raises `InformationLeakageError` otherwise.
- **v0.1 strategies** (perfect foresight, naive, seasonal naive, gradient boosting) still run on top of the new data layer, but they are slated for rewrite in Stages 3–6. The gradient-boosting model no longer uses PV or wind forecasts.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). The v0.1 optimiser uses the CBC solver bundled with PuLP, so no separate solver install is needed. HiGHS arrives with Stage 4.

```bash
uv sync
uv run pytest
```

Exact dependency versions are pinned in `uv.lock`. Pip users can install from the exported `requirements.txt` (runtime) or `requirements-dev.txt` (runtime plus tests and lint).

Other checks:

```bash
uv run ruff check src tests scripts notebooks
uv run mypy
uv run pytest -m network
```

The last command runs live contract checks against smard.de and is opt-in.

## Running the v0.1 pipeline

```bash
uv run python scripts/download_data.py --start 2022-01-01 --end 2025-01-01
uv run python scripts/run_all.py
```

Raw SMARD payloads are cached under `data/cache/`, outputs go to `runs/latest/` and nothing is committed. Each script writes a `data_provenance_<tag>.json` next to its outputs. Add `--offline` to `download_data.py` to work only from the cache. Remember that these are v0.1 strategies; see the status note above. Since Stage 3 the default conventions charge degradation on energy leaving the cells and count cycles against usable energy, so v0.1-strategy numbers differ from the v0.1 release: perfect foresight for 2023–24 drops from 43,782 to 41,816 €/MW-yr. `scripts/check_v01_schedules.py` verifies every day's schedule with the independent physics checker.

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
src/gridflex/optimize/      v0.1 MILP + parameter bridge        (rewrite: Stages 3-4)
src/gridflex/forecast/      v0.1 forecasters                    (rewrite: Stage 5)
src/gridflex/backtest/      v0.1 rolling backtest               (rewrite: Stage 6)
src/gridflex/analysis/      v0.1 metrics and plots              (rewrite: Stage 7)
scripts/                    CLI entry points; capture_smard_fixtures.py
tests/                      unit, property and offline fixture tests; data/ for the data layer
docs/                       AUDIT, DATA_DICTIONARY, MATHEMATICAL_MODEL, VALIDATION (more per stage)
results/legacy_v0.1.0/      superseded v0.1 outputs, kept for traceability
```

## Licence

MIT, see [LICENSE](LICENSE). Data remains subject to its own licence (CC BY 4.0, SMARD.de).

## Author

**Mohammad Faisal**, M.Sc. Power Engineering (Renewable Energy). GitHub: [CodingxFaisal](https://github.com/CodingxFaisal).
