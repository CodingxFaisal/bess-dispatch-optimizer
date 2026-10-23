# Backtest methodology

Status: §1 (price forecasting protocol, Stage 5) is complete. §2 (the rolling dispatch backtest and settlement) follows in Stage 6.

---

## 1. Price forecasting protocol

Implemented in `gridflex.forecasting`. Configured under `forecasting:` in [configs/default.yaml](../configs/default.yaml).

### 1.1 Decision timing and information set

The strategy is **day-ahead only**. For delivery day $D$ (a Europe/Berlin calendar day of 23, 24 or 25 h), the forecast is issued at the **origin**:

$$t_0(D) = D{-}1 \text{ at } 12{:}00 \text{ Europe/Berlin}$$

This is the SDAC gate closure. The information set at $t_0$ is defined by each series' publication rule ([DATA_DICTIONARY §2](DATA_DICTIONARY.md)):

| Input | Public at $t_0(D)$? | Used |
|---|---|---|
| Day-ahead prices for days $\le D{-}1$ (published by $D{-}2$ 13:00) | yes | price lags, previous-day statistics, training targets |
| Day-ahead prices for $D$ (published $D{-}1$ ~12:45) | **no** | never as an input, only for evaluation |
| Load forecast for $D$ (published $D{-}1$ 10:00) | yes | `load_forecast` |
| Load forecast for $D{-}1$ | yes | `load_forecast_lag_1d` |
| PV and wind forecasts for $D$ (published $D{-}1$ 18:00) | **no** | excluded (AUDIT D1) |

**Enforcement.** Every feature value carries the publication instant of the data behind it (`FeatureTable.feature_published_at`), and every target carries its own (`target_published_at`). Two guards then apply:

- `check_prediction_rows` raises `InformationLeakageError` if any input for $D$ was published after $t_0$.
- `check_training_rows` raises if a training row belongs to day $D$ or later, or if its target or features were published after $t_0$.

The guards run inside every `forecast_day` call; they cannot be bypassed by a model subclass. Tests show they stop deliberately leaky models, and that forecasts stay bit-for-bit identical when every value published after $t_0$ is replaced by garbage.

**Vintage limitation.** SMARD stores the final value of each forecast series. A load forecast that was public at $t_0$ may have been revised later; the backtest cannot tell. This is a stated limitation, not an assumption that it never happened.

### 1.2 Features

All features are per delivery interval (60 or 15 minutes, UTC identity, local calendar):

| Feature | Definition |
|---|---|
| `price_lag_{k}d`, $k \in \{1,2,7\}$ | Price at the same **local wall-clock** time $k$ days earlier. Both 02:00 hours of a 25 h day map to 02:00 on $D{-}k$; a 25 h $D{-}k$ contributes its first (CEST) 02:00; wall-clock times missing on a 23 h $D{-}k$ fall back to exactly $24k$ h earlier. Fixes AUDIT D10 |
| `price_{mean,min,max}_lag_1d` | Statistics over all intervals of local day $D{-}1$; NaN if that day is incomplete |
| `load_forecast`, `load_forecast_lag_1d` | Day-ahead total-load forecast for $D$ and $D{-}1$ (mean MW) |
| `interval_of_day`, `hour`, `day_of_week`, `month`, `is_weekend` | Local calendar; deterministic, so no publication constraint |

Missing inputs are never filled. A forecast whose inputs contain NaN raises `ForecastError`. Training uses only complete rows.

### 1.3 Models

| ID | Model | Training |
|---|---|---|
| `persistence` | $\hat p_{D,i} = p_{D-1,i}$ (wall-clock) | none |
| `seasonal_naive` | $\hat p_{D,i} = p_{D-7,i}$ (wall-clock); the rMAE benchmark | none |
| `lasso` | LEAR-style (Lago et al., 2021; methodological reference epftoolbox, Apache-2.0; independent implementation). Price inputs and target are transformed as $\operatorname{asinh}((x-\tilde p)/\mathrm{MAD})$, inputs standardised, one-hot interval-of-day and day-of-week, and the L1 penalty chosen by AIC (`LassoLarsIC`) | trailing window of $W$ days, recalibrated every day |
| `gbr` | `HistGradientBoostingRegressor` (learning rate 0.05, 400 iterations, L2 1.0, no early stopping, `random_state` 42) | trailing window of $W$ days, recalibrated every 7 days |

**Training-only preprocessing.** The median $\tilde p$, the MAD, the standardisation statistics and the dummy levels are learned only from the training window of the fit. A test checks them against hand formulas on the training rows, and checks that predicting on extreme unseen rows leaves them unchanged.

**Rolling recalibration.** A fit made at origin $t_0(D)$ is reused for the following days until the recalibration interval elapses. Its metadata (`fitted_at_origin_utc`, `training_data_cutoff_utc`) is carried on each forecast, so a forecast never claims more recent training data than it had.

### 1.4 Forecast record

Every `PriceForecast` contains:

- the delivery day and the origin ($t_0$);
- values in EUR/MWh per UTC interval start;
- `model_id` and `model_version` (`gridflex-<version>+<config hash>`) plus the full model config;
- the feature list;
- `feature_cutoff_utc` (latest publication of any input, $\le t_0$);
- `training_data_cutoff_utc` (latest publication of any training target or feature, $\le t_0$);
- `training_delivery_end_utc` (may be after $t_0$: $D{-}1$ prices are known at $t_0$);
- `fitted_at_origin_utc` and the number of training rows.

Non-finite values are rejected.

### 1.5 Evaluation protocol

1. **Selection period** (2023-01-01 → 2023-12-31). Each candidate is run day by day. Candidates are:
   - Lasso with $W \in \{182, 364, 728\}$ days;
   - GBR with $W \in \{364, 728\}$ and depth $\in \{4, 6\}$.

   The configuration with the lowest MAE per family is chosen.
2. **Hold-out** (2024-01-01 → 2025-09-30). Run **once** with the chosen configurations and the two baselines. No choice of any kind uses hold-out data. The hold-out ends where the hourly MTU regime ends; the 15-minute regime is evaluated separately later.
3. Training history starts on 2021-01-01, so the 728-day window is full from the start of the selection period.

**Metrics** (EUR/MWh, over all delivery intervals):

- $\text{MAE} = \overline{|\hat p - p|}$
- $\text{RMSE} = \sqrt{\overline{(\hat p - p)^2}}$
- $\text{bias} = \overline{\hat p - p}$
- $\text{rMAE} = \text{MAE}/\text{MAE}_{\text{seasonal naive}}$ on the same intervals

These are reported overall and per calendar year.

**Diebold-Mariano test** (multivariate form after Lago et al. 2021). With daily loss differential $d_t = \overline{|e_{A,t}|} - \overline{|e_{B,t}|}$:

$$DM = \frac{\bar d}{s_d/\sqrt N}, \qquad p = 1 - \Phi(DM) \text{ for } H_1\text{: B more accurate than A}$$

No autocorrelation correction is applied.

Command: `uv run python scripts/evaluate_forecasts.py --offline`. Outputs go to `runs/latest/forecast_evaluation/`. Results are reported in [RESULTS.md](RESULTS.md).

### 1.6 Limitations (forecasting)

- Point forecasts only; no quantiles or scenarios (spec §12).
- One exogenous driver (load). PV and wind forecasts would require vintaged data published before 12:00, which SMARD does not provide.
- Model selection uses a small, documented grid and a single selection year. The hold-out spans one and three-quarter years, covering 2024's negative-price regime.
- The DM test treats days as independent one-step-ahead forecasts.
- The v0.1 `gridflex.forecast` module remains only for the v0.1 backtest until Stage 6.

---

## 2. Rolling dispatch backtest and settlement

*To be written in Stage 6.*
