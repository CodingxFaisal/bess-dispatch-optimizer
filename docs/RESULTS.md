# Results

Every number here was produced by a command in this repository, run on validated SMARD data (Bundesnetzagentur | SMARD.de, CC BY 4.0) and recorded with its run metadata. Results from v0.1.0 are withdrawn; see [AUDIT.md](AUDIT.md) and [results/legacy_v0.1.0](../results/legacy_v0.1.0/README.md).

**Historical backtest or forecast accuracy is not a prediction of future performance.**

| Section | Status |
|---|---|
| 1. Day-ahead price forecast accuracy (hourly regime) | Stage 5, below |
| 2. Dispatch backtest: forecast-driven vs perfect foresight vs heuristic | Stage 6, below |
| 3. Financial metrics and sensitivity | Stage 7, below |

**Reproduced from a clean environment (Stage 9).** All results in this document were regenerated from scratch, as follows:

- a fresh clone of commit `de2ce0c` (clean tree);
- `uv sync --locked` into a new virtual environment;
- an empty data cache, so all SMARD data was downloaded anew;
- the five pipeline commands, run in 18 minutes in total.

Every output table matched the recorded runs **exactly**: forecast selection, hold-out metrics and all 61,340 hold-out forecasts; the backtest's 3,195 daily and 76,675 interval rows; the finance tables; and all 34 sensitivity rows. Only solver runtimes differ. Commands and timings: [VALIDATION.md](VALIDATION.md), Stage 9.

**Summary.**

- **Forecasting.** Trained forecasts (GBR, LEAR-style Lasso) cut MAE by 31–32 % against seasonal naive and 17–18 % against persistence. GBR and Lasso are statistically indistinguishable.
- **Dispatch.** Driving the same MILP with those forecasts captures 83–84 % of the perfect-foresight benchmark. Persistence captures 76 %, and the two-window heuristic 43 %.
- **Main uncertainty in absolute revenue.** Battery duration and the unsourced degradation cost dominate.
- **Limitations.** These figures are net operating revenue for a day-ahead-only, price-taking 10 MW / 20 MWh battery, not profit. See [LIMITATIONS.md](LIMITATIONS.md).

---

## 1. Day-ahead price forecast accuracy

**Run:**

- Command: `uv run python scripts/evaluate_forecasts.py --offline`, run 2026-10-23 in 375 s.
- Software: gridflex 0.2.0.dev0, Python 3.12.10.
- Data: SMARD 4169 (price) and 411 (load forecast), hourly, local delivery days 2021-01-01 → 2025-09-30. Validation passed: 41,615/41,615 intervals, no gaps.
- Protocol: [BACKTEST_METHODOLOGY §1](BACKTEST_METHODOLOGY.md). Forecasts are issued at D−1 12:00 Europe/Berlin, using inputs public at that time: prices up to D−1 and the load forecast for D. PV and wind forecasts are not used.

### 1.1 Model selection (2023-01-01 → 2023-12-31, 365 days)

| Family | Configuration | MAE | RMSE | Bias | rMAE |
|---|---|--:|--:|--:|--:|
| persistence | D−1 same time | 27.20 | 40.29 | −0.02 | 0.809 |
| seasonal naive | D−7 same time | 33.64 | 48.23 | 0.45 | 1.000 |
| lasso | window 182 d | 23.72 | 34.42 | 1.30 | 0.705 |
| lasso | window 364 d | 24.29 | 35.09 | 0.21 | 0.722 |
| **lasso** | **window 728 d** (chosen) | 23.50 | 32.98 | 2.89 | 0.699 |
| gbr | window 364 d, depth 4 | 23.46 | 33.31 | −0.19 | 0.697 |
| gbr | window 364 d, depth 6 | 23.83 | 33.64 | −0.67 | 0.708 |
| gbr | window 728 d, depth 4 | 23.48 | 33.08 | 1.24 | 0.698 |
| **gbr** | **window 728 d, depth 6** (chosen) | 23.32 | 32.90 | 1.17 | 0.693 |

EUR/MWh; rMAE relative to seasonal naive. The differences between Lasso and GBR configurations are small (MAE 23.3–24.3), so the choice of configuration within each family matters little.

### 1.2 Hold-out (2024-01-01 → 2025-09-30, 639 days, 15,335 hourly intervals)

These are the chosen configurations, run once. No hold-out data influenced any choice.

| Model | MAE | RMSE | Bias | rMAE |
|---|--:|--:|--:|--:|
| **GBR** (728 d, depth 6) | **22.18** | **34.80** | 1.39 | **0.682** |
| Lasso, LEAR-style (728 d) | 22.44 | 35.21 | 0.99 | 0.690 |
| Persistence (D−1) | 27.07 | 42.74 | −0.20 | 0.832 |
| Seasonal naive (D−7) | 32.55 | 52.53 | −0.87 | 1.000 |

**By calendar year:**

| Model | 2024 MAE | 2024 rMAE | 2025 (Jan–Sep) MAE | 2025 rMAE |
|---|--:|--:|--:|--:|
| GBR | 21.67 | 0.661 | 22.87 | 0.710 |
| Lasso | 22.75 | 0.693 | 22.04 | 0.684 |
| Persistence | 27.83 | 0.848 | 26.04 | 0.809 |
| Seasonal naive | 32.80 | 1.000 | 32.21 | 1.000 |

**Diebold-Mariano tests** (daily mean absolute error, one-sided, 639 days):

| A | B | DM | p (B more accurate than A) |
|---|---|--:|--:|
| Lasso | GBR | 0.53 | 0.30 |
| Persistence | GBR | 7.36 | < 1e-4 |
| Persistence | Lasso | 7.11 | < 1e-4 |
| Seasonal naive | GBR | 10.60 | < 1e-4 |
| Seasonal naive | Lasso | 11.07 | < 1e-4 |
| Seasonal naive | Persistence | 4.80 | < 1e-4 |

### 1.3 Interpretation

- Both trained models beat both naive baselines, with statistically significant margins on the hold-out. Their error is about 32 % below seasonal naive and about 17–18 % below persistence.
- **GBR and Lasso are statistically indistinguishable** (p = 0.30). GBR is slightly better in 2024, Lasso in 2025. The hold-out gives no basis for claiming that one dominates.
- The selection-period ranking carried over to the hold-out without degradation. Hold-out rMAE (0.68–0.69) is close to selection rMAE (0.69–0.70).
- These are **price-accuracy** results. Whether lower MAE translates into more battery revenue depends on how well the forecast ranks hours within each day. That is evaluated in Stage 6 by settling forecast-driven schedules against realised prices.
- Forecast accuracy is only one input to the value of a strategy, and these results are specific to 2024–2025 market conditions.

Outputs: `runs/latest/forecast_evaluation/` contains `selection.csv`, `holdout_metrics.csv`, `holdout_by_year.csv`, `dm_tests.csv`, `holdout_forecasts.csv` (all 61,340 forecast rows with metadata) and `metadata.json` (config fingerprint, versions, data provenance). The directory is git-ignored; re-create it with the command above.

---

## 2. Day-ahead dispatch backtest (hourly regime)

**Run:**

- Command: `uv run python scripts/run_backtest.py --offline`, run 2026-10-23 in 521 s, deterministic. A second run on the same code reproduced every number exactly.
- Code: working tree based on commit `c9ba599` (recorded as dirty in `metadata.json`); config fingerprint `50d4855ec19d…`.
- Period: local delivery days 2024-01-01 → 2025-09-30, 639 days, the same hold-out as §1.
- Method: [BACKTEST_METHODOLOGY §2](BACKTEST_METHODOLOGY.md). Day-ahead only, price taker, full-day commitment at D−1 12:00, daily cyclic state, physically carried energy.
- Battery: 10 MW / 20 MWh, round-trip efficiency 0.86, SoC 5–95 %, cap of 1.5 EFC/day (DC discharge over usable energy), degradation 16.67 EUR per DC MWh (unsourced placeholder), no trading cost.

### 2.1 Totals

| Strategy | Failed days | Expected net | Realised gross | Degradation | **Net operating revenue** | Sold (MWh) | EFC | Share of perfect foresight |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Perfect foresight (benchmark, not deployable) | 0 | 942,903 | 1,181,489 | 238,586 | **942,903** | 13,275 | 795 | 100 % |
| Lasso forecast + MILP | 0 | 566,453 | 1,027,570 | 237,344 | **790,226** | 13,206 | 791 | 83.8 % |
| GBR forecast + MILP | 0 | 766,007 | 1,024,455 | 242,855 | **781,601** | 13,513 | 810 | 82.9 % |
| Persistence forecast + MILP | 0 | 938,549 | 951,156 | 238,586 | **712,570** | 13,275 | 795 | 75.6 % |
| Persistence forecast + two-window heuristic | 0 | 528,618 | 502,364 | 95,250 | **407,114** | 5,300 | 318 | 43.2 % |

EUR unless stated; totals over 639 days. "Share" is net operating revenue divided by the perfect-foresight net over the same days.

### 2.2 By calendar year

| Strategy | 2024 net (366 d) | 2024 share | 2025 net (Jan–Sep, 273 d) | 2025 share |
|---|--:|--:|--:|--:|
| Perfect foresight | 470,350 | 100 % | 472,553 | 100 % |
| Lasso + MILP | 376,516 | 80.1 % | 413,710 | 87.5 % |
| GBR + MILP | 379,331 | 80.6 % | 402,270 | 85.1 % |
| Persistence + MILP | 334,265 | 71.1 % | 378,304 | 80.1 % |
| Persistence + heuristic | 189,278 | 40.2 % | 217,836 | 46.1 % |

### 2.3 Interpretation

- **Trained forecasts are worth about 8 percentage points of the benchmark.** GBR and Lasso earn 70–78 kEUR (about 10 %) more than the same optimiser driven by yesterday's prices. Between the two trained models the difference is small (Lasso +8.6 kEUR), in line with their statistically indistinguishable forecast accuracy (§1.3). No significance test was run on revenue.
- **The optimiser is worth more than the forecast.** On the same persistence forecast, the MILP earns 1.75× the two-window heuristic. The heuristic uses one cycle of at most half the usable window per day (0.5 EFC/day).
- **Expected and realised values diverge, in both directions:**
  - persistence plans are optimistic: 939 kEUR expected vs 713 kEUR realised;
  - Lasso plans are pessimistic: 566 vs 790 kEUR;
  - GBR plans are close: 766 vs 782 kEUR.
  
  Planned value from a forecast is therefore not a reliable estimate of realised value.
- **Throughput is the same across MILP strategies, and the value comes from timing.** All MILP strategies discharge about 13.2–13.5 GWh (≈ 795–810 EFC). They differ in which hours they trade.
- **Some days lose money:** 57 (Lasso), 60 (GBR) and 75 (persistence) of 639 days had negative realised net, when the committed schedule met prices unlike the forecast.
- **Persistence + MILP matches perfect foresight in throughput and degradation exactly.** This is structural, not a bug. Its schedule for day D is the perfect-foresight schedule for D−1: this held on 632 of 638 comparable days, with the exceptions being the clock-change days and the day after each, where a 24-hour shift isn't the same wall-clock time. The two boundary days both sit at the daily cycle cap, 25.04 MWh, so the totals coincide.

### 2.4 What these numbers are not

- **Not profit.** They exclude capital cost, fixed O&M, grid charges and levies, taxes, financing, imbalance risk and trading fees (`trading_cost_eur_per_mwh` = 0 here).
- **Not annualised.** 2025 covers January–September only. Normalised metrics (EUR/MW-yr, EUR/MWh-yr) and sensitivity analysis follow in Stage 7.
- **Not a forecast of future revenue.** They are specific to 2024–2025 prices, to the day-ahead-only fixed-schedule design, and to an unsourced degradation cost.

Outputs: `runs/latest/backtest/` contains `daily.csv` (3,195 rows), `intervals.csv` (76,675 rows), `summary.csv` and `metadata.json`. The directory is git-ignored; re-create it with the command above.

---

## 3. Financial metrics and sensitivity

**Run:**

- Commands: `uv run python scripts/run_backtest.py --offline` (505 s), then `uv run python scripts/run_finance.py` and `uv run python scripts/run_sensitivity.py --offline`.
- Code and config: working tree based on commit `b4ec8f6`, config fingerprint `76690a7fb527…`.
- The backtest reproduced §2 exactly; the fingerprint changed only because the investment and sensitivity config sections were added.

### 3.1 Metric definitions

All formulas are in `gridflex.finance` (EUR, MWh, MW).

| Metric | Definition |
|---|---|
| Net operating revenue | $\sum_d$ (gross revenue − degradation cost − trading cost) over delivery days $d$ (BACKTEST_METHODOLOGY §2.4) |
| Annualised net | net × 365 / $n_{days}$ (**365-day basis**). Periods under 365 days are flagged `partial_year`, and their annualised values are extrapolations |
| Net per MW-year | annualised net / rated discharge power (10 MW) |
| Net per MWh-year | annualised net / nameplate energy capacity (20 MWh) |
| EFC per day | total equivalent full cycles / $n_{days}$ (DC discharge over usable energy) |
| **Forecast capture ratio** | net(strategy) / net(perfect foresight), summed over the **same delivery days**, with the same battery, costs, policy and start state. NaN if the benchmark net is ≤ 0. Gross capture is defined the same way on gross revenue |
| Period tables | calendar month or year totals, with `days` vs `calendar_days` and a `complete` flag; `net_per_mw_eur` is the period total per MW (not annualised) |
| Simple payback | capex / A, with A = annualised net − fixed O&M; undiscounted. Computed only if `capex_eur_per_kwh` and `fixed_om_eur_per_kw_year` are configured |
| NPV | −capex + Σ CF_y/(1+r)^y over `horizon_years`. Also needs `discount_rate` and a `replacement` policy. Under `explicit` replacement, the degradation charge is added back and the replacement cost is booked in its year, so wear is not counted twice |

**Not profit, not IRR.** Capital cost, fixed O&M, grid charges and levies, taxes, financing and imbalance risk are outside net operating revenue.

### 3.2 Hold-out financial summary (639 days, 2024-01-01 → 2025-09-30)

| Strategy | Net (EUR) | Annualised net (EUR/yr) | EUR/MW-yr | EUR/MWh-yr | EFC/day | Days with net < 0 | Capture ratio |
|---|--:|--:|--:|--:|--:|--:|--:|
| Perfect foresight | 942,903 | 538,591 | 53,859 | 26,930 | 1.245 | 0 | 1.000 |
| Lasso + MILP | 790,226 | 451,381 | 45,138 | 22,569 | 1.238 | 57 | 0.838 |
| GBR + MILP | 781,601 | 446,454 | 44,645 | 22,323 | 1.267 | 60 | 0.829 |
| Persistence + MILP | 712,570 | 407,023 | 40,702 | 20,351 | 1.245 | 75 | 0.756 |
| Persistence + heuristic | 407,114 | 232,546 | 23,255 | 11,627 | 0.497 | 88 | 0.432 |

The 639-day period is longer than a year, so it is not flagged as partial. Annualisation pools 2024 with January–September 2025, so it inherits that period's seasonal mix.

**By calendar year** (totals, not annualised; 2025 is **incomplete**, 273 of 365 days):

| Strategy | 2024 net | 2024 EUR/MW | 2024 capture | 2025 (Jan–Sep) net | 2025 EUR/MW | 2025 capture |
|---|--:|--:|--:|--:|--:|--:|
| Perfect foresight | 470,350 | 47,035 | 1.000 | 472,553 | 47,255 | 1.000 |
| Lasso + MILP | 376,516 | 37,652 | 0.801 | 413,710 | 41,371 | 0.875 |
| GBR + MILP | 379,331 | 37,933 | 0.806 | 402,270 | 40,227 | 0.851 |
| Persistence + MILP | 334,265 | 33,427 | 0.711 | 378,304 | 37,830 | 0.801 |
| Persistence + heuristic | 189,278 | 18,928 | 0.402 | 217,836 | 21,784 | 0.461 |

**Monthly spread.** Perfect-foresight monthly net ranges from 8,934 EUR (February 2024) to 72,508 EUR (September 2025). GBR's monthly capture ratio ranges from 0.14 (February 2024, the weakest month for spreads) to 0.91 (August 2025). Full tables: `runs/latest/backtest/finance/by_month.csv` and `capture_by_month.csv`.

**Investment metrics** are not computed: `capex_eur_per_kwh` and `fixed_om_eur_per_kw_year` are not configured, and `investment.json` lists the missing inputs. No payback or NPV is claimed.

### 3.3 Sensitivity (one at a time; perfect foresight and GBR + MILP)

Base case: 10 MW / 20 MWh, round-trip efficiency 0.86, degradation 16.67 EUR/DC-MWh, cap 1.5 EFC/day. Net is the total over the 639 days (EUR); capture is GBR relative to perfect foresight under the same variant.

| Parameter | Value | PF net | PF EUR/MW-yr | GBR net | GBR EUR/MW-yr | GBR capture |
|---|---|--:|--:|--:|--:|--:|
| power (energy 20 MWh) | 5 MW (4 h) | 779,212 | 89,018 | 673,618 | 76,955 | 0.864 |
| | **10 MW (2 h)** | 942,903 | 53,859 | 781,601 | 44,645 | 0.829 |
| | 20 MW (1 h) | 1,008,965 | 28,816 | 822,777 | 23,499 | 0.815 |
| duration (power 10 MW) | 1 h | 504,482 | 28,816 | 411,388 | 23,499 | 0.815 |
| | **2 h** | 942,903 | 53,859 | 781,601 | 44,645 | 0.829 |
| | 4 h | 1,558,424 | 89,018 | 1,347,237 | 76,955 | 0.864 |
| round-trip efficiency | 0.80 | 857,600 | 48,987 | 692,146 | 39,536 | 0.807 |
| | **0.86** | 942,903 | 53,859 | 781,601 | 44,645 | 0.829 |
| | 0.92 | 1,030,811 | 58,880 | 872,946 | 49,863 | 0.847 |
| degradation cost × | 0 | 1,204,481 | 68,801 | 1,050,696 | 60,016 | 0.872 |
| | 0.5 | 1,068,779 | 61,049 | 910,365 | 52,001 | 0.852 |
| | **1** | 942,903 | 53,859 | 781,601 | 44,645 | 0.829 |
| | 2 | 729,282 | 41,657 | 567,028 | 32,389 | 0.778 |
| cycle cap (EFC/day) | 1.0 | 856,754 | 48,938 | 712,067 | 40,674 | 0.831 |
| | **1.5** | 942,903 | 53,859 | 781,601 | 44,645 | 0.829 |
| | 2.0 | 973,369 | 55,599 | 813,753 | 46,482 | 0.836 |
| | none | 973,501 | 55,607 | 813,588 | 46,473 | 0.836 |

Base values in bold. Each row is a full 639-day rolling backtest. GBR forecasts are computed once and reused (forecasts do not depend on the battery).

**Checks on these results:**

- **Scale invariance.** 5 MW / 20 MWh is exactly half of 10 MW / 40 MWh (779,212 vs 1,558,424), and 20 MW / 20 MWh is exactly twice 10 MW / 10 MWh. The EUR/MW-yr figures are identical, as they must be for a price taker at the same duration.
- **Monotonicity.** The perfect-foresight benchmark behaves as theory requires: it falls as degradation cost rises, and rises with power at fixed energy, with a looser cycle cap and with efficiency. The forecast-driven strategy need not be monotone. Removing the cap entirely even lowers GBR slightly (−165 EUR), because a looser constraint lets it act more on a wrong forecast.

**Interpretation:**

- **Duration and power are the dominant levers.** Per MW, a 4-hour battery earns about 1.65× a 2-hour one, and a 1-hour battery about 0.54×. Per MWh of capacity, shorter is better (1 h: 28,816 EUR/MWh-yr; 4 h: 22,254). The trade-off depends on the relative cost of power and energy, which is not configured. Forecast-driven batteries capture more of their benchmark at longer durations (0.815 → 0.864).
- **Degradation cost is the largest uncertain input.** Moving from 0× to 2× the placeholder value changes forecast-driven net by a factor of 1.85 (1.05 M → 0.57 M EUR) and the cycling rate by about 1.5×. Because this input is an unsourced placeholder, no absolute revenue figure here should be relied on until it is sourced.
- **The cycle cap binds only below about 2 EFC/day.** Between a cap of 2.0 and no cap, the benchmark gains 0.01 %.
- **Efficiency matters moderately.** ±0.06 of round-trip efficiency moves net by about ±9 % for perfect foresight and ±12 % for GBR.
