# Validation record

Every entry lists the commands actually run and the results observed. Nothing here is projected or assumed.

---

## Stage 2: configuration, data contracts and validation (2026-10-22)

**Environment:**

- Windows 11, CPython 3.12.10, uv 0.11.21.
- Dependencies from `uv.lock`: pydantic 2.14.1, pandas 3.1.0, numpy 2.5.4, PuLP 3.3.2, scikit-learn 1.9.1, pytest 9.1.1.
- Branch `gridflex/stage-2-data-contracts`, uncommitted working tree.

### Automated checks (working tree)

| Command | Result |
|---|---|
| `uv run pytest` | **127 passed**, 5 deselected (network) |
| `uv run pytest -m network` | **5 passed** (live smard.de contract) |
| `uv run pytest --cov=gridflex` | 88 % total line coverage. `config/*` 100 %; `data/*` 90–100 % (timeutils 90, availability 97, cache 97, dataset 97, smard 98, validate 99, others 100); v0.1 `analysis/*` 0 % (rewritten in Stage 7) |
| `uv run ruff check src tests scripts notebooks` | All checks passed |
| `uv run ruff format --check src tests scripts notebooks` | 47 files already formatted |
| `uv run mypy` | Success, 24 source files. Stricter per-module flags apply to `gridflex.config.*` and `gridflex.data.*` |

### Clean-environment checks

The tracked and untracked (non-ignored) files were copied to an empty directory, and both installation routes were run:

| Route | Commands | Result |
|---|---|---|
| uv | `uv sync --locked` → ruff check, ruff format --check, mypy, pytest | all pass; **127 passed** |
| pip | `python -m venv`, `pip install -r requirements-dev.txt -e .`, pytest | **127 passed** |

**Observed pitfall.** The first attempt ran in a directory about 150 characters deep. On Windows without long-path support, scikit-learn's install then failed silently: a compiled module was missing (`_radius_neighbors_classmode`), causing 2 collection errors. With the uv route it showed up as that missing module; with pip, pydantic was not importable at all. Both routes passed from a short path. Documented in RUNNING.md.

This is not yet the spec §15 reproduction: CI on GitHub has not run (no push), and Linux and macOS were not tested.

### Test inventory added in Stage 2

| File | What it checks |
|---|---|
| `tests/test_config_models.py` (30) | default values against hand calculations (√0.86, 120000/7200, SoC MWh bounds); one-way efficiency mode; regime and price-limit lookups at boundary dates; fingerprint stability; frozen models; 21 invalid-config cases; missing/invalid YAML |
| `tests/test_dispatch_params.py` (11) | v0.1 bridge reproduces v0.1 parameters; rejects asymmetric power and non-cyclic policy; invalid-parameter cases |
| `tests/data/test_http.py` (7) | timeout passed through; retry on Timeout, ConnectionError and 503; backoff sequence 1, 2, 4 s; cap; bounded attempts; no retry on 404 or 403; pacing |
| `tests/data/test_cache.py` (7) | exact-byte round trip; sidecar hash; corruption detection; index freshness; chunk finality (null and lag rules); open vs final expiry |
| `tests/data/test_smard_client.py` (19) | 169 h DST chunk boundaries; download with provenance; cache hits; index refresh while final chunks are never refetched; open chunks with nulls refetched after expiry and nulls kept as NaN; refresh; offline with no network; clear offline errors; stale flag; coverage errors; naive or reversed windows; malformed index and chunk payloads rejected and not cached |
| `tests/data/test_clean.py` (5) | identical duplicates collapsed; conflicting duplicates raise; price unchanged; MWh per interval → MW (hour ×1, quarter-hour ×4) with hand values; NaN kept |
| `tests/data/test_validate.py` (15) | complete series OK; 23/25 h days and the 100-interval quarter-hour DST day; missing, null, duplicate, unsorted, off-grid; resolution mismatch both ways; scalar and time-varying bounds; NaN not counted as a bound violation; non-UTC index; severities |
| `tests/data/test_availability.py` (10) | gate-closure instants across CET, CEST and both DST changes; publication instants; usable series = load forecast only; latest available day; leakage errors; guard that the v0.1 ML inputs are public at decision time |
| `tests/data/test_dataset_fixture.py` (10) | real SMARD fixture offline: spring 71 h and autumn 73 h windows (missing and repeated local 02:00); quarter-hour regime with independent raw ×4 check and distinct 15-min prices; regime rejection both ways; uncovered window; unknown series; zero network access; provenance and attribution; injected null price → strict failure, and with `strict=False` NaN is kept, never filled |
| `tests/data/test_smard_live.py` (5, opt-in) | live endpoints: four quarter-hour values sum to the hourly value for 411/125/123/3791; quarter-hour prices repeat the hourly price before the switch |

**Mutation spot-check** (each change reverted afterwards):

| Deliberate fault | Result |
|---|---|
| Skip the MWh → MW conversion | 2 tests fail |
| Treat chunks with nulls as final | 1 test fails |
| Move the PV publication time to 11:00 | 4 tests fail |

### Real-data run through the new data layer

Command: `uv run python scripts/run_all.py` (12 min 23 s; exit 0; outputs in `runs/latest/`, not committed).

- **Validation**, 2022-01-01 → 2024-12-31: price and load forecast **26,304/26,304** valid hourly intervals, 0 errors, all six DST days reported. Price range −500.0 to 936.3 EUR/MWh; 827 negative-price hours.
- **Equivalence with v0.1** (same strategies, same data window):

| Strategy | v0.1 committed | Stage 2 run | |
|---|--:|--:|---|
| Perfect foresight, 2023–24 | 43,782 €/MW-yr | 43,782 €/MW-yr | identical |
| Naive | 30,354 (69.3 %) | 30,354 (69.3 %) | identical |
| Seasonal naive | 32,227 (73.6 %) | 32,227 (73.6 %) | identical |
| Gradient boosting | 37,016 (84.5 %) | **30,989 (70.8 %)** | changed by design: PV/wind inputs removed (D1); equals the Stage 1 audit rerun |
| Perfect foresight 2022–24, by year | 82,924 / 38,514 / 49,170 €/MW | same | identical |
| Duration sweep 1/2/4/8 h | 23,664 / 43,782 / 69,926 / 95,522 €/MW-yr | same | identical |

These figures confirm that the data-layer rewrite did not change the inputs. **They are not GridFlex results.** The v0.1 strategies keep the open defects D2–D4 and D11–D16 until Stages 3–7.

---

## Stage 3: battery physics and hand-verifiable tests (2026-10-23)

**Environment:** as Stage 2 (CPython 3.12.10, `uv.lock` unchanged). Branch `gridflex/stage-3-battery-physics`, uncommitted working tree.

### Automated checks

| Command | Result |
|---|---|
| `uv run pytest` | **193 passed**, 5 deselected (network) |
| `uv run pytest -m network` | 5 passed |
| `uv run pytest --cov=gridflex` | 90 % total. `battery/feasibility.py` 100 %, `battery/model.py` 100 %, `battery/physics.py` 98 %, `optimize/params.py` 99 % |
| `uv run ruff check …` / `ruff format --check …` | all passed / 56 files formatted |
| `uv run mypy` | Success, 28 files (stricter flags also apply to `gridflex.battery.*`) |
| Clean copy, `uv sync --locked` + pytest | 193 passed |

### Tests added in Stage 3

| File | What it checks (expected values derived by hand in the test comments) |
|---|---|
| `tests/battery/test_physics.py` (19) | state equation with $\eta^{ch}=0.9$, $\eta^{dis}=0.8$ over irregular intervals 0.5/0.25/1.0/0.25 h (3.8, 2.8625, 4.6625, 3.4125 MWh); AC/DC throughput, losses, both EFC bases, both degradation bases; energy-balance identity (hand case plus 200 random schedules); RTE applied exactly once (1 MWh in → 0.72 MWh out); lossless reversibility; default-config model; durations from irregular timestamps; 23/24/25 h DST days with the pro-rated cycle limit; quarter-hour intervals; invalid indexes; schedule immutability |
| `tests/battery/test_feasibility.py` (26) | every violation code from a hand case; limits hit exactly vs exceeded; tolerance edges; initial state; cyclic (relative to the horizon start), fixed and free terminal policies; cycle limit under DC/usable vs AC/nameplate (same schedule: 1.25 EFC fails vs 0.8 EFC passes) and over 12 h vs 24 h; availability profile, scalar and override; NaN/inf; zero or negative durations; invalid models (14 cases) |
| `tests/battery/test_v01_cross_check.py` (13) | v0.1 MILP schedules on 12 random price days (half 25 h, many negative prices), under both convention sets, pass the independent checker; v0.1 degradation cost equals the physics value (rel 1e-9); the cycle cap binds at exactly 1.5 EFC (DC/usable) |
| `tests/test_config_battery_conventions.py` (6) | DC defaults; v0.1 bases accepted; `free` requires an explicit terminal value; invalid bases rejected |
| `tests/test_dispatch_params.py` (13, 2 new) | bridge hand values under DC defaults (17.9721 EUR per AC MWh; 25.0388 AC MWh/day); v0.1 conventions reproduce v0.1 (16.667; 30.0); availability derating |

**Defects found by the hand-calculated tests while writing them:**

1. `interval_hours_from_index` first converted the raw `DatetimeIndex.asi8` integers assuming nanoseconds. Under pandas 3 the index can store microseconds, which made durations 1000× too small. 6 tests failed; the code now uses unit-safe `Timedelta` arithmetic.
2. One test expectation was my own arithmetic error (0.8 MW × 1.25 h / 0.8 = 1.25 MWh, not 1 MWh). The test was corrected; the code was right.

**Mutation spot-check** (each change reverted afterwards):

| Deliberate fault | Result |
|---|---|
| Multiply instead of divide by $\eta^{dis}$ in the state equation | 21 tests fail |
| Cycle limit not pro-rated to the horizon length | 9 tests fail |
| Bridge ignores the DC degradation basis | 7 tests fail |

### Real-data check

Command: `uv run python scripts/check_v01_schedules.py --offline --conventions {default|v01}`. This runs the v0.1 perfect-foresight backtest on validated SMARD data for 2023-01-01 → 2024-12-31 and checks every day with the independent checker.

| Conventions | Days feasible (tol 1e-6) | Max per-day SoC gap, MILP vs state equation | 731-day state drift | Mean / max EFC per day | Net revenue (perfect foresight) |
|---|---|---|---|---|---|
| default (DC discharge, EFC on usable) | **731 / 731** | 6.05e-7 MWh | 4.80e-5 MWh | 1.229 / 1.5000 | 837,470 EUR (41,816 €/MW-yr) |
| v0.1 (AC discharge, EFC on nameplate) | **731 / 731** | 5.67e-7 MWh | 4.24e-5 MWh | 1.126 / 1.5000 | 876,837 EUR (43,782 €/MW-yr), identical to v0.1 |

**Interpretation:**

- Under v0.1 conventions the bridge reproduces the v0.1 figure exactly. The default DC conventions lower perfect-foresight net revenue by 4.5 %: degradation is costed per MWh leaving the cells (17.97 instead of 16.67 EUR per AC MWh), and the cap allows 25.04 instead of 30 AC MWh of discharge per day.
- The state drift exists because v0.1 carries the solver-reported SoC between days (D23). It is fixed in Stage 6.
- These are v0.1-strategy diagnostics, **not GridFlex results**.

---

## Stage 4: optimisation model and solver diagnostics (2026-10-23)

**Environment:**

- As Stage 3, plus `highspy` 1.15.1 (HiGHS 1.15.1, githash 04024d7, MIT licence) added to `uv.lock`.
- PuLP 3.3.2; CBC bundled with PuLP.
- Branch `gridflex/stage-4-optimisation`, uncommitted working tree.

### Automated checks

| Command | Result |
|---|---|
| `uv run pytest` | **322 passed**, 5 deselected (network), 0 skipped |
| `uv run pytest -m network` | 5 passed |
| `uv run pytest --cov=gridflex` | 91 % total. `optimization/formulation.py` 100 %, `problem.py` 96 %, `solve.py` 94 % |
| `uv run ruff check …` / `ruff format --check …` | all passed |
| `uv run mypy` | Success, 32 files (stricter flags also apply to `gridflex.optimization.*`) |

### Tests added in Stage 4

| File | What it checks |
|---|---|
| `tests/optimization/test_hand_cases.py` (18) | hand-derived optima: simple arbitrage (900); efficiency (16) and break-even (idle at 27, 0.16 at 28); degradation on DC (7) and AC (8.8) bases; negative prices (50, 42.8, 41); flat prices idle (0, 50 EUR/MWh); flat −30 EUR/MWh earns 22.5 through sequential loss-burning; lossless flat price (0); irregular durations (180); fixed terminal (−10); free terminal valued (+10 / 0) including start-energy value; cycle cap (400 → 50); 25 h DST day cap (100·25/24); asymmetric power (200 / 100); availability profile (100); start energy (100 / 0) |
| `tests/optimization/test_failure_modes.py` (26) | infeasible (HiGHS and CBC); time limit not a success (HiGHS and CBC); D24 reproduced (PuLP "Optimal" on a time-limited incumbent) and not inherited; time-limited acceptance policy (4 cases); solver exception → `error`; real HiGHS `kSolutionLimit` → `error` (D25); corrupted solution → `invalid_solution` with `CHARGE_POWER_LIMIT`; objective mismatch → `invalid_solution`; dropping the binary → simultaneous-operation LP solution (28 EUR) rejected while the MILP idles (0); 11 malformed problems refused before solving, including NaN prices |
| `tests/optimization/test_cross_checks.py` (77) | **40 random problems vs mode enumeration** with SciPy `linprog` (39 compared, 38 with non-zero dispatch, 1 infeasible correctly reported; random η, asymmetric power, irregular Δt, availability, both bases, cycle caps, all terminal policies); HiGHS vs CBC on 8 daily problems (hourly and quarter-hour); equivalence with the v0.1 MILP on 8 days of 23–25 h; deterministic repeat solves; 20 random problems with feasibility, objective decomposition and populated diagnostics (gap ≤ 1e-6, dual bound ≥ objective) |
| `tests/test_config_optimization.py` (8) | defaults; 6 invalid settings rejected; no time limit allowed |

**Mutation spot-check** on the formulation (each reverted afterwards; counts are for `tests/optimization`):

| Deliberate fault | Result |
|---|---|
| Charge efficiency dropped from the SoC balance | 80 tests fail |
| Terminal constraint dropped | 65 tests fail |
| Degradation sign flipped | 79 tests fail |
| Free-terminal term values $E_{end}$ instead of $E_{end} - E_{start}$ | 24 tests fail |

**Expectation corrected while writing tests.** I had expected a flat −30 EUR/MWh price to leave the battery idle. HiGHS found 22.5 EUR. The hand derivation confirms it: at negative prices, losses are paid consumption, earning 7.5 EUR per sequential cycle. The test now asserts 22.5 and that no interval charges and discharges at once.

### Solver behaviour probes

| Case | HiGHS | CBC (via PuLP) |
|---|---|---|
| 2160 h, 0.01 s limit | time_limit_no_solution | time_limit_no_solution |
| 8760 h, 0.5 s limit | time_limit_feasible (incumbent obj 0, idle), **rejected** | time_limit_no_solution |
| 8760 h, 60 s limit | optimal in 8.0 s | "Optimal", but **invalid_solution**: SoC up to 5.3e-6 MWh above the maximum; terminal and cycle-limit round-off |
| same, tolerance 1e-5 | – | accepted; objective within 1.2e-3 EUR of HiGHS |

### Real-data comparison

Command: `uv run python scripts/compare_optimizers.py --offline [--solver cbc]`. Perfect foresight, local delivery days 2023-01-01 → 2024-12-31, daily cyclic terminal policy. The new model starts each day from the physically carried state.

| Solver (new model) | Status | Total objective, new | Total objective, v0.1 MILP | Max per-day difference | MIP gap (max) | Runtime, new | Runtime, v0.1 | Carried start-energy range |
|---|---|---|---|---|---|---|---|---|
| HiGHS | **731/731 optimal** (2 × 23 h, 727 × 24 h, 2 × 25 h) | 837,469.67 EUR | 837,469.67 EUR | 3.0e-5 EUR | 6.8e-16 | 11.3 s | 65.2 s | exactly 10.000000000 MWh |
| CBC | 731/731 optimal | 837,469.56 EUR | 837,469.67 EUR | 1.9e-3 EUR | n/a | 63.6 s | 63.6 s | 9.999999888 – 10.000017664 MWh |

**Interpretation:**

- The new formulation reproduces the v0.1 optimum day by day under identical conventions. HiGHS is about 6× faster than CBC here.
- With HiGHS, carrying the physical state introduces no drift. With CBC, the round-off in its solutions makes the carried state wander by up to 1.8e-5 MWh, which shifts the two-year total by 0.11 EUR. This is why HiGHS is the default.
- These are solver diagnostics, **not strategy results**.

---

## Stage 5: forecast baselines and leakage tests (2026-10-23)

**Environment:** as Stage 4 (no new dependencies; scikit-learn 1.9.1 and SciPy from `uv.lock`). Branch `gridflex/stage-5-forecasting`, uncommitted working tree.

### Automated checks

| Command | Result |
|---|---|
| `uv run pytest` | **362 passed**, 5 deselected (network) |
| `uv run pytest --cov=gridflex` | 92 % total. `forecasting/models.py` 99 %, `evaluate.py` 97 %, `features.py` 94 %, `base.py` 94 % |
| `uv run ruff check …` / `ruff format --check …` | all passed |
| `uv run mypy` | Success, 37 files (stricter flags also apply to `gridflex.forecasting.*`) |

### Tests added in Stage 5

| File | What it checks |
|---|---|
| `tests/forecasting/test_leakage.py` (16) | **End-to-end:** for all 4 models, corrupting every value published after the origin (hand-counted 696 values: prices of 15–29 Apr, load forecasts of 16–29 Apr) leaves the forecast bit-for-bit identical. Control: corrupting public D−1 data does change it. Also: PV forecast for D raises (lagged by a day it passes); same-day price feature raises; publication boundary exact to the minute; training rows on or after D raise; training targets published after the origin raise; a model subclass with a leaky training selection is stopped; default training rows are public and earlier, with metadata cutoffs ≤ origin (including DST days); LEAR preprocessing statistics equal hand formulas on the training rows and are unchanged by prediction; missing inputs raise, never filled; insufficient history raises |
| `tests/forecasting/test_features_models.py` (16) | wall-clock lags hand-checked on an ordinary day, the spring gap (24 h fallback), out of the spring day, and both 02:00 hours of the autumn day; calendar features; previous-day statistics over a 23 h day; publication instants (hand UTC values); incomplete previous day gives NaN statistics; quarter-hour interval index 0–95; naive forecasts equal lagged prices; trained models within 30 % of the theoretical noise floor 4·√(2/π) and rMAE < 0.85 on synthetic data; recalibration every 7 days (fits on days 0, 7, 14); GBR deterministic; model version tracks configuration; forecast record validation; accuracy metrics by hand (MAE 2, RMSE √5, bias −1, rMAE 1); DM test direction; period days |
| `tests/forecasting/test_protocol.py` (8) | default protocol; 6 invalid protocols rejected (overlap, history after selection, unknown series, missing naive lag, reversed period, too-short window); end-to-end selection then hold-out on synthetic data: argmin choice, disjoint days, origin before delivery, benchmark rMAE = 1 |

**Mutation spot-check** (each change reverted afterwards; counts are for `tests/forecasting`):

| Deliberate fault | Result |
|---|---|
| Prediction guard disabled | 3 tests fail |
| Training allowed on day D with both training guards disabled | 11 tests fail, including the end-to-end corruption test, which catches it independently |
| Lags by absolute 24 h instead of wall-clock | 2 tests fail |

**Expectations corrected while writing tests:**

- A model-quality threshold (rMAE < 0.6) was unjustified for synthetic data with N(0, 4) noise. It was replaced by a bound derived from the theoretical MAE floor $4\sqrt{2/\pi}$ = 3.19.
- A corruption-size check ("> 1000") was replaced by the exact hand count of 696.

### Real-data evaluation

Command: `uv run python scripts/evaluate_forecasts.py --offline` (375 s).

- Data: 2021-01-01 → 2025-09-30 hourly, 41,615/41,615 valid intervals.
- Selection: 2023, 9 candidates.
- Hold-out: 2024-01-01 → 2025-09-30, 639 days, 15,335 intervals. Hand check: 639 × 24 − 1, for the 23 h day on 2025-03-30.

Results are in [RESULTS.md §1](RESULTS.md).

**Post-run checks on the outputs:**

- 61,340 forecast rows (4 × 15,335).
- On every row, origin < delivery interval, `feature_cutoff_utc` ≤ origin and `training_data_cutoff_utc` ≤ origin.
- Derived figures in RESULTS were recomputed from the CSVs.

**Rerun on final code.** Lint-only edits were made to `features.py` while the first run was in progress. The evaluation was rerun on the final code and its outputs compared with the first run: `selection.csv`, `holdout_metrics.csv` and all 61,340 rows of `holdout_forecasts.csv` are **identical** (max abs numeric difference 0.0; rerun 398 s).

---

## Stage 6: rolling backtest and settlement (2026-10-23)

**Environment:** as Stage 5; `seaborn` removed from the dependencies. Branch `gridflex/stage-6-backtest`, uncommitted working tree.

**Retired v0.1 code.** `gridflex.optimize`, `gridflex.forecast`, `gridflex.backtest` (v0.1 `rolling.py`), `gridflex.analysis`, the v0.1 scripts and their 47 tests have been removed. Their equivalence with the new modules was recorded in the Stage 3–4 sections above before removal. Archived outputs remain in `results/legacy_v0.1.0/`.

### Automated checks

| Command | Result |
|---|---|
| `uv run pytest` | **411 passed**, 5 deselected (network); also in a clean copy installed with `uv sync --locked` |
| `uv run pytest --cov=gridflex` | 97 % total (v0.1 modules removed). `backtest/heuristic.py` 100 %, `engine.py` 98 %, `settlement/settle.py` 98 %, `strategies.py` 91 %, `reporting/metadata.py` 86 % |
| `uv run ruff check …` / `ruff format --check …` | all passed |
| `uv run mypy` | Success, 35 files (stricter flags also apply to `settlement`, `backtest`, `reporting`) |

### Tests added in Stage 6

| File | What it checks |
|---|---|
| `tests/test_settlement.py` (9) | hand settlement: purchases 40, sales 90, gross 50, DC degradation 22.5, trading cost 1.9, end energy 1.55 MWh; negative-price purchase is income; idle = 0; free terminal value reported separately (36 EUR); expected vs realised differ only by price (14.4); invalid prices or fees rejected |
| `tests/optimization/test_hand_cases.py` (+1) | trading cost in the optimiser: 16 − 2 × 1.72 = 12.56; idle above the break-even fee of 9.30 |
| `tests/backtest/test_heuristic.py` (67) | hand cases: charge-first (140), discharge-first with losses (80 − 100/9), idle cases, costs making it idle, cycle cap (Q = 1 MWh → 100), irregular durations (180); **60 random batteries and price days**: always feasible and cyclic, value ≥ 0 and equal to its settlement at plan prices |
| `tests/backtest/test_engine.py` (16) | non-benchmark strategies receive `None`, never realised prices; perfect foresight equals a direct HiGHS solve per day and expected = realised; settlement reconciles with the interval export; failed forecast days (hand boundary: first forecastable day 22 Mar) are idle, zero-revenue, flagged, and `abort` stops the run; a physically invalid schedule is rejected, not settled; physical state carried across days (10 + k·0.92736 MWh); deterministic runs with metadata; perfect foresight is a daily upper bound; strategy-spec validation; heuristic requires the cyclic policy; availability derating |
| `tests/backtest/test_fixture_end_to_end.py` (3) | real SMARD fixture, 2024-03-30 → 2024-04-07 (includes the 23 h day): regression totals, determinism, perfect-foresight upper bound, cyclic end energy, reconciliation with the interval export, CC BY 4.0 provenance |

**Mutation spot-check** on `engine.py` (each reverted afterwards; counts are for `tests/backtest` and `tests/test_settlement.py`):

| Deliberate fault | Result |
|---|---|
| State not carried between days | 1 test fails |
| Realised prices passed to every strategy | 4 tests fail, 3 error |
| Rejected schedule settled anyway | 1 test fails |

**Expectation corrected while writing tests:**

- My first failed-day test window ended before any forecastable day. The hand boundary (22 Mar) is now asserted exactly.
- Wall-clock solver runtime is excluded from determinism checks.

### Real-data backtest

Command: `uv run python scripts/run_backtest.py --offline`: 5 strategies × 639 days, 0 failed days, 521 s. Results are in [RESULTS.md §2](RESULTS.md).

**Checks on the outputs:**

- A rerun on the final code reproduced `summary.csv` exactly (max abs difference 0.0, excluding runtime). An earlier run made before two test-only fixes gave identical numbers.
- 3,195 daily rows (5 × 639) and 76,675 interval rows (5 × 15,335).
- The mean daily forecast MAE in the backtest (GBR 22.19, Lasso 22.45, persistence 27.07 EUR/MWh) agrees with the Stage 5 hold-out (22.18, 22.44, 27.07). The small differences come from averaging per day rather than per interval.
- The identical throughput of persistence + MILP and perfect foresight was investigated:
  - the persistence schedule for day D equals the perfect-foresight schedule for D−1 on 632/638 days; the 6 exceptions are the clock-change days and the day after each, where a 24-hour shift isn't the same wall-clock time;
  - the two boundary days are both at the 25.04 MWh daily cap.
  
  It is structural, not a defect.
