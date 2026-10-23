# Results

Every number here was produced by a command in this repository, run on validated SMARD data (Bundesnetzagentur | SMARD.de, CC BY 4.0) and recorded with its run metadata. Results from v0.1.0 are withdrawn; see [AUDIT.md](AUDIT.md) and [results/legacy_v0.1.0](../results/legacy_v0.1.0/README.md).

**Historical backtest or forecast accuracy is not a prediction of future performance.**

| Section | Status |
|---|---|
| 1. Day-ahead price forecast accuracy (hourly regime) | Stage 5, below |
| 2. Dispatch backtest: forecast-driven vs perfect foresight vs heuristic | Stage 6, below |
| 3. Financial metrics and sensitivity | Stage 7 |

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
- Code: working tree based on commit `c80c0db` (recorded as dirty in `metadata.json`); config fingerprint `50d4855ec19d…`.
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
