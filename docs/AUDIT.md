# GridFlex — Stage 1 Repository Audit

**Subject:** `CodingxFaisal/bess-dispatch-optimizer` @ `078d3f4` (3 commits, branch `main`)
**Audit date:** 2026-10-22
**Status:** Approved 2026-10-22 with all recommended decisions in §14. Sections 0–14 are the audit as approved; §15 records findings made during later stages. The defect IDs (D1–D26) are referenced from code comments and later docs.

---

## 0. Evidence base

Everything below comes from files I read or commands I ran. Where something could not be verified, the text says so.

| What | How |
|---|---|
| Repository | Cloned read-only into a scratch directory. All 41 tracked files read: source, config, tests, scripts, notebook, README, RUNNING, LICENSE, results CSVs. No `.github/` workflows exist. |
| Sister repo | `CodingxFaisal/power-spread-trader` @ `c8205b9`, cloned. Only its SMARD client was reviewed in depth. |
| References | `PyPSA/PyPSA` (shallow clone; `LICENSE` = MIT; `define_storage_unit_constraints` read). `jeslago/epftoolbox` (shallow clone; `LICENSE` = **Apache-2.0**; LEAR model and evaluation modules listed and read). |
| Environment | Windows 11, CPython 3.12.10, fresh venv, `pip install -r requirements.txt` with unpinned latest: numpy 2.5.4, pandas 3.1.0, PuLP 3.3.2 (bundled CBC), scikit-learn 1.9.1, pytest 9.1.1. |
| Test run | `pytest --cov=bess` → **19 passed**, 7 warnings (pandas 4 deprecations), **57 % line coverage**. |
| Lint / types | `ruff check --select E,F,W,I,B,UP` → 23 findings. `mypy src` → 1 error. |
| Live data probes | SMARD chart-data endpoints queried 2026-10-22 ~13:50 UTC for series 4169, 411, 125, 123, 3791 at `hour` and `quarterhour` resolution. |
| Defect probes | Small scripts that exercise suspected defects against the unmodified code (§7). |
| Independent check | 30 random 6-hour cases. Each enumerates all 2⁶ charge/discharge modes and solves every LP with SciPy/HiGHS, then compares the best result with the repo's PuLP/CBC MILP objective. |
| Reproduction | `python scripts/run_all.py` run end-to-end in the scratch clone (§6). |
| Regulation | ENTSO-E Transparency Platform definition of article 14.1.D (day-ahead wind/solar forecast). SMARD data-use statement. |

---

## 1. Current architecture

```
config/battery.yaml ──► bess.config.BatteryConfig (frozen dataclass, derived η, E bounds, c_deg)
                               │
SMARD chart_data API ─► bess.data.smard.fetch_series / build_dataset ─► data/raw/*.csv, data/processed/*.csv
                               │
                     bess.forecast.models  (Perfect, Naive24h, SeasonalNaive168h, HistGBR)
                               │ forecast(dataset, start, end) -> Series
                     bess.backtest.rolling.run_backtest  (one MILP per local day, settle on actual)
                               │
                     bess.optimize.dispatch  (PuLP MILP + settle())
                               │
                     bess.analysis.metrics / plots  ─► results/*.csv, *.png
scripts/*.py  = CLI orchestration (download, tier 1, tier 2, duration sweep, run_all)
```

| Module | Responsibility | Lines | Coverage |
|---|---|--:|--:|
| `config.py` | YAML → typed config, validation, derived quantities | 148 | 97 % |
| `data/smard.py` | HTTP fetch, weekly-chunk cache, dataset assembly, imputation | 186 | **0 %** |
| `forecast/models.py` | 4 forecasters, feature builder, walk-forward GBR | 207 | 93 % |
| `optimize/dispatch.py` | MILP formulation, solve, settlement | 216 | 97 % |
| `backtest/rolling.py` | Daily rolling loop, planned vs settled accounting | 137 | 89 % |
| `analysis/metrics.py` | Annualisation, capture ratio, payback, yearly split | 84 | **0 %** |
| `analysis/plots.py` | Matplotlib/Seaborn charts | 157 | **0 %** |

The structure is clean and small, and the code reads well. Physics, optimisation, forecasting, and backtest are already in separate modules, and there is no UI. The main structural gaps are: no data-validation layer; no forecast metadata object; no solver report object; and run metadata is never recorded.

---

## 2. Optimisation model (as implemented in `optimize/dispatch.py`)

**Variables** (per interval *t*, `dt = cfg.delta_t_hours` = 1 h, fixed):
`p_ch[t], p_dis[t] ∈ [0, P]` MW; `e[t] ∈ [E_min, E_max]` MWh (state *after* interval t); `u[t] ∈ {0,1}`.

**Objective** (maximise): `Σ_t price[t]·(p_dis[t] − p_ch[t])·dt − c_deg·p_dis[t]·dt`

**Constraints:**
- `e[t] = e[t−1] + η_ch·p_ch[t]·dt − p_dis[t]·dt/η_dis`, with `e[−1] = e_start`
- `p_ch[t] ≤ P·u[t]`, `p_dis[t] ≤ P·(1 − u[t])`
- `Σ p_dis·dt ≤ 1.5 · E_nameplate · (T·dt/24)` (discharge throughput cap)
- `e[T−1] = e_start` (daily cyclic terminal SoC)

**Parameters** (`config/battery.yaml`): 10 MW / 20 MWh; RTE 0.86 split as η_ch = η_dis = √0.86 = 0.9274 (one-way, applied once each, **not double-counted**); SoC window 5–95 % (1–19 MWh); initial SoC 50 %; `c_deg = 120 €/kWh·1000 / (8000·0.9) = 16.67 €/MWh` discharged.

**Verdict.** The formulation matches the specification's state equation exactly, and the price-taker sign convention is correct (import is a cost, export is revenue, negative prices are handled). The independent mode-enumeration check found the MILP optimum to within **7.1 × 10⁻⁶ €** in all 30 random cases. The binary does prevent simultaneous charge and discharge. The cyclic terminal constraint does prevent free initial energy. The defects are in how the solver is called, how the time axis is handled, and how conventions are documented, not in the core equations (see D3, D4, D11, D12).

A precision note on a README claim: with the default `c_deg`, an LP without the binary only starts the simultaneous-charge/discharge loop below about **−102 €/MWh**. The loop pays off when |p|·(1/RTE − 1) > c_deg. Probe F confirmed this: no loop at −100 €/MWh, loop at −110 €/MWh. The README's wording ("fails the moment price < 0") is exact only when `c_deg = 0`, as in the test. The binary is still the right choice.

**Comparison with PyPSA.** PyPSA's `StorageUnit` uses the same structure: `soc(t) = η_standing·soc(t−1) + η_store·p_store·Δt − p_dispatch·Δt/η_dispatch`, plus spill and inflow terms. It also supports a per-snapshot `Δt` (snapshot weightings), standing losses, and cyclic or initial-SoC options. GridFlex should adopt per-interval `Δt` and an optional standing-loss term as a methodological reference. No code needs to be copied.

---

## 3. Data sources (verified live 2026-10-22)

| Field | SMARD ID | Unit | Native resolution | Coverage | Notes |
|---|--:|---|---|---|---|
| DE-LU day-ahead price | 4169 | €/MWh | `hour` and `quarterhour` endpoints | from 2018-09-30 | **See D5. Since the switch to a 15-min market time unit (between 2025-09-22 and 2025-10-06 in the data, consistent with the SDAC go-live on 2025-10-01), the `hour` series is the arithmetic mean of four quarter-hour prices.** Probe: week of 2025-10-06 had 168/168 hours with differing quarter-hour values, and hourly = mean(QH) in 168/168. Before the switch, quarter-hour values simply repeat the hourly price. |
| Load forecast (total) | 411 | MW | hour / quarterhour | from 2014-12-28 | File for D+1 created 08:00 UTC (10:00 CEST) on D. That is consistent with availability before the 12:00 gate closure (one observation). Only one vintage is stored. |
| PV forecast | 125 | MW | hour / quarterhour | from 2014-12-28 | **File created 2026-10-21 16:07 UTC (18:07 CEST) and covering through 22 Oct. At 13:50 UTC on 22 Oct, no values for 23 Oct existed, even though 23 Oct prices were already published.** See D1. |
| Wind onshore forecast | 123 | MW | as above | as above | as above |
| Wind offshore forecast | 3791 | MW | as above | as above | as above |

- **Timestamps:** UTC epoch milliseconds marking the interval start. The repo converts them to Europe/Berlin. Weekly chunks start at local Monday 00:00, so a chunk holds 167, 168, or 169 hours. The code assumes 168 (D8).
- **Quality for 2022-01-01 to 2024-12-31** (from the reproduction download): all five series have 26,304/26,304 hourly intervals, 0 duplicates, and 0 missing. The price range is −500.0 to 936.3 €/MWh. **There are 827 negative-price hours (69 / 301 / 457 by year), which matches the README.** So the gap and imputation defects below are latent for the published window. They are live for current data: the latest chunk contains nulls for future hours.
- **Licence:** SMARD states that its data may be downloaded, stored, and reused under **CC BY 4.0**. Attribution is required. The README says only "used under their terms".
- **regelleistung.net:** not investigated in depth. Reserve participation is an advanced extension (spec §12) and stays out of scope until the core is validated.

---

## 4. Forecasting and evaluation

| Model | Implementation | Assessment |
|---|---|---|
| Perfect foresight | Returns realised price | Correct as an upper benchmark under the same constraints. |
| Naive persistence | `price.shift(freq="24h")` | Uses no information unknown at D−1 12:00, because D−1 prices clear on D−2. Absolute 24 h shift misaligns local hours on the day after a DST change (D10). |
| Seasonal naive | `shift(freq="168h")` | As above. |
| Gradient boosting | `HistGradientBoostingRegressor`; features: calendar, load/PV/wind forecasts, residual load, price lags 24/48/168 h; expanding window; retrain every 30 days; seasonal-naive fallback for < 60 days of history | The training cutoff (`index < delivery-day start`) is correct for price targets. **Four of the eleven features are unavailable at the decision time (D1).** Hyperparameters are fixed in code, and there is no record of how they were chosen. |

**Evaluation procedure.** No forecast-accuracy metric (MAE, RMSE, rMAE) is computed or reported anywhere. Models are compared only through the downstream capture ratio. 2023–2024 is both the only evaluation period and the period behind every design choice, so model selection and final evaluation are not separated. No regularised linear baseline exists. epftoolbox's LEAR (Lasso with AIC-selected λ, daily recalibration, fixed calibration windows) is the standard reference for one.

---

## 5. Backtest and settlement (`backtest/rolling.py`)

**Policy as implemented:** day-ahead only. For each Europe/Berlin calendar day *D*:
1. Take the forecast for *D* (computed for the whole window up front).
2. Solve the MILP on the forecast with `e_start` = carried SoC and a cyclic end.
3. Settle the same physical schedule at realised prices (`net_settled`) and at forecast prices (`net_planned`).
4. Carry the end SoC forward.

**Correct and worth retaining:** decisions are separated from outcomes; forecast-driven schedules are never optimised on realised prices; planned and settled revenue are reported separately; per-day solver status is recorded; 23- and 25-hour days are handled because the horizon is the length of the day's rows.

**Problems:** unsolved days are still settled (D3). NaN forecasts are silently replaced with the day mean or with 0 (D2). Missing intervals collapse the time axis (D4). The commitment model is a fixed self-schedule, accepted whatever the clearing price; a real day-ahead bid is a price–quantity curve. The README overstates the realism of this ("exactly as in the real auction"; D16). No heuristic baseline exists.

---

## 6. Reproduction of published results

`scripts/run_all.py` ran end-to-end from a fresh download in the scratch clone (Python 3.12, latest dependency versions).

**Result: the published numbers reproduce exactly.** Runtime was 13 min 31 s; exit code 0.

| Output | Committed vs reproduced |
|---|---|
| `backtest_summary.csv` (all 4 strategies, all 9 metrics) | max abs. difference **0.0** |
| `duration_sweep.csv` | max abs. difference **0.0** |
| `daily_*.csv` | 731/731 days `Optimal` for every strategy; contents identical, only line endings differ (CRLF on Windows vs LF committed) |
| Tier 1, 2022–2024 | 56,817 €/MW-yr; years 82,924 / 38,514 / 49,170 €/MW. Matches the README. |

So the code is deterministic and the README numbers really are what it produces. **Reproducing the numbers does not make them valid:** see D1 for the ML figure.

Caveats: this was *not* a clean-environment reproduction in the spec §15 sense, because dependencies are unpinned and resolved to today's latest versions. SMARD data was downloaded fresh on 2026-10-22; values could differ if SMARD revises history.

---

## 7. Defect register

Severity: **Critical** = published results are wrong or unsupported; **High** = can silently produce wrong results; **Medium** = correctness or robustness risk under plausible conditions; **Low** = hygiene.

| ID | Sev. | Area | Defect | Evidence |
|---|---|---|---|---|
| **D1** | **Critical** | Leakage | PV and wind forecast features (SMARD 125/123/3791, plus derived `wind_forecast` and `residual_load_forecast`) are used to forecast prices for day D at the 12:00 D−1 gate closure. They are published around 18:00 D−1. The README (L249–250), `models.py` L6, and `smard.py` L150–152 say the opposite. | ENTSO-E 14.1.D: day-ahead wind/solar forecast "no later than 18.00 Brussels time, one day before". Live SMARD file for these series created 18:07 CEST on D−1. At 15:50 CEST on D, no D+1 values existed. **Impact measured:** I reran the same ML backtest (2023–2024, same hyperparameters) without PV, wind, and residual-load features, keeping calendar, load forecast, and price lags. Net revenue was **620,627 €** (30,989 €/MW-yr) instead of 741,331 €. Capture ratio fell from **84.5 % to 70.8 %**: *below* seasonal naive (73.6 %) and close to naive persistence (69.3 %). Forecast MAE 25.65, RMSE 38.00 €/MWh. The published ML advantage depends almost entirely on information that was not yet available. |
| **D2** | High | Backtest | `_fill_forecast` (`rolling.py` L59–66) replaces NaN forecasts with the day mean, or with **0.0** when the whole day is NaN. The optimiser then sees a flat price, does nothing, and reports `Optimal`. | Probe C: seasonal naive without 168 h of history → forecast `[0.]`, status `Optimal`, revenue 0. |
| **D3** | High | Optimiser | Infeasible or unsolved solves are settled as if they were schedules. `nan_to_num` and `clip` (`dispatch.py` L173–180) turn solver garbage into numbers, and `rolling.py` L99 settles them regardless of status. `solved` checks only `LpStatus`. MIP gap, `sol_status`, runtime, and solver version are not captured. | Probe B: infeasible case returns `p_ch = 19.41 MW` (rating is 10 MW) and settles −970.49 €. Probe D: time-limited solve returns `Not Solved`, which the backtest would still settle. |
| **D4** | High | Time axis | Missing intervals are dropped (`smard.py` L108 drops nulls; L182 drops NaN prices). The MILP and `settle` then treat consecutive rows as adjacent with a fixed `dt`, so SoC dynamics and energy are wrong across the gap and no warning is raised. | Probe A: 6-hour gap → 18-row day solved as `Optimal`. Latent for 2022–2024 (0 gaps); live for current data. |
| **D5** | High | Data | The market time unit is now 15 min. The code hard-codes hourly (`index_hour`, `delta_t_hours: 1.0`, hour-based lags and features). Any run after 2025-09-30 would optimise and settle against hourly averages that were never traded, understating spreads. | Live probe (§3). Does not affect the published 2022–2024 results. |
| D6 | Medium | Leakage / imputation | `df[feature_cols].ffill().bfill()` (`smard.py` L184). `bfill` copies later observations backwards. `ffill` invents values across gaps of any length; for example, today a missing PV/wind forecast for D+1 would be filled with D's last value for the whole day. | Code reading. |
| D7 | Medium | Data | The cache never revalidates. Complete weeks are never refetched, so SMARD revisions are ignored. The processed cache is keyed only by `start_end` (`_common.py` L32) and ignores config and series changes. The index endpoint is always fetched, so offline mode is impossible. There are no retries, no `raise_for_status`, no retrieval timestamps, and no raw-payload preservation. | Code reading. |
| D8 | Low | Data | Weekly chunks are assumed to be exactly 168 h (`smard.py` L29, L92, L104). DST weeks have 167 or 169 h, which can drop an edge hour and causes redundant refetches. | Live probe: latest chunk has 169 points. |
| D9 | Medium | Validation | There is no duplicate, missing-interval, unit, or range validation. The README (L264–266) says series IDs "are validated in code"; **no such code exists.** | Code search. |
| D10 | Low | Forecast | Absolute 24 h / 168 h shifts misalign local hour-of-day after a DST change. | Probe E: forecast for 30 Oct 2023 00:00, 01:00, 02:00 uses 29 Oct 01:00 CEST, 02:00 CEST, 02:00 CET. |
| D11 | Medium | Physics convention | Degradation and EFC are measured on **AC discharge** against **nameplate** energy. Cycle-life ratings are usually cell/DC throughput, and the usable window is 18 MWh. At cell level, degradation cost would be 7.3 % higher (c_deg/η_dis) and cycles about 11 % higher against the usable window. Neither convention is documented, and the AC/DC measurement point of the power limits is undocumented. | Probe G: 8.35 MWh AC = 9.0 MWh DC; EFC 0.417 (nameplate) vs 0.464 (usable). |
| D12 | Low | Policy | Every day must return to 50 % SoC. This is consistent across strategies, but it rules out shifting energy between days and is not presented as a policy choice. There is no terminal-value alternative. | `rolling.py` L97. |
| D13 | Medium | Finance | Payback is undiscounted and based on revenue only: no O&M, trading or exchange fees, grid charges, taxes, capacity fade, or augmentation. It is printed even for **perfect foresight** (`run_perfect_foresight.py` L45) and written for every strategy (`backtest_summary.csv`), which breaks spec §11. | Code and results. |
| D14 | Medium | Forecast eval | No MAE or RMSE is reported. No hold-out between model selection and evaluation. No regularised linear baseline. No heuristic dispatch baseline. | Code reading. |
| D15 | Medium | Reproducibility | Dependencies use only `>=` bounds; there is no lockfile, no CI, and no Ruff or mypy config. Scripts overwrite the committed `results/`, so example and fresh outputs are mixed. Run metadata (git SHA, config hash, data retrieval time, solver version) is never saved. | Repo inspection. |
| D16 | Medium | Claims | Unsupported or overstated statements; see §8. | README. |
| D17 | Low | Tests | `data/` and `analysis/` have 0 % coverage. Two config tests restate the implementation formula. There is only one hand-calculated optimisation case. There are no tests for DST, gaps, duplicates, infeasibility, forecast cutoffs, or the settlement edge cases listed in spec §14. | Coverage report. |
| D18 | Low | API | `run_backtest` raises `ValueError` when given tz-aware `Timestamp` start/end (`pd.Timestamp(ts, tz=…)`, `rolling.py` L79). | Probe. |
| D19 | Low | Hygiene | 23 Ruff findings (8 unsorted imports, 7 long lines, 4 f-strings without placeholders, 2 unused imports, …). 1 mypy error (`models.py` L203, instantiating an abstract class through the registry). pandas 4 deprecation of `dayofweek`. | Tool output. |

**Sister repo `power-spread-trader`.** Its SMARD client (`smard_prices.py`) is a copy of the same pattern and shares D4 (dropped nulls), D5 (hourly only), D7 (no retries or revalidation), and D8. Its docstring refers to a sibling `zones.py` that does not exist. The GridFlex SMARD adapter should replace both clients.

---

## 8. Financial and methodological claims

| Claim (README) | Assessment |
|---|---|
| Perfect foresight 43,782 €/MW-yr, 2023–2024 | Reproduced exactly (§6). The figure is net of modelled degradation; it should be labelled so. |
| Gradient boosting captures 84.5 % | **Not supported.** It relies on features unavailable at the decision time (D1). The leakage-free rerun gives 70.8 %, below seasonal naive. |
| Naive 69.3 %, seasonal naive 73.6 % | No leakage found for these. Valid within the model's assumptions. |
| "Optimism gap": expect 876 k €, collect 608 k € | Consistent with the CSV. Valid as a planned-vs-settled comparison. |
| 827 negative-price hours 2022–2024 (3.1 %) | **Verified** (69 / 301 / 457). |
| Per-year ceiling 82,924 / 38,514 / 49,170 €/MW | Reproduced in §6. These are calendar-year totals, not annualised (2024 has 366 days); should be labelled. |
| "Forecast fields … published before gate closure" | **Contradicted** for PV and wind (D1). Plausible for load. |
| "IDs … validated in code" | **False**: no validation code exists. |
| "Forecast error affects revenue but never feasibility, exactly as in the real auction" | Overstated. The model commits a fixed schedule whatever the clearing price; real bids are price-dependent. |
| "Forecast-error gap is an upper bound on what a real desk leaves on the table" | Unsupported. No intraday or bid-curve model exists to bound it. |
| "Most merchant batteries in Germany are built at 1–2 hours" | No source. Remove it or cite one. |
| Simple payback 11.4 years (perfect foresight) | Not a valid investment metric here (D13). Remove until CAPEX, OPEX, horizon, and discounting are configured. |
| Degradation cost 16.7 €/MWh "derived, not guessed" | The arithmetic is correct. The inputs (120 €/kWh, 8000 cycles, 90 % DoD) are unsourced, and the AC/DC basis is undocumented (D11). |

---

## 9. Retain vs change

| Retain (with tests extended) | Change |
|---|---|
| MILP formulation and sign convention (verified) | Solver call, status handling, `SolveReport`, HiGHS default (D3) |
| Daily rolling decide/settle separation; planned vs settled | Per-interval `Δt`, gap detection, no silent fills (D2, D4) |
| Perfect, naive, and seasonal-naive forecasters | SMARD adapter rewrite: resolution-aware, raw/clean split, metadata, retries, offline (D5–D9) |
| Typed, validated config with derived quantities | Feature availability registry; remove post-gate-closure features (D1, D6) |
| Hand-checked toy test; negative-price binary test; terminal test | Forecast interface with origin, cutoffs, and model metadata; LEAR-style Lasso; MAE/RMSE; hold-out (D14) |
| MIT licence | Finance module with explicit formulas; payback/NPV only when configured (D13) |
| | README claims (D16); documentation set; lockfile; CI (D15) |

---

## 10. Licence and attribution

| Item | Licence | Implication |
|---|---|---|
| This repo | MIT (© 2026 Mohammad Faisal) | Retain. |
| PyPSA | MIT | Methodological reference only. Cite the storage formulation. No code copied. |
| epftoolbox | **Apache-2.0** | Methodological reference (LEAR, rMAE, DM test). If any code is adapted, keep its licence notice, add a NOTICE entry, and mark the changes. The plan is an independent implementation with scikit-learn, citing Lago et al. (2021). Its benchmark datasets (Zenodo) are not used. |
| SMARD data | CC BY 4.0 | Attribution such as "Bundesnetzagentur \| SMARD.de" in README, docs, UI, and exported data. Any committed fixture must carry the attribution. |
| PuLP (MIT), CBC (EPL-2.0, bundled), HiGHS/highspy (MIT), pandas/NumPy/scikit-learn (BSD-3) | Permissive | List them in `docs/` with versions. |
| `power-spread-trader` | MIT, same author | Its SMARD client is superseded; nothing needs carrying over. |

---

## 11. Proposed architecture

Evolve the existing repo in place (history kept) and rename the package `bess` → `gridflex`:

```
src/gridflex/
  config/        pydantic v2 models: AssetConfig, MarketConfig, EconomicsConfig, ExperimentConfig (units in field names)
  data/          smard.py (resolution-aware adapter, retries, raw JSON + metadata), cache.py, validate.py, availability.py
  forecasting/   base.py (PriceForecast: origin, delivery index, values, model_id/version, training_cutoff, feature_cutoff, config)
                 naive.py, lear.py (Lasso), gbr.py, evaluate.py (MAE, RMSE, rMAE; expanding window)
  battery/       physics.py (state equation, conventions, feasibility checker independent of the optimiser)
  optimization/  model.py (PuLP formulation, per-interval Δt), solve.py (HiGHS default, CBC fallback, SolveReport)
  backtest/      engine.py (decision → commit → settle → state update), policies.py (DA-only, terminal policy), heuristic.py
  settlement/    settle.py (realised vs expected, cost breakdown)
  finance/       metrics.py (explicit formulas), sensitivity.py, investment.py (payback/NPV only if configured)
  reporting/     exports (CSV + run metadata JSON), plotly figures
app/             Streamlit UI that reads saved runs only (no optimisation code)
configs/         default.yaml, examples/
tests/           unit, property, hand-calculated, integration; fixtures/ (small SMARD extract, CC BY 4.0)
docs/            AUDIT, ARCHITECTURE, MATHEMATICAL_MODEL, DATA_DICTIONARY, BACKTEST_METHODOLOGY, VALIDATION, LIMITATIONS, RESULTS
results/examples/ (committed, labelled) vs runs/ (git-ignored, fresh outputs)
```

Key design decisions:

- **Solver stack: keep PuLP and switch the default solver to HiGHS.** The audit shows the PuLP model is correct and small, and the spec allows keeping it in that case. Migrating to Pyomo would add weight without fixing any defect found. HiGHS via `highspy` (MIT) gives better status and MIP-gap reporting; CBC stays as a fallback.
- **Time resolution:** per-interval `Δt` throughout. Hourly products up to 2025-09-30, 15-min products from 2025-10-01, as separate market regimes, never mixed in one optimisation horizon.
- **Decision-time availability registry:** each input series declares when it becomes available relative to the decision time (D−1 12:00 Europe/Berlin). The forecaster refuses features that are not available by then. Load forecast: allowed, with the caveat that only one vintage is stored. PV/wind: excluded for the DA decision unless properly vintaged data (for example the ENTSO-E 18:00 vintage) is added later, and even then it is not available at 12:00.
- **Conventions documented once:** power limits at the AC grid connection; η one-way; degradation basis selectable (`ac_discharge` or `dc_throughput`) with DC as the documented default; EFC defined against usable energy, with the nameplate figure also reported.
- **Failure semantics:** a non-optimal solve yields an *idle* schedule flagged `failed`, never a settled optimisation result. The backtest reports the count of failed days and can be configured to abort.

---

## 12. Acceptance criteria per stage

| Stage | Done when |
|---|---|
| 2 Config, data contracts, validation | Pydantic configs load the example and reject invalid ones (tests). The SMARD adapter fetches hour and quarterhour data with timeout, bounded retries, raw/clean split, metadata, and documented cache invalidation; offline mode works from fixtures. The validator reports duplicates, missing intervals, resolution changes, and 23/25 h days, and never fills prices. Tests cover all of these on a committed fixture that includes both DST transitions and the 15-min switch. `DATA_DICTIONARY.md` written. |
| 3 Battery model | Independent physics module plus a feasibility checker. Hand-calculated tests for the state equation with η ≠ 1, irregular Δt, SoC and power limits, terminal policies, throughput limits, and the AC/DC degradation basis. |
| 4 Optimisation | Per-interval Δt; HiGHS default; `SolveReport` (status, termination, objective, gap, runtime, solver and version). Infeasible, time-limited, and error cases are tested. Independent enumeration cross-check kept as a test. Negative, zero, and flat price cases tested. `MATHEMATICAL_MODEL.md` written. |
| 5 Forecasting | `PriceForecast` metadata object; availability registry enforced; persistence, seasonal naive, Lasso, and GBR implemented. Leakage tests: a feature after the cutoff raises; a training row at or after the origin raises; preprocessing is fit on training data only. MAE, RMSE, and rMAE on a held-out period not used for selection. |
| 6 Backtest and settlement | Policy stated (DA-only, D−1 12:00, full-day commitment). Perfect foresight, forecast-driven, and heuristic baseline run under identical assumptions. Failed days are never settled as optimised. Settlement arithmetic is tested by hand. Deterministic end-to-end result on the fixture. `BACKTEST_METHODOLOGY.md` written. |
| 7 Finance | Every metric in spec §11 with a formula in the docs. Annualisation is labelled and partial years flagged. Sensitivity over power, duration, η, c_deg, and cycle cap. Payback/NPV only when inputs are configured (tests). |
| 8 UI and exports | Streamlit pages per spec §13, reading saved runs. Every chart shows units, timezone, and range. CSV, config, and metadata download. No optimisation code in `app/`. |
| 9 Integration | CI (Ruff, mypy, pytest) green. Clean-environment reproduction from the lockfile with the commands recorded. All docs complete. README claims each trace to a run. |

---

## 13. Prioritised remediation plan

| Priority | Item | Stage |
|---|---|---|
| P0 | Withdraw or caveat the 84.5 % ML claim and the other README claims (D1, D16). Done during Stage 2 docs, not before your approval. | 2 |
| P0 | Data contracts: resolution-aware SMARD adapter, validation, no silent fills, availability registry (D1, D2, D4–D9) | 2 |
| P0 | Solver status semantics and per-interval Δt (D3, D4) | 3–4 |
| P1 | Physics conventions: AC/DC basis, EFC definition, terminal policy options (D11, D12) | 3 |
| P1 | Forecast metadata, leakage tests, Lasso baseline, accuracy metrics, hold-out (D14) | 5 |
| P1 | Heuristic baseline; backtest failure handling; run metadata (D3, D15) | 6 |
| P2 | Finance module; remove default payback (D13) | 7 |
| P2 | Lockfile (uv), CI, Ruff and mypy config, results separation (D15, D19) | 2, then each stage |
| P3 | Streamlit UI | 8 |
| P3 | Clean-environment reproduction; full docs set | 9 |

---

## 14. Decisions (approved 2026-10-22: all recommended options)

1. **Repository:** evolve `bess-dispatch-optimizer` in place on a feature branch and rename the package to `gridflex` *(recommended)*, or start a new repository.
2. **Solver stack:** keep PuLP with HiGHS default and CBC fallback *(recommended)*, or migrate to Pyomo with HiGHS.
3. **PV/wind features:** exclude them from day-ahead decisions *(recommended)*, or add ENTSO-E vintaged forecasts later (needs an API token; they are still published after 12:00).
4. **Study periods:** hourly regime 2023-01-01 → 2025-09-30, using 2023 for model selection and 2024-01-01 → 2025-09-30 as the final hold-out; the 15-min regime from 2025-10-01 reported separately *(recommended)*.
5. **Python and tooling:** Python 3.12 (verified working here; 3.11 is not installed on this machine) with a `uv` lockfile *(recommended)*.

---

## 15. Addendum: findings after approval

| ID | Sev. | Area | Finding | Evidence | Resolution |
|---|---|---|---|---|---|
| **D20** | Medium | Units | SMARD load and generation series are **energy per market time unit (MWh per interval)**, not MW. The v0.1 code labelled them MW, which is numerically right only at hourly resolution; quarter-hour values are one quarter of mean MW. | Live check 2026-10-22: for series 411, 125, 123, 3791 and 410, four quarter-hour values sum to the hourly value (ratio 1.0000 over 48 h). | Cleaning converts to mean MW (`gridflex.data.clean`). Covered by `tests/data/test_clean.py`, the fixture test, and the opt-in live test. |
| D21 | Low | Market rules | Day-ahead price limits change over time. The harmonised minimum became −600 EUR/MWh from trading date 2026-05-28. Sources conflict on whether the maximum rose from +4,000 to +5,000 EUR/MWh in Sep 2022. | NEMO Committee SDAC page; EPEX SPOT notice of 13 Sep 2022. | Dated `market.price_limits` envelope; violations are errors, never clipped. The conflict is documented and still unresolved. |
| D22 | Low | Repo hygiene | A `.gitignore` entry `data/` (carried over from v0.1, which used `data/raw`) would also have ignored `src/gridflex/data/` and `tests/data/`. | `git status` during Stage 2. | Anchored to `/data/` and `/runs/`. |
| D23 | Low | Backtest state | The v0.1 backtest starts each day from the solver-reported SoC variable instead of the state-equation energy of the committed schedule. | Stage 3 real-data check (`scripts/check_v01_schedules.py`, 2023–2024): the two agree to 6e-7 MWh within a day but differ by 4.8e-5 MWh after 731 days. | Stage 6 carries the physically simulated state (MATHEMATICAL_MODEL §2.6). |
| D24 | High | Solver status | PuLP maps a HiGHS time-limit stop that has an incumbent to `LpStatus "Optimal"` / `"Solution Found"`. Any code trusting PuLP's status would present an unproven schedule as optimal. | Stage 4: 8760 h problem at a 2 s limit, HiGHS "Time limit reached" with gap 0.49 %, PuLP "Optimal" (`tests/optimization/test_failure_modes.py`). | `gridflex.optimization` reads HiGHS's native model status and primal-solution status; time-limited incumbents are rejected by default. |
| D25 | Low | Solver status | PuLP raises `KeyError` when HiGHS stops on a solution limit (`kSolutionLimit`). | Stage 4 probe (`mip_max_improving_sols=1`). | Solver exceptions are caught and reported as status `error`, with no schedule. |
| D26 | Medium | Packaging | `requirements.txt` / `requirements-dev.txt` were not regenerated after dependency changes in Stages 4–6. `highspy` (the default solver) was missing and `seaborn` stale, so a pip-based install of Stages 4–7 would have lacked HiGHS. The clean-environment checks in those stages used only the uv route. | Found in Stage 8 when Streamlit and Plotly were added. | Files regenerated; `tests/test_packaging.py` now fails whenever they omit a declared dependency; pip and uv clean installs both verified. |

### Resolution status

| ID | Status | Where |
|---|---|---|
| D1, D5–D9, D19–D22 | resolved | Stage 2 |
| D11 (AC/DC basis, EFC definition) | resolved: selectable, DC/usable default, bridge converts exactly | Stage 3; MATHEMATICAL_MODEL §2.4–2.5 |
| D12 (terminal policy) | resolved: cyclic, fixed, and free with an explicit value, in physics, config and optimiser | Stages 3–4 |
| D13 (payback misuse) | resolved: payback and NPV only when every input is configured, with assumptions listed and replacement handled without double counting; the default config computes neither | Stage 7 |
| D16 | README claims withdrawn | Stage 2 |
| D3 (failed solves settled) | resolved in the optimiser: no schedule unless proven optimal and independently verified; the v0.1 backtest still uses the v0.1 MILP until Stage 6 | Stage 4; Stage 6 |
| D24, D25 | resolved | Stage 4 |
| D10 (DST lag misalignment) | resolved: wall-clock lags with a documented DST rule | Stage 5; BACKTEST_METHODOLOGY §1.2 |
| D14 (forecast evaluation) | resolved for forecasts: MAE/RMSE/rMAE and DM tests on a hold-out separate from selection, plus a LEAR-style Lasso baseline. The heuristic dispatch baseline follows in Stage 6 | Stage 5 |
| D1 (forecast leakage) | additionally enforced at runtime: publication-time guards in every forecast, plus an end-to-end corruption test | Stage 5 |
| D2 (silent forecast fill) | resolved: missing inputs raise; failed days are idle and flagged | Stages 5–6 |
| D3 (failed solves settled) | resolved in the backtest: failed or rejected plans are committed idle and flagged, never settled as optimised | Stage 6 |
| D4 (gaps collapse the time axis) | resolved: validated datasets, per-interval durations from timestamps, incomplete days abort | Stages 2, 4, 6 |
| D15 (run metadata) | resolved: git commit and dirty flag, config fingerprint and dump, package versions, provenance in every run | Stage 6 |
| D18 (tz-aware API) | resolved: the v0.1 API was removed; the new APIs take dates or tz-aware UTC instants | Stage 6 |
| D23 (state carry) | resolved: the next day starts from the state-equation energy of the committed schedule | Stage 6 |
| D26 (stale pip exports) | resolved: regenerated, guarded by `tests/test_packaging.py`, pip and uv clean installs verified | Stage 8 |
| D17 (test gaps) | resolved: finance (Stage 7), UI and exports (Stage 8), offline end-to-end integration test (Stage 9) | Stages 7–9 |


---

## 16. Final status: definition of done (spec §18), Stage 9

| Criterion | Status | Evidence |
|---|---|---|
| The data pipeline is documented and validated | met | DATA_DICTIONARY; `tests/data/*`; validation reports in every run (41,615/41,615 valid hourly intervals, 2021-01 → 2025-09) |
| Physical constraints are tested | met | MATHEMATICAL_MODEL §2; hand-calculated tests in `tests/battery/*`; the independent checker runs on every schedule |
| Optimisation outputs are feasible and solver status is checked | met | HiGHS native status (D24, D25); physics check and objective recomputation; 3,195/3,195 strategy-days `ok` in the hold-out run |
| Forecast-driven strategies do not use future information | met | Publication-time guards; corruption test (forecasts unchanged when all post-origin data is altered); PV/wind excluded |
| Settlement is separate from forecast-based expected revenue | met | `gridflex.settlement`; expected and realised columns reported separately (RESULTS §2.1) |
| Financial metrics have explicit definitions | met | RESULTS §3.1; payback and NPV only with configured inputs |
| Results are reproducible | met | Clean clone, `uv sync --locked`, empty cache, full pipeline: every output identical to the recorded runs (VALIDATION Stage 9) |
| The application runs with the documented example workflow | met | `scripts/run_example.py` (integration test); Streamlit viewer checked in Stage 8 |
| Tests pass in a clean environment | met locally; **CI not yet run** | Clean copies pass on Windows (uv route in Stage 9, pip route in Stage 8). GitHub Actions (Ubuntu and Windows) needs a push, which has not been made |
| Known limitations and upstream attribution are documented | met | LIMITATIONS; README (SMARD CC BY 4.0, PyPSA and epftoolbox as methodological references, dependency licences) |

**Open items:**

- **CI on GitHub.** The Stage 9 criterion "CI green" is unverified until the branches are pushed.
- **Unsourced degradation cost** (LIMITATIONS §2). It must be replaced with a cited value before absolute revenue is quoted.
- **Research question 4** (uncertainty-aware optimisation) is not answered. Only point forecasts are implemented.
- **The 15-minute regime** (from 2025-10-01) is supported by the data layer but not yet evaluated.
