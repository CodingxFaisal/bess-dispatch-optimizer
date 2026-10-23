# Limitations

What GridFlex does not model, the assumptions it makes, and how each affects the results. Read this before quoting any number from [RESULTS.md](RESULTS.md).

## 1. Market and strategy design

| Limitation | Effect on results |
|---|---|
| **Day-ahead auction only.** No intraday trading, balancing reserves (FCR/aFRR) or imbalance settlement. | Revenue from other markets is excluded. Forecast errors cannot be corrected after gate closure, so the gap to perfect foresight is larger than for a desk that re-trades intraday. |
| **Fixed physical schedule, accepted at any clearing price.** Real bids are price–quantity curves. | Bids that would not clear at unfavourable prices are still executed, so losses on mis-forecast days (57–75 of 639) are probably overstated. |
| **Price taker.** The battery's bids do not move prices. | Reasonable at 10 MW; not valid for very large fleets. |
| **Daily cyclic terminal policy.** Every day returns to its start energy. | Energy is never shifted across days. Multi-day optimisation would earn somewhat more. |
| **One market time-unit regime per run.** Results cover the hourly regime (to 2025-09-30). | The 15-minute regime from 2025-10-01 is supported by the data layer but has not been evaluated. |
| **No trading or exchange fees** (`trading_cost_eur_per_mwh` = 0). | Net revenue is overstated by the fee × traded volume (about 27 GWh over the hold-out for the MILP strategies). |
| **Simple heuristic baseline.** One cycle per day, at most half the usable window from a 50 % start. | It is a weak baseline by design; it bounds rule-of-thumb operation, not the best simple rule. |

## 2. Battery physics

| Limitation | Effect |
|---|---|
| **Degradation cost is an unsourced placeholder** (120 EUR/kWh, 8000 cycles, 90 % DoD → 16.67 EUR per DC MWh). | The largest uncertainty in absolute revenue: 0×–2× changes forecast-driven net by a factor of 1.85 (RESULTS §3.3). Replace it with a cited value before quoting results. |
| Linear throughput degradation only: no calendar ageing, temperature, C-rate or DoD dependence, and no capacity fade within the horizon. | Cycling cost is approximate; long-horizon value is overstated where capacity fades. |
| Constant one-way efficiencies; no standing losses, auxiliary load or power-dependent efficiency. | Small optimistic bias, mostly at low power. |
| Availability is a deterministic derating or a given profile; no stochastic outages. | Revenue is overstated by the outage rate. |
| Only AC limits at the grid connection; no separate inverter and cell ratings, and no ramp limits. | Schedules may need to be smoothed in practice. |

## 3. Data and information

| Limitation | Effect |
|---|---|
| **Single forecast vintage.** SMARD stores the final value of each load forecast; any revision after publication is invisible. | Forecasts may use slightly better load information than was available at 12:00. Publication *times* are enforced; *versions* cannot be. |
| PV and wind day-ahead forecasts are published after gate closure and are therefore not used. | Forecast accuracy is lower than models with vintaged renewable forecasts could reach. |
| Price publication cut-off fixed at D−1 13:00; delayed or decoupled auctions are not modelled. | No effect on the 12:00 decision; matters only for intraday extensions. |
| Price plausibility maximum after 2022-09-20 is unresolved between sources (+4,000 vs +5,000 EUR/MWh). | Validation only; no observed price comes near either value. |
| Final SMARD chunks are not re-downloaded unless refreshed. | Silent historical revisions would be missed; `--refresh` re-downloads everything. |

## 4. Forecasting and evaluation

| Limitation | Effect |
|---|---|
| Point forecasts only; no quantiles, scenarios or uncertainty-aware dispatch. | Research question 4 (does uncertainty-aware optimisation help?) is **not yet answered**. |
| One selection year (2023), a small hyperparameter grid, and one hold-out (2024 – Sep 2025). | Model ranking may change in other regimes. GBR vs Lasso is statistically indistinguishable (DM p = 0.30). |
| The DM test treats days as independent one-step-ahead forecasts. | Significance levels are approximate. |

## 5. Financial interpretation

| Limitation | Effect |
|---|---|
| **Net operating revenue is not profit.** Capital cost, fixed O&M, grid charges and levies, taxes, insurance and financing are excluded. | Do not compare it with investment returns. |
| Annualisation on a 365-day basis over 639 days pools 2024 with January–September 2025. | Annual figures inherit that seasonal mix. 2025 is a partial year (flagged). |
| Investment metrics are **not computed** in the default configuration (no CAPEX or O&M inputs). | No payback or NPV is claimed. When configured, they assume the backtest revenue recurs unchanged every year. |
| Sensitivity is one-at-a-time around a single base case. | Interactions between parameters (for example duration × degradation cost) are not explored. |
| **Historical backtest results do not predict future revenue.** | Market design, renewables build-out and battery fleet growth will change spreads. |

## 6. Engineering

| Limitation | Effect |
|---|---|
| CI is configured (GitHub Actions; Ubuntu and Windows), but GitHub has not created any workflow runs for this repository, although the branches are pushed and a pull request is open. The repository's Actions settings allow all actions; the cause is not yet known. | Cross-platform behaviour is verified on Windows only. |
| On Windows, deep install paths can exceed the 260-character limit and silently break package installs (observed with scikit-learn). | Use a short path or enable long-path support (RUNNING.md). |
| The Streamlit UI is a viewer: it cannot launch runs or change assumptions. | Re-run the scripts to refresh results. |
