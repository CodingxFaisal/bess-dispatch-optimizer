# Results

Every number here was produced by a command in this repository, run on validated SMARD data (Bundesnetzagentur | SMARD.de, CC BY 4.0) and recorded with its run metadata. Results from v0.1.0 are withdrawn; see [AUDIT.md](AUDIT.md) and [results/legacy_v0.1.0](../results/legacy_v0.1.0/README.md).

**Historical backtest or forecast accuracy is not a prediction of future performance.**

| Section | Status |
|---|---|
| 1. Day-ahead price forecast accuracy (hourly regime) | Stage 5, below |
| 2. Dispatch backtest: forecast-driven vs perfect foresight vs heuristic | Stage 6 |
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
